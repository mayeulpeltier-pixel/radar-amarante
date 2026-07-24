# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur ADB (Asian Development Bank).
=========================================================

POURQUOI CETTE SOURCE
---------------------
ADB finance des projets dans toute l'Asie-Pacifique, dont des zones Amarante :
Afghanistan, Pakistan, Bangladesh, Sri Lanka, Myanmar, plus le Caucase
(Armenie, Azerbaidjan, Georgie) et l'Asie centrale (Kazakhstan, Kirghizistan,
Tadjikistan, Turkmenistan, Ouzbekistan).

ACCES (verifie, juillet 2026)
-----------------------------
Depuis la migration SearchStax, ADB n'expose plus de flux RSS fiable pour les
tenders. La page https://www.adb.org/projects/tenders liste les avis avec des
CHAMPS EN CLAIR tres exploitables :
    Status / Deadline / [reference: code-pays: Titre](lien PDF)
    Country/Economy: <NOM DE PAYS EN CLAIR> / Sector / Posting Date / Notice Type
On lit donc cette page (pagination searchstax[page]=N) et on parse par LABELS,
methode robuste a la structure HTML. Le pays vient du champ Country/Economy
(nom en clair), bien plus fiable que les codes ADB du titre.

GARDE-FOU : si la page revient SANS notices (probablement rendue en JavaScript
cote navigateur), le collecteur le signale clairement et invite a fournir
l'URL de l'API JSON (F12 > Reseau). Il ne fabrique jamais de faux resultats.

REUTILISATION : comme AfDB, ce collecteur REUTILISE le coeur ted_complet_v14
(session, LLM, scoring, escalade, memoire, Sheet) sans le modifier. Onglet
SEPARE "adb_radar", meme echelle de score que TED/BM.

Interrupteur : RADAR_ADB=0 desactive.
Reglage : ADB_DRY_RUN=1 montre l'entonnoir SANS appel LLM (et liste les pays
hors zone rencontres, canari de derive du mapping).
"""

import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

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

# URL de la page tenders avec un emplacement {page} pour la pagination.
# Surchargeable par ADB_URL (colle l'URL exacte vue dans ton navigateur, en
# remplacant le numero de page par {page}).
ADB_URL = os.environ.get(
    "ADB_URL",
    "https://www.adb.org/projects/tenders?searchstax[query]=*"
    "&searchstax[page]={page}&searchstax[order]=ds_date_closing%20desc")
NB_PAGES = int(os.environ.get("ADB_PAGES", "5"))     # 12 notices par page
NOM_ONGLET = "adb_radar"

NB_JOURS_FENETRE = int(os.environ.get("ADB_JOURS", "30"))
MAX_AVIS_LLM = int(os.environ.get("ADB_BUDGET", "60"))


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# --- Country/Economy (nom en clair) -> ISO3 ----------------------------------
# On mappe les pays suivis ET quelques non-suivis courants (pour les reconnaitre
# puis les filtrer proprement). "Regional" et "Multinational" = pas de pays.
NOM_VERS_ISO3 = {
    # -- zones Amarante (suivies) --
    "afghanistan": "AFG", "pakistan": "PAK", "bangladesh": "BGD",
    "sri lanka": "LKA", "nepal": "NPL", "myanmar": "MMR", "burma": "MMR",
    "cambodia": "KHM", "lao pdr": "LAO", "lao people's democratic republic": "LAO",
    "laos": "LAO", "indonesia": "IDN", "philippines": "PHL",
    "papua new guinea": "PNG", "kazakhstan": "KAZ", "kyrgyz republic": "KGZ",
    "kyrgyzstan": "KGZ", "tajikistan": "TJK", "turkmenistan": "TKM",
    "uzbekistan": "UZB", "georgia": "GEO", "armenia": "ARM", "azerbaijan": "AZE",
    # -- non suivis, mappes pour etre reconnus puis filtres --
    "viet nam": "VNM", "vietnam": "VNM", "india": "IND", "mongolia": "MNG",
    "china": "CHN", "people's republic of china": "CHN", "thailand": "THA",
    "maldives": "MDV", "bhutan": "BTN", "fiji": "FJI", "samoa": "WSM",
    "tonga": "TON", "vanuatu": "VUT", "solomon islands": "SLB",
    "timor-leste": "TLS", "malaysia": "MYS",
}
# Valeurs "region" a ignorer (pas de pays precis exploitable).
REGIONAUX = {"regional", "multinational", "asia and the pacific", "asia pacific", ""}

_SUIVIS = set(getattr(ted, "CODES_PAYS_SUIVIS", []))


# ===========================================================================
# PARTIE 2 -- COLLECTE (page tenders)
# ===========================================================================

def collecter_pages(fetch=None, session=None, pages=NB_PAGES):
    """Renvoie le texte concatene des pages tenders. `fetch` injectable pour
    tests : callable(url) -> str (ou callable() -> str)."""
    if fetch is not None:
        try:
            return fetch(ADB_URL.format(page=1))
        except TypeError:
            return fetch()
    session = session or ted.session_robuste()
    entetes = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
    }
    morceaux = []
    for p in range(1, pages + 1):
        url = ADB_URL.format(page=p)
        rep = session.get(url, timeout=30, headers=entetes)
        rep.raise_for_status()
        morceaux.append(rep.text)
        time.sleep(0.5)
    return "\n".join(morceaux)


def _texte_brut(html):
    """HTML -> texte : retire scripts/styles et balises, en conservant les
    liens PDF sous forme lisible [texte](url) pour ne pas perdre le lien."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    # Conserve les ancres PDF : <a href="X.pdf">T</a> -> [T](X.pdf)
    html = re.sub(r'(?is)<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>',
                  lambda m: "[{}]({})".format(re.sub(r"<[^>]+>", "", m.group(2)).strip(), m.group(1)),
                  html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)          # autres balises
    html = (html.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#8211;", "-").replace("&ndash;", "-")
                .replace("&#039;", "'").replace("&rsquo;", "'"))
    return html


# ===========================================================================
# PARTIE 3 -- PARSING PAR LABELS
# ===========================================================================

_RE_REFERENCE = re.compile(r"\b(\d{5}-\d{3})\b")
_RE_DATE = re.compile(r"(\d{1,2}\s+\w{3,9}\s+\d{4})")
_RE_LIEN_PDF = re.compile(r"\]\((https?://[^)\s]+\.pdf)\)")


def _champ(bloc, label, fins):
    """Extrait la valeur d'un label jusqu'au prochain label (labels colles sans
    espace dans la page ADB : 'Country/Economy: Sri LankaSector: ...')."""
    motif = re.escape(label) + r"\s*[:\-]?\s*(.+?)\s*(?:" + "|".join(re.escape(f) for f in fins) + r"|$)"
    m = re.search(motif, bloc, re.IGNORECASE | re.DOTALL)
    return (m.group(1).strip() if m else "")


LABELS = ["Status", "Deadline", "Country/Economy", "Sector",
          "Posting Date", "Notice Type", "Approval Number"]


def parser_notices(texte):
    """Texte de la ou des pages -> liste de notices (dicts bruts). Chaque notice
    commence par 'Status:'. Robuste aux labels colles."""
    blocs = re.split(r"(?=Status\s*:\s*(?:Active|Closed|Awarded|Cancelled))", texte)
    notices = []
    for bloc in blocs:
        if "Country/Economy" not in bloc:
            continue
        pays = _champ(bloc, "Country/Economy", ["Sector", "Posting Date", "Notice Type", "Status"])
        if not pays:
            continue
        ref = ""
        m = _RE_REFERENCE.search(bloc)
        if m:
            ref = m.group(1)
        lien = ""
        ml = _RE_LIEN_PDF.search(bloc)
        if ml:
            lien = ml.group(1)
        # Titre : contenu entre crochets si present, sinon 1re ligne apres Deadline.
        titre = ""
        mt = re.search(r"\[([^\]]+)\]\(", bloc)
        if mt:
            titre = mt.group(1).strip()
        else:
            mt2 = re.search(r"\d{5}-\d{3}:\s*(.+)", bloc)
            titre = mt2.group(1).strip()[:300] if mt2 else pays
        type_notice_txt = _champ(bloc, "Notice Type", ["Approval Number", "Status", "Deadline"])
        deadline = ""
        md = re.search(r"Deadline\s*:?\s*" + _RE_DATE.pattern, bloc)
        if md:
            deadline = md.group(1)
        posting = ""
        mp = re.search(r"Posting Date\s*:?\s*" + _RE_DATE.pattern, bloc)
        if mp:
            posting = mp.group(1)
        notices.append({
            "pays_clair": pays, "reference": ref, "lien": lien, "titre": titre,
            "type_notice_txt": type_notice_txt, "deadline": deadline,
            "posting_date": posting,
        })
    return notices


def resoudre_iso3(pays_clair, titre=""):
    """Country/Economy en clair -> ISO3 (source primaire). Repli : scan du titre.
    Renvoie (iso3, nom_hors_zone)."""
    cle = _norm(pays_clair)
    if cle in REGIONAUX:
        return "", ""
    iso = NOM_VERS_ISO3.get(cle)
    if iso and iso in _SUIVIS:
        return iso, ""
    if iso:
        return "", pays_clair            # pays reconnu mais hors zone suivie
    # Repli : un nom de pays suivi apparait-il dans le titre ?
    hay = " " + _norm(titre) + " "
    for nom in sorted((n for n, v in NOM_VERS_ISO3.items() if v in _SUIVIS), key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(nom) + r"(?![a-z])", hay):
            return NOM_VERS_ISO3[nom], ""
    return "", ""


def type_notice(texte):
    t = _norm(texte)
    if "advance" in t or "general procurement" in t:
        return ("Avis anticipe", "amont")
    if "prequalification" in t or "expression of interest" in t or "eoi" in t:
        return ("Prequalification / EOI", "amont")
    if "consult" in t:
        return ("Services de conseil", "tender")
    if "invitation for bid" in t or "ifb" in t or "bid" in t:
        return ("Appel d'offres", "tender")
    if "works" in t:
        return ("Travaux", "tender")
    if "goods" in t:
        return ("Fournitures", "tender")
    return ("Avis de marche", "tender")


def _date_iso(txt):
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(txt.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def normaliser(notice):
    """Notice brute -> avis dict compatible coeur TED, ou (None, hors_zone)."""
    iso3, hors_zone = resoudre_iso3(notice["pays_clair"], notice["titre"])
    if not iso3:
        return None, hors_zone
    libelle, phase = type_notice(notice["type_notice_txt"] + " " + notice["titre"])
    d = _date_iso(notice["deadline"])
    avis = {
        "acheteur": "Asian Development Bank",
        "pays_acheteur": "",
        "pays_execution": iso3,
        "titre": notice["titre"][:300],
        "cpv": "",
        "description": "",
        "type_notice": libelle,
        "phase": phase,
        "lien_avis": notice["lien"],
        "publication_number": notice["reference"] or notice["lien"] or notice["titre"],
        "deadline": d.isoformat() if d else "",
        "date_publication": notice["posting_date"],
    }
    return avis, ""


def collecter_et_normaliser(fetch=None, session=None, pages=NB_PAGES):
    """Pages -> notices -> avis normalises (filtres pays + fraicheur + dedup).
    stats['pays_hors_zone'] = canari ; stats['page_vide'] = garde-fou JS."""
    texte = _texte_brut(collecter_pages(fetch=fetch, session=session, pages=pages))
    notices = parser_notices(texte)
    seuil = date.today() - timedelta(days=NB_JOURS_FENETRE)
    avis, vus, hors_zone = [], set(), {}
    hors_perimetre = 0
    for n in notices:
        d = _date_iso(n["posting_date"])
        if d and d < seuil:
            continue
        a, hz = normaliser(n)
        if a is None:
            hors_perimetre += 1
            if hz:
                hors_zone[hz] = hors_zone.get(hz, 0) + 1
            continue
        cle = a["publication_number"]
        if cle in vus:
            continue
        vus.add(cle)
        avis.append(a)
    return avis, {
        "notices": len(notices), "hors_perimetre": hors_perimetre,
        "retenus": len(avis), "pays_hors_zone": hors_zone,
        "page_vide": len(notices) == 0,
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
    # Index construit en LECTURE POSITIONNELLE depuis le SCHEMA (regle 4).
    # `get_all_records()` numerisait les identifiants ("12345678" -> entier,
    # donc plus aucune correspondance avec les chaines, donc re-ajout
    # silencieux a chaque run) et levait `GSpreadException` sur un en-tete
    # duplique, en fin de run, apres avoir paye les appels au modele.
    index = ted.charger_index_publication(feuille, COLONNES_ADB)
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
    # Securite deja en place : critere PARTAGE (ted.escalade_pour_securite).
    # Couvre `interne_client` (on va ecarter : une erreur coute un marche)
    # ET `prestataire_tiers` (on va pousser en conquete : jugement le plus
    # fin du pipeline). La lecture directe de `securite_existante_detectee`
    # avait cesse de couvrir le second depuis le passage a l'enum.
    if ted.escalade_pour_securite(e):
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
        print("ERREUR : collecte ADB impossible ({}).".format(e))
        raise

    if stats["page_vide"]:
        print("GARDE-FOU : la page ADB est revenue SANS notice exploitable.")
        print("  -> Elle est probablement rendue en JavaScript (SearchStax).")
        print("  -> Ouvre la page tenders, F12 > Reseau, repere la requete qui")
        print("     renvoie du JSON avec les tenders, et donne-moi son URL :")
        print("     je bascule le collecteur en mode JSON (plus robuste).")
        return

    print("Page : {notices} notice(s) | hors perimetre : {hors_perimetre} | retenus : {retenus}".format(**stats))
    if stats["pays_hors_zone"]:
        top = sorted(stats["pays_hors_zone"].items(), key=lambda x: -x[1])[:8]
        print("  (info) pays hors zone suivie : " +
              ", ".join("{}x{}".format(n, p) for p, n in top))

    if DRY_RUN:
        print("(ADB_DRY_RUN=1 : entonnoir seulement, aucun appel LLM)")
        for a in avis[:30]:
            print("  [{:5}] {:4} {}".format(a["phase"][:5], a["pays_execution"], a["titre"][:80]))
        return

    if not avis:
        print("Aucun avis ADB en zone suivie ce run.")
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
