# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur RELIEFWEB (offres d'emploi terrain).
=================================================================

Quatrieme source publique du radar. ReliefWeb (service d'information
humanitaire de l'OCHA/ONU) expose une API publique, gratuite et sans cle,
qui renvoie du JSON. On exploite l'endpoint JOBS : chaque offre d'emploi
publiee pour un pays a risque revele une organisation qui DEPLOIE du
personnel sur le terrain (coordinateur pays, chef de projet, logisticien,
expert...). C'est exactement le profil de client d'Amarante -- et, a la
difference d'un marche public, l'organisation qui recrute EST elle-meme le
deployeur, donc la cible commerciale directe (pas un titulaire a retrouver).

DOC API (verifiee) : https://apidoc.reliefweb.int/
  - Endpoint jobs : https://api.reliefweb.int/v2/jobs
  - Le parametre `appname` est OBLIGATOIRE (dans l'URL, GET comme POST) et
    sert au suivi statistique cote ReliefWeb.
  - Enveloppe de reponse : {"data": [{"id": ..., "fields": {...}}],
    "count": N, "totalCount": N}. On lit tout de facon DEFENSIVE (les
    champs peuvent etre absents ou de forme variable), comme le reste du
    radar.
  - Aucun frais, mais limite a 1000 entrees par appel : on pagine.

REUTILISATION : ce module reutilise INTEGRALEMENT la plomberie de
ted_complet_v14 (session resiliente, appel modele, reparation JSON,
scoring deterministe, garde-fou anti faux-FORT, escalade Sonnet, ecriture
Sheet groupee, memoire inter-runs). Seuls la COLLECTE, la NORMALISATION,
le PROMPT (adapte a une offre d'emploi, pas a un marche public) et le
SCHEMA Sheet sont propres a ReliefWeb.

Pre-requis : ted_complet_v14.py dans le MEME dossier. Ecrit dans l'onglet
SEPARE "reliefweb_radar" du meme Google Sheet.

Interrupteur : RADAR_RELIEFWEB=0 desactive completement le collecteur.
Mode reglage : RELIEFWEB_DRY_RUN=1 montre l'entonnoir SANS aucun appel paye.
"""

import json
import os
import time
from datetime import date, datetime, timedelta

import requests

# --- Reutilisation du coeur TED (aucune modification de ce fichier) ---------
try:
    import ted_complet_v14 as ted
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce "
        "collecteur (il en importe le coeur : session, LLM, scoring, Sheet)."
    )


# ===========================================================================
# PARTIE 1 -- CONFIGURATION SPECIFIQUE RELIEFWEB
# ===========================================================================

RELIEFWEB_ENDPOINT = "https://api.reliefweb.int/v2/jobs"
# appname non generique demande par ReliefWeb (suivi statistique). Surchargeable.
APPNAME = os.environ.get("RELIEFWEB_APPNAME", "radar-amarante")
LIEN_RELIEFWEB = "https://reliefweb.int/node/{}"

NB_JOURS_FENETRE_RW = int(os.environ.get("RELIEFWEB_JOURS", "30"))  # offres publiees dans les N derniers jours
ROWS_RW = 200                 # entrees par page (max API = 1000, on reste modere)
MAX_PAGES_RW = 10             # garde-fou anti-boucle
MAX_AVIS_LLM_RW = int(os.environ.get("RELIEFWEB_BUDGET", "120"))  # plafond d'appels LLM par run

ACTIVER_RELIEFWEB = os.environ.get("RADAR_RELIEFWEB", "1") != "0"

NOM_ONGLET_RW = "reliefweb_radar"

# Univers de risque = exactement celui du radar (codes ISO3 suivis par TED).
# Une offre hors de cet ensemble est ignoree (pays sans enjeu surete).
CODES_RISQUE = set(ted.CODES_PAYS_SUIVIS)

# Pre-filtre bon marche (AVANT tout appel LLM) : postes manifestement SANS
# deploiement terrain expose. Volontairement court et prudent -- on n'exclut
# PAS les intitules ambigus, le LLM tranche ensuite. On ecarte surtout le
# distanciel et les postes non deployes.
MOTS_EXCLUSION_RW = [
    "home-based", "home based", "remote", "teleworking", "telework",
    "télétravail", "teletravail", "work from home", "fully remote",
    "internship", "intern ", "stagiaire", "stage ", "trainee",
    "roster", "consultancy - home", "desk officer", "hq-based", "hq based",
]

# Override POSITIF : un intitule qui evoque explicitement le terrain/la
# securite est garde meme si un mot d'exclusion apparait par ailleurs.
MOTS_SIGNAL_TERRAIN_RW = [
    "field", "terrain", "deployment", "déploiement", "security",
    "sûreté", "surete", "safety", "logistic", "logistique", "convoy",
    "fleet", "movement", "access", "area coordinator", "head of office",
    "chef de base", "coordinateur terrain", "field coordinator",
]


# ===========================================================================
# PARTIE 2 -- COLLECTE (API ReliefWeb v2, POST JSON)
# ===========================================================================

# Champs demandes a l'API. On demande des champs de HAUT NIVEAU (objets
# complets), pas des sous-champs pointes : l'API jobs refuse (400) certains
# sous-champs (ex: source.type, country.location, url_alias). Les extracteurs
# defensifs du module lisent ensuite name/iso3 dans ces objets.
_CHAMPS_RW = [
    "title", "body", "date", "source", "country",
    "city", "type", "career_categories", "how_to_apply", "url",
]


def _payload_rw(offset, seuil_iso):
    """Corps POST : offres creees depuis `seuil_iso`, les plus recentes
    d'abord. On filtre par DATE cote serveur uniquement (robuste) ; le tri
    par pays a risque se fait cote client (pertinent_reliefweb), car la
    validite du filtre pays sur l'endpoint jobs n'est pas garantie."""
    return {
        "filter": {"field": "date.created", "value": {"from": seuil_iso}},
        "fields": {"include": _CHAMPS_RW},
        "sort": ["date.created:desc"],
        "limit": ROWS_RW,
        "offset": offset,
    }


def collecte_reliefweb(fetch=None, session=None):
    """Pagine l'API jobs et renvoie (bruts, total_api).
    `fetch` injectable pour tests : callable(url, payload_json) -> dict JSON.
    En production, POST resiliente via la session du coeur TED."""
    seuil = datetime.utcnow() - timedelta(days=NB_JOURS_FENETRE_RW)
    seuil_iso = seuil.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    url = RELIEFWEB_ENDPOINT + "?appname=" + APPNAME
    session = session or ted.session_robuste()

    def appeler(payload):
        if fetch is not None:
            return fetch(url, payload)
        rep = session.post(url, json=payload, timeout=30)
        if rep.status_code >= 400:
            # L'API ReliefWeb renvoie un message JSON explicite : on le montre.
            detail = ""
            try:
                detail = rep.json().get("error", {}).get("message") or rep.text[:300]
            except Exception:
                detail = rep.text[:300]
            raise RuntimeError("HTTP {} -- {}".format(rep.status_code, detail))
        return rep.json()

    bruts, total_api = [], None
    for page in range(MAX_PAGES_RW):
        try:
            charge = appeler(_payload_rw(page * ROWS_RW, seuil_iso))
        except Exception as e:
            print("  (info) API ReliefWeb indisponible (page {}) : {}.".format(page, e))
            break
        if total_api is None:
            try:
                total_api = int(charge.get("totalCount") or charge.get("count") or 0)
            except (TypeError, ValueError):
                total_api = None
        lot = charge.get("data") or []
        if not lot:
            break
        # On aplatit {id, fields:{...}} en un dict simple, id conserve.
        for item in lot:
            champs = dict(item.get("fields") or {})
            champs["_id"] = item.get("id", "")
            bruts.append(champs)
        if len(lot) < ROWS_RW:
            break   # derniere page
        time.sleep(0.3)
    else:
        print("ATTENTION : plafond de {} pages ReliefWeb atteint. Augmenter "
              "MAX_PAGES_RW si ce message revient.".format(MAX_PAGES_RW))
    return bruts, total_api


# ===========================================================================
# PARTIE 3 -- EXTRACTION DE CHAMPS (defensive : formats variables)
# ===========================================================================

def _texte(v):
    """Coerce un champ ReliefWeb (str, dict, liste de dicts) en chaine."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        for cle in ("name", "value", "label", "title"):
            if v.get(cle):
                return _texte(v[cle])
        return ""
    if isinstance(v, list):
        for x in v:
            t = _texte(x)
            if t:
                return t
        return ""
    return str(v)


def _liste_noms(v):
    """Liste de libelles a partir d'un champ multi-valeur (country, career...)."""
    if v is None:
        return []
    if isinstance(v, dict):
        return [_texte(v)] if _texte(v) else []
    if isinstance(v, list):
        out = []
        for x in v:
            t = _texte(x)
            if t:
                out.append(t)
        return out
    t = _texte(v)
    return [t] if t else []


def _premier_pays_a_risque(record):
    """Renvoie (nom_pays, iso3) du PREMIER pays a risque suivi trouve dans le
    champ country (une offre peut lister plusieurs pays). '' si aucun."""
    pays = record.get("country")
    entrees = pays if isinstance(pays, list) else [pays] if pays else []
    for p in entrees:
        if not isinstance(p, dict):
            continue
        iso3 = (_texte(p.get("iso3")) or "").upper()
        if iso3 in CODES_RISQUE:
            return _texte(p.get("name")) or iso3, iso3
    return "", ""


def _date_iso(v):
    """'2026-06-20T09:00:00+00:00' -> '2026-06-20'. Robuste."""
    s = _texte(v)
    return s[:10] if s else ""


# ===========================================================================
# PARTIE 4 -- PRE-FILTRE (equivalent du CPV / cible_amarante)
# ===========================================================================

def pertinent_reliefweb(record):
    """Garde une offre seulement si (1) elle concerne un pays a risque suivi,
    ET (2) elle n'est pas manifestement non deployee (distanciel, stage...),
    l'override terrain sauvant les intitules explicitement terrain/securite.
    But : ne payer le LLM que sur des signaux de deploiement plausibles."""
    _, iso3 = _premier_pays_a_risque(record)
    if not iso3:
        return False
    titre = (_texte(record.get("title")) + " " +
             " ".join(_liste_noms(record.get("career_categories"))) + " " +
             _texte(record.get("type"))).lower()
    # (2a) Override positif terrain/securite -> on garde.
    if any(sig in titre for sig in MOTS_SIGNAL_TERRAIN_RW):
        return True
    # (2b) Exclusion distanciel / non deploye.
    if any(mot in titre for mot in MOTS_EXCLUSION_RW):
        return False
    return True


# ===========================================================================
# PARTIE 5 -- NORMALISATION (vers la forme d'avis commune au coeur TED)
# ===========================================================================

def normaliser_reliefweb(record):
    """Construit l'avis normalise attendu par calculer_scores /
    calculer_fenetre_action + champs propres ReliefWeb pour le Sheet.
    L'ORGANISATION qui recrute est l'acheteur ET le deployeur (cible directe)."""
    nom_pays, iso3 = _premier_pays_a_risque(record)
    org = ""
    src = record.get("source")
    if isinstance(src, list) and src:
        org = _texte(src[0].get("name")) or _texte(src[0].get("shortname"))
    else:
        org = _texte(src)

    description = ted._nettoyer_html(_texte(record.get("body")))
    if len(description) > ted.MAX_CARACTERES_DESCRIPTION:
        description = description[:ted.MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"

    rid = _texte(record.get("_id"))
    lien = _texte(record.get("url")) or _texte(record.get("url_alias")) or \
        (LIEN_RELIEFWEB.format(rid) if rid else "")

    return {
        "publication_number": "RW" + rid if rid else "",
        "titre": _texte(record.get("title"))[:300],
        "acheteur": org or "Organisation humanitaire (non precisee)",
        "pays_acheteur": "",                 # organisation internationale
        "pays_execution": nom_pays,          # NOM lisible (prompt + affichage + Sheet)
        "pays_iso3": iso3,                   # CODE (scoring zone, usage interne)
        "pays_execution_incertitude": False,
        "cpv": "",                           # pas de CPV
        "description": description,
        "deadline": _date_iso(record.get("date", {}).get("closing")
                              if isinstance(record.get("date"), dict) else record.get("date.closing")),
        "date_publication": _date_iso(record.get("date", {}).get("created")
                                      if isinstance(record.get("date"), dict) else record.get("date.created")),
        "valeur_estimee": "inconnu",
        "source_mode_b": False,
        "lien_avis": lien,
        # Champs propres ReliefWeb (Sheet)
        "organisation": org,
        "type_contrat": _texte(record.get("type")),
        "categorie": ", ".join(_liste_noms(record.get("career_categories"))),
        "how_to_apply": ted._nettoyer_html(_texte(record.get("how_to_apply")))[:300],
        "ville": _texte(record.get("city")),
    }


def avis_pour_scoring_rw(avis):
    """Copie ajustee pour calculer_scores UNIQUEMENT : pays_execution devient
    l'ISO3 (multiplicateur de zone). Pas de CPV synthetique : une offre
    d'emploi n'est pas un marche de travaux, on ne force pas de bonus infra."""
    copie = dict(avis)
    copie["pays_execution"] = avis.get("pays_iso3") or avis.get("pays_execution", "")
    return copie


def cible_commerciale_rw(avis, extraction):
    """Qui demarcher. Sur une offre ReliefWeb, l'organisation qui recrute EST
    le deployeur : elle expose directement du personnel sur le terrain, donc
    c'est le contact commercial. Nuance ONU : la securite y est souvent geree
    en interne (marche moins accessible), ce que le score commercial capte
    deja via accessibilite_commerciale."""
    org = avis.get("organisation") or "l'organisation qui recrute"
    type_client = (extraction or {}).get("type_client", "")
    if type_client == "institution_ue_onu":
        return ("{} (deployeur direct). Securite souvent geree en interne cote "
                "ONU : viser la direction surete/logistique, marche parfois "
                "verrouille.".format(org))
    return ("{} recrute et deploie directement ce personnel : contact commercial "
            "direct (direction surete, logistique, RH terrain).".format(org))


# ===========================================================================
# PARTIE 6 -- PROMPT LLM (adapte a une offre d'emploi, meme SCHEMA que TED)
# ===========================================================================
# Le schema de sortie est IDENTIQUE a celui du prompt TED, pour que
# ted.calculer_scores / calculer_action_recommandee fonctionnent SANS
# modification. Seul le cadrage change : ici on lit une offre d'emploi, pas
# un marche public.
PROMPT_RELIEFWEB = """Tu es analyste sûreté pour une société française de protection de personnes en zones à risque (escorte, protection rapprochée CPO/CPD, chauffeur sécurité, véhicule sécurisé, sécurisation de déplacements terrain). Elle ne vend PAS de conseil voyage générique ni de simple briefing.

On te donne une OFFRE D'EMPLOI humanitaire/développement publiée sur ReliefWeb pour un pays à risque. L'organisation qui recrute déploie ce personnel sur place. Détermine si ce poste implique une présence PHYSIQUE et RÉGULIÈRE sur le terrain à l'étranger, créant un besoin probable de prestations opérationnelles de sûreté.

RÈGLE SUR LE PROFIL DÉPLOYÉ : un poste international/expatrié (chef de mission, coordinateur pays, expert international, logisticien mobile) expose davantage qu'un poste 100% national basé en capitale au bureau. Un poste "national staff" en exécution locale a un profil de risque plus faible pour Amarante.

RÈGLE SUR LA MOBILITÉ TERRAIN (distincte de la durée) : classe le profil de mobilité dans UNE seule catégorie (la plus représentative) :
- aucune : travail documentaire ou 100% à distance.
- capitale : présence en capitale/grand centre urbain, déplacements limités.
- multi_sites : déplacements entre plusieurs sites/provinces.
- chantier : présence régulière sur une installation/base opérationnelle.
- terrain_isole : zones rurales/isolées, accès difficile.
- frontiere : zone frontalière ou de tension active.

RÈGLE SUR LA SÉCURITÉ DÉJÀ EN PLACE : si l'offre indique qu'un dispositif de sûreté/escorte existe déjà (poste de "security officer" interne, dispositif UNDSS, prestataire déjà en place), l'opportunité pour Amarante sur ce signal précis est réduite.

RÈGLE SUR LE TYPE DE CLIENT : agence ONU (UNDSS gère souvent la sûreté en interne -> marché moins accessible), ONG internationale, bailleur, ou entreprise privée. Une ONG internationale ou un acteur privé est souvent plus accessible commercialement qu'une agence ONU.

RÈGLE SUR L'ACCESSIBILITÉ COMMERCIALE (distincte du besoin sûreté) : besoin élevé n'égale pas marché ouvert. UNDSS/dispositif interne -> difficile. ONG/privé sur déploiement classique -> plus facile.

RÈGLE SUR LES PROFILS D'ACTEURS : ne cite JAMAIS de nom d'entreprise réelle, décris des PROFILS de type d'acteur.

Le texte peut être dans n'importe quelle langue. Raisonne en anglais, cite les indices dans leur langue.

Réponds UNIQUEMENT en JSON valide, sans texte ni Markdown autour, et SANS commentaire entre parenthèses dans les valeurs.

Schéma de sortie :
{{
  "deploiement_terrain_reel": true | false,
  "type_mobilite": "aucune | capitale | multi_sites | chantier | terrain_isole | frontiere",
  "profil_personnes_exposees": "expert_international | executive | technicien | ouvrier_local | aucun",
  "securite_existante_detectee": true | false,
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

Offre à analyser :
Organisation qui recrute : {acheteur}
Pays de déploiement : {pays_execution}
Intitulé du poste : {titre}
Type / catégorie : {categorie}
Description (peut être tronquée) : {description}
"""


def analyser_reliefweb(avis, modele=None):
    """Extraction LLM d'une offre ReliefWeb. Meme echelle de recuperation JSON
    que ted.appeler_llm (parse direct -> sous-chaine {..} -> reparation Sonnet),
    mais avec le prompt propre a ReliefWeb. Reutilise ted.appeler_modele et
    ted.reparer_json (aucune duplication de la logique reseau ni du parseur)."""
    prompt = PROMPT_RELIEFWEB.format(
        acheteur=avis.get("acheteur", ""),
        pays_execution=avis.get("pays_execution", ""),
        titre=avis.get("titre", ""),
        categorie=avis.get("categorie", "") or "(non precise)",
        description=avis.get("description", "") or "(non fournie par l'offre)",
    )
    texte = ted.appeler_modele(prompt, modele=modele)
    if texte is None:
        return None
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        pass
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin != -1 and fin > debut:
        try:
            return json.loads(texte[debut:fin + 1])
        except json.JSONDecodeError:
            pass
    repare = ted.reparer_json(texte, modele=ted.MODELE_RAFFINEMENT)
    if repare is None:
        return None
    try:
        return json.loads(repare)
    except json.JSONDecodeError:
        return None


# ===========================================================================
# PARTIE 7 -- SORTIE GOOGLE SHEET (onglet reliefweb_radar)
# ===========================================================================
# Schema aligne sur la logique du dashboard (ligne_vers_lead) : mets d'abord
# les colonnes communes lues par le dashboard, puis les colonnes propres.
COLONNES_RW = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance",
    "modele", "raffine", "divergence",
    "organisation", "type_contrat", "categorie", "ville", "how_to_apply",
    "publication_number", "lien_avis", "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_RW = COLONNES_RW + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille_rw(sheet_id, fichier_compte_service):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_compte_service, scopes=portee)
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        feuille = classeur.worksheet(NOM_ONGLET_RW)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET_RW, rows=2000, cols=len(TOUTES_COLONNES_RW))
        feuille.append_row(TOUTES_COLONNES_RW)
        return feuille
    entetes = feuille.row_values(1)
    if COLONNE_DATE_DETECTION not in entetes:
        feuille.update(values=[TOUTES_COLONNES_RW], range_name="A1")
    return feuille


def ligne_depuis_resultat_rw(r):
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
        "type_client": extraction.get("type_client") if extraction else "",
        "type_mobilite": extraction.get("type_mobilite") if extraction else "",
        "profil_personnes_exposees": extraction.get("profil_personnes_exposees") if extraction else "",
        "duree_estimee": extraction.get("duree_estimee") if extraction else "",
        "accessibilite_commerciale": extraction.get("accessibilite_commerciale") if extraction else "",
        "securite_existante_detectee": extraction.get("securite_existante_detectee") if extraction else "",
        "profils_acteurs_probables": ", ".join(extraction.get("profils_acteurs_probables") or []) if extraction else "",
        "cible_commerciale_reelle": cible_commerciale_rw(avis, extraction),
        "justification": extraction.get("justification") if extraction else "",
        "confiance": extraction.get("confiance") if extraction else "",
        "modele": modele_utilise,
        "raffine": r["raffine"],
        "divergence": r["divergence"],
        "organisation": avis.get("organisation", ""),
        "type_contrat": avis.get("type_contrat", ""),
        "categorie": avis.get("categorie", ""),
        "ville": avis.get("ville", ""),
        "how_to_apply": avis.get("how_to_apply", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(valeurs.get(c, "")) for c in COLONNES_RW]


def ecrire_resultats_rw(feuille, resultats):
    """Ecriture groupee (2 appels max). statut_suivi et date_detection (zone
    preservee) jamais ecrases sur un re-run. Reutilise l'indexation par
    publication_number du coeur TED."""
    index = ted.charger_index_publication(feuille)
    derniere_lettre = ted.lettre_colonne(len(COLONNES_RW))
    maj_groupees, nouvelles_lignes = [], []
    nb_nouveaux, nb_maj = 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne_valeurs = ligne_depuis_resultat_rw(r)
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
    # Double ecriture (etape 2 du cap produit, 21/07/2026) : miroir Postgres
    # best-effort. On passe TOUS les resultats (le miroir a sa propre memoire,
    # ON CONFLICT DO NOTHING : remplissage retroactif inclus). Ne peut JAMAIS
    # faire echouer le run. NB : en phase de double ecriture, le Sheet reste
    # la reference ; les mises a jour de scores ne touchent que le Sheet.
    try:
        import radar_stockage
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET_RW, resultats))
    except Exception as e:                     # module absent : run intact
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_nouveaux, nb_maj


# ===========================================================================
# PARTIE 8 -- POINT D'ENTREE
# ===========================================================================

def main():
    if not ACTIVER_RELIEFWEB:
        print("(info) Collecteur ReliefWeb desactive (RADAR_RELIEFWEB=0).")
        return
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("RELIEFWEB_DRY_RUN"):
        print("ERREUR : ANTHROPIC_API_KEY n'est pas definie. Definis-la avant "
              "de lancer, ou utilise RELIEFWEB_DRY_RUN=1 pour regler sans cout.")
        return

    print("Etape 1/2 -- Collecte ReliefWeb (jobs, pays a risque, fenetre {} j)...".format(
        NB_JOURS_FENETRE_RW))
    bruts, total_api = collecte_reliefweb()

    if not bruts:
        print("\n/!\\ ALERTE : l'API ReliefWeb a renvoye 0 offre (total annonce = {}). "
              "Causes possibles : rien de nouveau, ou changement d'API. "
              "Verifier {} manuellement avant de conclure.".format(total_api, RELIEFWEB_ENDPOINT))
        return

    cibles = [r for r in bruts if pertinent_reliefweb(r)]
    # Dedup par id.
    vus, uniques = set(), []
    for r in cibles:
        rid = _texte(r.get("_id"))
        if rid and rid not in vus:
            vus.add(rid)
            uniques.append(r)

    # Tri par niveau de risque pays decroissant : si le plafond LLM est atteint,
    # on garde les zones les plus exposees.
    def _tier(r):
        _, iso3 = _premier_pays_a_risque(r)
        return ted.MULTIPLICATEUR_ZONE.get(iso3, 0.2)
    uniques.sort(key=_tier, reverse=True)
    plafonne = len(uniques) > MAX_AVIS_LLM_RW
    if plafonne:
        uniques = uniques[:MAX_AVIS_LLM_RW]

    avis_normalises = [normaliser_reliefweb(r) for r in uniques]
    print("ReliefWeb -- Bruts : {} | cibles (risque + deploiement plausible) : {}".format(
        len(bruts), len(avis_normalises)))
    if plafonne:
        print("    (plafond de {} atteint : seules les zones les plus a risque "
              "sont analysees ce run.)".format(MAX_AVIS_LLM_RW))

    if not avis_normalises:
        print("Aucune offre ReliefWeb a analyser.")
        return

    # Memoire inter-runs (tolerante : pas de Sheet -> on analyse tout).
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    deja_vus = ted.numeros_publication_existants(
        sheet_id, fichier, NOM_ONGLET_RW, COLONNES_RW)
    if deja_vus:
        avant = len(avis_normalises)
        avis_normalises = [a for a in avis_normalises
                           if str(a.get("publication_number", "")).strip() not in deja_vus]
        print("Memoire : {} offre(s) deja analysee(s) ignoree(s), {} nouvelle(s).".format(
            avant - len(avis_normalises), len(avis_normalises)))
    if not avis_normalises:
        print("Aucune NOUVELLE offre ReliefWeb a analyser (tout deja vu).")
        return

    # Mode DRY-RUN : entonnoir sans aucun appel paye.
    if os.environ.get("RELIEFWEB_DRY_RUN"):
        print("\n=== MODE DRY-RUN : aucun appel LLM, aucun cout ===")
        for i, a in enumerate(avis_normalises, start=1):
            tier = ted.MULTIPLICATEUR_ZONE.get(a.get("pays_iso3", ""), 0.2)
            print("  {:3}. [risque {}] {} | {} ({})".format(
                i, tier, a["titre"][:60], a.get("organisation", "")[:30],
                a.get("pays_execution", "")))
        print("\nRetire RELIEFWEB_DRY_RUN pour lancer l'analyse reelle.")
        return

    print("\nEtape 2/2 -- Extraction LLM et score ({} offres, modele {})...\n".format(
        len(avis_normalises), ted.MODELE))

    resultats = []
    for i, avis in enumerate(avis_normalises, start=1):
        print("[{}/{}] {}...".format(i, len(avis_normalises), avis["titre"][:60]))
        extraction = analyser_reliefweb(avis)
        s, c, f = ted.calculer_scores(avis_pour_scoring_rw(avis), extraction)
        resultats.append({
            "avis": avis, "extraction": extraction,
            "surete_haiku": s, "commercial_haiku": c, "final_haiku": f,
            "surete": s, "commercial": c, "score": f,
            "raffine": False, "divergence": False,
        })
        time.sleep(0.5)

    # Escalade Sonnet, memes criteres que TED/BM.
    def merite_escalade(r):
        if r["extraction"] is None:
            return False
        if r["final_haiku"] >= 5:
            return True
        if r["extraction"].get("confiance", 1.0) < 0.7:
            return True
        if r["extraction"].get("securite_existante_detectee"):
            return True
        return False

    a_escalader = [r for r in resultats if merite_escalade(r)]
    if a_escalader:
        print("\n{} offre(s) escaladee(s) vers {}...\n".format(len(a_escalader), ted.MODELE_RAFFINEMENT))
        for i, r in enumerate(a_escalader, start=1):
            print("[{}/{}] Raffinement : {}...".format(i, len(a_escalader), r["avis"]["titre"][:60]))
            raffinee = analyser_reliefweb(r["avis"], modele=ted.MODELE_RAFFINEMENT)
            if raffinee is not None:
                s, c, f = ted.calculer_scores(avis_pour_scoring_rw(r["avis"]), raffinee)
                r["extraction"] = raffinee
                r["surete"], r["commercial"], r["score"] = s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0
            time.sleep(0.5)

    resultats.sort(key=lambda r: r["score"], reverse=True)

    if sheet_id and fichier:
        print("\nEcriture dans l'onglet '{}' ({} offres)...".format(NOM_ONGLET_RW, len(resultats)))
        try:
            feuille = ouvrir_feuille_rw(sheet_id, fichier)
            nb_nouveaux, nb_maj = ecrire_resultats_rw(feuille, resultats)
            print("-> {} nouvelle(s) offre(s), {} mise(s) a jour (statut_suivi jamais touche).".format(
                nb_nouveaux, nb_maj))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("\n(Pas de Sheet configure : definis TED_SHEET_ID et "
              "GOOGLE_SERVICE_ACCOUNT_FILE pour activer l'ecriture.)")

    # Affichage console
    print("\n" + "=" * 70)
    print("RESULTATS RELIEFWEB (score = surete x0.5 + commercial x0.5)")
    nb_fort = sum(1 for r in resultats if r["score"] >= ted.SEUIL_ALERTE)
    nb_surv = sum(1 for r in resultats
                  if ted.SEUIL_SURVEILLANCE <= r["score"] < ted.SEUIL_ALERTE)
    print("Bilan : {} FORT(S) | {} a surveiller | {} faible(s)".format(
        nb_fort, nb_surv, len(resultats) - nb_fort - nb_surv))
    print("=" * 70)
    for r in resultats:
        score, avis, extraction = r["score"], r["avis"], r["extraction"]
        etiquette = ("[FORT]" if score >= ted.SEUIL_ALERTE
                     else "[A SURVEILLER]" if score >= ted.SEUIL_SURVEILLANCE else "[faible]")
        suffixe = ""
        if r["raffine"]:
            suffixe = " (relu par {} ; Haiku avait {:.1f})".format(ted.MODELE_RAFFINEMENT, r["final_haiku"])
            if r["divergence"]:
                suffixe += "  /!\\ ECART NOTABLE"
        print("\n{} Score {:.1f}/10 (surete {:.1f} | commercial {:.1f}){}".format(
            etiquette, score, r["surete"], r["commercial"], suffixe))
        print("  {}".format(avis["titre"][:90]))
        print("  Organisation : {} | Pays : {} | Ville : {}".format(
            avis["acheteur"], avis["pays_execution"], avis.get("ville") or "n.c."))
        if extraction:
            print("  Mobilite : {} | Personnes exposees : {} | Accessibilite : {}".format(
                extraction.get("type_mobilite"), extraction.get("profil_personnes_exposees"),
                extraction.get("accessibilite_commerciale")))
            print("  Qui demarcher : {}".format(cible_commerciale_rw(avis, extraction)))
            print("  Justification : {}".format(extraction.get("justification")))
        print("  Candidater/contact : {}".format(avis.get("how_to_apply") or "voir l'offre"))
        print("  Lien : {}".format(avis["lien_avis"]))


if __name__ == "__main__":
    main()
