# -*- coding: utf-8 -*-
"""
Radar Amarante - COLLECTEUR IE INTERNATIONAL (chantier IE-5, 31/10/2026).

Applique la lentille IE aux marches INTERNATIONAUX : TED (institutions et
agences hors France) et UNGM (agences ONU). Meme pole ARISE, meme classeur,
meme table, meme moteur que le collecteur BOAMP (collecteur_ie.py) : ce module
n'en est qu'une SOURCE de plus. Il IMPORTE collecteur_ie et REUTILISE son
ecriture, son schema, sa memoire et ses lignes de sortie SANS les dupliquer
(exactement comme ted_complet_bm importe le coeur TED). Resultat : BOAMP, TED
et UNGM remontent dans le MEME onglet "Leads IE", distingues par la colonne
`source` ; le dashboard ARISE ne demande aucun cablage.

SONDE PREALABLE (sonde_international_ie, 31/10/2026, run reel 180 jours)
----------------------------------------------------------------------
  - TED hors France : ~1,7 avis IE utile / mois -> EXPLOITABLE.
  - UNGM            : ~1,2 avis IE utile / mois -> EXPLOITABLE.
  - Banque mondiale : 1 avis / 6 mois -> ECARTEE (pas de collecteur).
Syntaxes verifiees en direct :
  - TED : champ `title-proc` (le plein texte `FT` noie 3000 avis/6 mois pour
    "due diligence" contre 14 sur le titre ; `notice-title` est prefixe du
    libelle CPV traduit, qui fabrique de faux termes IE -> on ne le lit pas).
  - UNGM : la recherche exige le jeton anti-falsification de la page publique
    et PageSize <= 15 (sinon HTTP 400) -- meme lecon que le collecteur UNGM
    Operations repare le 30/10.

PERIMETRE
  - TED : acheteurs FRANCAIS ECARTES (deja couverts par le BOAMP, IE-4) : on
    ne veut pas le meme marche deux fois.
  - Attributions (TED can-* / UNGM award) ECARTEES : ce collecteur vise les
    opportunites ouvertes, pas les marches deja attribues.

VARIABLES
  RADAR_IE=1               active (meme interrupteur que le volet IE)
  RADAR_IE_DEBUG=1         collecte + pre-filtre SANS LLM ni ecriture
  RADAR_IE_INTL_JOURS      fenetre de publication (defaut 30)
  RADAR_IE_INTL_BUDGET     avis analyses max par run (defaut 20)
  IE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE, DATABASE_URL, ANTHROPIC_API_KEY
"""

import datetime
import html as _html
import json
import os
import re
import time

import lentille_ie as L
import collecteur_ie as C


def _entier(nom, defaut):
    try:
        return int(os.environ.get(nom) or defaut)
    except ValueError:
        return defaut


DEBUG = os.environ.get("RADAR_IE_DEBUG", "0") == "1"
JOURS = _entier("RADAR_IE_INTL_JOURS", 30)
BUDGET = _entier("RADAR_IE_INTL_BUDGET", 20)
PAUSE = float(os.environ.get("RADAR_IE_INTL_PAUSE", "1.0"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Termes FORTS de la lentille : c'est le pre-filtre local qui tranche ensuite,
# mais on ne demande au serveur que les termes sans ambiguite (les "a_mesurer"
# n'apporteraient que du bruit, lecon IE-0).
REQUETES = sorted({m for m, n in L.TERMES if n == "fort"})

# --- TED -------------------------------------------------------------------
URL_TED = "https://api.ted.europa.eu/v3/notices/search"
LIEN_TED = "https://ted.europa.eu/en/notice/{}/html"
LIMITE_TED = 100
PAGES_TED = _entier("RADAR_IE_INTL_PAGES_TED", 3)
TAILLE_GROUPE_TED = 6
CHAMPS_TED = ["publication-number", "title-proc", "buyer-name", "buyer-country",
              "publication-date", "deadline", "notice-type", "description-proc"]

# --- UNGM ------------------------------------------------------------------
PAGE_UNGM = "https://www.ungm.org/Public/Notice"
URL_UNGM = "https://www.ungm.org/Public/Notice/Search"
LIEN_UNGM = "https://www.ungm.org/Public/Notice/{}"
TAILLE_UNGM = 15                      # plafond du portail (sinon HTTP 400)
PAGES_UNGM = _entier("RADAR_IE_INTL_PAGES_UNGM", 3)
ENTETES_UNGM = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                "X-Requested-With": "XMLHttpRequest", "Referer": PAGE_UNGM,
                "Content-Type": "application/json"}

RE_INSTITUTION_UE = re.compile(
    r"(?i)\beuropean\b|\beuropeenne?\b|\bfrontex\b|\beuaa\b|\beuropol\b|\beeas\b|"
    r"\bolaf\b|\bcommission\b|\bparliament\b|\bparlement\b|\bcouncil of the\b")
RE_JETON = re.compile(r'name="__RequestVerificationToken"[^>]*\bvalue="([^"]+)"', re.I)
RE_LIGNE_UNGM = re.compile(r'<div role="row"[^>]*data-noticeid="(\d+)"')
RE_CELLULE_UNGM = re.compile(r'<div role="cell" class="([^"]*)"[^>]*>')


# ===========================================================================
# OUTILS COMMUNS
# ===========================================================================
_SESSION = None


def session():
    global _SESSION
    if _SESSION is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util import Retry
        s = requests.Session()
        retry = Retry(total=4, connect=3, read=3, backoff_factor=2.0,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=frozenset(["GET", "POST"]),
                      respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _SESSION = s
    return _SESSION


def _texte_html(s):
    t = re.sub(r"(?s)<[^>]+>", " ", str(s or ""))
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def morceaux(valeur):
    """Toutes les chaines d'un champ TED (chaine, liste, dict multilingue)."""
    if valeur is None:
        return []
    if isinstance(valeur, str):
        return [valeur]
    if isinstance(valeur, list):
        out = []
        for v in valeur:
            out.extend(morceaux(v))
        return out
    if isinstance(valeur, dict):
        out = []
        for v in valeur.values():
            out.extend(morceaux(v))
        return out
    return [str(valeur)]


def texte_langue(valeur, langues=("eng", "fra")):
    """Anglais, sinon francais, sinon premiere langue (titre TED multilingue)."""
    if isinstance(valeur, dict):
        for lg in langues:
            if valeur.get(lg):
                return " ".join(morceaux(valeur[lg])).strip()
    tout = morceaux(valeur)
    return (tout[0] if tout else "").strip()


def _iso(brut):
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", " ".join(morceaux(brut)))
    return "{}-{}-{}".format(m.group(1), m.group(2), m.group(3)) if m else ""


def _iso_libre(s):
    """'24-Sep-2026', '15-Oct-2026 14:00', '2026-10-15' -> 'YYYY-MM-DD' ou ''."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    m = re.search(r"\b(\d{1,2})-([A-Za-z]{3})-(\d{4})\b", s)
    if m:
        try:
            return datetime.datetime.strptime(m.group(0), "%d-%b-%Y").date().isoformat()
        except ValueError:
            return ""
    return _iso(s)


def nature_ted(type_notice):
    """Attribution (can-*/award) a ecarter, vs avis ouvert."""
    t = (type_notice or "").lower()
    if t.startswith("can") or "award" in t:
        return "attribution"
    return "avis"


# ===========================================================================
# COLLECTE TED (champ title-proc, fenetre de dates, hors France)
# ===========================================================================
def requete_ted(termes, debut, fin):
    clauses = " OR ".join('title-proc ~ "{}"'.format(t.replace('"', "")) for t in termes)
    return "({}) AND PD = ({} <> {}) SORT BY publication-date DESC".format(
        clauses, debut.strftime("%Y%m%d"), fin.strftime("%Y%m%d"))


def _corps_ted(query, page):
    return {"query": query, "fields": CHAMPS_TED, "page": page, "limit": LIMITE_TED,
            "scope": "ALL", "checkQuerySyntax": False, "paginationMode": "PAGE_NUMBER"}


def _fetch_ted(corps):
    rep = session().post(URL_TED, json=corps, timeout=60)
    try:
        return rep.status_code, rep.json()
    except ValueError:
        return rep.status_code, {"_brut": rep.text[:200]}


def normaliser_ted(rec):
    pub = " ".join(morceaux(rec.get("publication-number"))).strip()
    acheteur = texte_langue(rec.get("buyer-name"))
    pays = sorted(set(morceaux(rec.get("buyer-country"))))
    type_n = " ".join(morceaux(rec.get("notice-type")))
    titre = texte_langue(rec.get("title-proc"))
    desc = _texte_html(texte_langue(rec.get("description-proc")))
    return {
        "source": "TED", "id_avis": pub,
        "titre": re.sub(r"\s+", " ", titre).strip()[:400],
        "acheteur": acheteur, "nature": nature_ted(type_n),
        "date_publication": _iso(rec.get("publication-date")),
        "date_limite": _iso(rec.get("deadline")),
        "description": (desc or titre)[:2500], "corps": desc,
        "pays": ",".join(pays), "francais": "FRA" in pays,
        "lien": LIEN_TED.format(pub) if pub else "",
    }


def collecter_ted(debut, fin, fetch=None, requetes=REQUETES):
    """Avis TED par groupes de termes (OR), repli terme par terme si l'API
    refuse le groupement (HTTP 400). Dedup par publication-number."""
    fetch = fetch or _fetch_ted
    journal = {"mode": "groupe", "erreurs": [], "plafonds": 0, "requetes": 0}
    par_id = {}
    groupes = [requetes[i:i + TAILLE_GROUPE_TED]
               for i in range(0, len(requetes), TAILLE_GROUPE_TED)]
    for groupe in groupes:
        lots = [groupe] if journal["mode"] == "groupe" else [[t] for t in groupe]
        while lots:
            termes = lots.pop(0)
            journal["requetes"] += 1
            recs, statut, charge = _paginer_ted(requete_ted(termes, debut, fin), journal, fetch)
            # On ne scinde QU'EN mode groupe : une fois en mode terme, un 400
            # sur un terme unique est journalise, jamais re-scinde (sinon boucle
            # infinie sur ce terme).
            if statut == 400 and journal["mode"] == "groupe" and len(termes) > 1:
                journal["mode"] = "terme"
                lots = [[t] for t in termes] + lots
                continue
            if statut != 200:
                journal["erreurs"].append("TED {} : HTTP {} {}".format(
                    termes, statut, str(charge)[:120]))
                continue
            for r in recs:
                a = normaliser_ted(r)
                if a["id_avis"]:
                    par_id.setdefault(a["id_avis"], a)
            time.sleep(PAUSE)
    return list(par_id.values()), journal


def _paginer_ted(query, journal, fetch):
    out = []
    for page in range(1, PAGES_TED + 1):
        statut, charge = fetch(_corps_ted(query, page))
        if statut != 200:
            return out, statut, charge
        lot = charge.get("notices") or []
        out.extend(lot)
        if len(lot) < LIMITE_TED:
            break
        if page == PAGES_TED:
            journal["plafonds"] += 1
        time.sleep(PAUSE)
    return out, 200, {}


# ===========================================================================
# COLLECTE UNGM (jeton anti-falsification, PageSize 15, recherche par Title)
# ===========================================================================
_JETON = {"v": None}


def _jeton():
    if _JETON["v"] is None:
        try:
            rep = session().get(PAGE_UNGM, headers={"User-Agent": UA}, timeout=60)
            m = RE_JETON.search(rep.text or "")
            _JETON["v"] = m.group(1) if m else ""
        except Exception:  # noqa: BLE001
            _JETON["v"] = ""
    return _JETON["v"]


def _charge_ungm(terme, page):
    return {"PageIndex": page, "PageSize": TAILLE_UNGM, "Title": terme,
            "Description": "", "Reference": "", "NoticeTypes": [], "Agencies": [],
            "Countries": [], "UNSPSCs": [], "SortField": "DatePublished",
            "SortAscending": False}


def _fetch_ungm(charge):
    entetes = dict(ENTETES_UNGM)
    if _jeton():
        entetes["RequestVerificationToken"] = _jeton()
    rep = session().post(URL_UNGM, data=json.dumps(charge).encode("utf-8"),
                         headers=entetes, timeout=60)
    return rep.status_code, rep.text


def parser_ungm(texte):
    """HTML de resultats -> lignes {id, titre, agence, type, date, deadline,
    pays, ouvert}. Cellules reconnues par leur classe (robuste a l'ordre)."""
    src = str(texte or "")
    bornes = [(m.start(), m.group(1)) for m in RE_LIGNE_UNGM.finditer(src)]
    lignes = []
    for i, (deb, ident) in enumerate(bornes):
        bloc = src[deb:bornes[i + 1][0] if i + 1 < len(bornes) else len(src)]
        bloc = bloc.split("<script")[0]
        positions = list(RE_CELLULE_UNGM.finditer(bloc))
        cellules = []
        for j, m in enumerate(positions):
            fin = positions[j + 1].start() if j + 1 < len(positions) else len(bloc)
            cellules.append((m.group(1), bloc[m.end():fin]))
        titre = re.search(r'<span class="ungm-title[^"]*">(.*?)</span>', bloc, re.S)
        agence, deadline, reference, restant, simples = "", "", "", None, []
        for classe, contenu in cellules:
            if "resultAgency" in classe:
                agence = _texte_html(contenu)
            elif "deadline" in classe:
                deadline = _texte_html(contenu.split("remainingDays")[0])
                r = re.search(r'remainingDaysToDeadline[^>]*>\s*(-?[\d.]+)', contenu)
                restant = float(r.group(1)) if r else None
            elif 'data-description="Reference"' in contenu or "resultInfo1" in classe:
                reference = _texte_html(contenu)
            elif classe.strip() == "tableCell":
                simples.append(_texte_html(contenu))
        titre_txt = _texte_html(titre.group(1)) if titre else ""
        if reference and titre_txt.startswith(reference):
            titre_txt = titre_txt[len(reference):].strip(" :-")
        lignes.append({
            "id": ident, "titre": titre_txt, "agence": agence,
            "date": _iso_libre(simples[0]) if simples else "",
            "type": simples[1] if len(simples) > 1 else "",
            "pays": simples[2] if len(simples) > 2 else "",
            "deadline": _iso_libre(deadline), "ouvert": (restant or 0) > 0,
        })
    return lignes


def normaliser_ungm(ligne):
    type_n = str(ligne.get("type") or "")
    return {
        "source": "UNGM", "id_avis": str(ligne.get("id") or ""),
        "titre": str(ligne.get("titre") or "")[:400],
        "acheteur": ligne.get("agence") or "Nations Unies",
        "nature": "attribution" if "award" in type_n.lower() else "avis",
        "date_publication": ligne.get("date") or "",
        "date_limite": ligne.get("deadline") or "",
        "description": str(ligne.get("titre") or ""), "corps": "",
        "pays": ligne.get("pays") or "", "francais": False,
        "ouvert": bool(ligne.get("ouvert")),
        "lien": LIEN_UNGM.format(ligne.get("id")) if ligne.get("id") else "",
    }


def collecter_ungm(depuis, fetch=None, parser=parser_ungm, requetes=REQUETES):
    fetch = fetch or _fetch_ungm
    journal = {"erreurs": [], "requetes": 0}
    par_id = {}
    for terme in requetes:
        for page in range(PAGES_UNGM):
            journal["requetes"] += 1
            try:
                statut, texte = fetch(_charge_ungm(terme, page))
            except Exception as e:  # noqa: BLE001
                journal["erreurs"].append("UNGM {} : {}".format(terme, e))
                break
            if statut != 200:
                journal["erreurs"].append("UNGM {} : HTTP {}".format(terme, statut))
                break
            lot = parser(texte)
            for c in lot:
                a = normaliser_ungm(c)
                if a["id_avis"]:
                    par_id.setdefault(a["id_avis"], a)
            if len(lot) < TAILLE_UNGM:
                break
            time.sleep(PAUSE)
        time.sleep(PAUSE)
    return list(par_id.values()), journal


# ===========================================================================
# SELECTION (ouverture, hors France, pre-filtre, memoire, plafond)
# ===========================================================================
def est_ouvert(avis, aujourd):
    """Avis ouvert : non attribution, et date limite non depassee. UNGM fournit
    aussi un drapeau `ouvert` (jours restants) : s'il dit ferme, on l'ecarte."""
    if avis.get("nature") == "attribution":
        return False
    if avis.get("source") == "UNGM" and avis.get("ouvert") is False and not avis.get("date_limite"):
        return False
    lim = avis.get("date_limite") or ""
    if lim:
        try:
            return datetime.date.fromisoformat(lim[:10]) >= aujourd
        except ValueError:
            return True
    return True


def file_d_analyse(avis, deja_vus, aujourd, budget=None):
    """Ouverture -> hors France -> pre-filtre lentille -> memoire -> plafond,
    dans cet ordre (regle 9 : memoire AVANT plafond). Priorite : terme dans le
    titre d'abord, puis le plus recent. Reutilise cle_avis et prefiltre."""
    budget = BUDGET if budget is None else budget
    ent = {"collectes": len(avis), "fermes": 0, "francais": 0, "deja_vus": 0, "classes": {}}
    retenus = []
    for a in avis:
        if not est_ouvert(a, aujourd):
            ent["fermes"] += 1
            continue
        if a.get("francais"):                      # deja couvert par le BOAMP
            ent["francais"] += 1
            continue
        classe, termes = L.prefiltre(a["titre"], a.get("corps", ""))
        ent["classes"][classe] = ent["classes"].get(classe, 0) + 1
        if not L.merite_llm(classe):
            continue
        a = dict(a, classe=classe, termes=termes)
        if C.cle_avis(a) in deja_vus:
            ent["deja_vus"] += 1
            continue
        retenus.append(a)
    retenus = (sorted([a for a in retenus if a["classe"] == "ie_titre"],
                      key=lambda a: a["date_publication"], reverse=True)
               + sorted([a for a in retenus if a["classe"] != "ie_titre"],
                        key=lambda a: a["date_publication"], reverse=True))
    ent["en_attente"] = len(retenus)
    ent["hors_budget"] = max(0, len(retenus) - budget)
    return retenus[:budget], ent


# ===========================================================================
# ORCHESTRATION (ecriture et lignes reutilisees de collecteur_ie)
# ===========================================================================
def executer(aujourd=None, fetch_ted=None, fetch_ungm=None, parser_ungm_=None,
             appel=None, deja_vus=None, debug=None, budget=None):
    """Collecte TED + UNGM -> file -> analyse. Aucune ecriture ; renvoie les
    lignes a ecrire (format collecteur_ie) et un rapport."""
    aujourd = aujourd or datetime.date.today()
    debug = DEBUG if debug is None else debug
    debut = aujourd - datetime.timedelta(days=JOURS)
    avis_ted, j_ted = collecter_ted(debut, aujourd, fetch=fetch_ted)
    avis_ungm, j_ungm = collecter_ungm(debut, fetch=fetch_ungm,
                                       parser=parser_ungm_ or parser_ungm)
    avis = avis_ted + avis_ungm
    file_, ent = file_d_analyse(avis, deja_vus or set(), aujourd, budget=budget)
    lignes_pg, lignes_sheet, actions = [], [], {}
    if not debug:
        for a in file_:
            r = L.analyser(a, appel=appel, pause=PAUSE if appel is None else 0.0)
            if r["extraction"] is None:
                actions["echec_extraction"] = actions.get("echec_extraction", 0) + 1
                continue
            lp = C.ligne_pg(a, r, aujourd)
            lignes_pg.append(lp)
            actions[lp["action"]] = actions.get(lp["action"], 0) + 1
            if C.visible_arise(r["note"]):
                lignes_sheet.append(C.ligne_sheet(lp))
    rapport = {"debut": debut, "ent": ent, "actions": actions, "file": file_,
               "debug": debug, "erreurs": j_ted["erreurs"] + j_ungm["erreurs"],
               "requetes": {"TED": j_ted["requetes"], "UNGM": j_ungm["requetes"]},
               "collecte": {"TED": len(avis_ted), "UNGM": len(avis_ungm)}}
    return lignes_pg, lignes_sheet, rapport


def rapport_markdown(rapport, ecritures):
    ent = rapport["ent"]
    md = ["# Collecteur IE international (TED + UNGM)", "",
          "Mode : {}. Fenetre depuis {}.".format(
              "DEBUG (sans LLM ni ecriture)" if rapport["debug"] else "production",
              rapport["debut"].isoformat()), "",
          "## Entonnoir", "",
          "- Collectes : TED {TED}, UNGM {UNGM}".format(**rapport["collecte"]),
          "- Requetes : TED {TED}, UNGM {UNGM}".format(**rapport["requetes"]),
          "- Fermes (attribution / date limite passee) : {}".format(ent["fermes"]),
          "- Acheteurs francais ecartes (deja BOAMP) : {}".format(ent["francais"]),
          "- Pre-filtre : {}".format(", ".join("{} {}".format(k, v)
                                               for k, v in sorted(ent["classes"].items())) or "-"),
          "- Deja analyses (memoire) : {}".format(ent["deja_vus"]),
          "- En attente : {} (hors budget ce run : {})".format(
              ent["en_attente"], ent["hors_budget"]), ""]
    if rapport["actions"]:
        md += ["## Analyse", ""] + ["- {} : {}".format(k, v)
                                    for k, v in sorted(rapport["actions"].items())] + [""]
    if rapport["debug"] and rapport["file"]:
        md += ["## File qui partirait au LLM", ""]
        md += ["- [{}] {} | {} | {} | {}".format(
            a["classe"], a["source"], a["date_publication"], a["acheteur"][:35],
            a["titre"][:80]) for a in rapport["file"]] + [""]
    md += ["## Ecritures", ""] + ["- " + e for e in ecritures]
    if rapport["erreurs"]:
        md += ["", "## Erreurs", ""] + ["- " + e for e in rapport["erreurs"][:20]]
    return "\n".join(md) + "\n"


def main():
    if not L.ACTIF:
        print("Collecteur IE international desactive (RADAR_IE=0).")
        return
    if not DEBUG and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return
    url = os.environ.get("DATABASE_URL")
    sheet_id = os.environ.get("IE_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    feuille = None
    if sheet_id and fichier and not DEBUG:
        try:
            feuille = C.ouvrir_onglet(sheet_id, fichier)
        except Exception as e:  # noqa: BLE001
            print("  (info) classeur ARISE inaccessible ({}).".format(str(e)[:120]))
    deja_vus = C.memoire_pg(url)
    if deja_vus is None:
        deja_vus = set()

    lignes_pg, lignes_sheet, rapport = executer(deja_vus=deja_vus)
    if rapport["debug"]:
        ecritures = ["aucune (mode debug)"]
    else:
        ecritures = [C.ecrire_pg(url, lignes_pg), C.ecrire_sheet(feuille, lignes_sheet)]
    md = rapport_markdown(rapport, ecritures)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
