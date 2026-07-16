# -*- coding: utf-8 -*-
"""
Radar Amarante - Moteur de signaux PRIVES (Phase 2, pilote BITD).

Pour chaque entreprise d'une whitelist (onglet 'comptes_cibles_bitd'), interroge
Google News RSS, fait extraire par Haiku les signaux de DEPLOIEMENT a l'etranger
(contrat export, implantation, essais, formation/MCO, recrutement local, incident),
croise le pays avec la carte de risque du radar, score, et ecrit les opportunites
dans l'onglet 'prive_radar' (source PRIVE du dashboard).

Fiabilite / faible bruit :
- classifieur a temperature 0 ;
- pre-filtre anti-bruit (titres manifestement hors sujet) AVANT tout appel LLM ;
- filtre de fraicheur (articles recents seulement) ;
- double lecture Haiku -> Sonnet sur les seuls signaux a fort enjeu ;
- seuil de confiance ;
- deduplication par EVENEMENT (entreprise+pays+activite), pas par article ;
- normalisation du pays (code ISO3 rattrape via la carte) ;
- memoire inter-runs (un article n'est jamais retraite).

Tolerant aux pannes : tout flux/appel en echec est ignore, jamais bloquant.
Reutilise la plomberie du radar (ted_complet_v14). Aucune dependance nouvelle.
"""

import os
import re
import json
import time
import hashlib
import datetime
import email.utils
import urllib.parse
import xml.etree.ElementTree as ET

import ted_complet_v14 as ted

try:
    import radar_dashboard as _dash
    ZONE_PAR_ISO3 = _dash.ZONE_PAR_ISO3
except Exception:
    ZONE_PAR_ISO3 = {}

# ===========================================================================
# CARTE NOM -> ISO3 (pour rattraper un code pays errone renvoye par le LLM)
# ===========================================================================
def _construire_nom_vers_iso3():
    table = {}
    for d in (getattr(ted, "PAYS_ROUGE", {}), getattr(ted, "PAYS_ORANGE", {}),
              getattr(ted, "AFRIQUE", {}), getattr(ted, "MOYEN_ORIENT", {}),
              getattr(ted, "AMERIQUE_DU_SUD", {}),
              getattr(ted, "EUROPE_EST_CAUCASE_ASIE_CENTRALE", {}),
              getattr(ted, "ASIE_A_RISQUE", {}), getattr(ted, "ILES_A_RISQUE", {}),
              getattr(ted, "TERRITOIRES_FRANCAIS_OUTRE_MER_A_RISQUE", {})):
        for nom, iso in d.items():
            table[nom.strip().lower()] = iso
    # noms FR canoniques depuis la carte de zone
    for iso, paire in ZONE_PAR_ISO3.items():
        if isinstance(paire, (tuple, list)) and paire:
            table.setdefault(paire[0].strip().lower(), iso)
    return table

NOM_VERS_ISO3 = _construire_nom_vers_iso3()

# ===========================================================================
# CONFIGURATION
# ===========================================================================
NOM_ONGLET_WHITELIST = "comptes_cibles_bitd"
NOM_ONGLET_PRIVE = "prive_radar"

GNEWS_BASE = "https://news.google.com/rss/search"
DECLENCHEURS = ("contrat OR export OR implantation OR usine OR filiale OR livraison "
                "OR chantier OR essais OR démonstration OR formation OR déploiement")
MAX_ARTICLES_PAR_ENTREPRISE = 6
PAUSE_ENTRE_REQUETES = 1.0
JOURS_FRAICHEUR = 120                 # on ignore les articles plus vieux
# Budget d'analyses LLM par run : garantit qu'un run finit toujours dans le
# temps imparti (le backlog s'etale sur plusieurs runs grace a la memoire).
MAX_ANALYSES_PAR_RUN = int(os.environ.get("RADAR_BITD_BUDGET", "200"))
# Levier de temps PRINCIPAL : nb d'entreprises traitees par run. Chaque
# entreprise = une collecte reseau (Google News), c'est ca qui prend du
# temps, pas les analyses. On borne donc au niveau entreprise, avec un curseur
# de reprise (rotation sur toute la whitelist en quelques runs).
MAX_ENTREPRISES_PAR_RUN = int(os.environ.get("RADAR_BITD_MAX_ENTREPRISES", "15"))
NOM_ONGLET_ETAT = "radar_etat"
# Memoire de TOUS les articles analyses (pas seulement des signaux retenus) :
# evite de re-analyser les non-signaux a chaque run (cause du timeout).
NOM_ONGLET_VUS = "prive_vus"
MAX_VUS_MEMOIRE = 6000

SEUIL_CONTACTER = 6.0
SEUIL_SURVEILLER = 4.0
SEUIL_CONFIANCE_MIN = 0.45            # sous ce niveau, signal ecarte
BANDE_ESCALADE = (0.45, 0.72)         # confiance dans cette bande -> Sonnet verifie

# Intensite de presence humaine sur le terrain (coeur du metier escorte).
POIDS_ACTIVITE = {
    "formation_mco": 1.0,            # experts residents/recurrents
    "incident": 1.0,                 # besoin reactif immediat
    "implantation": 0.9,             # presence durable
    "essais_demonstration": 0.8,
    "livraison_mise_en_service": 0.6,
    "recrutement_local": 0.6,
    "contrat_export": 0.4,           # signal amont, presence a venir (revu a la baisse)
    "autre": 0.25,
}
POIDS_IMMINENCE = {"immediate": 1.0, "court_terme": 0.85, "indetermine": 0.7}

# Pre-filtre anti-bruit : titres manifestement hors sujet (evite un appel LLM).
MOTIFS_BRUIT = re.compile(
    r"résultats? (annuels|semestriels|trimestriels|financiers)|chiffre d'affaires"
    r"|dividende|cours de (l'action|bourse)|\ben bourse\b|assemblée générale"
    r"|nomm[ée]e?\b|nomination|obsèques|décès|rétrospective|carnet du jour",
    re.IGNORECASE)

# Schema de l'onglet prive_radar (compatible dashboard).
COLONNES_PRIVE = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "titre", "acheteur",
    "pays_execution", "zone", "type_activite", "confiance", "modele",
    "cible_commerciale_reelle", "justification", "entreprise",
    "priorite_compte", "publication_number", "lien_avis", "date_publication",
]
COLONNE_STATUT = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_PRIVE = COLONNES_PRIVE + [COLONNE_STATUT, COLONNE_DATE_DETECTION]


# ===========================================================================
# LECTURE DE LA WHITELIST
# ===========================================================================
def lire_whitelist(sheet_id, fichier_cs):
    if not (sheet_id and fichier_cs):
        return []
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
        classeur = gspread.authorize(creds).open_by_key(sheet_id)
        valeurs = classeur.worksheet(NOM_ONGLET_WHITELIST).get_all_values()
    except Exception as e:
        print("  (info) Whitelist illisible ({}). Moteur prive inactif.".format(e))
        return []
    return _whitelist_depuis_valeurs(valeurs)


def _whitelist_depuis_valeurs(valeurs):
    if not valeurs or len(valeurs) < 2:
        return []
    entetes = [str(c).strip() for c in valeurs[0]]
    lignes = []
    for row in valeurs[1:]:
        if not any(str(c).strip() for c in row):
            continue
        d = {entetes[i]: (row[i] if i < len(row) else "") for i in range(len(entetes))}
        if d.get("entreprise", "").strip():
            lignes.append(d)
    return lignes


# ===========================================================================
# COLLECTE GOOGLE NEWS RSS
# ===========================================================================
def url_google_news(entreprise, requete_perso="", hl="fr", gl="FR", ceid="FR:fr"):
    """URL du flux Google News RSS. hl/gl/ceid definissent la LOCALE
    interrogee : defaut FR (retro-compatible), mais surchargeable pour aller
    chercher la presse internationale (hl='en', gl='US', ceid='US:en'), la ou
    vivent les annonces des majors etrangers de la watchlist."""
    requete = requete_perso.strip() or '"{}" ({})'.format(entreprise, DECLENCHEURS)
    params = {"q": requete, "hl": hl, "gl": gl, "ceid": ceid}
    return GNEWS_BASE + "?" + urllib.parse.urlencode(params)


def parser_rss(xml_texte):
    articles = []
    try:
        racine = ET.fromstring(xml_texte)
    except ET.ParseError:
        return articles
    for item in racine.iter("item"):
        titre = (item.findtext("title") or "").strip()
        lien = (item.findtext("link") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        resume = _nettoyer(item.findtext("description") or "")
        if titre and lien:
            articles.append({"titre": titre, "lien": lien,
                             "date": date, "resume": resume})
    return articles


def _nettoyer(html):
    return re.sub(r"<[^>]+>", " ", html or "").replace("&nbsp;", " ").strip()


def article_frais(article, aujourd=None):
    """False si l'article est plus vieux que JOURS_FRAICHEUR. Date illisible -> frais."""
    brut = article.get("date", "")
    if not brut:
        return True
    try:
        dt = email.utils.parsedate_to_datetime(brut)
        if dt is None:
            return True
        ref = aujourd or datetime.datetime.now(dt.tzinfo)
        return (ref - dt).days <= JOURS_FRAICHEUR
    except Exception:
        return True


def bruit_evident(article):
    """True si le titre/extrait est manifestement hors sujet (pre-filtre)."""
    texte = (article.get("titre", "") + " " + article.get("resume", ""))
    return bool(MOTIFS_BRUIT.search(texte))


def id_article(lien):
    return hashlib.sha1(lien.encode("utf-8", "ignore")).hexdigest()[:16]


# ===========================================================================
# EXTRACTION / VERIFICATION LLM
# ===========================================================================
PROMPT_SIGNAL = """Tu analyses une actualité concernant une entreprise française de défense (BITD) pour une société de protection de personnes. Objectif : repérer si l'actualité indique que cette entreprise VA DÉPLOYER ou DÉPLOIE DÉJÀ des personnels (cadres, techniciens, formateurs, équipes projet) À L'ÉTRANGER, ce qui crée un besoin de sûreté (escorte, protection, chauffeur sécurité).

Entreprise : {entreprise}
Titre : {titre}
Extrait : {resume}

Signal PERTINENT : contrat d'export signé, implantation (usine/filiale/bureau) à l'étranger, essais ou démonstration terrain, mission de formation ou de MCO sur site client, recrutement de personnel en pays étranger, incident sécuritaire touchant l'entreprise ou ses équipes.
PAS un signal : résultats financiers, nominations, produits sans déploiement, actualité franco-française, rumeur vague.

Sois STRICT : dans le doute, signal=false. La confiance reflète ta certitude que c'est un vrai déploiement à l'étranger.

Réponds UNIQUEMENT par un objet JSON strict, sans texte autour :
{{"signal": true/false, "iso3": "code ISO3 du pays de déploiement ou vide", "pays": "nom du pays ou vide", "type_activite": "formation_mco|implantation|essais_demonstration|livraison_mise_en_service|recrutement_local|incident|contrat_export|autre", "imminence": "immediate|court_terme|indetermine", "confiance": 0.0, "resume": "une phrase factuelle"}}"""

PROMPT_VERIFICATION = """Vérification rigoureuse d'un signal commercial pour une société de protection de personnes en zones à risque. Un premier tri a jugé cet article comme un possible signal de déploiement de personnels français à l'étranger. Confirme ou infirme.

Entreprise : {entreprise}
Titre : {titre}
Extrait : {resume}

Confirme (confirme=true) UNIQUEMENT si l'article établit de façon crédible un déploiement réel ou imminent de personnels de cette entreprise dans un pays étranger identifiable. Sinon confirme=false. Corrige le pays et le type d'activité si le premier tri s'est trompé.

Réponds UNIQUEMENT par un objet JSON strict :
{{"confirme": true/false, "iso3": "ISO3 ou vide", "pays": "nom ou vide", "type_activite": "formation_mco|implantation|essais_demonstration|livraison_mise_en_service|recrutement_local|incident|contrat_export|autre", "imminence": "immediate|court_terme|indetermine", "confiance": 0.0, "resume": "une phrase factuelle"}}"""


def _appel_llm(prompt, modele=None, temperature=0.0):
    cle = os.environ.get("ANTHROPIC_API_KEY", "")
    if not cle:
        raise RuntimeError("ANTHROPIC_API_KEY absente")
    session = ted.session_robuste()
    entetes = {"x-api-key": cle, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    corps = {"model": modele or ted.MODELE, "max_tokens": 400,
             "temperature": temperature,
             "messages": [{"role": "user", "content": prompt}]}
    rep = session.post(ted.ANTHROPIC_ENDPOINT, headers=entetes, json=corps, timeout=40)
    rep.raise_for_status()
    return rep.json()["content"][0]["text"]


def analyser_signal_llm(entreprise, article, appel=None):
    """Tri Haiku. `appel` injectable pour tests (callable(prompt)->texte)."""
    prompt = PROMPT_SIGNAL.format(
        entreprise=entreprise, titre=article.get("titre", ""),
        resume=(article.get("resume") or article.get("titre", ""))[:800])
    try:
        return _parser_json((appel or (lambda p: _appel_llm(p, ted.MODELE)))(prompt))
    except Exception as e:
        print("  (info) Analyse LLM échouée ({}).".format(e))
        return None


def verifier_signal_sonnet(entreprise, article, appel=None):
    """Verification Sonnet (escalade sur signaux a fort enjeu)."""
    prompt = PROMPT_VERIFICATION.format(
        entreprise=entreprise, titre=article.get("titre", ""),
        resume=(article.get("resume") or article.get("titre", ""))[:800])
    try:
        return _parser_json(
            (appel or (lambda p: _appel_llm(p, ted.MODELE_RAFFINEMENT)))(prompt))
    except Exception as e:
        print("  (info) Vérification LLM échouée ({}).".format(e))
        return None


def _parser_json(texte):
    if not texte:
        return None
    m = re.search(r"\{.*\}", texte, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def normaliser_iso3(extraction):
    """Rattrape un ISO3 errone via le nom de pays. Renvoie l'ISO3 (ou '')."""
    iso3 = (extraction.get("iso3") or "").strip().upper()
    if iso3 in ted.CODES_PAYS_SUIVIS:
        return iso3
    nom = (extraction.get("pays") or "").strip().lower()
    if nom in NOM_VERS_ISO3:
        return NOM_VERS_ISO3[nom]
    return iso3  # peut ne pas etre a risque -> filtre plus loin


# ===========================================================================
# SCORING
# ===========================================================================
def clef_evenement(entreprise, nom_pays, type_activite):
    return "|".join([str(entreprise).strip().lower(),
                     str(nom_pays).strip().lower(),
                     str(type_activite).strip().lower()])


# ===========================================================================
# ECRITURE
# ===========================================================================
def ligne_prive(entreprise_row, article, extraction, sc, modele):
    aujourd = datetime.date.today().isoformat()
    valeurs = {
        "date_maj": aujourd, "score_final": sc["final"], "score_surete": sc["surete"],
        "score_commercial": sc["commercial"], "action_recommandee": sc["action"],
        "fenetre_action": extraction.get("imminence") or "indetermine",
        "titre": extraction.get("resume") or article.get("titre", ""),
        "acheteur": entreprise_row.get("entreprise", ""),
        "pays_execution": sc["nom"] or extraction.get("pays", ""), "zone": sc["zone"],
        "type_activite": extraction.get("type_activite", ""),
        "confiance": round(float(extraction.get("confiance") or 0), 2), "modele": modele,
        "cible_commerciale_reelle": entreprise_row.get("angle_contact", ""),
        "justification": extraction.get("resume", ""),
        "entreprise": entreprise_row.get("entreprise", ""),
        "priorite_compte": entreprise_row.get("priorite_socle", ""),
        "publication_number": id_article(article.get("lien", "")),
        "lien_avis": article.get("lien", ""), "date_publication": article.get("date", ""),
    }
    return [str(valeurs.get(c, "")) for c in COLONNES_PRIVE]


def cles_evenements_existantes(sheet_id, fichier_cs):
    """Cles d'evenements deja presents dans prive_radar (dedup inter-runs)."""
    if not (sheet_id and fichier_cs):
        return set()
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
        classeur = gspread.authorize(creds).open_by_key(sheet_id)
        valeurs = classeur.worksheet(NOM_ONGLET_PRIVE).get_all_values()
    except Exception:
        return set()
    return _cles_depuis_valeurs(valeurs)


def _cles_depuis_valeurs(valeurs):
    if not valeurs:
        return set()
    i_ent = COLONNES_PRIVE.index("entreprise")
    i_pays = COLONNES_PRIVE.index("pays_execution")
    i_act = COLONNES_PRIVE.index("type_activite")
    debut = 1 if valeurs and "entreprise" in [str(c).strip() for c in valeurs[0]] else 0
    cles = set()
    for row in valeurs[debut:]:
        if max(i_ent, i_pays, i_act) < len(row):
            cles.add(clef_evenement(row[i_ent], row[i_pays], row[i_act]))
    return cles


def ouvrir_ou_creer_onglet(classeur):
    import gspread
    try:
        return classeur.worksheet(NOM_ONGLET_PRIVE)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET_PRIVE, rows=2000,
                                   cols=len(TOUTES_COLONNES_PRIVE))
        f.update([TOUTES_COLONNES_PRIVE])
        return f


def ecrire_resultats(feuille, resultats):
    if not resultats:
        return 0
    aujourd = datetime.date.today().isoformat()
    lignes = [r["ligne"] + ["nouveau", aujourd] for r in resultats]
    feuille.append_rows(lignes, value_input_option="RAW")
    return len(lignes)


# ===========================================================================
# ORCHESTRATION
# ===========================================================================
def charger_vus(sheet_id, fichier_cs):
    """Set des hash d'articles deja analyses (memoire persistante)."""
    if not (sheet_id and fichier_cs):
        return set()
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
        classeur = gspread.authorize(creds).open_by_key(sheet_id)
        valeurs = classeur.worksheet(NOM_ONGLET_VUS).get_all_values()
    except Exception:
        return set()
    vus = set()
    for row in valeurs:
        if row and str(row[0]).strip() and str(row[0]).strip() != "article_hash":
            vus.add(str(row[0]).strip())
    return vus


def persister_vus(classeur, nouveaux):
    """Ajoute les nouveaux hash a l'onglet prive_vus, plafonne la taille."""
    import gspread
    if not nouveaux:
        return
    try:
        feuille = classeur.worksheet(NOM_ONGLET_VUS)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(title=NOM_ONGLET_VUS, rows=MAX_VUS_MEMOIRE + 100, cols=2)
        feuille.update([["article_hash", "date_vu"]])
    aujourd = datetime.date.today().isoformat()
    feuille.append_rows([[h, aujourd] for h in nouveaux], value_input_option="RAW")
    # Plafonnement : on ne garde que les MAX_VUS_MEMOIRE plus recents.
    valeurs = feuille.get_all_values()
    corps = [r for r in valeurs if r and r[0] != "article_hash"]
    if len(corps) > MAX_VUS_MEMOIRE:
        recents = corps[-MAX_VUS_MEMOIRE:]
        feuille.clear()
        feuille.update([["article_hash", "date_vu"]] + recents)


def lire_curseur(sheet_id, fichier_cs):
    """Index de l'entreprise a traiter en premier ce run (rotation)."""
    if not (sheet_id and fichier_cs):
        return 0
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
        classeur = gspread.authorize(creds).open_by_key(sheet_id)
        for row in classeur.worksheet(NOM_ONGLET_ETAT).get_all_values():
            if row and str(row[0]).strip() == "bitd_curseur":
                return int(str(row[1]).strip())
    except Exception:
        return 0
    return 0


def ecrire_curseur(classeur, valeur):
    """Enregistre le curseur (upsert dans l'onglet radar_etat)."""
    import gspread
    try:
        feuille = classeur.worksheet(NOM_ONGLET_ETAT)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(title=NOM_ONGLET_ETAT, rows=50, cols=2)
        feuille.update([["cle", "valeur"]])
    valeurs = feuille.get_all_values()
    for i, row in enumerate(valeurs, start=1):
        if row and str(row[0]).strip() == "bitd_curseur":
            feuille.update_cell(i, 2, str(valeur))
            return
    feuille.append_row(["bitd_curseur", str(valeur)], value_input_option="RAW")
