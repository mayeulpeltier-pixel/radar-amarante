# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- Collecteur MIGA (garanties risque politique, Groupe BM).
=========================================================================

Pourquoi cette source (le contraire de Prozorro)
------------------------------------------------
MIGA assure les investissements PRIVES TRANSFRONTALIERS contre le risque
politique. Chaque projet nomme le Guarantee Holder (l'investisseur), son pays
d'origine, le pays hote, le secteur, le montant. Par statut, l'investisseur est
TOUJOURS etranger au pays hote : c'est un acteur qui deploie du capital et des
equipes dans un pays a risque = coeur de cible Amarante. Les SPG (Summary of
Proposed Guarantee) sont publies 30-60 j AVANT le board = signal PRECOCE.

Acquis des sondes (27/07/2026, verifie sur le brut) :
  - Portail www.miga.org/projects accessible depuis CI AVEC en-tetes navigateur
    complets (le 403 initial n'etait qu'un filtre d'en-tetes). HTML Drupal.
  - Liste : liens fiche href="/project/<slug>", pagination ?page=N.
  - Fiche : champs reguliers <div class="field__label">X</div> ... <div
    class="field__item">valeur</div> pour Guarantee Holder, Investor Country,
    Host Country, Project Type, Fiscal Year, Environmental Category, Project
    Status ; description riche sous "Project Description".
  - Environmental Category "FI" = garantie financiere (banque, reserves, trade
    finance) = AUCUN deploiement physique -> crible de bruit (equivalent du
    crible categorie Prozorro). On garde A/B/C (projets physiques).
  - API Finances One abandonnee (resourceId introuvable ; le portail contient
    tout, proposes + emis + historique).

Architecture : reutilise le coeur TED sans duplication (session, LLM, scoring,
ecriture Sheet positionnelle, memoire, miroir Postgres), comme ReliefWeb. Pas
de CPV (comme ReliefWeb) : le scoring s'appuie sur l'extraction LLM + le
multiplicateur de zone du PAYS HOTE (resolu en ISO3).

MODE DEBUG (RADAR_MIGA_DEBUG=1) : collecte + parsing + crible FI + resolution
pays, AFFICHE, sans LLM ni ecriture. A utiliser au 1er passage (comme IsDB/IDB)
pour valider l'entonnoir sur donnees reelles avant tout write.

Discipline : fetch injectables pour les tests (aucun reseau en test).
"""

import os
import re
import time
from datetime import date

import ted_complet_v14 as ted


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================
ACTIVER = os.environ.get("RADAR_MIGA", "1") != "0"
DEBUG = os.environ.get("RADAR_MIGA_DEBUG", "0") == "1"

BASE = "https://www.miga.org"
LISTE = BASE + "/projects"
FICHE = BASE + "{}"                         # {} = /project/<slug>
TIMEOUT = 45
NOM_ONGLET = "miga_radar"

# Nombre de pages de liste a parcourir par run (volume MIGA faible : quelques
# dizaines d'items recents suffisent ; la memoire evite de retraiter).
MIGA_PAGES = int(os.environ.get("MIGA_PAGES", "4"))
# Categories E&S a ECARTER (financier, sans empreinte terrain). Surchargeable.
CATEGORIES_EXCLUES = set(filter(None, (
    x.strip().upper() for x in
    os.environ.get("MIGA_CATEGORIES_EXCLUES", "FI").split(","))))
MAX_AVIS_LLM = int(os.environ.get("MIGA_MAX_LLM", "60"))
PAUSE = float(os.environ.get("MIGA_PAUSE", "0.3"))

# En-tetes navigateur complets : indispensables (sinon 403 Akamai). Valide en CI.
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Referer": "https://www.miga.org/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Connection": "keep-alive",
}


# ===========================================================================
# PARTIE 2 -- RESOLUTION PAYS HOTE -> ISO3 (pour le multiplicateur de zone)
# ===========================================================================
# MIGA nomme le pays hote en anglais ("Congo, Democratic Republic of",
# "Nigeria", "Yemen, Republic of"). On resout vers l'ISO3 attendu par
# ted.MULTIPLICATEUR_ZONE. Perimetre a risque Amarante ; un pays hors carte
# tombe sur le multiplicateur par defaut (faible), ce qui est le comportement
# voulu (une garantie au Chili scorera bas toute seule). Calibrable.
_MOTS_ISO3 = {
    "ukraine": "UKR", "nigeria": "NGA", "mali": "MLI", "niger": "NER",
    "burkina": "BFA", "chad": "TCD", "tchad": "TCD", "somalia": "SOM",
    "libya": "LBY", "yemen": "YEM", "iraq": "IRQ", "syria": "SYR",
    "afghanistan": "AFG", "pakistan": "PAK", "mozambique": "MOZ",
    "burundi": "BDI", "cameroon": "CMR", "ethiopia": "ETH", "kenya": "KEN",
    "mauritania": "MRT", "senegal": "SEN", "haiti": "HTI", "venezuela": "VEN",
    "colombia": "COL", "mexico": "MEX", "egypt": "EGY", "tunisia": "TUN",
    "algeria": "DZA", "jordan": "JOR", "lebanon": "LBN", "tanzania": "TZA",
    "uganda": "UGA", "madagascar": "MDG", "zimbabwe": "ZWE", "angola": "AGO",
    "ghana": "GHA", "tajikistan": "TJK", "kyrgyz": "KGZ", "uzbekistan": "UZB",
    "kazakhstan": "KAZ", "georgia": "GEO", "armenia": "ARM", "azerbaijan": "AZE",
    "myanmar": "MMR", "bangladesh": "BGD", "sri lanka": "LKA",
    "central african": "CAF", "sierra leone": "SLE", "liberia": "LBR",
    "togo": "TGO", "benin": "BEN", "guinea-bissau": "GNB",
    "equatorial guinea": "GNQ", "papua new guinea": "PNG", "guinea": "GIN",
    "cote d": "CIV", "ivoire": "CIV", "ivory coast": "CIV", "djibouti": "DJI",
    "rwanda": "RWA", "gabon": "GAB", "timor": "TLS", "west bank": "PSE",
    "gaza": "PSE", "palestin": "PSE", "zambia": "ZMB", "malawi": "MWI",
    "nepal": "NPL", "honduras": "HND", "guatemala": "GTM", "ecuador": "ECU",
    "bolivia": "BOL", "el salvador": "SLV", "nicaragua": "NIC",
}


def _iso3_pays(nom):
    """Pays hote (anglais) -> ISO3. Cas ambigus traites en premier."""
    n = (nom or "").strip().lower()
    if not n:
        return ""
    if "south sudan" in n:
        return "SSD"
    if "sudan" in n:
        return "SDN"
    if "congo" in n:
        return "COD" if "democratic" in n else "COG"
    for mot, iso in _MOTS_ISO3.items():
        if re.search(r"\b" + re.escape(mot), n):
            return iso
    return ""


# ===========================================================================
# PARTIE 3 -- COLLECTE (liste paginee + fiches)
# ===========================================================================

def _session():
    s = ted.session_robuste()
    s.headers.update(ENTETES)
    return s


def _liens_projets(html):
    """Liens fiche uniques d'une page de liste, avec le titre si dispo."""
    liens = list(dict.fromkeys(re.findall(r'href="(/project/[^"#?]+)"', html)))
    titres = dict(re.findall(
        r'<div class="title"><a href="(/project/[^"#?]+)"[^>]*>([^<]+)</a>', html))
    return [(l, (titres.get(l) or "").strip()) for l in liens]


def _champ(html, label):
    """Valeur d'un champ Drupal <div class="field__label">LABEL</div> ... item."""
    m = re.search(
        r'field__label">\s*' + re.escape(label) + r'\s*</div>.{0,600}?'
        r'field[_-]+item[^>]*>\s*(.*?)\s*</div>', html, re.S)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", " ", m.group(1)).strip()


def _description(html):
    """Texte riche apres 'Project Description'."""
    m = re.search(r"Project Description\s*</strong>\s*</p>\s*<p>(.*?)</p>", html, re.S)
    if not m:
        m = re.search(r'class="lead">(.*?)</div>', html, re.S)
    texte = re.sub(r"<[^>]+>", " ", m.group(1)) if m else ""
    texte = re.sub(r"\s+", " ", texte).strip()
    if len(texte) > ted.MAX_CARACTERES_DESCRIPTION:
        texte = texte[:ted.MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"
    return texte


def _type_document(description, html):
    """SPG (propose, precoce) vs Brief (emis) via la formulation."""
    d = (description or "").lower()
    if re.search(r"\bissued\b|\bsigned\b", d):
        return "emis (Project Brief)"
    if re.search(r"application|proposed|yet to be identified|has applied|covers an", d):
        return "propose (SPG)"
    return "inconnu"


def parser_fiche(slug, titre_liste, html):
    """Fiche detail -> avis normalise (forme commune au coeur TED)."""
    gh = _champ(html, "Guarantee Holder")
    ic = _champ(html, "Investor Country")
    hc = _champ(html, "Host Country")
    cat = _champ(html, "Environmental Category")
    statut = _champ(html, "Project Status")
    fy = _champ(html, "Fiscal Year")
    ptype = _champ(html, "Project Type")
    desc = _description(html)

    titre = titre_liste or slug.rsplit("/", 1)[-1].replace("-", " ").strip().title()
    # Resolution pays hote : champ Host Country, sinon repli sur le titre puis le
    # slug (certaines fiches ne parsent pas le champ ; le pays est souvent dans
    # le titre, ex. "Setrag Gabon", "Timor Leste Solar").
    iso3 = _iso3_pays(hc) or _iso3_pays(titre) or _iso3_pays(slug.replace("-", " "))
    # publication_number NORMALISE : on retire le suffixe Drupal -\d+ pour que
    # les doublons (SPG puis Brief du meme projet) partagent une seule ligne,
    # qui evolue au lieu de se dupliquer.
    tail = re.sub(r"-\d+$", "", slug.rsplit("/", 1)[-1])

    contexte = ("Investisseur (Guarantee Holder) : {} ({}). Pays hote : {}. "
                "Categorie E&S : {}. Annee fiscale : {}. {}").format(
        gh or "n.c.", ic or "n.c.", hc or "n.c.", cat or "n.c.", fy or "n.c.", desc)

    return {
        "publication_number": "MIGA:" + tail,
        "titre": titre[:300],
        "acheteur": gh or "Investisseur MIGA (non precise)",
        "investisseur_pays": ic,
        "pays_acheteur": iso3 or ic,
        "pays_execution": hc or iso3 or "n.c.",  # nom lisible, sinon ISO3 resolu
        "pays_iso3": iso3,                     # code (scoring zone)
        "pays_execution_incertitude": not bool(iso3),
        "cpv": "",                             # MIGA n'a pas de CPV (comme ReliefWeb)
        "description": contexte,
        "categorie_es": cat,
        "type_document": _type_document(desc, html),
        "annee_fiscale": fy,
        "project_type": ptype,
        "statut": statut,
        "deadline": "",
        "date_publication": "",
        "valeur_estimee": _montant(desc),
        "source_mode_b": False,
        "lien_avis": BASE + slug,
    }


def _montant(texte):
    m = re.search(r"(US\$|USD|EUR|€|CHF)\s?[\d\.,]+\s*(million|billion)?", texte or "")
    return m.group(0).strip() if m else "inconnu"


def collecte(session=None, fetch_liste=None, fetch_fiche=None, deja_vus=None):
    """Parcourt MIGA_PAGES pages de liste, telecharge les fiches nouvelles,
    ecarte les categories financieres (FI). Renvoie (avis, compteurs).
    fetch_liste(page)->html ; fetch_fiche(slug)->html. Injectables (tests)."""
    session = session or _session()
    deja_vus = deja_vus or set()

    def get_liste(page):
        if fetch_liste is not None:
            return fetch_liste(page)
        r = session.get(LISTE + "?page={}".format(page), timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

    def get_fiche(slug):
        if fetch_fiche is not None:
            return fetch_fiche(slug)
        r = session.get(BASE + slug, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

    compteurs = {"pages": 0, "liens_vus": 0, "deja_connus": 0, "fiches_lues": 0,
                 "rejet_categorie_fi": 0, "fiches_illisibles": 0, "retenus": 0}
    avis, vus_run = [], set()

    for page in range(MIGA_PAGES):
        try:
            html = get_liste(page)
        except Exception as e:
            print("  (info) liste MIGA page {} indisponible : {}".format(page, e))
            break
        compteurs["pages"] += 1
        paires = _liens_projets(html)
        if not paires:
            break
        for slug, titre in paires:
            compteurs["liens_vus"] += 1
            # Slug normalise (suffixe -\d+ retire) = cle de dedup et de memoire :
            # collapse les doublons SPG/Brief d'un meme projet.
            pub = "MIGA:" + re.sub(r"-\d+$", "", slug.rsplit("/", 1)[-1])
            if pub in vus_run:
                continue
            vus_run.add(pub)
            if pub in deja_vus:
                compteurs["deja_connus"] += 1
                continue
            try:
                fiche_html = get_fiche(slug)
            except Exception:
                compteurs["fiches_illisibles"] += 1
                continue
            compteurs["fiches_lues"] += 1
            if PAUSE and fetch_fiche is None:
                time.sleep(PAUSE)
            a = parser_fiche(slug, titre, fiche_html)
            if (a.get("categorie_es") or "").upper() in CATEGORIES_EXCLUES:
                compteurs["rejet_categorie_fi"] += 1
                continue
            avis.append(a)
            compteurs["retenus"] += 1

    return avis, compteurs


def avis_pour_scoring(avis):
    """Copie pour calculer_scores : pays_execution = ISO3 du pays hote (zone)."""
    copie = dict(avis)
    copie["pays_execution"] = avis.get("pays_iso3") or avis.get("pays_execution")
    return copie


def cible_commerciale(avis, extraction):
    gh = avis.get("acheteur") or "l'investisseur"
    pays = avis.get("pays_execution") or "le pays hote"
    return ("Investisseur etranger (Guarantee Holder : {}) engageant du capital "
            "en {}. Cible directe : cet investisseur et l'entreprise projet, qui "
            "deploieront cadres et actifs sur zone a risque.").format(gh, pays)


# ===========================================================================
# PARTIE 4 -- PROMPT LLM (meme schema que TED/ReliefWeb)
# ===========================================================================
PROMPT_MIGA = """Tu es analyste sûreté pour une société française de protection de personnes en zones à risque (escorte, protection rapprochée CPO/CPD, chauffeur sécurité, véhicule sécurisé, sécurisation de déplacements terrain). Elle ne vend PAS de conseil voyage générique.

On te donne un projet d'INVESTISSEMENT PRIVÉ ÉTRANGER assuré par la MIGA (Groupe Banque Mondiale) contre le risque politique. Par construction, le Guarantee Holder est un investisseur ÉTRANGER qui engage des capitaux et des équipes dans un pays hôte à risque. Détermine si ce projet implique une présence PHYSIQUE de personnel étranger (cadres expatriés, techniciens, superviseurs) sur le terrain, créant un besoin probable de prestations opérationnelles de sûreté.

RÈGLE DÉPLOIEMENT : un projet industriel, énergétique, minier, d'infrastructure, agro-industriel ou de construction implique des actifs et des équipes sur site (déploiement réel). Un projet purement financier (déjà largement écarté en amont) ou de portefeuille n'expose pas de personnel.

RÈGLE MOBILITÉ TERRAIN : classe dans UNE catégorie : aucune | capitale | multi_sites | chantier | terrain_isole | frontiere.

RÈGLE SÉCURITÉ EXISTANTE : "securite_existante" = aucune | interne_client | prestataire_tiers | inconnu. "prestataire_tiers" n'est PAS un motif d'exclusion (opportunité de déplacement concurrentiel, à conserver).

RÈGLE CLIENT : le Guarantee Holder (investisseur étranger) et l'entreprise projet sont des acteurs PRIVÉS, commercialement accessibles (contrairement à un acheteur public).

RÈGLE PROFILS : ne cite JAMAIS d'entreprise réelle, décris des PROFILS d'acteur.

Réponds UNIQUEMENT en JSON valide, sans texte ni Markdown autour, sans commentaire entre parenthèses dans les valeurs.

Schéma :
{{
  "deploiement_terrain_reel": true | false,
  "type_mobilite": "aucune | capitale | multi_sites | chantier | terrain_isole | frontiere",
  "profil_personnes_exposees": "expert_international | executive | technicien | ouvrier_local | aucun",
  "securite_existante": "aucune | interne_client | prestataire_tiers | inconnu",
  "indices_deploiement": ["courtes citations"],
  "type_activite": "assistance_technique | supervision_chantier | etude_terrain | fourniture_equipement | formation | autre",
  "type_client": "bailleur_donateur | institution_ue_onu | etat_administration_locale | entreprise_privee | autre",
  "duree_estimee": "courte_ponctuelle | longue_ou_residente | indetermine",
  "accessibilite_commerciale": "facile | moyenne | difficile",
  "profils_acteurs_probables": ["types de profils, jamais de noms reels"],
  "besoin_securite_operationnel_probable": true | false,
  "niveau_opportunite_amarante": "fort | moyen | faible",
  "justification": "une à deux phrases, besoin opérationnel concret",
  "confiance": 0.0 à 1.0
}}

Projet à analyser :
Investisseur (Guarantee Holder) : {acheteur}
Pays d'origine de l'investisseur : {investisseur_pays}
Pays hôte (exécution) : {pays_execution}
Type de document : {type_document}
Contexte : {description}
"""


def analyser(avis, modele=None):
    import json
    prompt = PROMPT_MIGA.format(
        acheteur=avis.get("acheteur", ""),
        investisseur_pays=avis.get("investisseur_pays", "") or "n.c.",
        pays_execution=avis.get("pays_execution", ""),
        type_document=avis.get("type_document", ""),
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
# PARTIE 5 -- SORTIE GOOGLE SHEET (miga_radar) + miroir Postgres
# ===========================================================================
COLONNES = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "investisseur_pays", "pays_execution",
    "type_document", "categorie_es", "annee_fiscale",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance", "modele", "raffine", "divergence",
    "statut", "valeur_estimee", "publication_number", "lien_avis",
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
        feuille = classeur.add_worksheet(title=NOM_ONGLET, rows=2000, cols=len(TOUTES_COLONNES))
        feuille.append_row(TOUTES_COLONNES)
        return feuille
    if COLONNE_DATE_DETECTION not in feuille.row_values(1):
        feuille.update(values=[TOUTES_COLONNES], range_name="A1")
    return feuille


def ligne_depuis_resultat(r):
    avis, extraction = r["avis"], r["extraction"]
    modele_utilise = ted.MODELE_RAFFINEMENT if r["raffine"] else ted.MODELE
    v = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"], "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.calculer_action_recommandee(r["score"], extraction, surete=r["surete"]),
        "fenetre_action": ted.calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": extraction.get("niveau_opportunite_amarante") if extraction else "",
        "titre": avis.get("titre", ""), "acheteur": avis.get("acheteur", ""),
        "investisseur_pays": avis.get("investisseur_pays", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "type_document": avis.get("type_document", ""),
        "categorie_es": avis.get("categorie_es", ""),
        "annee_fiscale": avis.get("annee_fiscale", ""),
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
        "modele": modele_utilise, "raffine": r["raffine"], "divergence": r["divergence"],
        "statut": avis.get("statut", ""), "valeur_estimee": avis.get("valeur_estimee", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
    }
    return [str(v.get(c, "")) for c in COLONNES]


def ecrire_resultats(feuille, resultats):
    index = ted.charger_index_publication(feuille, COLONNES)
    derniere = ted.lettre_colonne(len(COLONNES))
    maj, nouvelles, nb_n, nb_m = [], [], 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne = ligne_depuis_resultat(r)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere), "values": [ligne]})
            nb_m += 1
        else:
            nouvelles.append(ligne + ["nouveau", date.today().isoformat()])
            nb_n += 1
    if maj:
        feuille.batch_update(maj)
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, ligne_depuis_resultat(r))) for r in resultats]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_n, nb_m


# ===========================================================================
# PARTIE 6 -- POINT D'ENTREE
# ===========================================================================

def _afficher_entonnoir(c):
    print("\n--- ENTONNOIR MIGA ---")
    print("  Pages liste parcourues     : {}".format(c["pages"]))
    print("  Liens projet vus           : {}".format(c["liens_vus"]))
    print("  Deja connus (sautes)       : {}".format(c["deja_connus"]))
    print("  Fiches lues                : {}".format(c["fiches_lues"]))
    print("  Fiches illisibles          : {}".format(c["fiches_illisibles"]))
    print("  Rejetes -- categorie {} : {}".format(
        "/".join(sorted(CATEGORIES_EXCLUES)), c["rejet_categorie_fi"]))
    print("  RETENUS (projets physiques): {}".format(c["retenus"]))


def main():
    if not ACTIVER:
        print("(info) Collecteur MIGA desactive (RADAR_MIGA=0).")
        return
    if not DEBUG and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente. Definis-la, ou lance en "
              "RADAR_MIGA_DEBUG=1 pour valider sans cout.")
        return

    deja_vus = set()
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not DEBUG:
        deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES) or set()

    print("Etape 1/2 -- Collecte MIGA (portail, {} page(s))...".format(MIGA_PAGES))
    avis, compteurs = collecte(deja_vus=deja_vus)
    _afficher_entonnoir(compteurs)

    if DEBUG:
        print("\n=== MODE DEBUG : aucun LLM, aucune ecriture ===")
        for i, a in enumerate(avis[:30], start=1):
            print("  {:2}. [{}|{}] {} | hote={} ({}) | invest={} ({})".format(
                i, a.get("categorie_es") or "?", a.get("type_document", "")[:12],
                a.get("titre", "")[:50], a.get("pays_execution", "")[:22],
                a.get("pays_iso3") or "?", a.get("acheteur", "")[:28],
                a.get("investisseur_pays", "")[:14]))
        print("\nRetire RADAR_MIGA_DEBUG pour lancer l'analyse reelle.")
        return

    if not avis:
        print("Aucun projet MIGA nouveau a analyser ce run.")
        return
    if len(avis) > MAX_AVIS_LLM:
        print("    (plafond {} : {} en attente).".format(MAX_AVIS_LLM, len(avis) - MAX_AVIS_LLM))
        avis = avis[:MAX_AVIS_LLM]

    print("\nEtape 2/2 -- Extraction LLM et score ({} projets, {})...\n".format(len(avis), ted.MODELE))
    resultats = []
    for i, a in enumerate(avis, start=1):
        print("[{}/{}] {}...".format(i, len(avis), a["titre"][:60]))
        extraction = analyser(a)
        s, c, f = ted.calculer_scores(avis_pour_scoring(a), extraction)
        resultats.append({"avis": a, "extraction": extraction, "final_haiku": f,
                          "surete": s, "commercial": c, "score": f,
                          "raffine": False, "divergence": False})
        time.sleep(0.4)

    def merite_escalade(r):
        if r["extraction"] is None:
            return False
        return (r["final_haiku"] >= 5 or r["extraction"].get("confiance", 1.0) < 0.7
                or ted.escalade_pour_securite(r["extraction"]))

    a_escalader = [r for r in resultats if merite_escalade(r)]
    if a_escalader:
        print("\n{} projet(s) escalade(s) vers {}...\n".format(len(a_escalader), ted.MODELE_RAFFINEMENT))
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
        print("\nEcriture dans l'onglet '{}' ({} projets)...".format(NOM_ONGLET, len(resultats)))
        try:
            feuille = ouvrir_feuille(sheet_id, fichier)
            nb_n, nb_m = ecrire_resultats(feuille, resultats)
            print("-> {} nouveau(x), {} mis a jour (statut_suivi jamais touche).".format(nb_n, nb_m))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("\n(Pas de Sheet configure : definis TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE.)")

    print("\n" + "=" * 70)
    nb_fort = sum(1 for r in resultats if r["score"] >= ted.SEUIL_ALERTE)
    nb_surv = sum(1 for r in resultats if ted.SEUIL_SURVEILLANCE <= r["score"] < ted.SEUIL_ALERTE)
    print("RESULTATS MIGA : {} FORT(S) | {} a surveiller | {} faible(s)".format(
        nb_fort, nb_surv, len(resultats) - nb_fort - nb_surv))
    print("=" * 70)
    for r in resultats:
        avis_r, extraction = r["avis"], r["extraction"]
        etiquette = ("[FORT]" if r["score"] >= ted.SEUIL_ALERTE
                     else "[A SURVEILLER]" if r["score"] >= ted.SEUIL_SURVEILLANCE else "[faible]")
        print("\n{} Score {:.1f}/10 (surete {:.1f} | commercial {:.1f}) [{}]".format(
            etiquette, r["score"], r["surete"], r["commercial"], avis_r.get("type_document", "")))
        print("  {}".format(avis_r["titre"][:90]))
        print("  Investisseur : {} ({}) -> {} | {}".format(
            avis_r.get("acheteur", "")[:40], avis_r.get("investisseur_pays", ""),
            avis_r.get("pays_execution", ""), avis_r.get("valeur_estimee", "")))
        if extraction:
            print("  Justification : {}".format(extraction.get("justification")))
        print("  Lien : {}".format(avis_r["lien_avis"]))


if __name__ == "__main__":
    main()
