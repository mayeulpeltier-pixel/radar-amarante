# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COLLECTEUR AMONT : Banque Mondiale Projects API.
=================================================================

CE QUE FAIT CE MODULE (12/08/2026)
----------------------------------
Passe de l'AVAL (l'appel d'offres BM existe, deja collecte par ted_complet_bm)
a l'AMONT : le PROJET BM approuve, qui PRECEDE de plusieurs mois ses appels
d'offres. C'est l'USP « detecter avant l'appel d'offres ». Un projet approuve
en zone rouge = un deploiement a venir, donc un titulaire a demarcher tot.

SOURCE CONFIRMEE PAR SONDE (search.worldbank.org/api/v3/projects)
----------------------------------------------------------------
  - JSON, HTTP 200 depuis un runner ; racine {projects{...}, total, ...}.
  - Champs REELS d'un enregistrement : proj_id, project_name, countryshortname,
    boardapprovaldate (ISO, future pour les 'Pipeline'), status (Pipeline/Active
    /Dropped, disponible via fl), impagency, borrower, totalamt/lendprojectcost,
    closingdate. PAS de sector/url dans la reponse -> secteur derive du titre
    (par le dashboard), url construite.
  - PIEGE PAYS confirme : `countrycode_exact` utilise des codes WB NON standard
    (RDC 'CD'->0, Yemen 'YE'->0). On NE filtre donc PAS par code cote serveur :
    on balaie le recent global (tri boardapprovaldate desc) et on resout par NOM
    via notre table RISQUE (lecon IsDB/IDB : ne jamais faire confiance a un
    filtre pays serveur non verifie).

SCORING : DETERMINISTE, SANS LLM
--------------------------------
Un projet amont a un texte mince (un titre) et il y en a beaucoup : un scoring
LLM couterait cher pour peu. On score comme le socle TITULAIRE : zone (tier
sûreté) x montant (proxy taille de deploiement). Echelle propre, non comparable
aux avis LLM -- le dashboard l'etiquette par la source.

Interrupteur : RADAR_BM_PROJETS=0 desactive. pays_execution stocke en ISO3
(comme TED/IDB) : resolution zone directe cote dashboard.
"""

import os
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

import requests

import ted_complet_v14 as ted
import radar_resilience


ACTIVER = os.environ.get("RADAR_BM_PROJETS", "1") != "0"
NOM_ONGLET = "bm_projets_radar"

API = "https://search.worldbank.org/api/v3/projects"
CHAMPS = ("id,proj_id,project_name,countryshortname,regionname,boardapprovaldate,"
          "closingdate,borrower,impagency,totalamt,lendprojectcost,status")
LIGNES_PAR_PAGE = 100
PAGES_MAX = int(os.environ.get("RADAR_BMP_PAGES", "40"))
FENETRE_MOIS = int(os.environ.get("RADAR_BMP_FENETRE_MOIS", "18"))  # +/- autour d'aujourd'hui
STATUTS_GARDES = {"pipeline", "active"}
LIEN_PROJET = "https://projects.worldbank.org/en/projects-operations/project-detail/{}"
UA = {"User-Agent": "RadarAmarante/1.0"}


# ===========================================================================
# TABLE RISQUE : nom WB (minuscule, sans accent) -> ISO3.
# Sert a la fois de FILTRE (hors table = ignore) et de resolveur. Les alias en
# ", Republic of" couvrent les libelles longs de la BM. Etendre au besoin.
# ===========================================================================
def _norm(s):
    return unicodedata.normalize("NFD", str(s or "").lower()).encode(
        "ascii", "ignore").decode("ascii").strip()


_RISQUE_BRUT = {
    # Sahel
    "MLI": ["mali"], "NER": ["niger"], "TCD": ["chad"], "BFA": ["burkina faso"],
    "MRT": ["mauritania"],
    # Afrique centrale
    "CAF": ["central african republic"], "CMR": ["cameroon"],
    "COD": ["congo, democratic republic of", "democratic republic of congo",
            "dr congo", "congo, dem. rep."],
    "COG": ["congo, republic of", "republic of congo", "congo, rep."],
    "GAB": ["gabon"], "GNQ": ["equatorial guinea"],
    # Afrique de l'Est / Corne
    "BDI": ["burundi"], "DJI": ["djibouti"], "ERI": ["eritrea"],
    "ETH": ["ethiopia"], "KEN": ["kenya"], "RWA": ["rwanda"], "SDN": ["sudan"],
    "SOM": ["somalia"], "SSD": ["south sudan"], "TZA": ["tanzania"],
    "UGA": ["uganda"], "COM": ["comoros"],
    # Afrique de l'Ouest
    "BEN": ["benin"], "CIV": ["cote d'ivoire", "cote divoire", "ivory coast"],
    "GHA": ["ghana"], "GIN": ["guinea"], "GMB": ["gambia, the", "gambia"],
    "GNB": ["guinea-bissau"], "LBR": ["liberia"], "NGA": ["nigeria"],
    "SEN": ["senegal"], "SLE": ["sierra leone"], "TGO": ["togo"],
    "CPV": ["cabo verde", "cape verde"],
    # Afrique australe (enjeu extractif/instable)
    "MOZ": ["mozambique"], "ZWE": ["zimbabwe"], "AGO": ["angola"],
    "ZMB": ["zambia"], "MDG": ["madagascar"], "MWI": ["malawi"],
    # Proche / Moyen-Orient
    "IRQ": ["iraq"], "YEM": ["yemen, republic of", "yemen"],
    "SYR": ["syrian arab republic", "syria"], "LBN": ["lebanon"],
    "JOR": ["jordan"], "IRN": ["iran, islamic republic of", "iran"],
    "PSE": ["west bank and gaza"], "LBY": ["libya"],
    # Asie centrale / du Sud
    "AFG": ["afghanistan"], "PAK": ["pakistan"], "TJK": ["tajikistan"],
    "KGZ": ["kyrgyz republic", "kyrgyzstan"], "TKM": ["turkmenistan"],
    "UZB": ["uzbekistan"], "KAZ": ["kazakhstan"], "MMR": ["myanmar"],
    # Europe de l'Est
    "UKR": ["ukraine"], "MDA": ["moldova"],
}
RISQUE_NOM_ISO3 = {}
for _iso, _noms in _RISQUE_BRUT.items():
    for _n in _noms:
        RISQUE_NOM_ISO3[_norm(_n)] = _iso


def resoudre_iso3(countryshortname):
    """Nom WB -> ISO3 si pays a risque, sinon None (le projet est ignore).
    Essaie le nom entier puis le token avant virgule."""
    cle = _norm(countryshortname)
    if cle in RISQUE_NOM_ISO3:
        return RISQUE_NOM_ISO3[cle]
    tete = cle.split(",")[0].strip()
    return RISQUE_NOM_ISO3.get(tete)


# ===========================================================================
# COLLECTE (I/O tolerant, pagination bornee)
# ===========================================================================
def _get_page(os_offset, fetch=None, session=None):
    params = {"format": "json", "rows": LIGNES_PAR_PAGE, "os": os_offset,
              "srt": "boardapprovaldate", "order": "desc", "fl": CHAMPS}
    if fetch is not None:
        return fetch(API, params)
    session = session or ted.session_robuste()
    try:
        r = session.get(API, params=params, headers=UA, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("  (info) BM Projects indisponible (os={}) : {}".format(os_offset, e))
        return None


def _projets(donnees):
    p = (donnees or {}).get("projects") if isinstance(donnees, dict) else None
    if isinstance(p, dict):
        return list(p.values())
    if isinstance(p, list):
        return p
    return []


def _date_iso(brut):
    """'2027-10-26T00:00:00Z' -> date, ou None."""
    s = str(brut or "")[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def collecter_flux(fetch=None, session=None, aujourd=None):
    """Balaie les projets par date d'approbation decroissante, garde les projets
    a risque DANS la fenetre [today-N mois, today+N mois], s'arrete des que la
    date passe sous le bord ancien (tri desc). Renvoie [projets bruts]. Borne par
    PAGES_MAX. Ne leve jamais."""
    auj = aujourd or date.today()
    bas = auj - timedelta(days=FENETRE_MOIS * 31)
    haut = auj + timedelta(days=FENETRE_MOIS * 31)
    gardes = []
    for page in range(PAGES_MAX):
        donnees = _get_page(page * LIGNES_PAR_PAGE, fetch=fetch, session=session)
        lot = _projets(donnees)
        if not lot:
            break
        stop = False
        for p in lot:
            d = _date_iso(p.get("boardapprovaldate"))
            if d is None:
                continue
            if d < bas:
                stop = True                      # tri desc : le reste est plus vieux
                break
            if d > haut:
                continue                          # future lointaine : on saute
            gardes.append(p)
        if stop:
            break
    return gardes


# ===========================================================================
# NORMALISATION + SCORING DETERMINISTE (pur, testable)
# ===========================================================================
def _montant(projet):
    for c in ("totalamt", "lendprojectcost"):
        try:
            v = float(str(projet.get(c) or "0").replace(",", "").strip())
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return 0.0


def _score_commercial(montant):
    """Proxy taille de deploiement (USD)."""
    paliers = [(500e6, 8.0), (200e6, 7.0), (100e6, 6.0), (50e6, 5.0),
               (25e6, 4.0), (10e6, 3.0)]
    for seuil, note in paliers:
        if montant >= seuil:
            return note
    return 2.0


def scorer(iso3, montant):
    """(surete, commercial, final) deterministes. surete = risque zone ;
    commercial = taille du projet. Echelle 0-10, propre a l'amont."""
    tier = ted.MULTIPLICATEUR_ZONE.get(iso3, 0.2)         # 1.0 rouge / 0.6 orange
    surete = round(min(10.0, 2.0 + tier * 8.0), 1)
    commercial = _score_commercial(montant)
    final = round(0.5 * surete + 0.5 * commercial, 1)
    return surete, commercial, final


def _action(final):
    if final >= 6.5:
        return "contacter"
    if final >= 4.5:
        return "surveiller"
    return "ignorer"


def normaliser(projet, aujourd=None):
    """Projet BM brut -> avis amont normalise, ou None (hors risque / statut /
    fenetre). Fonction PURE."""
    statut = _norm(projet.get("status"))
    if statut not in STATUTS_GARDES:
        return None
    iso3 = resoudre_iso3(projet.get("countryshortname"))
    if not iso3:
        return None
    d_appro = _date_iso(projet.get("boardapprovaldate"))
    if d_appro is None:
        return None
    auj = aujourd or date.today()
    if not (auj - timedelta(days=FENETRE_MOIS * 31) <= d_appro
            <= auj + timedelta(days=FENETRE_MOIS * 31)):
        return None
    montant = _montant(projet)
    surete, commercial, final = scorer(iso3, montant)
    action = _action(final)
    pid = str(projet.get("proj_id") or projet.get("id") or "").strip()
    statut_lisible = "Pipeline" if statut == "pipeline" else "Active"
    recent = (auj - d_appro).days <= 365 if d_appro <= auj else False
    fenetre = "court_terme" if (statut == "active" and recent) else "indetermine"
    montant_txt = "{:.0f} M USD".format(montant / 1e6) if montant else "montant n.c."
    just = ("Projet BM {} approuve le {} ({}). Signal AMONT : deploiement a venir "
            "en amont de l'appel d'offres. Cible : le titulaire qui executera, "
            "pas l'agence."
            .format(statut_lisible, d_appro.isoformat(), montant_txt))
    return {
        "titre": str(projet.get("project_name") or "").strip()[:300],
        "acheteur": str(projet.get("impagency") or projet.get("borrower") or "").strip(),
        "pays_execution": iso3,
        "score_final": final, "score_surete": surete, "score_commercial": commercial,
        "action_recommandee": action, "fenetre_action": fenetre,
        "type_notice": "Projet BM ({})".format(statut_lisible),
        "justification": just,
        "publication_number": "BMP-" + pid if pid else "",
        "lien_avis": LIEN_PROJET.format(pid) if pid else "",
        "deadline": (_date_iso(projet.get("closingdate")).isoformat()
                     if _date_iso(projet.get("closingdate")) else ""),
        "date_publication": d_appro.isoformat(),
        # Enveloppe PROJET (cout total, USD) : proxy taille de deploiement, PAS
        # un montant de marche. Suffixe USD pour la conversion EUR aval.
        "enveloppe_usd": "{:.0f} USD".format(montant) if montant else "",
    }


def collecter_et_normaliser(fetch=None, session=None, aujourd=None):
    """Flux -> avis amont normalises, dedup par publication_number. Pur cote
    normalisation ; I/O deleguee a collecter_flux."""
    bruts = collecter_flux(fetch=fetch, session=session, aujourd=aujourd)
    vus, out = set(), []
    for p in bruts:
        avis = normaliser(p, aujourd=aujourd)
        if not avis:
            continue
        pub = avis["publication_number"]
        if not pub or pub in vus:
            continue
        vus.add(pub)
        out.append(avis)
    return out


# ===========================================================================
# SORTIE GOOGLE SHEET (+ miroir Postgres), calquee sur afdb_radar
# ===========================================================================
COLONNES_BMP = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "titre", "acheteur",
    "pays_execution", "type_notice", "justification", "publication_number",
    "lien_avis", "deadline", "date_publication",
    "enveloppe_usd",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_BMP = COLONNES_BMP + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_cs)
    try:
        feuille = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET, rows=3000, cols=len(TOUTES_COLONNES_BMP))
        feuille.append_row(TOUTES_COLONNES_BMP)
        return feuille
    if COLONNE_DATE_DETECTION not in feuille.row_values(1):
        feuille.update(values=[TOUTES_COLONNES_BMP], range_name="A1")
    return feuille


def ligne_depuis_avis(avis):
    v = dict(avis)
    v["date_maj"] = date.today().isoformat()
    return [str(v.get(c, "")) for c in COLONNES_BMP]


def ecrire_resultats(feuille, avis_list):
    """Insere les nouveaux projets, met a jour les scores des connus SANS toucher
    statut_suivi/date_detection. Ecriture groupee, miroir Postgres best-effort.
    Meme logique que les collecteurs ISO (index positionnel depuis le schema)."""
    index = ted.charger_index_publication(feuille, COLONNES_BMP)
    derniere = ted.lettre_colonne(len(COLONNES_BMP))
    maj, nouvelles, nb_maj, nb_new = [], [], 0, 0
    for avis in avis_list:
        pub = avis.get("publication_number", "")
        ligne = ligne_depuis_avis(avis)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere), "values": [ligne]})
            nb_maj += 1
        else:
            nouvelles.append(ligne + ["nouveau", date.today().isoformat()])
            nb_new += 1
    if maj:
        radar_resilience.avec_retry(lambda: feuille.batch_update(maj), "bmp batch_update")
    if nouvelles:
        radar_resilience.avec_retry(
            lambda: feuille.append_rows(nouvelles, value_input_option="RAW"), "bmp append_rows")
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES_BMP, ligne_depuis_avis(a))) for a in avis_list]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_new, nb_maj


def main():
    if not ACTIVER:
        print("(info) BM Projects desactive (RADAR_BM_PROJETS=0).")
        return
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    print("=== COLLECTE AMONT -- BM Projects API ===")
    avis = collecter_et_normaliser()
    print("  {} projet(s) amont a risque retenus (fenetre +/-{} mois).".format(
        len(avis), FENETRE_MOIS))
    par_action = {}
    for a in avis:
        par_action[a["action_recommandee"]] = par_action.get(a["action_recommandee"], 0) + 1
    print("  repartition : {}".format(par_action or "aucun"))
    if not (sheet_id and fichier_cs):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    if not avis:
        print("  rien a ecrire ce run.")
        return
    feuille = ouvrir_feuille(sheet_id, fichier_cs)
    nb_new, nb_maj = ecrire_resultats(feuille, avis)
    print("  ecrit : {} nouveau(x), {} mis a jour.".format(nb_new, nb_maj))


if __name__ == "__main__":
    main()
