# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- Collecteur IFC (Societe Financiere Internationale, Gpe BM).
=========================================================================

Pourquoi cette source
---------------------
L'IFC finance des investissements PRIVES dans les pays en developpement. Chaque
divulgation nomme l'entreprise/projet, le pays hote, le secteur, la categorie
E&S, les sponsors, le montant, et un texte descriptif riche. Les SPI (Summary
of Proposed Investment) sont publies AVANT le board = signal precoce. C'est la
meme veine que MIGA, en plus large (IFC = premier investisseur prive multilateral).

Source (craquee par sondes le 27/07/2026)
-----------------------------------------
Le portail disclosures.ifc.org est un SPA ; il interroge une API Azure Cognitive
Search via un proxy. Requete validee depuis le CI :

    POST https://webapi.worldbank.org/aemsite/ifc-disclosure-search
         ?search=&$orderby=Disclosed_Date desc
         &$filter=Disclosed_Date ge <cutoff>Z&$skip=<n>
    corps JSON : {}   (les filtres facettes iraient dans le corps ; vide ici)

Reponse : {"value": [ ... ]}, ~50 records/page, pagination $skip=50. Global,
recent-d'abord. Champs : Project_Number, Project_Name, Company_Name,
Country_Description, Environmental_Category_Description, Document_Type_Description,
Type_Description, Status_Description, Sector, Sponsor, Projected_Board_Date,
Estimated_Total_Budget, Project_Description (texte complet -> pas besoin de
fetcher la fiche). Doublons frequents -> dedup par Project_Number.

Architecture : reutilise le coeur TED (LLM, scoring, ecriture, memoire, miroir)
et le resolveur pays de MIGA. Ecriture durcie 503 des le depart (radar_resilience).

MODE DEBUG (RADAR_IFC_DEBUG=1) : collecte + parsing + cribles, AFFICHE l'entonnoir
et la fenetre de dates, SANS LLM ni ecriture. A utiliser au 1er passage.
"""

import os
import re
import time
from datetime import date, timedelta

import ted_complet_v14 as ted
import radar_resilience
import miga_radar as miga            # reutilise _iso3_pays (resolveur pays -> ISO3)


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================
ACTIVER = os.environ.get("RADAR_IFC", "1") != "0"
DEBUG = os.environ.get("RADAR_IFC_DEBUG", "0") == "1"

URL = "https://webapi.worldbank.org/aemsite/ifc-disclosure-search"
TIMEOUT = 45
NOM_ONGLET = "ifc_radar"

FENETRE_JOURS = int(os.environ.get("IFC_FENETRE_JOURS", "120"))   # divulgations recentes
IFC_PAGES = int(os.environ.get("IFC_PAGES", "8"))                 # 8 x 50 = 400 recents max
TAILLE_PAGE = 50
# Categories E&S a ECARTER (financier, sans empreinte terrain). "FI", "FI-1"...
CATEGORIES_EXCLUES = tuple(filter(None, (
    x.strip().upper() for x in os.environ.get("IFC_CATEGORIES_EXCLUES", "FI").split(","))))
# Ne garder que les pays du perimetre a risque (ceux que _iso3_pays sait resoudre) ?
FILTRER_PERIMETRE = os.environ.get("IFC_PERIMETRE", "1") != "0"
# Ecarter les projets "Advisory Services" (conseil IFC, pas d'investisseur qui
# deploie capitaux/equipes = hors ICP, comme les FI). Surchargeable.
EXCLURE_ADVISORY = os.environ.get("IFC_EXCLURE_ADVISORY", "1") != "0"
MAX_AVIS_LLM = int(os.environ.get("IFC_MAX_LLM", "60"))
PAUSE = float(os.environ.get("IFC_PAUSE", "0.2"))

ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://disclosures.ifc.org/",
    "Origin": "https://disclosures.ifc.org",
}

# Codes de type de document pour construire un lien fiche best-effort.
_CODE_TYPE = [
    ("environmental", "ESRS"), ("summary of proposed", "SPI"),
    ("summary of investment", "SII"), ("advisory", "AS"),
]


# ===========================================================================
# PARTIE 2 -- COLLECTE (API Azure Search, fenetre par date, pagination $skip)
# ===========================================================================

def _session():
    s = ted.session_robuste()
    s.headers.update(ENTETES)
    return s


def _cutoff_iso():
    return (date.today() - timedelta(days=FENETRE_JOURS)).isoformat() + "T00:00:00Z"


def _nettoyer(txte):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txte or "")).strip()
    if len(t) > ted.MAX_CARACTERES_DESCRIPTION:
        t = t[:ted.MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"
    return t


def _code_doc(rec):
    libel = (rec.get("Document_Type_Description") or rec.get("Type_Description") or "").lower()
    for motif, code in _CODE_TYPE:
        if motif in libel:
            return code
    return "SII"


def _lien(rec, num):
    slug = re.sub(r"[^a-z0-9]+", "-", (rec.get("Project_Name") or "").lower()).strip("-")[:60]
    return "https://disclosures.ifc.org/project-detail/{}/{}/{}".format(_code_doc(rec), num, slug or "projet")


def parser_record(rec):
    """Record Azure -> avis normalise (forme commune au coeur TED)."""
    num = str(rec.get("Project_Number") or rec.get("Project_Id") or "").strip()
    pays = (rec.get("Country_Description") or "").strip()
    iso3 = miga._iso3_pays(pays)
    cat = (rec.get("Environmental_Category_Description") or "").strip()
    company = (rec.get("Company_Name") or "").strip()
    sponsor = (rec.get("Sponsor") or "").strip()
    secteur = (rec.get("Sector") or rec.get("Industry_Description") or "").strip()
    type_doc = (rec.get("Document_Type_Description") or rec.get("Type_Description") or "").strip()
    board = str(rec.get("Projected_Board_Date") or "")[:10]
    disclosed = str(rec.get("Disclosed_Date") or "")[:10]
    budget = rec.get("Estimated_Total_Budget") or rec.get("Investment") or "inconnu"
    desc = _nettoyer(rec.get("Project_Description") or "")

    contexte = ("Source : projet finance par l'IFC (Groupe Banque Mondiale). "
                "Entreprise/projet : {}. Pays hote : {}. Secteur : {}. Categorie "
                "E&S : {}. Type : {}. Board prevu : {}. Sponsors : {}. {}").format(
        company or "n.c.", pays or "n.c.", secteur or "n.c.", cat or "n.c.",
        type_doc or "n.c.", board or "n.c.", sponsor or "n.c.", desc)

    return {
        "publication_number": "IFC:" + num,
        "titre": (rec.get("Project_Name") or num or "Projet IFC")[:300],
        "acheteur": company or "Projet IFC (entreprise non precisee)",
        "investisseur_pays": "",              # non fourni ; sponsors dans le texte
        "sponsor": sponsor,
        "pays_acheteur": iso3 or pays,
        "pays_execution": pays or iso3 or "n.c.",
        "pays_iso3": iso3,
        "pays_execution_incertitude": not bool(iso3),
        "cpv": "",
        "description": contexte,
        "categorie_es": cat,
        "type_document": type_doc,
        "secteur": secteur,
        "board_prevu": board,
        "date_publication": disclosed,
        "statut": (rec.get("Status_Description") or "").strip(),
        "deadline": "",
        "valeur_estimee": str(budget),
        "source_mode_b": False,
        "lien_avis": _lien(rec, num),
    }


def collecte(session=None, fetch_page=None, deja_vus=None):
    """Tire les divulgations recentes (fenetre FENETRE_JOURS), dedup par
    Project_Number, ecarte FI et (option) hors-perimetre. fetch_page(skip)->list
    de records ; injectable pour les tests. Renvoie (avis, compteurs)."""
    session = session or _session()
    deja_vus = deja_vus or set()
    cutoff = _cutoff_iso()

    def get_page(skip):
        if fetch_page is not None:
            return fetch_page(skip)
        params = {"search": "", "$orderby": "Disclosed_Date desc",
                  "$filter": "Disclosed_Date ge " + cutoff, "$skip": skip}
        def _appel():
            r = session.post(URL, params=params, json={}, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        charge = radar_resilience.avec_retry(_appel, description="POST recherche IFC")
        return charge.get("value") if isinstance(charge, dict) else (charge or [])

    compteurs = {"pages": 0, "records_vus": 0, "doublons": 0, "deja_connus": 0,
                 "rejet_fi": 0, "rejet_advisory": 0, "hors_perimetre": 0, "retenus": 0}
    avis, vus_run = [], set()

    for page in range(IFC_PAGES):
        try:
            records = get_page(page * TAILLE_PAGE)
        except Exception as e:
            print("  (info) page IFC skip={} indisponible : {}".format(page * TAILLE_PAGE, e))
            break
        if not records:
            break
        compteurs["pages"] += 1
        for rec in records:
            compteurs["records_vus"] += 1
            num = str(rec.get("Project_Number") or rec.get("Project_Id") or "").strip()
            if not num:
                continue
            pub = "IFC:" + num
            if pub in vus_run:
                compteurs["doublons"] += 1
                continue
            vus_run.add(pub)
            if pub in deja_vus:
                compteurs["deja_connus"] += 1
                continue
            a = parser_record(rec)
            if (a["categorie_es"] or "").upper().startswith(CATEGORIES_EXCLUES):
                compteurs["rejet_fi"] += 1
                continue
            if EXCLURE_ADVISORY and "advisory" in (rec.get("Type_Description") or "").lower():
                compteurs["rejet_advisory"] += 1
                continue
            if FILTRER_PERIMETRE and not a["pays_iso3"]:
                compteurs["hors_perimetre"] += 1
                continue
            avis.append(a)
            compteurs["retenus"] += 1
        if len(records) < TAILLE_PAGE:
            break                                     # derniere page
        if PAUSE and fetch_page is None:
            time.sleep(PAUSE)

    return avis, compteurs


def avis_pour_scoring(avis):
    copie = dict(avis)
    copie["pays_execution"] = avis.get("pays_iso3") or avis.get("pays_execution")
    return copie


def cible_commerciale(avis, extraction):
    ent = avis.get("acheteur") or "l'entreprise projet"
    pays = avis.get("pays_execution") or "le pays hote"
    return ("Investissement prive finance par l'IFC (entreprise projet : {}) en {}. "
            "Cible : l'entreprise projet et ses sponsors etrangers, qui deploient "
            "cadres et actifs sur zone a risque.").format(ent, pays)


# ===========================================================================
# PARTIE 3 -- PROMPT LLM (meme schema que TED/MIGA)
# ===========================================================================
PROMPT_IFC = """Tu es analyste sûreté pour une société française de protection de personnes en zones à risque (escorte, protection rapprochée CPO/CPD, chauffeur sécurité, véhicule sécurisé, sécurisation de déplacements terrain). Elle ne vend PAS de conseil voyage générique.

On te donne une DIVULGATION D'INVESTISSEMENT PRIVÉ financé par l'IFC (Groupe Banque Mondiale) dans un pays hôte à risque. L'IFC finance une entreprise projet privée ; des sponsors étrangers sont souvent impliqués. Détermine si le projet implique une présence PHYSIQUE de personnel (cadres expatriés, techniciens, superviseurs) sur le terrain, créant un besoin probable de prestations opérationnelles de sûreté.

RÈGLE DÉPLOIEMENT : un projet industriel, énergétique, minier, d'infrastructure, agro-industriel ou de construction implique des actifs et des équipes sur site (déploiement réel). Un projet purement financier (déjà largement écarté en amont) ou de conseil (Advisory Services) sans chantier n'expose pas de personnel.

RÈGLE MOBILITÉ TERRAIN : classe dans UNE catégorie : aucune | capitale | multi_sites | chantier | terrain_isole | frontiere.

RÈGLE SÉCURITÉ EXISTANTE : "securite_existante" = aucune | interne_client | prestataire_tiers | inconnu. "prestataire_tiers" n'est PAS un motif d'exclusion (opportunité de déplacement concurrentiel).

RÈGLE CLIENT : l'entreprise projet et ses sponsors sont des acteurs PRIVÉS, commercialement accessibles.

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
Entreprise/projet : {acheteur}
Pays hôte (exécution) : {pays_execution}
Type de document : {type_document}
Secteur : {secteur}
Contexte : {description}
"""


def analyser(avis, modele=None):
    import json
    prompt = PROMPT_IFC.format(
        acheteur=avis.get("acheteur", ""),
        pays_execution=avis.get("pays_execution", ""),
        type_document=avis.get("type_document", ""),
        secteur=avis.get("secteur", "") or "n.c.",
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
# PARTIE 4 -- SORTIE GOOGLE SHEET (ifc_radar) + miroir Postgres
# ===========================================================================
COLONNES = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "type_document", "categorie_es",
    "secteur", "board_prevu", "date_publication",
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
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_compte_service)
    try:
        feuille = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(title=NOM_ONGLET, rows=3000, cols=len(TOUTES_COLONNES))
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
        "pays_execution": avis.get("pays_execution", ""),
        "type_document": avis.get("type_document", ""),
        "categorie_es": avis.get("categorie_es", ""),
        "secteur": avis.get("secteur", ""),
        "board_prevu": avis.get("board_prevu", ""),
        "date_publication": avis.get("date_publication", ""),
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
        radar_resilience.avec_retry(lambda: feuille.batch_update(maj), "ecriture batch_update")
    if nouvelles:
        radar_resilience.avec_retry(
            lambda: feuille.append_rows(nouvelles, value_input_option="RAW"), "ecriture append_rows")
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, ligne_depuis_resultat(r))) for r in resultats]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_n, nb_m


# ===========================================================================
# PARTIE 5 -- POINT D'ENTREE
# ===========================================================================

def _afficher_entonnoir(c):
    print("\n--- ENTONNOIR IFC (fenetre {} j) ---".format(FENETRE_JOURS))
    print("  Pages tirees            : {}".format(c["pages"]))
    print("  Records vus             : {}".format(c["records_vus"]))
    print("  Doublons (memes projets): {}".format(c["doublons"]))
    print("  Deja connus (sautes)    : {}".format(c["deja_connus"]))
    print("  Rejetes -- categorie {} : {}".format("/".join(CATEGORIES_EXCLUES), c["rejet_fi"]))
    print("  Rejetes -- Advisory     : {}".format(c["rejet_advisory"]))
    print("  Hors perimetre (pays)   : {}".format(c["hors_perimetre"]))
    print("  RETENUS                 : {}".format(c["retenus"]))


def main():
    if not ACTIVER:
        print("(info) Collecteur IFC desactive (RADAR_IFC=0).")
        return
    if not DEBUG and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente. Definis-la, ou lance en "
              "RADAR_IFC_DEBUG=1 pour valider sans cout.")
        return

    deja_vus = set()
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not DEBUG:
        deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES) or set()

    print("Etape 1/2 -- Collecte IFC (API divulgations, fenetre {} j)...".format(FENETRE_JOURS))
    avis, compteurs = collecte(deja_vus=deja_vus)
    _afficher_entonnoir(compteurs)

    if DEBUG:
        print("\n=== MODE DEBUG : aucun LLM, aucune ecriture ===")
        for i, a in enumerate(avis[:30], start=1):
            print("  {:2}. [{}] {} | {} ({}) | {} | {}".format(
                i, (a.get("categorie_es") or "?")[:14], a.get("titre", "")[:42],
                a.get("pays_execution", "")[:18], a.get("pays_iso3") or "?",
                (a.get("type_document", "") or "")[:26], a.get("date_publication", "")))
        print("\nRetire RADAR_IFC_DEBUG pour lancer l'analyse reelle.")
        return

    if not avis:
        print("Aucune divulgation IFC nouvelle a analyser ce run.")
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
            print("(ifc) ecriture impossible ({}). Le run continue.".format(e))
    else:
        print("\n(Pas de Sheet configure : definis TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE.)")

    print("\n" + "=" * 70)
    nb_fort = sum(1 for r in resultats if r["score"] >= ted.SEUIL_ALERTE)
    nb_surv = sum(1 for r in resultats if ted.SEUIL_SURVEILLANCE <= r["score"] < ted.SEUIL_ALERTE)
    print("RESULTATS IFC : {} FORT(S) | {} a surveiller | {} faible(s)".format(
        nb_fort, nb_surv, len(resultats) - nb_fort - nb_surv))
    print("=" * 70)
    for r in resultats:
        avis_r, extraction = r["avis"], r["extraction"]
        etiquette = ("[FORT]" if r["score"] >= ted.SEUIL_ALERTE
                     else "[A SURVEILLER]" if r["score"] >= ted.SEUIL_SURVEILLANCE else "[faible]")
        print("\n{} Score {:.1f}/10 (surete {:.1f} | commercial {:.1f}) [{}]".format(
            etiquette, r["score"], r["surete"], r["commercial"], avis_r.get("type_document", "")[:20]))
        print("  {}".format(avis_r["titre"][:90]))
        print("  {} -> {} | {}".format(
            avis_r.get("acheteur", "")[:40], avis_r.get("pays_execution", ""),
            avis_r.get("valeur_estimee", "")))
        if extraction:
            print("  Justification : {}".format(extraction.get("justification")))
        print("  Lien : {}".format(avis_r["lien_avis"]))


if __name__ == "__main__":
    main()
