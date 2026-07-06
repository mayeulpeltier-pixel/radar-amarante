# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur ADB (Asian Development Bank).
=========================================================

POURQUOI CETTE SOURCE
---------------------
ADB finance des projets dans toute l'Asie-Pacifique, dont des zones Amarante :
Afghanistan, Pakistan, Bangladesh, Sri Lanka, Myanmar, plus le Caucase
(Armenie, Azerbaidjan, Georgie) et l'Asie centrale (Kazakhstan, Kirghizistan,
Tadjikistan, Turkmenistan, Ouzbekistan). Le flux "Tenders - Advanced Notices"
porte l'amont (avis anticipes avant approbation du pret).

ACCES (verifie, juillet 2026) : ADB expose des flux RSS (page https://www.adb.org/rss),
dont "Procurement Notices" et "Tenders - Advanced Notices". Pas d'API JSON.
L'URL exacte du .xml n'a pas pu etre lue en direct (anti-bot) : elle est donc
CONFIGURABLE via la variable ADB_FLUX, avec un garde-fou qui verifie que le
contenu recu est bien du RSS. Confirme-la en un clic sur le bouton RSS de
https://www.adb.org/projects/tenders (recommande : le flux "Advanced Notices").

DIFFERENCE CLE AVEC AfDB (pourquoi ce collecteur est distinct)
--------------------------------------------------------------
Les titres ADB n'utilisent PAS l'ISO3 et le code pays est a position instable :
  - avant les deux-points : "TA-10693 ARM: Armenia... - Legal Expert (59351-001)"
  - apres                 : "Loan 4534: NEP - Kathmandu Valley Water..."
  - entre parentheses     : "TA-10041 REG: ... (AZE: Wastewater...) (56136-001)"
  - regional              : "58369-001; Regional; ..." (pas de pays precis)
Donc on NE parse PAS par position : on SCANNE tout le titre a la recherche d'un
code pays ADB en MAJUSCULES (insensible aux collisions, le texte courant n'est
pas en majuscules), avec repli sur les noms de pays anglais. On mappe ensuite
le code ADB vers l'ISO3 (VIE->VNM, NEP->NPL, BAN->BGD, INO->IDN...).

REUTILISATION : comme AfDB, ce collecteur REUTILISE le coeur ted_complet_v14
(session, LLM, scoring, escalade, memoire, Sheet) sans le modifier. Ecrit dans
l'onglet SEPARE "adb_radar". Meme echelle de score que TED/BM.

Interrupteur : RADAR_ADB=0 desactive.
Reglage : ADB_DRY_RUN=1 montre l'entonnoir SANS appel LLM, et liste les
prefixes pays NON resolus (canari de derive du mapping).
"""

import email.utils
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

try:
    import ted_complet_v14 as ted
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce collecteur.")


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_ADB", "1") != "0"
DRY_RUN = os.environ.get("ADB_DRY_RUN", "0") == "1"

# URL du flux RSS ADB. A CONFIRMER via le bouton RSS de la page tenders
# (recommande : "Tenders - Advanced Notices"). Surchargeable par ADB_FLUX.
FLUX_ADB = os.environ.get("ADB_FLUX", "https://www.adb.org/projects/tenders/rss")
NOM_ONGLET = "adb_radar"

NB_JOURS_FENETRE = int(os.environ.get("ADB_JOURS", "30"))
MAX_AVIS_LLM = int(os.environ.get("ADB_BUDGET", "60"))


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# --- Mapping code pays ADB (3 lettres, MAJUSCULES) -> ISO3 --------------------
# On mappe les pays suivis ET quelques non-suivis courants (VIE, IND, PRC...),
# afin qu'un code hors zone soit RECONNU puis FILTRE (et n'entraine pas une
# mauvaise attribution a un autre code du titre). REG/regional = pas de pays.
CODE_ADB_VERS_ISO3 = {
    # -- zones Amarante (suivies) --
    "AFG": "AFG", "PAK": "PAK", "BAN": "BGD", "SRI": "LKA", "NEP": "NPL",
    "MYA": "MMR", "CAM": "KHM", "LAO": "LAO", "INO": "IDN", "PHI": "PHL",
    "PNG": "PNG", "KAZ": "KAZ", "KGZ": "KGZ", "TAJ": "TJK", "TKM": "TKM",
    "UZB": "UZB", "ARM": "ARM", "AZE": "AZE", "GEO": "GEO",
    # -- non suivis, mappes pour etre reconnus puis filtres --
    "VIE": "VNM", "IND": "IND", "PRC": "CHN", "MON": "MNG", "THA": "THA",
    "MAL": "MYS", "BHU": "BTN", "MLD": "MDV", "FIJ": "FJI", "SAM": "WSM",
    "TON": "TON", "VAN": "VUT", "SOL": "SLB", "TIM": "TLS", "KOR": "KOR",
}
# Codes "region" a ignorer (pas de pays precis exploitable).
CODES_REGIONAUX = {"REG", "RRP", "SUB"}

# --- Repli : noms de pays anglais -> ISO3 (seulement zones suivies) -----------
NOM_EN_VERS_ISO3 = {
    "afghanistan": "AFG", "pakistan": "PAK", "bangladesh": "BGD",
    "sri lanka": "LKA", "nepal": "NPL", "myanmar": "MMR", "burma": "MMR",
    "cambodia": "KHM", "lao": "LAO", "laos": "LAO", "lao pdr": "LAO",
    "indonesia": "IDN", "philippines": "PHL", "papua new guinea": "PNG",
    "kazakhstan": "KAZ", "kyrgyz republic": "KGZ", "kyrgyzstan": "KGZ",
    "tajikistan": "TJK", "turkmenistan": "TKM", "uzbekistan": "UZB",
    "georgia": "GEO", "armenia": "ARM", "azerbaijan": "AZE",
}
_NOMS_EN_TRIES = sorted(NOM_EN_VERS_ISO3, key=len, reverse=True)

_SUIVIS = set(getattr(ted, "CODES_PAYS_SUIVIS", []))


# ===========================================================================
# PARTIE 2 -- COLLECTE RSS
# ===========================================================================

def collecter_flux(fetch=None, session=None):
    """Texte XML brut du flux. `fetch` injectable pour tests. Garde-fou : on
    verifie que le contenu ressemble a du RSS, sinon message clair (mauvaise URL)."""
    if fetch is not None:
        return fetch()
    session = session or ted.session_robuste()
    rep = session.get(FLUX_ADB, timeout=30, headers={
        "User-Agent": "radar-amarante/1.0 (veille commerciale)"})
    rep.raise_for_status()
    texte = rep.text
    if "<rss" not in texte[:2000].lower() and "<item" not in texte[:5000].lower():
        raise ValueError(
            "Le contenu recupere depuis ADB_FLUX ({}) n'est pas un flux RSS. "
            "Confirme l'URL en cliquant le bouton RSS sur "
            "https://www.adb.org/projects/tenders, puis renseigne ADB_FLUX.".format(FLUX_ADB))
    return texte


def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def parser_items(xml_texte):
    items = []
    try:
        root = ET.fromstring(xml_texte)
    except ET.ParseError as e:
        print("  (attention) flux ADB illisible (XML invalide : {}).".format(e))
        return items
    for n in [x for x in root.iter() if _localname(x.tag) == "item"]:
        champ = {"titre": "", "lien": "", "date": "", "description": "", "categorie": ""}
        for enfant in list(n):
            nom = _localname(enfant.tag)
            txt = (enfant.text or "").strip()
            if nom == "title":
                champ["titre"] = txt
            elif nom == "link":
                champ["lien"] = txt or enfant.attrib.get("href", "").strip()
            elif nom in ("pubDate", "date"):
                champ["date"] = champ["date"] or txt
            elif nom == "description":
                champ["description"] = txt
            elif nom == "category":
                champ["categorie"] = (champ["categorie"] + " " + txt).strip()
            elif nom == "guid" and not champ["lien"]:
                champ["lien"] = txt
        if champ["titre"]:
            items.append(champ)
    return items


# ===========================================================================
# PARTIE 3 -- NORMALISATION (scan pays, type de notice, reference)
# ===========================================================================

def resoudre_iso3(texte):
    """Scanne le texte : d'abord les codes pays ADB en MAJUSCULES (whole word),
    puis les noms de pays anglais. Renvoie (iso3, code_trouve_hors_zone).
      - iso3 non vide  : pays a risque suivi identifie.
      - ('', code)     : un pays a ete reconnu mais hors zone suivie (on skippe).
      - ('', '')       : aucun pays identifiable (regional ou inconnu).
    """
    # 1) Codes ADB en majuscules (mots entiers de 3 lettres).
    codes_presents = re.findall(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])", texte or "")
    hors_zone = ""
    for code in codes_presents:
        if code in CODES_REGIONAUX:
            continue
        iso = CODE_ADB_VERS_ISO3.get(code)
        if iso and iso in _SUIVIS:
            return iso, ""
        if iso:
            hors_zone = hors_zone or code
    # 2) Repli : noms anglais (whole word, du plus long au plus court).
    hay = " " + _norm(texte) + " "
    for nom in _NOMS_EN_TRIES:
        if re.search(r"(?<![a-z])" + re.escape(nom) + r"(?![a-z])", hay):
            iso = NOM_EN_VERS_ISO3[nom]
            if iso in _SUIVIS:
                return iso, ""
    return "", hors_zone


# Type de notice (mots-cles) -> (libelle, phase amont/tender).
def type_notice(texte):
    t = _norm(texte)
    if "advance" in t or "advanced notice" in t or "general procurement" in t:
        return ("Avis anticipe", "amont")
    if "prequalification" in t or "expression of interest" in t or "eoi" in t:
        return ("Prequalification / EOI", "amont")
    if "consult" in t:
        return ("Services de conseil", "tender")
    if "works" in t or "civil work" in t:
        return ("Travaux", "tender")
    if "goods" in t:
        return ("Fournitures", "tender")
    return ("Avis de marche", "tender")


_RE_REFERENCE = re.compile(r"\b(\d{5}-\d{3})\b")
_RE_DEADLINE = re.compile(r"deadline\s*[:\-]?\s*([0-3]?\d\s+\w+\s+\d{4})", re.IGNORECASE)


def _extraire_deadline(description):
    m = _RE_DEADLINE.search(description or "")
    if not m:
        return ""
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(m.group(1).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def normaliser(item):
    """Item RSS ADB -> avis dict compatible coeur TED, ou None si hors perimetre.
    Renvoie aussi le prefixe non resolu (pour le canari de derive)."""
    titre = item["titre"]
    contexte = titre + " " + item.get("categorie", "")
    iso3, hors_zone = resoudre_iso3(contexte)
    if not iso3:
        return None, hors_zone
    libelle, phase = type_notice(titre + " " + item.get("description", "") + " " + item.get("categorie", ""))
    ref = ""
    m = _RE_REFERENCE.search(titre) or _RE_REFERENCE.search(item.get("description", ""))
    if m:
        ref = m.group(1)
    description = ted._nettoyer_html(item.get("description", ""))[:ted.MAX_CARACTERES_DESCRIPTION]
    avis = {
        "acheteur": "Asian Development Bank",
        "pays_acheteur": "",
        "pays_execution": iso3,
        "titre": titre[:300],
        "cpv": "",
        "description": description,
        "type_notice": libelle,
        "phase": phase,
        "lien_avis": item.get("lien", ""),
        "publication_number": ref or item.get("lien", ""),
        "deadline": _extraire_deadline(item.get("description", "")),
        "date_publication": item.get("date", ""),
    }
    return avis, ""


def _date_dans_fenetre(date_rfc822, seuil):
    if not date_rfc822:
        return True
    try:
        d = email.utils.parsedate_to_datetime(date_rfc822)
    except (TypeError, ValueError):
        return True
    if d is None:
        return True
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d >= seuil


def collecter_et_normaliser(fetch=None, session=None):
    """Flux -> items -> avis normalises (filtres pays + fenetre + dedup).
    Renvoie (avis, stats). stats['prefixes_hors_zone'] = canari de derive."""
    xml_texte = collecter_flux(fetch=fetch, session=session)
    items = parser_items(xml_texte)
    seuil = datetime.now(timezone.utc) - timedelta(days=NB_JOURS_FENETRE)
    avis, vus, hors_zone_codes = [], set(), {}
    hors_perimetre = 0
    for it in items:
        if not _date_dans_fenetre(it.get("date"), seuil):
            continue
        a, hz = normaliser(it)
        if a is None:
            hors_perimetre += 1
            if hz:
                hors_zone_codes[hz] = hors_zone_codes.get(hz, 0) + 1
            continue
        cle = a["publication_number"] or a["titre"]
        if cle in vus:
            continue
        vus.add(cle)
        avis.append(a)
    return avis, {
        "items": len(items), "hors_perimetre": hors_perimetre,
        "retenus": len(avis), "prefixes_hors_zone": hors_zone_codes,
    }


# ===========================================================================
# PARTIE 4 -- CIBLE COMMERCIALE
# ===========================================================================

def cible_commerciale(avis, extraction):
    if avis.get("phase") == "amont":
        return ("Projet ADB en phase amont : reperer tot le bureau / consortium "
                "qui repondra et deploiera des experts en zone a risque (Asie centrale, "
                "Caucase, Pakistan...).")
    return ("Titulaire (bureau d'etudes ou entreprise) qui executera le marche ADB "
            "sur le terrain, pas la Banque. Viser sa direction operations / surete.")


# ===========================================================================
# PARTIE 5 -- SORTIE GOOGLE SHEET
# ===========================================================================

COLONNES_ADB = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "pays_acheteur",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance", "modele", "raffine", "divergence",
    "type_notice", "phase", "publication_number", "lien_avis",
    "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_ADB = COLONNES_ADB + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        feuille = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET, rows=2000, cols=len(TOUTES_COLONNES_ADB))
        feuille.append_row(TOUTES_COLONNES_ADB)
        return feuille
    if COLONNE_DATE_DETECTION not in feuille.row_values(1):
        feuille.update(values=[TOUTES_COLONNES_ADB], range_name="A1")
    return feuille


def ligne_depuis_resultat(r):
    avis, extraction = r["avis"], r["extraction"]
    modele = ted.MODELE_RAFFINEMENT if r["raffine"] else ted.MODELE
    v = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"], "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.calculer_action_recommandee(r["score"], extraction, surete=r["surete"]),
        "fenetre_action": ted.calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": (extraction or {}).get("niveau_opportunite_amarante", ""),
        "titre": avis.get("titre", ""), "acheteur": avis.get("acheteur", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "pays_acheteur": avis.get("pays_acheteur", ""),
        "type_client": (extraction or {}).get("type_client", ""),
        "type_mobilite": (extraction or {}).get("type_mobilite", ""),
        "profil_personnes_exposees": (extraction or {}).get("profil_personnes_exposees", ""),
        "duree_estimee": (extraction or {}).get("duree_estimee", ""),
        "accessibilite_commerciale": (extraction or {}).get("accessibilite_commerciale", ""),
        "securite_existante_detectee": (extraction or {}).get("securite_existante_detectee", ""),
        "profils_acteurs_probables": ", ".join((extraction or {}).get("profils_acteurs_probables") or []),
        "cible_commerciale_reelle": cible_commerciale(avis, extraction),
        "justification": (extraction or {}).get("justification", ""),
        "confiance": (extraction or {}).get("confiance", ""),
        "modele": modele, "raffine": r["raffine"], "divergence": r["divergence"],
        "type_notice": avis.get("type_notice", ""), "phase": avis.get("phase", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(v.get(c, "")) for c in COLONNES_ADB]


def ecrire_resultats(feuille, resultats):
    valeurs = feuille.get_all_records()
    index = {}
    for num, ligne in enumerate(valeurs, start=2):
        pub = ligne.get("publication_number", "")
        if pub:
            index[pub] = num
    derniere = ted.lettre_colonne(len(COLONNES_ADB))
    maj, nouvelles, nb_maj, nb_new = [], [], 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne = ligne_depuis_resultat(r)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere), "values": [ligne]})
            nb_maj += 1
        else:
            nouvelles.append(ligne + ["nouveau", date.today().isoformat()])
            nb_new += 1
    if maj:
        feuille.batch_update(maj)
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    return nb_new, nb_maj


# ===========================================================================
# PARTIE 6 -- POINT D'ENTREE
# ===========================================================================

def merite_escalade(r):
    e = r["extraction"]
    if e is None:
        return False
    if r["final_haiku"] >= 5:
        return True
    if e.get("confiance", 1.0) < 0.7:
        return True
    if e.get("securite_existante_detectee"):
        return True
    return False


def main():
    if not ACTIVER:
        print("(info) Collecteur ADB desactive (RADAR_ADB=0).")
        return
    print("=" * 60)
    print("COLLECTEUR ADB (Asian Development Bank) - Radar Amarante")
    print("=" * 60)

    try:
        avis, stats = collecter_et_normaliser()
    except Exception as e:
        print("ERREUR : collecte du flux ADB impossible ({}).".format(e))
        raise
    print("Flux : {items} item(s) | hors perimetre : {hors_perimetre} | retenus : {retenus}".format(**stats))
    if stats["prefixes_hors_zone"]:
        top = sorted(stats["prefixes_hors_zone"].items(), key=lambda x: -x[1])[:8]
        print("  (info) codes pays hors zone suivie : " +
              ", ".join("{}x{}".format(n, c) for c, n in top))

    if DRY_RUN:
        print("(ADB_DRY_RUN=1 : entonnoir seulement, aucun appel LLM)")
        for a in avis[:30]:
            print("  [{:5}] {:4} {}".format(a["phase"][:5], a["pays_execution"], a["titre"][:80]))
        return

    if not avis:
        print("Aucun avis ADB a analyser ce run.")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES_ADB)
    a_traiter = [a for a in avis if a["publication_number"] not in deja_vus][:MAX_AVIS_LLM]
    print("Memoire : {} deja vu(s), {} a analyser.".format(len(avis) - len(a_traiter), len(a_traiter)))
    if not a_traiter:
        print("Rien de nouveau.")
        return

    print("\nAnalyse LLM ({} avis, modele {})...\n".format(len(a_traiter), ted.MODELE))
    resultats = []
    for i, avis_un in enumerate(a_traiter, start=1):
        print("[{}/{}] {}...".format(i, len(a_traiter), avis_un["titre"][:60]))
        extraction = ted.appeler_llm(avis_un)
        surete, commercial, final = ted.calculer_scores(avis_un, extraction)
        resultats.append({
            "avis": avis_un, "extraction": extraction, "surete": surete,
            "commercial": commercial, "score": final, "final_haiku": final,
            "raffine": False, "divergence": False,
        })
        time.sleep(0.4)

    a_escalader = [r for r in resultats if merite_escalade(r)]
    if a_escalader:
        print("\n{} avis escalade(s) vers {}...\n".format(len(a_escalader), ted.MODELE_RAFFINEMENT))
        for r in a_escalader:
            ex = ted.appeler_llm(r["avis"], modele=ted.MODELE_RAFFINEMENT)
            if ex is not None:
                s, c, f = ted.calculer_scores(r["avis"], ex)
                r["extraction"], r["surete"], r["commercial"], r["score"] = ex, s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0
            time.sleep(0.4)

    resultats.sort(key=lambda r: r["score"], reverse=True)
    if sheet_id and fichier:
        try:
            feuille = ouvrir_feuille(sheet_id, fichier)
            nb_new, nb_maj = ecrire_resultats(feuille, resultats)
            print("\n-> {} nouvel(s) avis, {} mis a jour dans '{}'.".format(nb_new, nb_maj, NOM_ONGLET))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("\n(dry-run Sheet : identifiants Google absents)")


if __name__ == "__main__":
    main()
