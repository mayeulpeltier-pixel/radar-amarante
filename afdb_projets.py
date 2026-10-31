# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COLLECTEUR AMONT : projets AfDB via les donnees ouvertes IATI.
===============================================================================

POURQUOI CE MODULE (31/10/2026)
-------------------------------
Depuis le 29/10/2026, afdb.org est sous defi Cloudflare : le flux RSS du
collecteur d'AVIS `afdb_radar.py` ne ramene plus rien (HTTP 403 pour toute
requete non-navigateur, verifie depuis un runner ET une connexion de
particulier). La sonde `sonde_afdb_recherche` a montre qu'un moteur de
recherche ne donne que des avis tres anciens (index en retard de ~8 mois).

On recupere donc le signal AMONT par une AUTRE porte, ouverte et sans filtre :
l'AfDB publie ses projets sur le registre IATI (norme de transparence de
l'aide, licence ouverte), servi par le portail public d-portal.org. C'est la
meme logique que `bm_projets.py` cote Banque Mondiale : un projet AfDB approuve
en zone a risque = un deploiement a venir, donc un titulaire a demarcher AVANT
l'appel d'offres. Ce n'est PAS l'avis de marche lui-meme (l'amont, pas le
tender), et les donnees arrivent avec quelques semaines de decalage.

SOURCE CONFIRMEE PAR SONDE EN DIRECT (d-portal.org/q.json, 31/10/2026)
---------------------------------------------------------------------
  - JSON, HTTP 200 depuis un runner ; racine {rows:[...], count, ...}.
  - Publieur AfDB = reporting_ref `XM-DAC-46002`. ~200 activites demarrees sur
    18 mois, toute l'Afrique (Somalie, Tchad, RDC, Nigeria, Mozambique...).
  - Champs REELS d'une activite : aid (ex "46002-P-ZA-K00-009"), title,
    day_start / day_end (ENTIERS = jours depuis 1970-01-01), status_code
    (1 pipeline, 2 en cours, 3 finalisation), country_code (ISO2),
    country_percent, commitment (montant engage, USD), description.
  - Pas de filtre pays serieux cote serveur : on borne par date (day_start_gt)
    et on resout le pays par country_code ISO2 -> ISO3 (table locale validee
    contre l'univers du coeur). Lecon IsDB/IDB : ne jamais faire confiance a
    un filtre pays serveur non verifie.

SCORING : DETERMINISTE, SANS LLM (reutilise bm_projets.scorer)
--------------------------------------------------------------
Texte mince, gros volume : un scoring LLM couterait cher pour peu. zone (tier
surete du coeur) x montant (proxy taille de deploiement). Echelle propre a
l'amont, non comparable aux avis LLM -- le dashboard l'etiquette par la source.

Interrupteur : RADAR_AFDB_PROJETS=0 desactive. pays_execution en ISO3 (comme
TED/BM), ecrit dans l'onglet SEPARE `afdb_projets_radar`, jumeau de
`bm_projets_radar` : le dashboard le lit avec le meme helper generique, aucun
cablage specifique.
"""

import os
import time
from datetime import date, timedelta

import ted_complet_v14 as ted
import radar_resilience
import bm_projets                       # reutilise scorer / _score_commercial / _action


ACTIVER = os.environ.get("RADAR_AFDB_PROJETS", "1") != "0"
NOM_ONGLET = "afdb_projets_radar"

API = "https://d-portal.org/q.json"
REPORTING_REF = "XM-DAC-46002"          # AfDB sur le registre IATI
EPOCH = date(1970, 1, 1)                # day_start IATI = jours depuis cette date
LIGNES_PAR_PAGE = int(os.environ.get("RADAR_AFDBP_LIMITE", "500"))
PAGES_MAX = int(os.environ.get("RADAR_AFDBP_PAGES", "12"))
FENETRE_MOIS = int(os.environ.get("RADAR_AFDBP_FENETRE_MOIS", "18"))  # +/- autour d'aujourd'hui
# IATI : 1 pipeline, 2 implementation (en cours). On garde les deux, comme
# bm_projets garde pipeline + active. 3 finalisation / 4 close / 5 annule : hors.
STATUTS_GARDES = {"1", "2"}
STATUT_LISIBLE = {"1": "Pipeline", "2": "En cours"}
LIEN_PROJET = "https://d-portal.org/ctrack.html?reporting_ref={}#view=act&aid={}"
UA = {"User-Agent": "RadarAmarante/1.0"}


# ===========================================================================
# PAYS : country_code ISO2 (IATI) -> ISO3. Borne a l'Afrique + MENA couverts
# par l'AfDB ; chaque valeur est garantie presente dans ted.CODES_PAYS_SUIVIS
# (verifie a l'ecriture). Un code hors table = projet ignore.
# ===========================================================================
ISO2_VERS_ISO3 = {
    "DZ": "DZA", "AO": "AGO", "BJ": "BEN", "BW": "BWA", "BF": "BFA", "BI": "BDI",
    "CV": "CPV", "CM": "CMR", "CF": "CAF", "TD": "TCD", "KM": "COM", "CG": "COG",
    "CD": "COD", "CI": "CIV", "DJ": "DJI", "EG": "EGY", "GQ": "GNQ", "ER": "ERI",
    "SZ": "SWZ", "ET": "ETH", "GA": "GAB", "GM": "GMB", "GH": "GHA", "GN": "GIN",
    "GW": "GNB", "KE": "KEN", "LS": "LSO", "LR": "LBR", "LY": "LBY", "MG": "MDG",
    "MW": "MWI", "ML": "MLI", "MR": "MRT", "MU": "MUS", "MA": "MAR", "MZ": "MOZ",
    "NA": "NAM", "NE": "NER", "NG": "NGA", "RW": "RWA", "ST": "STP", "SN": "SEN",
    "SC": "SYC", "SL": "SLE", "SO": "SOM", "ZA": "ZAF", "SS": "SSD", "SD": "SDN",
    "TZ": "TZA", "TG": "TGO", "TN": "TUN", "UG": "UGA", "ZM": "ZMB", "ZW": "ZWE",
}


def resoudre_iso3(country_code):
    """ISO2 IATI -> ISO3 si pays suivi, sinon None (projet ignore)."""
    iso3 = ISO2_VERS_ISO3.get(str(country_code or "").strip().upper())
    return iso3 if iso3 in set(ted.CODES_PAYS_SUIVIS) else None


def _jour(brut):
    """day_start IATI (entier = jours depuis 1970-01-01) -> date, ou None."""
    try:
        return EPOCH + timedelta(days=int(str(brut).strip()))
    except (TypeError, ValueError):
        return None


def _montant(projet):
    """commitment IATI (USD) -> float >= 0."""
    try:
        v = float(str(projet.get("commitment") or "0").replace(",", "").strip())
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


# ===========================================================================
# COLLECTE (I/O tolerant, pagination bornee, ne leve jamais)
# ===========================================================================
def _get_page(offset, seuil_bas, fetch=None, session=None):
    params = {"from": "act,country", "reporting_ref": REPORTING_REF,
              "day_start_gt": str((seuil_bas - EPOCH).days),
              "limit": LIGNES_PAR_PAGE, "offset": offset, "orderby": "day_start-"}
    if fetch is not None:
        return fetch(API, params)
    session = session or ted.session_robuste()
    try:
        r = session.get(API, params=params, headers=UA, timeout=45)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        print("  (info) IATI AfDB indisponible (offset={}) : {}".format(offset, e))
        return None


def _rows(donnees):
    r = (donnees or {}).get("rows") if isinstance(donnees, dict) else None
    return r if isinstance(r, list) else []


def collecter_flux(fetch=None, session=None, aujourd=None):
    """Projets AfDB demarres dans la fenetre, bornes par date cote serveur
    (day_start_gt) et pagines par offset. Renvoie [projets bruts]. Ne leve
    jamais ; borne par PAGES_MAX."""
    auj = aujourd or date.today()
    bas = auj - timedelta(days=FENETRE_MOIS * 31)
    out = []
    for page in range(PAGES_MAX):
        donnees = _get_page(page * LIGNES_PAR_PAGE, bas, fetch=fetch, session=session)
        lot = _rows(donnees)
        if not lot:
            break
        out.extend(lot)
        if len(lot) < LIGNES_PAR_PAGE:
            break                        # derniere page
    return out


# ===========================================================================
# NORMALISATION + SCORING (pur, testable). Schema IDENTIQUE a bm_projets.
# ===========================================================================
def normaliser(projet, aujourd=None):
    """Activite IATI AfDB -> avis amont normalise, ou None (statut / pays /
    fenetre). Fonction PURE."""
    statut = str(projet.get("status_code") or "").strip()
    if statut not in STATUTS_GARDES:
        return None
    iso3 = resoudre_iso3(projet.get("country_code"))
    if not iso3:
        return None
    d_debut = _jour(projet.get("day_start"))
    if d_debut is None:
        return None
    auj = aujourd or date.today()
    if not (auj - timedelta(days=FENETRE_MOIS * 31) <= d_debut
            <= auj + timedelta(days=FENETRE_MOIS * 31)):
        return None
    montant = _montant(projet)
    surete, commercial, final = bm_projets.scorer(iso3, montant)
    action = bm_projets._action(final)
    aid = str(projet.get("aid") or "").strip()
    lisible = STATUT_LISIBLE.get(statut, "Projet")
    recent = (auj - d_debut).days <= 365 if d_debut <= auj else False
    fenetre = "court_terme" if (statut == "2" and recent) else "indetermine"
    montant_txt = "{:.0f} M USD".format(montant / 1e6) if montant else "montant n.c."
    just = ("Projet AfDB {} demarre le {} ({}). Signal AMONT (IATI) : deploiement "
            "a venir en amont de l'appel d'offres. Cible : le titulaire qui "
            "executera, pas la Banque."
            .format(lisible, d_debut.isoformat(), montant_txt))
    d_fin = _jour(projet.get("day_end"))
    return {
        "titre": str(projet.get("title") or "").strip()[:300],
        "acheteur": "Banque africaine de developpement",
        "pays_execution": iso3,
        "score_final": final, "score_surete": surete, "score_commercial": commercial,
        "action_recommandee": action, "fenetre_action": fenetre,
        "type_notice": "Projet AfDB ({})".format(lisible),
        "justification": just,
        "publication_number": "AFDBP-" + aid if aid else "",
        "lien_avis": LIEN_PROJET.format(REPORTING_REF, aid) if aid else "",
        "deadline": d_fin.isoformat() if d_fin else "",
        "date_publication": d_debut.isoformat(),
        "enveloppe_usd": "{:.0f} USD".format(montant) if montant else "",
    }


def collecter_et_normaliser(fetch=None, session=None, aujourd=None):
    """Flux -> avis amont normalises, dedup par publication_number."""
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
# SORTIE GOOGLE SHEET (+ miroir Postgres). Onglet et colonnes jumeaux de
# bm_projets : le dashboard lit `afdb_projets_radar` sans cablage specifique.
# ===========================================================================
COLONNES = list(bm_projets.COLONNES_BMP)
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_cs)
    try:
        feuille = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET, rows=3000, cols=len(TOUTES_COLONNES))
        feuille.append_row(TOUTES_COLONNES)
        return feuille
    if COLONNE_DATE_DETECTION not in feuille.row_values(1):
        feuille.update(values=[TOUTES_COLONNES], range_name="A1")
    return feuille


def ligne_depuis_avis(avis):
    v = dict(avis)
    v["date_maj"] = date.today().isoformat()
    return [str(v.get(c, "")) for c in COLONNES]


def ecrire_resultats(feuille, avis_list):
    """Insere les nouveaux projets, met a jour les scores des connus SANS
    toucher statut_suivi / date_detection (regle 5). Ecriture groupee, index
    positionnel depuis le schema (regle 4), miroir Postgres best-effort."""
    index = ted.charger_index_publication(feuille, COLONNES)
    derniere = ted.lettre_colonne(len(COLONNES))
    maj, nouvelles, nb_maj, nb_new = [], [], 0, 0
    for avis in avis_list:
        pub = avis.get("publication_number", "")
        ligne = ligne_depuis_avis(avis)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere),
                        "values": [ligne]})
            nb_maj += 1
        else:
            nouvelles.append(ligne + ["nouveau", date.today().isoformat()])
            nb_new += 1
    if maj:
        radar_resilience.avec_retry(lambda: feuille.batch_update(maj), "afdbp batch_update")
    if nouvelles:
        radar_resilience.avec_retry(
            lambda: feuille.append_rows(nouvelles, value_input_option="RAW"),
            "afdbp append_rows")
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, ligne_depuis_avis(a))) for a in avis_list]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:  # noqa: BLE001
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_new, nb_maj


def main():
    if not ACTIVER:
        print("(info) AfDB Projets (IATI) desactive (RADAR_AFDB_PROJETS=0).")
        return
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    print("=== COLLECTE AMONT -- AfDB Projets (IATI / d-portal) ===")
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
