# -*- coding: utf-8 -*-
"""
Radar Amarante - SONDE jetable : la DEMANDE PRIVEE d'IE / conformite est-elle
captable, et avec quelle precision, hors commande publique ?
===============================================================================

POURQUOI
--------
La commande publique (BOAMP, TED, UNGM) n'est qu'une fraction du marche IE.
L'essentiel de la due diligence et de la conformite se vend a des ENTREPRISES :
operations de M&A (due diligence d'acquisition), societes sous enquete, montee
en conformite Sapin II, recrutements IE/conformite. Le radar IE n'en voit rien.
Cette sonde mesure DEUX canaux de detection de ce besoin prive, par FAMILLE de
signal, pour decider lesquels valent un collecteur (et lesquels sont trop
bruites pour servir sans qualification LLM).

DEUX CANAUX
-----------
  1. ADZUNA (offres d'emploi) : une entreprise qui recrute un poste IE /
     conformite / due diligence a ce besoin en interne -> prospect concret,
     date, nomme, peu bruite. Recherche par INTITULE DE POSTE (pas par
     entreprise, contrairement au moteur Operations). Cle ADZUNA_APP_ID/KEY.
  2. PRESSE (Google News RSS) : familles M&A, enquete/PNF, Sapin II, IE
     explicite. Plus bruite (enquetes sur des particuliers, colloques
     anniversaires, commentaires sectoriels) : la sonde quantifie le bruit.

AUCUNE ECRITURE. AUCUN LLM. Declenchement manuel (workflow_dispatch). Code 0.
"""

import csv
import datetime
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET


def _entier(nom, defaut):
    try:
        return int(os.environ.get(nom) or defaut)
    except ValueError:
        return defaut


JOURS = _entier("SONDE_JOURS", 45)
SORTIE = os.environ.get("SONDE_SORTIE") or "sonde_demande_privee_ie_sortie"
PAUSE = float(os.environ.get("SONDE_PAUSE", "0.5"))

# --- Adzuna ---------------------------------------------------------------
ADZUNA_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_KEY = os.environ.get("ADZUNA_APP_KEY", "")
ADZUNA_EP = "https://api.adzuna.com/v1/api/jobs/{}/search/1"
ADZUNA_RES = 50
# Intitules de poste par portail (FR en francais, GB en anglais). Une entreprise
# qui recrute sur ces intitules valorise l'IE / la conformite.
POSTES = {
    "fr": ["intelligence économique", "due diligence", "conformité anticorruption",
           "compliance officer", "déontologue", "sécurité économique",
           "analyste risques tiers", "KYC conformité"],
    "gb": ["competitive intelligence", "due diligence analyst", "compliance officer",
           "third party risk", "KYC analyst", "integrity due diligence"],
}

# --- Presse (Google News RSS) ---------------------------------------------
GNEWS = "https://news.google.com/rss/search?q={}&hl=fr&gl=FR&ceid=FR:fr"
FAMILLES_PRESSE = {
    "ma_due_diligence": '("due diligence" OR "audit d\'acquisition") (acquisition OR rachat OR fusion)',
    "enquete_corruption": '(mise en examen OR perquisition OR "enquête préliminaire" OR PNF) (corruption OR fraude OR favoritisme)',
    "sapin_conformite": '("programme de conformité" OR "cartographie des risques de corruption" OR "mise en conformité Sapin")',
    "ie_explicite": '"intelligence économique" (entreprise OR groupe OR PME)',
}

# Heuristique de precision SANS LLM : un signal exploitable nomme une
# organisation (majuscules) ET porte un verbe de besoin, pas un commentaire.
RE_ORG = re.compile(r"\b([A-ZÉÈÀ][\wÉÈÀ&'-]{2,}(?:\s+[A-ZÉÈÀ][\wÉÈÀ&'-]+){0,3})\b")
# Listes en forme SANS ACCENT (cf. _sansacc) : la comparaison l'est aussi.
BRUIT_PRESSE = ("colloque", "anniversaire", "10 ans", "dix ans", "tribune",
                "chronique", "podcast", "webinaire", "replay", "panorama",
                "qu'est-ce que", "definition", "checklist", "livre blanc")
VERBES_BESOIN = ("acquisition", "rachat", "rachete", "fusion", "acquiert", "acquis",
                 "mise en examen", "perquisition", "enquete", "condamn", "sanction",
                 "recrute", "nomme", "programme de conformite", "mise en conformite")


# ===========================================================================
# OUTILS
# ===========================================================================
def _session():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]))))
    return s


_SES = None


def session():
    global _SES
    if _SES is None:
        _SES = _session()
    return _SES


def _norm(s):
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def _sansacc(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def jour_rss(pubdate):
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y"):
        try:
            return datetime.datetime.strptime(str(pubdate or "")[:len(
                datetime.datetime.now().strftime(fmt)) + 5].strip(), fmt).date()
        except ValueError:
            continue
    m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", str(pubdate or ""))
    if m:
        for f in ("%d %b %Y",):
            try:
                return datetime.datetime.strptime(m.group(0), f).date()
            except ValueError:
                pass
    return None


# ===========================================================================
# CANAL 1 : ADZUNA (offres d'emploi IE / conformite)
# ===========================================================================
def _fetch_adzuna(pays, params):
    rep = session().get(ADZUNA_EP.format(pays), params=params, timeout=25)
    if rep.status_code >= 400:
        return {"_http": rep.status_code}
    return rep.json()


def sonder_adzuna(fetch=None, postes=None):
    """Par (portail, intitule) : nombre d'offres, offres recentes, entreprises
    qui recrutent. `fetch(pays, params)->dict` injectable."""
    postes = postes or POSTES
    journal = {"actif": bool(ADZUNA_ID and ADZUNA_KEY) or fetch is not None,
               "erreurs": [], "par_requete": {}, "entreprises": {}}
    if not journal["actif"]:
        journal["erreurs"].append("ADZUNA_APP_ID/KEY absents : canal inactif.")
        return journal
    fetch = fetch or _fetch_adzuna
    seuil = datetime.date.today() - datetime.timedelta(days=JOURS)
    for pays, intitules in postes.items():
        for intitule in intitules:
            params = {"app_id": ADZUNA_ID, "app_key": ADZUNA_KEY,
                      "what_phrase": intitule, "results_per_page": ADZUNA_RES,
                      "max_days_old": JOURS, "content-type": "application/json"}
            try:
                data = fetch(pays, params)
            except Exception as ex:  # noqa: BLE001
                journal["erreurs"].append("{}/{} : {}".format(pays, intitule, ex))
                continue
            if "_http" in data:
                journal["erreurs"].append("{}/{} : HTTP {}".format(pays, intitule, data["_http"]))
                continue
            offres = data.get("results") or []
            frais = 0
            for o in offres:
                nom = _norm((o.get("company") or {}).get("display_name"))
                if nom:
                    journal["entreprises"][nom] = journal["entreprises"].get(nom, 0) + 1
                d = str(o.get("created") or "")[:10]
                if d and d >= seuil.isoformat():
                    frais += 1
            journal["par_requete"]["{}:{}".format(pays, intitule)] = (
                int(data.get("count") or len(offres)), len(offres), frais)
            time.sleep(PAUSE)
    return journal


# ===========================================================================
# CANAL 2 : PRESSE (Google News RSS, par famille de signal)
# ===========================================================================
def _fetch_rss(url):
    rep = session().get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
    rep.raise_for_status()
    return rep.text


def precision_titre(titre):
    """(nomme_org, est_bruit) : heuristique sans LLM pour juger si un titre de
    presse est un signal de besoin exploitable ou du commentaire."""
    t = _norm(titre)
    bas = _sansacc(t)
    est_bruit = any(b in bas for b in BRUIT_PRESSE)
    a_besoin = any(v in bas for v in VERBES_BESOIN)
    # "nomme une organisation" : au moins un groupe en capitales hors debut de phrase
    orgs = [m for m in RE_ORG.findall(t) if len(m) > 3]
    nomme = len(orgs) >= 1 and a_besoin
    return nomme, est_bruit


def sonder_presse(get=None, familles=None):
    get = get or _fetch_rss
    familles = familles or FAMILLES_PRESSE
    seuil = datetime.date.today() - datetime.timedelta(days=JOURS)
    journal = {"erreurs": [], "par_famille": {}, "exemples": {}}
    for nom, requete in familles.items():
        url = GNEWS.format(urllib.parse.quote(requete))
        try:
            root = ET.fromstring(get(url))
        except Exception as ex:  # noqa: BLE001
            journal["erreurs"].append("{} : {}".format(nom, ex))
            continue
        items = root.findall(".//item")
        recents, exploitables, bruit, ex = 0, 0, 0, []
        vus = set()
        for it in items:
            titre = _norm(it.findtext("title"))
            lien = _norm(it.findtext("link"))
            if lien in vus:
                continue
            vus.add(lien)
            d = jour_rss(it.findtext("pubDate"))
            frais = d is not None and d >= seuil
            if frais:
                recents += 1
            nomme, est_bruit = precision_titre(titre)
            if est_bruit:
                bruit += 1
            elif frais and nomme:
                exploitables += 1
                if len(ex) < 5:
                    ex.append(titre[:110])
        journal["par_famille"][nom] = {
            "items": len(vus), "recents": recents,
            "exploitables": exploitables, "bruit": bruit}
        journal["exemples"][nom] = ex
        time.sleep(PAUSE)
    return journal


# ===========================================================================
# VERDICTS
# ===========================================================================
SEUIL_ADZUNA_MOIS = 3.0      # offres fraiches/mois pour juger un intitule utile
SEUIL_PRESSE_PRECIS = 0.3    # part exploitable/recents pour garder une famille


def verdict_adzuna(journal):
    if not journal["actif"]:
        return "NON CONCLU", "clés Adzuna absentes du run."
    if journal["par_requete"] and not any(v[2] for v in journal["par_requete"].values()):
        if journal["erreurs"]:
            return "NON CONCLU", "aucune offre lue (erreurs API)."
    frais = sum(v[2] for v in journal["par_requete"].values())
    par_mois = round(frais / max(JOURS / 30.0, 1.0), 1)
    if par_mois >= SEUIL_ADZUNA_MOIS:
        return "EXPLOITABLE", "{} offres fraîches/mois sur les intitulés IE/conformité, " \
            "{} entreprises nommées.".format(par_mois, len(journal["entreprises"]))
    return "MARGINAL", "{} offres fraîches/mois (sous le seuil {}).".format(par_mois, SEUIL_ADZUNA_MOIS)


def verdict_presse(journal):
    if not journal["par_famille"]:
        return "NON CONCLU", "aucune donnée lue."
    bonnes = []
    for nom, s in journal["par_famille"].items():
        base = max(s["recents"], 1)
        if s["recents"] >= 3 and s["exploitables"] / base >= SEUIL_PRESSE_PRECIS:
            bonnes.append(nom)
    if bonnes:
        return "EXPLOITABLE (avec qualification)", \
            "familles nettes : {}. Les autres demandent une qualification LLM.".format(
                ", ".join(bonnes))
    return "A QUALIFIER", "aucune famille nette sans LLM : presse trop bruitée, " \
        "à garder seulement avec une qualification LLM."


# ===========================================================================
# RAPPORT
# ===========================================================================
def rapport(adz, presse, aujourd=None):
    aujourd = aujourd or datetime.date.today()
    va = verdict_adzuna(adz)
    vp = verdict_presse(presse)
    md = ["# Sonde demande privée IE / conformité", "",
          "Fenêtre {} jours. Aucune écriture, aucun LLM.".format(JOURS), "",
          "| Canal | Verdict |", "|---|---|",
          "| Adzuna (recrutements IE/conformité) | **{}** |".format(va[0]),
          "| Presse (Google News) | **{}** |".format(vp[0]), "",
          "## 1. Adzuna — {}".format(va[0]), "", va[1], ""]
    if adz["par_requete"]:
        md += ["| Portail : intitulé | annoncées | lues | fraîches |", "|---|---|---|---|"]
        for k, v in sorted(adz["par_requete"].items(), key=lambda kv: -kv[1][2]):
            md.append("| {} | {} | {} | {} |".format(k, v[0], v[1], v[2]))
        top = sorted(adz["entreprises"].items(), key=lambda kv: -kv[1])[:15]
        if top:
            md += ["", "- Entreprises qui recrutent (échantillon) : "
                   + ", ".join("{} ({})".format(n, c) for n, c in top)]
    for e in adz["erreurs"][:5]:
        md.append("- Erreur : {}".format(e))

    md += ["", "## 2. Presse — {}".format(vp[0]), "", vp[1], "",
           "| Famille | items | récents | exploitables | bruit |", "|---|---|---|---|---|"]
    for nom, s in presse["par_famille"].items():
        md.append("| {} | {} | {} | {} | {} |".format(
            nom, s["items"], s["recents"], s["exploitables"], s["bruit"]))
    for nom, ex in presse["exemples"].items():
        if ex:
            md += ["", "**{}** (exemples exploitables) :".format(nom)] + ["- " + x for x in ex]
    for e in presse["erreurs"][:5]:
        md.append("- Erreur : {}".format(e))
    md += ["", "## Lecture", "",
           "Adzuna = signal concret et daté (une entreprise qui recrute en "
           "IE/conformité). Presse = volume réel mais bruité ; à n'exploiter "
           "qu'avec une qualification LLM qui extrait (entreprise, besoin) et "
           "jette commentaires et affaires de particuliers."]
    return "\n".join(md) + "\n", (va, vp)


def main():
    adz = sonder_adzuna()
    presse = sonder_presse()
    md, _ = rapport(adz, presse)
    print(md)
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "rapport.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(SORTIE, "entreprises_adzuna.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entreprise", "offres"])
        for n, c in sorted(adz["entreprises"].items(), key=lambda kv: -kv[1]):
            w.writerow([n, c])
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
