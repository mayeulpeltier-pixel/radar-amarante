# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE JETABLE : l'AfDB est-elle recuperable via un moteur
de recherche, maintenant que son site bloque tout acces automatique ?
===========================================================================

CONTEXTE
--------
Depuis le 29/10/2026, afdb.org repond HTTP 403 (defi Cloudflare "Just a
moment...") a TOUTE requete non-navigateur : le flux RSS du collecteur
`afdb_radar.py` ne ramene plus rien, y compris depuis une connexion de
particulier (verifie le 31/10). Mais Cloudflare laisse passer les robots
d'indexation : les pages d'avis AfDB restent indexees par les moteurs. On
mesure donc si un moteur de recherche (Brave Search API) permet de retrouver
ces avis, sans jamais toucher au site de l'AfDB.

DEUX QUESTIONS, une reponse chiffree chacune
--------------------------------------------
  1. RAPPEL   : combien d'avis de passation AfDB distincts retrouve-t-on, et
                de quels types (amont GPN/EOI vs tender SPN/AOI) ?
  2. FRAICHEUR: quel est le delai entre la publication d'un avis et son
                apparition dans l'index ? C'est LA question qui tranche :
                un index qui retarde de plusieurs semaines reste utile pour
                les avis amont (publies tres tot) mais pas pour les tenders.

METHODE
-------
Operateur `site:afdb.org` + libelle de chaque type d'avis (EN et FR), via
l'API Brave Search (cle BRAVE_API_KEY, offre gratuite 2000 req/mois ; on en
consomme ~20). On ne lit JAMAIS afdb.org : seules les metadonnees renvoyees
par Brave comptent (titre, url, date de publication `page_age`). Un resultat
n'est COMPTE que si son URL est bien une page d'avis de passation AfDB
(confirmation locale, comme la sonde BOAMP confirme l'expression exacte).

AUCUNE ECRITURE (ni Sheet, ni Postgres, ni etat). AUCUN LLM. Sortie code 0.
Declenchement MANUEL (workflow_dispatch).
"""

import datetime
import json
import os
import re
import sys
import time
from collections import Counter

try:
    import config_env
except Exception:  # pragma: no cover
    config_env = None


def _entier(nom, defaut):
    if config_env:
        return config_env.entier(nom, defaut)
    v = (os.environ.get(nom) or "").strip()
    try:
        return int(v)
    except ValueError:
        return defaut


ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SORTIE = os.environ.get("SONDE_SORTIE") or "sonde_afdb_recherche_sortie"
PAUSE = 1.1           # 1 req/s sur l'offre gratuite Brave ; marge de securite
COUNT = 20            # plafond Brave
PAGES = _entier("SONDE_PAGES", 3)       # offset 0..PAGES-1 (Brave : max 10)
JOURS_FENETRE = _entier("SONDE_JOURS", 30)   # "frais" = publie dans N jours

# Hote et chemins d'avis de passation AfDB (releves via les resultats indexes
# le 31/10/2026). Un resultat ne compte que si son URL est sous l'un d'eux.
HOTE = "afdb.org"
CHEMINS_AVIS = (
    "/project-related-procurement",
    "/procurement-notices",
    "/general-procurement-notices",
    "/request-for-expression-of-interest",
    "/invitation-for-bids",
    "/specific-procurement-notices",
    "/documents/project-related-procurement",
    "/documents/category/general-procurement-notices",
)

# Requetes : un type d'avis par requete, pour mesurer le rappel PAR type.
# (libelle serveur, code type, phase). EN et FR : l'AfDB publie dans les deux.
REQUETES = [
    ('site:afdb.org "general procurement notice"', "GPN", "amont"),
    ('site:afdb.org "avis general de passation"', "GPN", "amont"),
    ('site:afdb.org "request for expression of interest"', "EOI", "amont"),
    ('site:afdb.org "sollicitation de manifestation"', "EOI", "amont"),
    ('site:afdb.org "specific procurement notice"', "SPN", "tender"),
    ('site:afdb.org "invitation for bids"', "IFB", "tender"),
    ('site:afdb.org "avis specifique de passation"', "SPN", "tender"),
    ('site:afdb.org "appel d\'offres"', "AON", "tender"),
]

# Marqueurs d'attribution : un avis d'attribution n'est PAS une opportunite.
MARQUEURS_ATTRIBUTION = ("award", "attribution", "attribue", "contract-award")

# Seuil de decision (HYPOTHESE, a discuter) : au moins 8 avis AfDB distincts
# retrouves, dont au moins un publie dans la fenetre de fraicheur. En dessous,
# le moteur ne remplace pas une vraie source ; la voie IATI reste le repli.
SEUIL_AVIS = 8


# ===========================================================================
# APPEL BRAVE (injectable pour tests : aucun reseau hors Actions)
# ===========================================================================
def _fetch_http(requete, offset):
    import requests
    cle = os.environ.get("BRAVE_API_KEY", "").strip()
    if not cle:
        return 0, {"_erreur": "BRAVE_API_KEY absente"}
    entetes = {"X-Subscription-Token": cle, "Accept": "application/json",
               "Accept-Encoding": "gzip"}
    params = {"q": requete, "count": COUNT, "offset": offset, "country": "ALL",
              "search_lang": "en"}
    try:
        rep = requests.get(ENDPOINT, headers=entetes, params=params, timeout=45)
    except Exception as e:  # noqa: BLE001
        return 0, {"_erreur": "reseau: {}".format(e)}
    try:
        return rep.status_code, rep.json()
    except ValueError:
        return rep.status_code, {"_erreur": (rep.text or "")[:200]}


def resultats_web(charge):
    """Liste des resultats web d'une reponse Brave, tolerante a la forme."""
    if not isinstance(charge, dict):
        return []
    web = charge.get("web")
    if isinstance(web, dict) and isinstance(web.get("results"), list):
        return web["results"]
    return []


# ===========================================================================
# CLASSIFICATION (fonctions PURES)
# ===========================================================================
def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def est_avis_afdb(url):
    """Vrai si l'URL est une page d'avis de passation AfDB (confirmation
    locale). Un resultat hors de ces chemins est du bruit d'indexation."""
    u = _norm(url)
    if HOTE not in u:
        return False
    return any(ch in u for ch in CHEMINS_AVIS)


def est_attribution(titre, url):
    t = _norm(titre) + " " + _norm(url)
    return any(m in t for m in MARQUEURS_ATTRIBUTION)


def date_publication(resultat):
    """`page_age` est la date de publication ISO (Brave). None si absente ou
    illisible. On ne devine pas : une date absente ne compte pas comme fraiche."""
    brut = resultat.get("page_age") or resultat.get("age")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(brut or ""))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normaliser(resultat, code_type, phase):
    url = str(resultat.get("url") or "")
    titre = str(resultat.get("title") or "")
    return {
        "url": url, "titre": re.sub(r"\s+", " ", titre).strip(),
        "type": code_type, "phase": phase,
        "attribution": est_attribution(titre, url),
        "date": date_publication(resultat),
    }


# ===========================================================================
# COLLECTE
# ===========================================================================
def sonder(fetch=None, aujourd=None, requetes=REQUETES):
    """Interroge Brave pour chaque requete, pagine, confirme localement.
    Renvoie (avis_par_url, journal). `fetch(requete, offset)->(statut, json)`
    est injectable ; sans lui, appel HTTP reel (en Actions)."""
    fetch = fetch or _fetch_http
    aujourd = aujourd or datetime.date.today()
    journal = {"requetes": 0, "erreurs": [], "par_requete": {}, "brut": 0}
    par_url = {}
    for requete, code_type, phase in requetes:
        bruts, confirmes = 0, 0
        for offset in range(PAGES):
            journal["requetes"] += 1
            statut, charge = fetch(requete, offset)
            if statut != 200:
                motif = charge.get("_erreur") if isinstance(charge, dict) else ""
                journal["erreurs"].append("{} [offset {}] : HTTP {} {}".format(
                    code_type, offset, statut, motif or ""))
                break
            lot = resultats_web(charge)
            bruts += len(lot)
            for r in lot:
                a = normaliser(r, code_type, phase)
                if not est_avis_afdb(a["url"]):
                    continue
                confirmes += 1
                # Memorise la 1re occurrence ; garde la date la plus precise.
                if a["url"] not in par_url:
                    par_url[a["url"]] = a
            if len(lot) < COUNT:
                break            # derniere page pour cette requete
            time.sleep(PAUSE)
        journal["brut"] += bruts
        journal["par_requete"][requete] = (bruts, confirmes)
        time.sleep(PAUSE)
    return list(par_url.values()), journal


# ===========================================================================
# ANALYSE ET VERDICT
# ===========================================================================
def stats(avis, aujourd=None, jours=JOURS_FENETRE):
    aujourd = aujourd or datetime.date.today()
    seuil = aujourd - datetime.timedelta(days=jours)
    opportunites = [a for a in avis if not a["attribution"]]
    dates = [a["date"] for a in opportunites if a["date"]]
    frais = [a for a in opportunites if a["date"] and a["date"] >= seuil]
    return {
        "total": len(avis),
        "opportunites": len(opportunites),
        "attributions": sum(1 for a in avis if a["attribution"]),
        "par_type": Counter(a["type"] for a in opportunites),
        "par_phase": Counter(a["phase"] for a in opportunites),
        "avec_date": len(dates),
        "plus_recent": max(dates) if dates else None,
        "retard_jours": (aujourd - max(dates)).days if dates else None,
        "frais": len(frais),
        "liste": opportunites,
    }


def verdict(st, erreurs):
    if st["total"] == 0 and erreurs:
        return "NON CONCLU", "aucun resultat exploitable (erreurs API ou cle)."
    if st["opportunites"] < SEUIL_AVIS:
        return "INSUFFISANT", ("{} avis d'opportunite distincts (< seuil {}) : "
                               "le moteur ne remplace pas une source dediee."
                               .format(st["opportunites"], SEUIL_AVIS))
    if not st["frais"]:
        return "AMONT SEULEMENT", ("{} avis retrouves mais aucun publie dans les "
                                   "{} derniers jours (index en retard de {} j) : "
                                   "utile pour l'amont, pas pour les tenders."
                                   .format(st["opportunites"], JOURS_FENETRE,
                                           st["retard_jours"]))
    return "EXPLOITABLE", ("{} avis d'opportunite, dont {} frais (< {} j). "
                           "Index a jour.".format(st["opportunites"], st["frais"],
                                                  JOURS_FENETRE))


# ===========================================================================
# RAPPORT
# ===========================================================================
def _d(x):
    return x.isoformat() if x else "n.c."


def _cell(s, n=90):
    return str(s or "").replace("|", "/").replace("\n", " ")[:n]


def rapport(avis, journal, aujourd=None):
    aujourd = aujourd or datetime.date.today()
    st = stats(avis, aujourd)
    v = verdict(st, journal["erreurs"])
    md = ["# Sonde AfDB via moteur de recherche (Brave)", "",
          "Date : {}. Aucune requete vers afdb.org, aucune ecriture, aucun LLM."
          .format(aujourd.isoformat()), "",
          "## Verdict : {}".format(v[0]), "", v[1], "",
          "- Requetes API : {} ; resultats bruts : {} ; avis AfDB confirmes "
          "(distincts) : {} ; erreurs : {}".format(
              journal["requetes"], journal["brut"], st["total"],
              len(journal["erreurs"]))]
    md.append("- Opportunites : {} | attributions ecartees : {}".format(
        st["opportunites"], st["attributions"]))
    md.append("- Par phase : amont {} / tender {}".format(
        st["par_phase"].get("amont", 0), st["par_phase"].get("tender", 0)))
    md.append("- Par type : " + (", ".join(
        "{} ({})".format(t, n) for t, n in st["par_type"].most_common()) or "aucun"))
    md.append("- Avis datés : {}/{} | plus récent : {} | retard de l'index : "
              "{} j | frais (< {} j) : {}".format(
                  st["avec_date"], st["opportunites"], _d(st["plus_recent"]),
                  st["retard_jours"] if st["retard_jours"] is not None else "n.c.",
                  JOURS_FENETRE, st["frais"]))
    if journal["erreurs"]:
        md += ["", "Erreurs :", ""] + ["- {}".format(_cell(e, 160))
                                       for e in journal["erreurs"][:8]]
    if st["liste"]:
        md += ["", "| Date | Type | Phase | Titre | Lien |",
               "|---|---|---|---|---|"]
        for a in sorted(st["liste"], key=lambda x: x["date"] or datetime.date.min,
                        reverse=True)[:40]:
            md.append("| {} | {} | {} | {} | [avis]({}) |".format(
                _d(a["date"]), a["type"], a["phase"], _cell(a["titre"]), a["url"]))
    md.append("")
    md.append("Rappel : site AfDB sous défi Cloudflare depuis le 29/10/2026 ; "
              "cette voie lit l'index du moteur, pas afdb.org.")
    return "\n".join(md), v


def main():
    avis, journal = sonder()
    md, _ = rapport(avis, journal)
    print(md)
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "rapport.md"), "w", encoding="utf-8") as f:
        f.write(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
