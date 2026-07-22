# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur BANQUE MONDIALE
============================================

Deuxieme source du radar, apres TED. Reutilise INTEGRALEMENT le coeur de
ted_complet_v14.py (prompt, extraction LLM, scoring deterministe, garde-fou
V9, escalade Sonnet, resilience reseau, nettoyage HTML) en l'important, sans
le dupliquer ni le modifier. Seul le COLLECTEUR (appel API + normalisation)
et le SCHEMA Sheet sont specifiques a la Banque Mondiale.

DECOUVERTES validees en interrogeant l'API en direct (search.worldbank.org
/api/v2/procnotices), pas devinees :
  - PAS de code CPV (nomenclature europeenne). Le filtrage de pertinence se
    fait sur notice_type + procurement_group (CS/CW), pas sur CPV.
  - Les CONTACTS sont exposes nativement (nom, organisation, email, tel) :
    captures dans le Sheet, c'est le "qui appeler" que les autres sources
    n'avaient pas.
  - Description tres riche (notice_text = texte complet de l'avis), nettoyee
    du HTML avant injection au modele.

Pre-requis : ted_complet_v14.py doit etre dans le MEME dossier (import).
Ecrit dans un onglet SEPARE "bm_radar" du meme Google Sheet que TED.
"""

import os
import re
import time
from datetime import date, datetime, timedelta

import requests

# --- Reutilisation du coeur TED (aucune modification de ce fichier) ---------
try:
    import ted_complet_v14 as ted
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce "
        "script (il en importe le coeur). Sur Drive, les deux fichiers vont "
        "dans /content/drive/MyDrive/Radar/, puis on lance ce script depuis "
        "ce dossier."
    )


# ===========================================================================
# PARTIE 1 -- CONFIGURATION SPECIFIQUE BANQUE MONDIALE
# ===========================================================================

BM_ENDPOINT = "https://search.worldbank.org/api/v2/procnotices"

# Types d'avis = opportunites ouvertes (on jette les avis d'attribution).
BM_NOTICE_TYPES = [
    "Invitation for Bids",
    "Invitation for Prequalification",
    "Request for Expression of Interest",
]

# procurement_group retenus :
#   CS = Consulting Services (experts internationaux deployes -> cibles)
#   CW = Civil Works         (chantiers, techniciens sur site   -> cibles)
# Ecartes : GO (Goods, pure fourniture = le bruit, equivalent de
# l'electricite chez TED), NC, etc.
BM_GROUPES_RETENUS = {"CS", "CW"}

NB_JOURS_FENETRE_BM = 30      # avis publies dans les 30 derniers jours
ROWS_BM = 500                 # enregistrements par page
MAX_PAGES_BM = 10             # garde-fou anti-boucle (5000 avis max scannes)

# --- PRE-FILTRE (equivalent du CPV chez TED) -------------------------------
# Sans CPV, le filtre CS/CW seul laisse passer ~780 avis (rôles bureau,
# construction locale...) sans besoin d'escorte. On filtre AVANT le LLM,
# avec deux leviers cheap, pour ne lui envoyer que des cibles plausibles.

# 1) Niveau de risque pays minimal (multiplicateur de zone TED reutilise) :
#    0.6 = rouge + orange (defaut, selectif). Abaisser a 0.3 pour inclure
#    tous les pays a risque listes (Afrique/MENA/Amerique latine), 0.2 pour
#    desactiver le filtre pays. L'escorte/CP est un metier de zones a risque.
TIER_RISQUE_MINIMAL = 0.6

# 2) Mots dans le titre qui trahissent un role SANS deploiement terrain
#    expose (bureau, admin, etude, construction locale). EN + FR + quelques
#    ES/PT frequents, car les avis BM sont multilingues. Volontairement
#    prudent : n'exclut PAS "supervision", "technical assistance", "works",
#    qui peuvent impliquer des experts internationaux sur le terrain.
MOTS_EXCLUSION_BM = [
    "administrative assistant", "administration assistant", "accountant",
    "comptable", "procurement specialist", "procurement officer",
    "passation de marche", "monitoring and evaluation", "m&e ",
    "suivi-evaluation", "suivi et evaluation", "communication",
    "translator", "traducteur", "interpreter", "data engineer",
    "system administrator", "database", "legal assistant", "asistente legal",
    "juriste", "gender", "genre", "gbv", "sexual exploitation",
    "safeguard", "knowledge management",
    "gestion des connaissances", "financial management", "gestion financiere",
    "human resources", "ressources humaines", "logistics officer",
    "responsable logistique", "epidemiolog",
    "feasibility study", "etude de faisabilite", "baseline", "market analysis",
    "etude de marche", "audit", "auditor", "call center",
    "teaching and learning", "learning materials", "course modules",
    "training materials", "pedagogique", "training specialist",
    "conducteur de vehicule", "vehicule administratif",
    "classroom", "school block", "salle de classe", "latrine", "dormitory",
    "rural housing", "housing reconstruction", "logement rural", "borehole",
    # --- Motifs ajoutes apres le 1er run reel (bruit constate) ---
    # Experts de programme education/social (rôles de bureau) :
    "quality assurance", "social and behavior change", "social protection",
    "reform coordination", "subject matter expert", "lecturer",
    "facilitateur", "facilitator", "focal point", "business development",
    "business analyst", "private sector development",
    # Construction d'ecoles locales (main d'oeuvre locale, pas d'expat expose).
    # N'exclut PAS "contrôle/surveillance/supervision des travaux", qui
    # impliquent des ingenieurs potentiellement internationaux sur site.
    "ecole", "école", "ecoles", "écoles", "school",
    "entrepot", "entrepôt", "hangar",
    # --- Motifs ajoutes apres le run reel complet (150 avis) ---
    # Bangladesh LGED travaux locaux (routes/drains/cliniques, zero expat) :
    "community clinic", "clinique communautaire",
    "repair & renovation", "repair and renovation",
    "rcc road", "rcc drain", "bc road", "footpath",
    "streetlight", "street light",
    # IT/developpement (roles de bureau, zero terrain) :
    "java developer", "developer", "system administrator",
    "information system", "mis/", "dashboard",
    "public key infrastructure", "pki",
    # Evaluations de politiques / strategies (documentaire, zero terrain) :
    "records management", "proof of concept",
    "strategie nationale", "stratégie nationale",
]

# Override POSITIF (fix audit, fait correctement) : un titre qui mentionne
# explicitement une prestation de SURETE PHYSIQUE ne doit JAMAIS etre exclu
# par un mot-cle bureau. C'est ce qui sauve "Physical security audit for
# remote mining sites" (garde malgre "audit"), pas une borne de mot. Termes
# volontairement specifiques au metier physique : on EVITE "security" seul,
# qui matcherait "food security" / "cyber security" / "social security"
# (hors-sujet). Compares avec une borne de DEBUT de mot (\b), donc
# "guarding" ne declenche pas sur "safeguarding".
MOTS_SIGNAL_SURETE = [
    "physical security", "close protection", "protection rapprochee",
    "protection rapprochée", "escort", "escorte", "bodyguard",
    "garde du corps", "gardiennage", "guarding", "convoy security",
    "armed escort", "armed guard",
]

# Plafond de securite : meme apres filtrage, ne jamais envoyer plus de N
# avis au LLM en un run. Les avis sont d'abord tries par niveau de risque
# pays decroissant, donc le plafond garde les plus pertinents s'il est atteint.
MAX_AVIS_LLM_BM = int(os.environ.get("BM_BUDGET", "150"))  # plafond d'appels LLM par run

NOM_ONGLET_BM = "bm_radar"

LIEN_BM = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{}"


# Correspondance NOM de pays (anglais, format Banque Mondiale) -> ISO3, pour
# que le multiplicateur de zone de calculer_scores (qui attend des codes
# ISO3, comme chez TED) s'applique de facon COHERENTE entre les deux
# sources. Couvre les zones rouge/orange et les principaux pays en
# developpement ; un pays non liste retombe sur le multiplicateur par
# defaut (0.2), le modele lisant de toute facon le risque dans la
# description. Inclut les formes "Banque Mondiale" usuelles (ex: "Egypt,
# Arab Republic of", "Yemen, Republic of", "Congo, Democratic Republic of").
PAYS_NOM_VERS_ISO3 = {
    # Rouge
    "libya": "LBY", "mali": "MLI", "niger": "NER", "burkina faso": "BFA",
    "congo, democratic republic of": "COD", "democratic republic of congo": "COD",
    "dr congo": "COD", "south sudan": "SSD", "yemen": "YEM",
    "yemen, republic of": "YEM", "somalia": "SOM", "iraq": "IRQ",
    "ukraine": "UKR", "mexico": "MEX", "west bank and gaza": "PSE",
    "afghanistan": "AFG", "haiti": "HTI", "syrian arab republic": "SYR",
    "syria": "SYR",
    # Orange
    "ethiopia": "ETH", "nigeria": "NGA", "cameroon": "CMR",
    "mozambique": "MOZ", "bangladesh": "BGD", "pakistan": "PAK",
    "egypt": "EGY", "egypt, arab republic of": "EGY", "uzbekistan": "UZB",
    "moldova": "MDA", "jamaica": "JAM", "armenia": "ARM", "jordan": "JOR",
    "papua new guinea": "PNG", "montenegro": "MNE", "albania": "ALB",
    "madagascar": "MDG", "oman": "OMN", "turkiye": "TUR", "turkey": "TUR",
    "south africa": "ZAF",
    # Afrique / zones a couverture large (multiplicateur 0.3)
    "algeria": "DZA", "angola": "AGO", "benin": "BEN", "botswana": "BWA",
    "burundi": "BDI", "cabo verde": "CPV", "central african republic": "CAF",
    "chad": "TCD", "comoros": "COM", "congo, republic of": "COG",
    "congo": "COG", "cote d'ivoire": "CIV", "côte d'ivoire": "CIV",
    "djibouti": "DJI", "equatorial guinea": "GNQ", "eritrea": "ERI",
    "eswatini": "SWZ", "gabon": "GAB", "gambia, the": "GMB",
    "gambia": "GMB", "ghana": "GHA", "guinea": "GIN", "guinea-bissau": "GNB",
    "kenya": "KEN", "lesotho": "LSO", "liberia": "LBR", "malawi": "MWI",
    "mauritania": "MRT", "mauritius": "MUS", "morocco": "MAR",
    "namibia": "NAM", "rwanda": "RWA", "sao tome and principe": "STP",
    "senegal": "SEN", "seychelles": "SYC", "sierra leone": "SLE",
    "sudan": "SDN", "tanzania": "TZA", "togo": "TGO", "tunisia": "TUN",
    "uganda": "UGA", "zambia": "ZMB", "zimbabwe": "ZWE",
    # Moyen-Orient / Asie / Amerique latine frequents
    "lebanon": "LBN", "iran, islamic republic of": "IRN", "iran": "IRN",
    "india": "IND", "nepal": "NPL", "sri lanka": "LKA", "myanmar": "MMR",
    "cambodia": "KHM", "lao people's democratic republic": "LAO",
    "indonesia": "IDN", "philippines": "PHL", "colombia": "COL",
    "peru": "PER", "bolivia": "BOL", "honduras": "HND", "guatemala": "GTM",
    "venezuela, rb": "VEN", "ecuador": "ECU", "tajikistan": "TJK",
    "kyrgyz republic": "KGZ", "kazakhstan": "KAZ",
}


# ===========================================================================
# PARTIE 2 -- COLLECTE
# ===========================================================================

# Fix 1 (audit) : plusieurs formats explicites essayes dans l'ordre, puis
# repli sur dateutil si present. Si la Banque Mondiale change "28-Oct-2025"
# en un autre format, l'outil s'adapte au lieu de s'effondrer silencieusement.
# dayfirst=True evite l'ambiguite US (jour/mois inverses) sur dateutil.
_FORMATS_DATE_BM = ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
                    "%Y-%m-%dT%H:%M:%SZ", "%d %b %Y", "%b %d, %Y")


def _date_notice(record):
    """Convertit noticedate en date Python. None si vraiment illisible.
    Robuste a un changement de format cote Banque Mondiale (fix audit)."""
    brut = (record.get("noticedate") or "").strip()
    if not brut:
        return None
    for fmt in _FORMATS_DATE_BM:
        try:
            return datetime.strptime(brut, fmt).date()
        except ValueError:
            continue
    try:  # dernier recours : dateutil (present par defaut sur Colab)
        from dateutil import parser as _parser_date
        return _parser_date.parse(brut, dayfirst=True).date()
    except Exception:
        print("  (info) Format de date inattendu, avis ignore : {!r}".format(brut))
        return None


def collecte_bm():
    """Pagine l'API procnotices (tri par defaut = date decroissante, donc
    avis les plus recents en premier) et s'arrete des qu'une page est
    entierement hors fenetre. Renvoie (bruts, retenus_dans_fenetre, total_api).

    N'utilise QUE des parametres verifies en direct (format, apilang, srce,
    rows, os, notice_type_exact). Pas de filtre date serveur (parametre non
    confirme) : la fenetre est appliquee cote client, ce qui est sur."""
    seuil = date.today() - timedelta(days=NB_JOURS_FENETRE_BM)
    params_base = {
        "format": "json", "apilang": "en", "srce": "both",
        "rows": ROWS_BM,
        "notice_type_exact": "^".join(BM_NOTICE_TYPES),
    }
    bruts, retenus = [], []
    total_api = None   # Fix 2 (audit) : total annonce par l'API (detection de silence)
    for page in range(MAX_PAGES_BM):
        params = dict(params_base)
        params["os"] = page * ROWS_BM
        reponse = ted.session_robuste().get(BM_ENDPOINT, params=params, timeout=30)
        reponse.raise_for_status()
        try:
            charge = reponse.json()
        except ValueError as e:
            print("ERREUR : reponse non-JSON de l'API BM (page {}) : {}. "
                  "La structure de l'API a peut-etre change.".format(page, e))
            break
        if total_api is None:
            try:
                total_api = int(charge.get("total", 0))
            except (TypeError, ValueError):
                total_api = None
        lot = charge.get("procnotices", [])
        if not lot:
            break
        bruts.extend(lot)
        dans_fenetre = [n for n in lot if (_date_notice(n) and _date_notice(n) >= seuil)]
        retenus.extend(dans_fenetre)
        if not dans_fenetre:
            break  # tri decroissant : plus aucune chance d'en trouver plus loin
        if len(lot) < ROWS_BM:
            break  # derniere page
    else:
        print("ATTENTION : plafond de {} pages BM atteint. Augmenter "
              "MAX_PAGES_BM si ce message revient souvent.".format(MAX_PAGES_BM))
    return bruts, retenus, total_api


def avis_correspond_bm(record):
    """Filtre de pertinence cote client : avis publie, et groupe d'achat
    consulting (CS) ou travaux civils (CW). Le type d'avis est deja filtre
    cote serveur, on le reverifie par securite."""
    if (record.get("notice_status") or "") != "Published":
        return False
    if (record.get("procurement_group") or "").upper() not in BM_GROUPES_RETENUS:
        return False
    if (record.get("notice_type") or "") not in BM_NOTICE_TYPES:
        return False
    return True


def tier_risque_record(record):
    """Niveau de risque du pays d'execution (multiplicateur de zone TED).
    0.2 par defaut si pays inconnu/non liste."""
    iso3 = code_iso3_pays(record.get("project_ctry_name") or "")
    return ted.MULTIPLICATEUR_ZONE.get(iso3, 0.2)


def cible_amarante(record):
    """PRE-FILTRE avant LLM (equivalent du CPV chez TED). Garde un avis
    seulement si (1) le pays atteint le tier de risque minimal, ET (2) soit
    le titre signale explicitement une prestation de surete physique
    (override positif), soit il ne contient aucun mot-cle bureau/admin/
    construction-locale. But : ne payer le LLM que sur des cibles plausibles.

    Fix audit (correspondance de mots) : on ancre chaque mot d'exclusion par
    une borne de DEBUT de mot (\\b en tete), pour ne plus sur-matcher un mot
    contenu dans un mot plus long ("school" dans "preschool"). On n'ancre
    PAS la fin : cela casserait les racines et pluriels volontaires
    ("epidemiolog" -> epidemiologist, "ecole" -> ecoles). L'override positif
    gere les cas que la borne ne peut pas sauver (mot entier "audit" dans un
    contexte de surete reelle)."""
    if not ted.dans_le_perimetre(code_iso3_pays(record.get("project_ctry_name") or ""),
                                 TIER_RISQUE_MINIMAL):
        return False
    titre = "{} {}".format(
        record.get("bid_description") or "", record.get("project_name") or ""
    ).lower()
    # (2a) Override positif : surete physique explicite -> on garde.
    if any(re.search(r"\b" + re.escape(sig), titre) for sig in MOTS_SIGNAL_SURETE):
        return True
    # (2b) Exclusion par mot ancre en debut (pas en fin).
    if any(re.search(r"\b" + re.escape(mot), titre) for mot in MOTS_EXCLUSION_BM):
        return False
    return True


# ===========================================================================
# PARTIE 3 -- NORMALISATION (vers la forme d'avis commune au coeur TED)
# ===========================================================================

def code_iso3_pays(nom):
    """NOM de pays anglais (Banque Mondiale) -> ISO3, pour le multiplicateur
    de zone. Chaine vide si non trouve (-> multiplicateur par defaut 0.2,
    donc ecarte par le filtre de risque, ce qui est le bon defaut prudent
    pour un pays non liste). Pas de repli par sous-chaine : "oman" est
    contenu dans "romania", "mali" dans "somalia" -- meme piege que le code
    TED avait deja corrige. On s'en tient a l'egalite exacte, sur le nom
    complet puis sur la partie avant la virgule ("Egypt, Arab Republic of"
    -> "egypt")."""
    if not nom:
        return ""
    n = nom.strip().lower()
    if n in PAYS_NOM_VERS_ISO3:
        return PAYS_NOM_VERS_ISO3[n]
    base = n.split(",")[0].strip()
    return PAYS_NOM_VERS_ISO3.get(base, "")


def _deadline_iso(record):
    """submission_deadline_date '2028-11-24T00:00:00Z' -> '2028-11-24'."""
    return (record.get("submission_deadline_date") or "")[:10]


def normaliser_bm(record):
    """Construit l'avis normalise attendu par le coeur TED (appeler_llm,
    calculer_scores, calculer_fenetre_action) + champs propres a la BM
    (contacts, groupe d'achat) pour le Sheet."""
    description = ted._nettoyer_html(record.get("notice_text") or "")
    if len(description) > ted.MAX_CARACTERES_DESCRIPTION:
        description = description[:ted.MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"

    bid = (record.get("bid_description") or "").strip()
    projet = (record.get("project_name") or "").strip()
    titre = " — ".join([t for t in (bid, projet) if t])[:300]

    pays_nom = (record.get("project_ctry_name") or "").strip()
    notice_iso = _date_notice(record)

    return {
        "publication_number": record.get("id", ""),
        "titre": titre,
        "acheteur": (record.get("contact_organization") or "").strip(),
        "pays_acheteur": "",            # financeur supranational (Banque Mondiale)
        "pays_execution": pays_nom,     # NOM lisible (prompt + affichage + Sheet)
        "pays_iso3": code_iso3_pays(pays_nom),   # CODE (scoring zone, usage interne)
        "pays_execution_incertitude": False,
        "cpv": "",                      # pas de CPV cote Banque Mondiale
        "description": description,
        "deadline": _deadline_iso(record),
        "date_publication": notice_iso.isoformat() if notice_iso else "",
        "valeur_estimee": "inconnu",
        "source_mode_b": False,
        "lien_avis": LIEN_BM.format(record.get("id", "")),
        # Champs propres BM (Sheet)
        "procurement_group": (record.get("procurement_group") or "").upper(),
        "procurement_method": record.get("procurement_method_name", ""),
        "contact_organization": (record.get("contact_organization") or "").strip(),
        "contact_name": (record.get("contact_name") or "").strip(),
        "contact_email": (record.get("contact_email") or "").strip(),
        "contact_phone": (record.get("contact_phone_no") or "").strip(),
    }


def avis_pour_scoring(avis, extraction=None):
    """Copie de l'avis ajustee pour calculer_scores UNIQUEMENT : le code
    ISO3 dans pays_execution (multiplicateur de zone) et, sous condition, un
    CPV synthetique de construction (bonus infrastructure critique, division
    45) pour la coherence avec TED. L'avis reel (nom de pays lisible) reste
    utilise pour le prompt et le Sheet.

    Fix 4 (audit -- l'exposition humaine prime sur le beton) : le bonus
    infrastructure des travaux civils n'est accorde QUE si le LLM a detecte
    des personnes reellement exposees (experts internationaux ou dirigeants).
    Un barrage de 5 ans avec uniquement des ouvriers locaux ne recoit donc
    PAS le bonus infra, alors qu'il l'aurait recu en se basant sur la seule
    nature du marche. C'est l'humain expose, pas la taille du chantier, qui
    fait la valeur pour Amarante."""
    copie = dict(avis)
    copie["pays_execution"] = avis.get("pays_iso3") or avis.get("pays_execution", "")
    profil = (extraction or {}).get("profil_personnes_exposees", "")
    exposition_internationale = profil in ("expert_international", "executive")
    if avis.get("procurement_group") == "CW" and exposition_internationale:
        copie["cpv"] = "45000000"   # division 45 = construction (infra critique)
    return copie


def cible_commerciale(avis, extraction):
    """Fix 3 (audit -- le piege du contact BM) : l'agence acheteuse de la
    Banque Mondiale (contact_organization) finance et publie, mais n'achete
    presque JAMAIS la prestation de securite. Le client reel d'Amarante est
    l'entite qui DEPLOIE et EXPOSE du personnel : le titulaire du marche.
    Cette colonne rappelle qui demarcher concretement. Les profils d'acteurs
    precis (bureau d'etudes, consortium...) viennent en complement du champ
    'profils_acteurs_probables' produit par le LLM."""
    groupe = avis.get("procurement_group", "")
    if groupe == "CW":
        return ("Titulaire du marche de travaux (entreprise BTP retenue) "
                "ou bureau de controle/supervision, PAS l'agence acheteuse BM.")
    if groupe == "CS":
        return ("Cabinet / consortium titulaire du marche de conseil qui "
                "deploie les experts, PAS l'agence acheteuse BM.")
    return "Titulaire du marche une fois attribue, pas l'agence acheteuse BM."


# ===========================================================================
# PARTIE 4 -- SORTIE GOOGLE SHEET (onglet bm_radar)
# ===========================================================================

COLONNES_BM = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance",
    "modele", "raffine", "divergence",
    "procurement_group", "procurement_method",
    "contact_organization", "contact_name", "contact_email", "contact_phone",
    "publication_number", "lien_avis", "deadline", "date_publication",
]
# Colonnes preservees (jamais ecrasees par un re-run), apres les donnees :
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_BM = COLONNES_BM + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille_bm(sheet_id, fichier_compte_service):
    import gspread
    from google.oauth2.service_account import Credentials

    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_compte_service, scopes=portee)
    client = gspread.authorize(creds)
    classeur = client.open_by_key(sheet_id)
    try:
        feuille = classeur.worksheet(NOM_ONGLET_BM)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET_BM, rows=2000, cols=len(TOUTES_COLONNES_BM)
        )
        feuille.append_row(TOUTES_COLONNES_BM)
        return feuille
    # Auto-reparation de l'en-tete (idempotent).
    entetes = feuille.row_values(1)
    if COLONNE_DATE_DETECTION not in entetes:
        feuille.update(values=[TOUTES_COLONNES_BM], range_name="A1")
    return feuille


def ligne_depuis_resultat_bm(r):
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
        "cible_commerciale_reelle": cible_commerciale(avis, extraction),
        "justification": extraction.get("justification") if extraction else "",
        "confiance": extraction.get("confiance") if extraction else "",
        "modele": modele_utilise,
        "raffine": r["raffine"],
        "divergence": r["divergence"],
        "procurement_group": avis.get("procurement_group", ""),
        "procurement_method": avis.get("procurement_method", ""),
        "contact_organization": avis.get("contact_organization", ""),
        "contact_name": avis.get("contact_name", ""),
        "contact_email": avis.get("contact_email", ""),
        "contact_phone": avis.get("contact_phone", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(valeurs.get(c, "")) for c in COLONNES_BM]


def ecrire_resultats_bm(feuille, resultats):
    """Ecriture groupee (batch) : mises a jour en un appel, nouveaux en un
    appel. statut_suivi et date_detection (zone preservee) jamais ecrases
    sur un re-run. Reutilise l'indexation par publication_number du coeur."""
    index = ted.charger_index_publication(feuille)
    derniere_lettre = ted.lettre_colonne(len(COLONNES_BM))
    maj_groupees, nouvelles_lignes = [], []
    nb_nouveaux, nb_maj = 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne_valeurs = ligne_depuis_resultat_bm(r)
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
    # best-effort, sous FORME PLATE (colonnes du Sheet) : la forme canonique
    # que lit le dashboard. On passe TOUT (le miroir a sa propre memoire,
    # ON CONFLICT DO NOTHING : remplissage retroactif inclus). Ne peut JAMAIS
    # faire echouer le run. NB : en phase de double ecriture, le Sheet reste
    # la reference ; les mises a jour de scores ne touchent que le Sheet.
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES_BM, ligne_depuis_resultat_bm(r))) for r in resultats]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET_BM, plates))
    except Exception as e:                     # module absent : run intact
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_nouveaux, nb_maj


# ===========================================================================
# PARTIE 5 -- POINT D'ENTREE
# ===========================================================================

def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY n'est pas definie. Definis-la dans "
              "une cellule SEPAREE avant de lancer ce script.")
        return

    print("Etape 1/2 -- Collecte Banque Mondiale (procnotices, CS/CW, fenetre {} j)...".format(
        NB_JOURS_FENETRE_BM
    ))
    bruts, recents, total_api = collecte_bm()

    # Fix 2 (audit) : detection de silence anormal de l'API. Un HTTP 200 avec
    # 0 avis peut signifier "rien de nouveau", mais aussi un changement
    # d'endpoint ou de structure JSON cote Banque Mondiale. On alerte plutot
    # que de passer silencieusement.
    if total_api == 0 or not bruts:
        print("\n/!\\ ALERTE : l'API Banque Mondiale a renvoye 0 avis "
              "(total annonce = {}). Causes possibles : aucun avis ouvert "
              "(peu probable), ou changement d'endpoint/structure cote serveur. "
              "Verifier {} manuellement avant de conclure.".format(total_api, BM_ENDPOINT))
        return

    pertinents = [n for n in recents if avis_correspond_bm(n)]
    cibles = [n for n in pertinents if cible_amarante(n)]

    # Dedup par id (un meme avis peut apparaitre sur plusieurs pages).
    vus, uniques = set(), []
    for n in cibles:
        if n.get("id") and n["id"] not in vus:
            vus.add(n["id"])
            uniques.append(n)

    # Tri par niveau de risque pays decroissant : si le plafond de securite
    # est atteint, on garde les avis les plus pertinents (zones les plus
    # exposees) plutot qu'un echantillon arbitraire.
    uniques.sort(key=tier_risque_record, reverse=True)

    avis_normalises = [normaliser_bm(n) for n in uniques]
    print("BM -- Bruts : {} | fenetre : {} | CS/CW publies : {} | cibles (risque+hors bureau) : {}".format(
        len(bruts), len(recents), len(pertinents), len(avis_normalises)
    ))

    if not avis_normalises:
        print("Aucun avis Banque Mondiale a analyser.")
        return

    # Memoire inter-runs : on ne reanalyse pas un avis deja traite lors d'un run
    # precedent (economie de tokens + temps), ce qui evite aussi de le
    # re-ajouter en double. Lecture tolerante : si pas de Sheet ou erreur, on
    # analyse tout (comportement d'avant).
    #
    # ORDRE CORRIGE LE 22/07/2026 : la memoire s'applique AVANT le plafond.
    # L'inverse gaspillait tout le budget d'analyse sur des avis deja connus :
    # au run du 22/07, 150 places retenues dont 146 deja vues, soit 4 nouveaux
    # avis analyses pendant que des centaines de candidats attendaient sans
    # jamais avoir leur tour (le tri par risque etant stable, c'etaient
    # toujours les memes qui passaient). AfDB et EBRD faisaient deja
    # correctement dans cet ordre.
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    deja_vus = ted.numeros_publication_existants(
        sheet_id, fichier, NOM_ONGLET_BM, COLONNES_BM)
    if deja_vus:
        avant = len(avis_normalises)
        avis_normalises = [a for a in avis_normalises
                           if str(a.get("publication_number", "")).strip() not in deja_vus]
        print("Memoire : {} avis deja analyses (runs precedents) ignores, "
              "{} nouveau(x) a analyser.".format(avant - len(avis_normalises), len(avis_normalises)))
    if not avis_normalises:
        print("Aucun NOUVEL avis Banque Mondiale a analyser (tout deja vu). "
              "Le Sheet et le dashboard restent a jour.")
        return

    # Plafond de securite, applique aux avis NEUFS uniquement : le budget sert
    # desormais a decouvrir, plus a redecouvrir. Le reliquat n'est pas perdu,
    # il passera au run suivant (il ne sera plus dans la memoire).
    if len(avis_normalises) > MAX_AVIS_LLM_BM:
        en_attente = len(avis_normalises) - MAX_AVIS_LLM_BM
        avis_normalises = avis_normalises[:MAX_AVIS_LLM_BM]
        print("    (plafond de {} : {} nouveau(x) avis analyses ce run, {} en "
              "attente pour le prochain, les plus a risque d'abord.)".format(
                  MAX_AVIS_LLM_BM, MAX_AVIS_LLM_BM, en_attente))

    # Fix 3 (audit) : mode DRY-RUN. Avec la variable d'env BM_DRY_RUN definie,
    # on s'arrete ici : on voit l'entonnoir et les titres qui PASSERAIENT au
    # LLM, sans aucun appel paye. Ideal pour regler les filtres (TIER, mots
    # d'exclusion) a cout zero avant de lancer la vraie analyse.
    if os.environ.get("BM_DRY_RUN"):
        print("\n=== MODE DRY-RUN : aucun appel LLM, aucun cout ===")
        print("Avis qui passeraient a l'analyse ({}) :".format(len(avis_normalises)))
        for i, a in enumerate(avis_normalises, start=1):
            tier = ted.MULTIPLICATEUR_ZONE.get(a.get("pays_iso3", ""), 0.2)
            print("  {:3}. [{} | risque {}] {} ({})".format(
                i, a.get("procurement_group", "  "), tier,
                a["titre"][:66], a.get("pays_execution", "")))
        print("\nRetire la variable BM_DRY_RUN pour lancer l'analyse reelle.")
        return

    nb_desc = sum(1 for a in avis_normalises if a.get("description"))
    print("\nEnrichissement description : {}/{} avis ont une description exploitable.".format(
        nb_desc, len(avis_normalises)
    ))

    print("\nEtape 2/2 -- Extraction LLM et score ({} avis, modele {})...\n".format(
        len(avis_normalises), ted.MODELE
    ))

    resultats = []
    for i, avis in enumerate(avis_normalises, start=1):
        print("[{}/{}] {}...".format(i, len(avis_normalises), avis["titre"][:60]))
        extraction = ted.appeler_llm(avis)                 # prompt = avis (nom pays lisible)
        s, c, f = ted.calculer_scores(avis_pour_scoring(avis, extraction), extraction)  # score = copie ISO3/CPV conditionnel
        resultats.append({
            "avis": avis, "extraction": extraction,
            "surete_haiku": s, "commercial_haiku": c, "final_haiku": f,
            "surete": s, "commercial": c, "score": f,
            "raffine": False, "divergence": False,
        })
        time.sleep(0.5)

    # Escalade Sonnet, memes criteres que TED.
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
        print("\n{} avis escalades vers {}...\n".format(len(a_escalader), ted.MODELE_RAFFINEMENT))
        for i, r in enumerate(a_escalader, start=1):
            print("[{}/{}] Raffinement : {}...".format(i, len(a_escalader), r["avis"]["titre"][:60]))
            raffinee = ted.appeler_llm(r["avis"], modele=ted.MODELE_RAFFINEMENT)
            if raffinee is not None:
                s, c, f = ted.calculer_scores(avis_pour_scoring(r["avis"], raffinee), raffinee)
                r["extraction"] = raffinee
                r["surete"], r["commercial"], r["score"] = s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0
            time.sleep(0.5)

    resultats.sort(key=lambda r: r["score"], reverse=True)

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sheet_id and fichier:
        print("\nEcriture dans l'onglet '{}' ({} avis)...".format(NOM_ONGLET_BM, len(resultats)))
        try:
            feuille = ouvrir_feuille_bm(sheet_id, fichier)
            nb_nouveaux, nb_maj = ecrire_resultats_bm(feuille, resultats)
            print("-> {} nouvel(s) avis ajoute(s), {} mis a jour (statut_suivi jamais touche).".format(
                nb_nouveaux, nb_maj
            ))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("\n(Pas de Sheet configure : definis TED_SHEET_ID et "
              "GOOGLE_SERVICE_ACCOUNT_FILE pour activer l'ecriture.)")

    # Affichage console
    print("\n" + "=" * 70)
    print("RESULTATS BANQUE MONDIALE (score = surete x0.5 + commercial x0.5)")
    print("FORT >= {} | A SURVEILLER >= {} | faible en dessous".format(
        ted.SEUIL_ALERTE, ted.SEUIL_SURVEILLANCE))
    nb_fort = sum(1 for r in resultats if r["score"] >= ted.SEUIL_ALERTE)
    nb_surv = sum(1 for r in resultats
                  if ted.SEUIL_SURVEILLANCE <= r["score"] < ted.SEUIL_ALERTE)
    nb_faible = len(resultats) - nb_fort - nb_surv
    print("Bilan : {} FORT(S) a contacter | {} a surveiller | {} faible(s)".format(
        nb_fort, nb_surv, nb_faible))
    print("=" * 70)
    for r in resultats:
        score, avis, extraction = r["score"], r["avis"], r["extraction"]
        if score >= ted.SEUIL_ALERTE:
            etiquette = "[FORT]"
        elif score >= ted.SEUIL_SURVEILLANCE:
            etiquette = "[A SURVEILLER]"
        else:
            etiquette = "[faible]"
        suffixe = ""
        if r["raffine"]:
            suffixe = " (relu par {} ; Haiku avait {:.1f})".format(ted.MODELE_RAFFINEMENT, r["final_haiku"])
            if r["divergence"]:
                suffixe += "  /!\\ ECART NOTABLE -- lire les deux justifications"
        print("\n{} Score final {:.1f}/10 (surete {:.1f} | commercial {:.1f}){}".format(
            etiquette, score, r["surete"], r["commercial"], suffixe))
        print("  Action recommandee : {} | Fenetre : {}".format(
            ted.calculer_action_recommandee(score, extraction, surete=r["surete"]),
            ted.calculer_fenetre_action(avis)))
        print("  {}".format(avis["titre"][:90]))
        print("  Agence : {} | Pays : {} | Groupe : {}".format(
            avis["acheteur"], avis["pays_execution"], avis.get("procurement_group", "")))
        contact = " / ".join([x for x in (avis.get("contact_name"), avis.get("contact_email"),
                                          avis.get("contact_phone")) if x]) or "non fourni"
        print("  Contact : {}".format(contact))
        if extraction:
            print("  Type client : {} | Duree : {} | Mobilite : {} | Personnes exposees : {}".format(
                extraction.get("type_client"), extraction.get("duree_estimee"),
                extraction.get("type_mobilite"), extraction.get("profil_personnes_exposees")))
            print("  Accessibilite : {} | Securite deja en place : {} | Opportunite : {}".format(
                extraction.get("accessibilite_commerciale"),
                extraction.get("securite_existante_detectee"),
                extraction.get("niveau_opportunite_amarante")))
            print("  Justification : {}".format(extraction.get("justification")))
            print("  Qui demarcher : {}".format(cible_commerciale(avis, extraction)))
        else:
            print("  (extraction echouee)")
        print("  Lien : {}".format(avis["lien_avis"]))


if __name__ == "__main__":
    main()
