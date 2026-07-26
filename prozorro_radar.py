# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- Collecteur PROZORRO (e-procurement public ukrainien).
=========================================================================

Pourquoi cette source
---------------------
Prozorro publie l'integralite des marches publics ukrainiens. L'Ukraine est
une zone COEUR (PAYS_ROUGE, multiplicateur 1.0) : reconstruction, deploiements
massifs, exposition maximale. Aucun concurrent ne surveille Prozorro
systematiquement, precisement a cause de la barriere linguistique -- c'est
donc un signal defendable.

Ce que la sonde a etabli (26/07/2026, verifie sur le brut)
---------------------------------------------------------
  - API publique https://public-api.prozorro.gov.ua/api/2.5, lecture sans cle.
  - Le flux /tenders est un FLUX DE CHANGEMENTS : il ne renvoie que
    {id, dateModified}. Tout le contenu (titre, valeur, CPV, acheteur, statut,
    awards) vit sur GET /tenders/{id}. Il faut donc UN detail par avis : c'est
    le cout incompressible de cette source.
  - Debit observe : ~100 avis / 1h35, soit ~1500/jour, dont ~95% de bruit
    municipal a faible valeur (ex. achat de lubrifiant a ~100 EUR).
  - Donnees tres propres : 15/15 avec value, 15/15 avec CPV au format standard
    (scheme "ДК021", id type "45250000-4" = CPV a 8 chiffres + cle).
  - 100% cyrillique : le pre-filtre mots-cles FR/EN est INAPPLICABLE ici. Le
    filtrage est donc 100% STRUCTUREL, puis le LLM lit l'ukrainien nativement.

Architecture (decidee apres lecture du gabarit ted_complet_reliefweb)
--------------------------------------------------------------------
Reutilise le coeur TED SANS duplication : session, appel LLM, reparation JSON,
normalisation securite, scoring, action/fenetre, ecriture Sheet positionnelle,
memoire (numeros_publication_existants), miroir Postgres (radar_stockage).

CRIBLES (le detail etant deja telecharge, ils s'appliquent apres le fetch ;
c'est la seule facon, le flux ne portant pas ces champs) :
  1. METHODE : on jette le bruit connu (reporting, belowThreshold,
     priceQuotation) -- petits achats directs et sous-seuil.
  2. VALEUR : montant >= PROZORRO_SEUIL_UAH (defaut 1 000 000 UAH ~ 22 kEUR).
     C'est le crible le plus robuste contre le bruit municipal.
  3. CPV : au moins un lot dans DIVISIONS_CPV_LARGEMENT_ADMISES du coeur, ou un
     code precis toujours admis. Les CONDITIONNELLES (75/79) sont ECARTEES ici :
     leur admission depend d'un mot-cle terrain FR/EN, inapplicable a un titre
     ukrainien -- choix conservateur, documente.
Le pays n'est pas un crible : tout Prozorro est en Ukraine (perimetre, rouge).

MODE DRY-RUN (PROZORRO_DRY_RUN=1 ou PROZORRO_DEBUG=1) -- MESURE "Option A" :
telecharge + filtre + COMPTE, sans aucun appel LLM, sans aucune ecriture, sans
toucher la memoire. Sort l'entonnoir chiffre (vus, rejets par crible,
survivants avis vs attribution) et le temps de fetch, pour valider le ROI
AVANT d'engager le moindre cout. C'est le livrable a lancer en premier.

PORTEE DE CE PREMIER JET : le mode reel traite entierement le flux des AVIS
OUVERTS (LLM + score + ecriture + miroir). Les ATTRIBUTIONS (marches attribues,
gagnant = prospect mobilisation) sont pour l'instant COMPTEES et signalees ;
leur ecriture sera la tranche suivante, une fois le ROI valide sur des chiffres
reels et le partage avis/attribution connu.

Discipline : fetch injectables pour les tests (aucun reseau en test).
"""

import os
import time
from datetime import datetime, timezone, date

import ted_complet_v14 as ted


# ===========================================================================
# PARTIE 1 -- CONFIGURATION (surchargeable par variables d'environnement)
# ===========================================================================
ACTIVER = os.environ.get("RADAR_PROZORRO", "1") != "0"
DRY_RUN = bool(os.environ.get("PROZORRO_DRY_RUN") or os.environ.get("PROZORRO_DEBUG"))

BASE = "https://public-api.prozorro.gov.ua/api/2.5"
ENDPOINT_LISTE = BASE + "/tenders"
ENDPOINT_DETAIL = BASE + "/tenders/{}"
LIEN_PUBLIC = "https://prozorro.gov.ua/tender/{}"   # {tenderID} lisible (UA-...)
TIMEOUT = 45
TAILLE_PAGE = 100

# Fenetre glissante : on ne regarde que les avis modifies dans les N derniers
# jours (defaut ~ l'ecart entre deux runs). Empeche de rejouer tout l'historique.
PROZORRO_JOURS = float(os.environ.get("PROZORRO_JOURS", "4"))
# Crible valeur. 1 000 000 UAH ~ 22 000 EUR. Surchargeable.
SEUIL_UAH = float(os.environ.get("PROZORRO_SEUIL_UAH", "1000000"))
# Garde-fous de cout (une source lourde : on borne tout).
MAX_PAGES = int(os.environ.get("PROZORRO_MAX_PAGES", "400"))
MAX_DETAILS = int(os.environ.get("PROZORRO_MAX_DETAILS", "8000"))
MINUTES_MAX = float(os.environ.get("PROZORRO_MINUTES_MAX", "18"))
# Plafond d'analyses LLM par run (les autres attendent le run suivant).
MAX_AVIS_LLM = int(os.environ.get("PROZORRO_MAX_LLM", "120"))
# Politesse reseau entre deux details.
PAUSE_DETAIL = float(os.environ.get("PROZORRO_PAUSE", "0.05"))

# Methodes = bruit connu (petits achats directs / sous-seuil). Liste NOIRE
# (plus robuste qu'une liste blanche face a l'ajout de nouveaux types).
METHODES_BRUIT = {"reporting", "belowThreshold", "priceQuotation"}

# Taux de change approximatifs, UNIQUEMENT pour le crible valeur quand un
# marche est libelle hors UAH (rare, souvent bailleur). Volontairement grossier.
TAUX_VERS_UAH = {"UAH": 1.0, "USD": 41.0, "EUR": 44.0}

ISO3 = "UKR"
PAYS_NOM = "Ukraine"
NOM_ONGLET = "prozorro_radar"


# ===========================================================================
# PARTIE 2 -- OUTILS BAS NIVEAU
# ===========================================================================

def _codes_cpv(detail):
    """Codes CPV normalises a 8 chiffres (sans la cle '-4') pour chaque lot.
    Prozorro : items[].classification.id, scheme ДК021 == CPV UE."""
    codes = []
    for it in (detail.get("items") or []):
        clas = it.get("classification") or {}
        brut = str(clas.get("id") or "")
        code8 = brut.split("-")[0].strip()
        if code8:
            codes.append(code8)
    return codes


def _valeur_uah(detail):
    """(montant_en_uah, texte_lisible). Montant None si absent."""
    v = detail.get("value") or {}
    montant = v.get("amount")
    devise = (v.get("currency") or "UAH").upper()
    if montant is None:
        return None, ""
    try:
        montant = float(montant)
    except (TypeError, ValueError):
        return None, ""
    taux = TAUX_VERS_UAH.get(devise)
    montant_uah = montant * taux if taux else None  # devise inconnue : pas de rejet valeur
    return montant_uah, "{:.0f} {}".format(montant, devise)


def _cpv_admissible(codes):
    """Meme doctrine que ted.avis_correspond, adaptee a Prozorro :
    - un code precis toujours admis -> ok ;
    - une division largement admise -> ok ;
    - les CONDITIONNELLES (75/79) sont ecartees (gating mots-cles FR/EN
      inapplicable a un titre ukrainien) ;
    - pas de CPV -> ecarte (le detail Prozorro en a toujours au moins un)."""
    if not codes:
        return False
    if set(codes) & ted.CODES_PRECIS_TOUJOURS_ADMIS:
        return True
    divisions = {c[:2] for c in codes}
    return bool(divisions & ted.DIVISIONS_CPV_LARGEMENT_ADMISES)


def _est_attribution(detail):
    """Vrai si un gagnant existe (award actif avec fournisseurs)."""
    for a in (detail.get("awards") or []):
        if a.get("status") == "active" and (a.get("suppliers")):
            return True
    return False


def _iso_moins_jours(nb_jours):
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(days=nb_jours)


def _dt(s):
    """Parse une date ISO Prozorro en datetime aware. None si illisible."""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ===========================================================================
# PARTIE 3 -- COLLECTE (flux descendant + detail + 3 cribles)
# ===========================================================================

def _session():
    return ted.session_robuste()


def collecte(session=None, fetch_liste=None, fetch_detail=None, deja_vus=None):
    """Parcourt le flux (recent d'abord) sur PROZORRO_JOURS, telecharge chaque
    detail, applique les 3 cribles. Renvoie (avis, attributions, compteurs).

    fetch_liste(url) -> dict JSON d'une page ; fetch_detail(id) -> dict detail.
    Injectables pour les tests (aucun reseau). En prod, GET via la session TED.
    `deja_vus` : set de publication_number deja ecrits -> on saute leur detail
    (economie de fetch). None/vide en dry-run (on mesure l'entonnoir brut)."""
    session = session or _session()
    deja_vus = deja_vus or set()
    seuil = _iso_moins_jours(PROZORRO_JOURS)
    t0 = time.time()

    def get_liste(url):
        if fetch_liste is not None:
            return fetch_liste(url)
        rep = session.get(url, timeout=TIMEOUT)
        rep.raise_for_status()
        return rep.json()

    def get_detail(tid):
        if fetch_detail is not None:
            return fetch_detail(tid)
        rep = session.get(ENDPOINT_DETAIL.format(tid), timeout=TIMEOUT)
        rep.raise_for_status()
        return (rep.json() or {}).get("data") or {}

    compteurs = {
        "vus_flux": 0, "deja_connus": 0, "details_lus": 0,
        "rejet_methode": 0, "rejet_valeur": 0, "rejet_cpv": 0,
        "survivants_avis": 0, "survivants_attribution": 0,
        "details_illisibles": 0, "arret": "fin_fenetre",
    }
    avis, attributions = [], []
    url = ENDPOINT_LISTE + "?descending=1&limit={}".format(TAILLE_PAGE)

    for page in range(MAX_PAGES):
        if (time.time() - t0) / 60.0 > MINUTES_MAX:
            compteurs["arret"] = "temps_max"
            break
        if compteurs["details_lus"] >= MAX_DETAILS:
            compteurs["arret"] = "max_details"
            break
        try:
            charge = get_liste(url)
        except Exception as e:
            print("  (info) flux Prozorro indisponible (page {}) : {}".format(page, e))
            compteurs["arret"] = "erreur_flux"
            break

        lot = charge.get("data") or []
        if not lot:
            break

        trop_vieux = False
        for entree in lot:
            compteurs["vus_flux"] += 1
            dm = _dt(entree.get("dateModified"))
            if dm is not None and dm < seuil:
                trop_vieux = True
                break
            tid = entree.get("id")
            if not tid:
                continue
            pub = "PZ" + tid
            if pub in deja_vus:
                compteurs["deja_connus"] += 1
                continue

            try:
                detail = get_detail(tid)
            except Exception:
                compteurs["details_illisibles"] += 1
                continue
            compteurs["details_lus"] += 1
            if PAUSE_DETAIL and fetch_detail is None:
                time.sleep(PAUSE_DETAIL)

            # Crible 1 : methode.
            if (detail.get("procurementMethodType") or "") in METHODES_BRUIT:
                compteurs["rejet_methode"] += 1
                continue
            # Crible 2 : valeur.
            montant_uah, _txt = _valeur_uah(detail)
            if montant_uah is not None and montant_uah < SEUIL_UAH:
                compteurs["rejet_valeur"] += 1
                continue
            # Crible 3 : CPV.
            if not _cpv_admissible(_codes_cpv(detail)):
                compteurs["rejet_cpv"] += 1
                continue

            if _est_attribution(detail):
                compteurs["survivants_attribution"] += 1
                attributions.append(detail)
            else:
                compteurs["survivants_avis"] += 1
                avis.append(detail)

        if trop_vieux:
            compteurs["arret"] = "fin_fenetre"
            break
        suivant = (charge.get("next_page") or {}).get("uri")
        if not suivant:
            break
        url = suivant
    else:
        compteurs["arret"] = "max_pages"

    compteurs["secondes"] = round(time.time() - t0, 1)
    return avis, attributions, compteurs


# ===========================================================================
# PARTIE 4 -- NORMALISATION (vers la forme d'avis commune au coeur TED)
# ===========================================================================

def _description(detail):
    """Texte pour le LLM (ukrainien assume). Titre + libelles de lots +
    methode + valeur + region : de quoi juger le deploiement."""
    bouts = [detail.get("title") or ""]
    for it in (detail.get("items") or [])[:6]:
        d = (it.get("description") or "").strip()
        if d:
            bouts.append("- " + d)
    _, val = _valeur_uah(detail)
    if val:
        bouts.append("Montant : " + val)
    meth = detail.get("procurementMethodType")
    if meth:
        bouts.append("Procedure : " + meth)
    pe = detail.get("procuringEntity") or {}
    reg = (pe.get("address") or {}).get("region")
    if reg:
        bouts.append("Region : " + reg)
    texte = "\n".join(b for b in bouts if b)
    if len(texte) > ted.MAX_CARACTERES_DESCRIPTION:
        texte = texte[:ted.MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"
    return texte


def normaliser(detail):
    """Avis normalise attendu par calculer_scores / calculer_fenetre_action,
    + champs propres Prozorro pour le Sheet. On CONSERVE le CPV reel (contrairement
    a ReliefWeb) : Prozorro fournit un vrai code, le bonus infra est legitime."""
    hexid = detail.get("id") or ""
    tender_lisible = detail.get("tenderID") or hexid
    codes = _codes_cpv(detail)
    pe = detail.get("procuringEntity") or {}
    region = (pe.get("address") or {}).get("region") or ""
    _, valeur_txt = _valeur_uah(detail)
    date_pub = (str(detail.get("date") or detail.get("dateCreated") or ""))[:10]
    fin = (((detail.get("tenderPeriod") or {}).get("endDate")) or "")[:10]

    return {
        "publication_number": ("PZ" + hexid) if hexid else "",
        "titre": (detail.get("title") or "")[:300],
        "acheteur": pe.get("name") or "Acheteur public ukrainien (non precise)",
        "pays_acheteur": ISO3,
        "pays_execution": PAYS_NOM,          # nom lisible (prompt + affichage + Sheet)
        "pays_iso3": ISO3,                    # code (scoring zone)
        "pays_execution_incertitude": False,
        "cpv": codes[0] if codes else "",
        "description": _description(detail),
        "deadline": fin,
        "date_publication": date_pub,
        "valeur_estimee": valeur_txt or "inconnu",
        "source_mode_b": False,
        "lien_avis": LIEN_PUBLIC.format(tender_lisible),
        # Champs propres Prozorro (Sheet)
        "statut": detail.get("status") or "",
        "methode": detail.get("procurementMethodType") or "",
        "region": region,
        "cpv_tous": ";".join(codes),
        "categorie_marche": detail.get("mainProcurementCategory") or "",
    }


def avis_pour_scoring(avis):
    """Copie pour calculer_scores : pays_execution devient l'ISO3 (multiplicateur
    de zone = 1.0 pour l'Ukraine). Le CPV reel est conserve (bonus infra)."""
    copie = dict(avis)
    copie["pays_execution"] = avis.get("pays_iso3") or ISO3
    return copie


def cible_commerciale(avis, extraction):
    """Qui demarcher. Sur un marche ouvert, le prospect est le futur titulaire
    (a suivre jusqu'a l'attribution) et l'acheteur/maitre d'ouvrage. La nuance
    accessibilite (marche d'Etat) est deja captee par le score commercial."""
    acheteur = avis.get("acheteur") or "l'acheteur public"
    return ("Marche public ukrainien porte par {}. Cible : le futur titulaire "
            "(mobilisation de personnel sur zone rouge) et la maitrise d'ouvrage. "
            "Suivre jusqu'a l'attribution pour identifier le gagnant a demarcher."
            ).format(acheteur)


# ===========================================================================
# PARTIE 5 -- PROMPT LLM (marche public ukrainien, MEME SCHEMA que TED)
# ===========================================================================
# Schema de sortie IDENTIQUE a TED/ReliefWeb pour que ted.calculer_scores /
# calculer_action_recommandee fonctionnent sans modification. Seul le cadrage
# change : ici un avis de marche public ukrainien, texte en ukrainien.
PROMPT_PROZORRO = """Tu es analyste sûreté pour une société française de protection de personnes en zones à risque (escorte, protection rapprochée CPO/CPD, chauffeur sécurité, véhicule sécurisé, sécurisation de déplacements terrain). Elle ne vend PAS de conseil voyage générique ni de simple briefing.

On te donne un AVIS DE MARCHÉ PUBLIC ukrainien (plateforme Prozorro), pour un pays en guerre (Ukraine). Le texte est en UKRAINIEN : raisonne en anglais, cite les indices dans leur langue d'origine. Détermine si l'exécution de ce marché implique une présence PHYSIQUE et RÉGULIÈRE de personnel sur le terrain, créant un besoin probable de prestations opérationnelles de sûreté pour le TITULAIRE qui l'exécutera.

RÈGLE SUR LE DÉPLOIEMENT : un marché de travaux, de construction/reconstruction, de fourniture avec installation sur site, de supervision de chantier ou d'assistance technique de terrain expose du personnel. Un marché purement documentaire, logiciel, ou de fourniture livrée sans présence n'expose pas.

RÈGLE SUR LA MOBILITÉ TERRAIN : classe le profil de mobilité dans UNE seule catégorie (la plus représentative) : aucune | capitale | multi_sites | chantier | terrain_isole | frontiere. En Ukraine, un chantier proche du front ou en zone libérée relève de frontiere ou terrain_isole.

RÈGLE SUR LA SÉCURITÉ DÉJÀ EN PLACE (distingue un marché fermé d'une conquête). Renseigne "securite_existante" :
- "interne_client" : sûreté gérée en interne par l'acheteur (garde étatique, unité dédiée) -> peu d'ouverture.
- "prestataire_tiers" : sûreté déjà confiée à un prestataire privé externe -> PAS une raison d'écarter, c'est une OPPORTUNITÉ DE DÉPLACEMENT concurrentiel, à CONSERVER et signaler.
- "aucune" : aucun dispositif mentionné, besoin potentiellement ouvert.
- "inconnu" : indéterminable depuis l'avis.

RÈGLE SUR LE TYPE DE CLIENT : acheteur = État/administration locale ukrainienne le plus souvent. Le titulaire final peut être une entreprise privée (BTP, ingénierie), plus accessible commercialement.

RÈGLE SUR L'ACCESSIBILITÉ COMMERCIALE : un marché d'État ukrainien passe par le titulaire ; l'accès commercial se joue au niveau de l'entreprise qui exécute, pas de l'acheteur public.

RÈGLE SUR LES PROFILS D'ACTEURS : ne cite JAMAIS de nom d'entreprise réelle, décris des PROFILS de type d'acteur.

Réponds UNIQUEMENT en JSON valide, sans texte ni Markdown autour, et SANS commentaire entre parenthèses dans les valeurs.

Schéma de sortie :
{{
  "deploiement_terrain_reel": true | false,
  "type_mobilite": "aucune | capitale | multi_sites | chantier | terrain_isole | frontiere",
  "profil_personnes_exposees": "expert_international | executive | technicien | ouvrier_local | aucun",
  "securite_existante": "aucune | interne_client | prestataire_tiers | inconnu",
  "indices_deploiement": ["courtes citations textuelles"],
  "type_activite": "assistance_technique | supervision_chantier | etude_terrain | fourniture_equipement | formation | autre",
  "type_client": "bailleur_donateur | institution_ue_onu | etat_administration_locale | entreprise_privee | autre",
  "duree_estimee": "courte_ponctuelle | longue_ou_residente | indetermine",
  "accessibilite_commerciale": "facile | moyenne | difficile",
  "profils_acteurs_probables": ["types de profils, JAMAIS de noms d'entreprises reelles"],
  "besoin_securite_operationnel_probable": true | false,
  "niveau_opportunite_amarante": "fort | moyen | faible",
  "justification": "une à deux phrases, en lien avec un besoin opérationnel concret (escorte, CPO, véhicule sécurisé)",
  "confiance": 0.0 à 1.0
}}

Avis à analyser :
Acheteur : {acheteur}
Pays d'exécution : {pays_execution}
Région : {region}
CPV : {cpv}
Intitulé : {titre}
Description (peut être tronquée, en ukrainien) : {description}
"""


def analyser(avis, modele=None):
    """Extraction LLM d'un avis Prozorro. Meme echelle de recuperation JSON que
    ted.appeler_llm (parse direct -> sous-chaine {..} -> reparation Sonnet), avec
    normaliser_securite systematique (comme ReliefWeb depuis le 23/07/2026)."""
    import json
    prompt = PROMPT_PROZORRO.format(
        acheteur=avis.get("acheteur", ""),
        pays_execution=avis.get("pays_execution", ""),
        region=avis.get("region", "") or "(non precisee)",
        cpv=avis.get("cpv_tous", "") or avis.get("cpv", "") or "(non precise)",
        titre=avis.get("titre", ""),
        description=avis.get("description", "") or "(non fournie)",
    )
    texte = ted.appeler_modele(prompt, modele=modele)
    if texte is None:
        return None
    try:
        return ted.normaliser_securite(json.loads(texte))
    except json.JSONDecodeError:
        pass
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin != -1 and fin > debut:
        try:
            return ted.normaliser_securite(json.loads(texte[debut:fin + 1]))
        except json.JSONDecodeError:
            pass
    repare = ted.reparer_json(texte, modele=ted.MODELE_RAFFINEMENT)
    if repare is None:
        return None
    try:
        return ted.normaliser_securite(json.loads(repare))
    except json.JSONDecodeError:
        return None


# ===========================================================================
# PARTIE 6 -- SORTIE GOOGLE SHEET (onglet prozorro_radar) + miroir Postgres
# ===========================================================================
COLONNES = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "region",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance",
    "modele", "raffine", "divergence",
    "statut", "methode", "valeur_estimee", "cpv", "categorie_marche",
    "publication_number", "lien_avis", "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_compte_service):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_compte_service, scopes=portee)
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        feuille = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET, rows=2000, cols=len(TOUTES_COLONNES))
        feuille.append_row(TOUTES_COLONNES)
        return feuille
    entetes = feuille.row_values(1)
    if COLONNE_DATE_DETECTION not in entetes:
        feuille.update(values=[TOUTES_COLONNES], range_name="A1")
    return feuille


def ligne_depuis_resultat(r):
    avis, extraction = r["avis"], r["extraction"]
    modele_utilise = ted.MODELE_RAFFINEMENT if r["raffine"] else ted.MODELE
    valeurs = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"],
        "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.calculer_action_recommandee(r["score"], extraction, surete=r["surete"]),
        "fenetre_action": ted.calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": extraction.get("niveau_opportunite_amarante") if extraction else "",
        "titre": avis.get("titre", ""),
        "acheteur": avis.get("acheteur", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "region": avis.get("region", ""),
        "type_client": extraction.get("type_client") if extraction else "",
        "type_mobilite": extraction.get("type_mobilite") if extraction else "",
        "profil_personnes_exposees": extraction.get("profil_personnes_exposees") if extraction else "",
        "duree_estimee": extraction.get("duree_estimee") if extraction else "",
        "accessibilite_commerciale": extraction.get("accessibilite_commerciale") if extraction else "",
        "securite_existante_detectee": extraction.get("securite_existante_detectee") if extraction else "",
        "profils_acteurs_probables": ", ".join(extraction.get("profils_acteurs_probables") or []) if extraction else "",
        "cible_commerciale_reelle": cible_commerciale(avis, extraction),
        "justification": extraction.get("justification") if extraction else "",
        "confiance": extraction.get("confiance") if extraction else "",
        "modele": modele_utilise,
        "raffine": r["raffine"],
        "divergence": r["divergence"],
        "statut": avis.get("statut", ""),
        "methode": avis.get("methode", ""),
        "valeur_estimee": avis.get("valeur_estimee", ""),
        "cpv": avis.get("cpv", ""),
        "categorie_marche": avis.get("categorie_marche", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(valeurs.get(c, "")) for c in COLONNES]


def ecrire_resultats(feuille, resultats):
    """Ecriture groupee. statut_suivi et date_detection preserves sur re-run.
    Index en LECTURE POSITIONNELLE depuis le SCHEMA (regle 4). Puis miroir
    Postgres best-effort (jamais bloquant)."""
    index = ted.charger_index_publication(feuille, COLONNES)
    derniere_lettre = ted.lettre_colonne(len(COLONNES))
    maj_groupees, nouvelles_lignes = [], []
    nb_nouveaux, nb_maj = 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne_valeurs = ligne_depuis_resultat(r)
        if pub and pub in index:
            num = index[pub]
            maj_groupees.append({
                "range": "A{0}:{1}{0}".format(num, derniere_lettre),
                "values": [ligne_valeurs],
            })
            nb_maj += 1
        else:
            nouvelles_lignes.append(ligne_valeurs + ["nouveau", date.today().isoformat()])
            nb_nouveaux += 1
    if maj_groupees:
        feuille.batch_update(maj_groupees)
    if nouvelles_lignes:
        feuille.append_rows(nouvelles_lignes, value_input_option="RAW")
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, ligne_depuis_resultat(r))) for r in resultats]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_nouveaux, nb_maj


# ===========================================================================
# PARTIE 7 -- POINT D'ENTREE
# ===========================================================================

def _afficher_entonnoir(compteurs):
    c = compteurs
    print("\n--- ENTONNOIR PROZORRO ---")
    print("  Vus dans le flux           : {}".format(c["vus_flux"]))
    print("  Deja connus (sautes)       : {}".format(c["deja_connus"]))
    print("  Details telecharges        : {}".format(c["details_lus"]))
    print("  Details illisibles         : {}".format(c["details_illisibles"]))
    print("  Rejetes -- methode (bruit) : {}".format(c["rejet_methode"]))
    print("  Rejetes -- valeur < seuil  : {}".format(c["rejet_valeur"]))
    print("  Rejetes -- CPV hors cible  : {}".format(c["rejet_cpv"]))
    print("  SURVIVANTS avis (ouverts)  : {}".format(c["survivants_avis"]))
    print("  SURVIVANTS attributions    : {}".format(c["survivants_attribution"]))
    survivants = c["survivants_avis"] + c["survivants_attribution"]
    if c["details_lus"]:
        taux = 100.0 * survivants / c["details_lus"]
        print("  Densite de pertinence      : {:.1f}% des details".format(taux))
        print("  Temps / detail             : {:.3f}s".format(c["secondes"] / c["details_lus"]))
    print("  Temps total collecte       : {}s (arret : {})".format(c["secondes"], c["arret"]))
    print("  Seuil valeur : {:.0f} UAH | fenetre : {}j".format(SEUIL_UAH, PROZORRO_JOURS))


def main():
    if not ACTIVER:
        print("(info) Collecteur Prozorro desactive (RADAR_PROZORRO=0).")
        return
    if not DRY_RUN and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente. Definis-la, ou lance en "
              "PROZORRO_DRY_RUN=1 pour mesurer sans cout.")
        return

    # Memoire : en mode reel, on saute le detail des avis deja ecrits.
    deja_vus = set()
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not DRY_RUN:
        deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES) or set()

    print("Etape 1/2 -- Collecte Prozorro (flux descendant, fenetre {}j, seuil {:.0f} UAH)...".format(
        PROZORRO_JOURS, SEUIL_UAH))
    avis_bruts, attributions_brutes, compteurs = collecte(deja_vus=deja_vus)
    _afficher_entonnoir(compteurs)

    # --- MODE DRY-RUN : mesure Option A, aucun cout, aucune ecriture ---
    if DRY_RUN:
        print("\n=== MODE DRY-RUN : aucun LLM, aucune ecriture ===")
        apercu = [normaliser(d) for d in avis_bruts[:15]]
        for i, a in enumerate(apercu, start=1):
            print("  {:2}. [{}] {} | {} | {}".format(
                i, a.get("valeur_estimee", ""), a.get("titre", "")[:60],
                a.get("region", "")[:24], a.get("cpv", "")))
        if attributions_brutes:
            print("\n  (Attributions detectees : {} -- ecriture a cabler en tranche "
                  "suivante une fois le ROI valide.)".format(len(attributions_brutes)))
        print("\nRetire PROZORRO_DRY_RUN pour lancer l'analyse reelle.")
        return

    # --- MODE REEL : analyse des AVIS ouverts ---
    if attributions_brutes:
        print("\n(info) {} attribution(s) detectee(s) : comptees, non ecrites "
              "(tranche suivante).".format(len(attributions_brutes)))
    if not avis_bruts:
        print("Aucun avis ouvert Prozorro a analyser ce run.")
        return

    avis = [normaliser(d) for d in avis_bruts]
    # Risque constant (UKR = 1.0) : on priorise par fraicheur (le plus recent
    # d'abord), la valeur departageant a date egale.
    def _priorite(a):
        return (a.get("date_publication", ""), a.get("valeur_estimee", ""))
    avis.sort(key=_priorite, reverse=True)
    if len(avis) > MAX_AVIS_LLM:
        en_attente = len(avis) - MAX_AVIS_LLM
        avis = avis[:MAX_AVIS_LLM]
        print("    (plafond {} : {} analyse(s) ce run, {} en attente).".format(
            MAX_AVIS_LLM, MAX_AVIS_LLM, en_attente))

    print("\nEtape 2/2 -- Extraction LLM et score ({} avis, modele {})...\n".format(
        len(avis), ted.MODELE))
    resultats = []
    for i, a in enumerate(avis, start=1):
        print("[{}/{}] {}...".format(i, len(avis), a["titre"][:60]))
        extraction = analyser(a)
        s, c, f = ted.calculer_scores(avis_pour_scoring(a), extraction)
        resultats.append({
            "avis": a, "extraction": extraction,
            "final_haiku": f, "surete": s, "commercial": c, "score": f,
            "raffine": False, "divergence": False,
        })
        time.sleep(0.4)

    def merite_escalade(r):
        if r["extraction"] is None:
            return False
        if r["final_haiku"] >= 5:
            return True
        if r["extraction"].get("confiance", 1.0) < 0.7:
            return True
        return ted.escalade_pour_securite(r["extraction"])

    a_escalader = [r for r in resultats if merite_escalade(r)]
    if a_escalader:
        print("\n{} avis escalade(s) vers {}...\n".format(len(a_escalader), ted.MODELE_RAFFINEMENT))
        for i, r in enumerate(a_escalader, start=1):
            print("[{}/{}] Raffinement : {}...".format(i, len(a_escalader), r["avis"]["titre"][:60]))
            raffinee = analyser(r["avis"], modele=ted.MODELE_RAFFINEMENT)
            if raffinee is not None:
                s, c, f = ted.calculer_scores(avis_pour_scoring(r["avis"]), raffinee)
                r["extraction"], r["surete"], r["commercial"], r["score"] = raffinee, s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0
            time.sleep(0.4)

    resultats.sort(key=lambda r: r["score"], reverse=True)

    if sheet_id and fichier:
        print("\nEcriture dans l'onglet '{}' ({} avis)...".format(NOM_ONGLET, len(resultats)))
        try:
            feuille = ouvrir_feuille(sheet_id, fichier)
            nb_nouveaux, nb_maj = ecrire_resultats(feuille, resultats)
            print("-> {} nouveau(x), {} mis a jour (statut_suivi jamais touche).".format(
                nb_nouveaux, nb_maj))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("\n(Pas de Sheet configure : definis TED_SHEET_ID et "
              "GOOGLE_SERVICE_ACCOUNT_FILE pour activer l'ecriture.)")

    print("\n" + "=" * 70)
    nb_fort = sum(1 for r in resultats if r["score"] >= ted.SEUIL_ALERTE)
    nb_surv = sum(1 for r in resultats if ted.SEUIL_SURVEILLANCE <= r["score"] < ted.SEUIL_ALERTE)
    print("RESULTATS PROZORRO : {} FORT(S) | {} a surveiller | {} faible(s)".format(
        nb_fort, nb_surv, len(resultats) - nb_fort - nb_surv))
    print("=" * 70)
    for r in resultats:
        score, avis_r, extraction = r["score"], r["avis"], r["extraction"]
        etiquette = ("[FORT]" if score >= ted.SEUIL_ALERTE
                     else "[A SURVEILLER]" if score >= ted.SEUIL_SURVEILLANCE else "[faible]")
        print("\n{} Score {:.1f}/10 (surete {:.1f} | commercial {:.1f})".format(
            etiquette, score, r["surete"], r["commercial"]))
        print("  {}".format(avis_r["titre"][:90]))
        print("  Acheteur : {} | Region : {} | Valeur : {}".format(
            avis_r["acheteur"][:50], avis_r.get("region") or "n.c.", avis_r.get("valeur_estimee")))
        if extraction:
            print("  Justification : {}".format(extraction.get("justification")))
        print("  Lien : {}".format(avis_r["lien_avis"]))


if __name__ == "__main__":
    main()
