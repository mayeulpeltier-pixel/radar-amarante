# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur Proparco (DFI francais, groupe AFD).
=================================================================

POURQUOI CETTE SOURCE
---------------------
Proparco (filiale secteur prive de l'AFD) finance des ENTREPRISES PRIVEES en
Afrique, Asie, Amerique latine, Moyen-Orient. Chaque projet NOMME le client
prive (`nom_du_client`), son pays, son secteur, son montant, avec des textes
riches (resume, description, impact, presentation du client). Un client prive
etranger qui deploie capital et equipes en zone a risque = coeur de cible
Amarante. Francais de surcroit : fit naturel avec le reseau commercial.

Meme DOCTRINE que IFC/MIGA (DFI, sponsor prive nomme, onglet dedie, LLM) :
  - Filtre PERIMETRE pays (on ne garde que les zones a risque couvertes).
  - Filtre FI : on ECARTE les clients de type "Institution financiere"
    (microcredit, fonds, banque) -- pas de deploiement terrain, exactement
    comme le crible FI de MIGA/IFC.
  - Scoring LLM (Haiku volume + escalade Sonnet) via le coeur TED.

VOIE D'ACCES VERIFIEE (sonde_proparco.py, sortie reelle)
--------------------------------------------------------
API Opendatasoft v2, ouverte, sans cle, FILTRAGE SERVEUR :
  https://opendata.afd.fr/api/explore/v2.1/catalog/datasets/
      donnees-de-laide-au-developpement-de-proparco/records
899 projets au total (mise a jour mensuelle). Volume faible => on pagine tout
(limit=100) et on filtre COTE CLIENT : pas de firehose, robuste.

Champs confirmes : nom_du_client, nature_du_client, pays_de_realisation,
pays_du_siege_social, secteur_s_concerne_s_par_le_projet,
montant_du_financement_en_euro, resume_du_projet, description_du_projet,
presentation_du_client, impact_du_projet, date_de_signature,
etat_en_cours_ou_cloture, ces (categorie E&S), id_concours/id_projet,
lien_vers_la_fiche_projet.

POSTURE : nouvelle source => RADAR_PROPARCO_DEBUG=1 valide l'entonnoir SANS LLM
ni ecriture (motif IsDB/IDB). Isole : un echec ici n'affecte rien d'autre.
Sortie en code 0 hors erreur de programmation.

    RADAR_PROPARCO_DEBUG=1 python proparco_radar.py
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta

import ted_complet_v14 as ted
import radar_resilience

# ===========================================================================
# CONFIGURATION
# ===========================================================================
BASE = os.environ.get("PROPARCO_BASE", "https://opendata.afd.fr")
DATASET = "donnees-de-laide-au-developpement-de-proparco"
URL_RECORDS = BASE + "/api/explore/v2.1/catalog/datasets/{}/records".format(DATASET)

NOM_ONGLET = "proparco_radar"
ACTIVER = os.environ.get("RADAR_PROPARCO", "1") != "0"
DEBUG = os.environ.get("RADAR_PROPARCO_DEBUG", "0") == "1"
MAX_AVIS_LLM = int(os.environ.get("PROPARCO_MAX_LLM", "60"))
FENETRE_JOURS = int(os.environ.get("PROPARCO_JOURS", "730"))     # signatures recentes (2 ans)
PAGE = 100                                                       # max Opendatasoft v2
PAGES_MAX = int(os.environ.get("PROPARCO_PAGES_MAX", "40"))
TIMEOUT = 45
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ENTETES = {"User-Agent": UA, "Accept": "application/json, */*"}

# Clients SANS deploiement terrain : intermediaires financiers (meme crible FI
# que MIGA/IFC). On les ecarte sur `nature_du_client`.
MOTS_FI = ("financiere", "financier", "financial", "fonds", "fund", "banque",
           "bank", "microfinance", "assurance", "insurance")


def _sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s or ""))
                   if unicodedata.category(c) != "Mn")


def _norm(s):
    return re.sub(r"\s+", " ", _sans_accents(s).lower()).strip()


# ---------------------------------------------------------------------------
# MAPPING NOM DE PAYS (francais) -> ISO3, reutilise les dicts du coeur TED.
# ---------------------------------------------------------------------------
def _construire_carte_pays():
    carte = {}
    for d in (ted.PAYS_ROUGE, ted.PAYS_ORANGE, ted.AFRIQUE, ted.MOYEN_ORIENT,
              ted.AMERIQUE_DU_SUD, ted.EUROPE_EST_CAUCASE_ASIE_CENTRALE,
              ted.ASIE_A_RISQUE, ted.ILES_A_RISQUE,
              ted.TERRITOIRES_FRANCAIS_OUTRE_MER_A_RISQUE):
        for nom, iso3 in d.items():
            carte[_norm(nom)] = iso3
    # Alias pour les libelles Proparco qui different des dicts TED (abreviations,
    # noms longs). A completer si le DEBUG revele d'autres non-mappes.
    alias = {
        "republique democratique du congo": "COD", "rd congo": "COD",
        "congo (rdc)": "COD", "congo, republique democratique": "COD",
        "republique du congo": "COG", "congo-brazzaville": "COG",
        "cote d ivoire": "CIV",
        "republique centrafricaine": "CAF", "centrafrique": "CAF",
        "guinee equatoriale": "GNQ", "guinee-bissau": "GNB",
        "tanzanie": "TZA", "republique unie de tanzanie": "TZA",
        "eswatini": "SWZ", "cap vert": "CPV", "cap-vert": "CPV",
        "territoires palestiniens": "PSE", "palestine": "PSE",
        "birmanie": "MMR", "myanmar (birmanie)": "MMR",
        "republique dominicaine": "DOM",
    }
    carte.update(alias)
    return carte


CARTE_PAYS = _construire_carte_pays()


def iso3_depuis_nom(nom_pays):
    return CARTE_PAYS.get(_norm(nom_pays), "")


def est_client_financier(nature):
    n = _norm(nature)
    return any(m in n for m in MOTS_FI)


# ===========================================================================
# COLLECTE (API Opendatasoft, pagination, filtres ; fetch injectable)
# ===========================================================================
def lire_page(offset, fetch=None):
    """Une page de l'API Opendatasoft v2. `fetch` injecte pour les tests."""
    params = {"limit": PAGE, "offset": offset, "order_by": "date_de_signature DESC"}
    if fetch is not None:
        charge = fetch(params)
    else:
        r = ted.session_robuste().get(URL_RECORDS, params=params,
                                      headers=ENTETES, timeout=TIMEOUT)
        r.raise_for_status()
        charge = r.json()
    charge = charge or {}
    return (charge.get("results") or []), charge.get("total_count")


def _texte_riche(rec):
    morceaux = [rec.get("resume_du_projet"), rec.get("description_du_projet"),
                rec.get("presentation_du_client"), rec.get("impact_du_projet")]
    texte = " | ".join(m for m in morceaux if m)
    texte = re.sub(r"\s+", " ", texte).strip()
    if len(texte) > ted.MAX_CARACTERES_DESCRIPTION:
        texte = texte[:ted.MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"
    return texte


def _dans_fenetre(rec, aujourd_hui):
    d = (rec.get("date_de_signature") or rec.get("date_d_octroi") or "")[:10]
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    if not m:
        return True, d                       # date absente : on ne rejette pas
    try:
        signee = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return True, d
    return (aujourd_hui - signee).days <= FENETRE_JOURS, d


def rec_vers_avis(rec):
    pays_nom = rec.get("pays_de_realisation") or rec.get("pays_du_siege_social") or ""
    iso3 = iso3_depuis_nom(pays_nom)
    client = (rec.get("nom_du_client") or "").strip()
    montant = rec.get("montant_du_financement_en_euro")
    ident = (rec.get("id_concours") or rec.get("id_projet") or "").strip()
    return {
        "publication_number": ident,
        "titre": (rec.get("titre_court_du_projet") or rec.get("titre_du_projet")
                  or client or "Projet Proparco")[:300],
        "acheteur": client,
        "pays_execution": pays_nom,
        "pays_iso3": iso3,
        "nature_client": rec.get("nature_du_client") or "",
        "secteur": rec.get("secteur_s_concerne_s_par_le_projet") or "",
        "categorie_es": rec.get("ces") or "",
        "statut": rec.get("etat_en_cours_ou_cloture") or "",
        "valeur_estimee": ("{:,.0f} EUR".format(float(montant)).replace(",", " ")
                           if montant not in (None, "") else ""),
        "date_publication": (rec.get("date_de_signature") or "")[:10],
        "type_document": "Projet finance (Proparco)",
        "description": _texte_riche(rec),
        "lien_avis": rec.get("lien_vers_la_fiche_projet") or "",
    }


def collecte(deja_vus=None, fetch=None, aujourd_hui=None):
    """Pagine le dataset, filtre (perimetre pays + FI + fenetre + memoire),
    renvoie (avis_list, compteurs). Volume faible => on lit tout."""
    deja_vus = deja_vus or set()
    aujourd_hui = aujourd_hui or date.today()
    c = {"records_vus": 0, "hors_perimetre": 0, "rejet_fi": 0, "hors_fenetre": 0,
         "deja_connus": 0, "sans_id": 0, "retenus": 0, "pays_non_mappes": {}}
    avis, offset, total = [], 0, None
    for _ in range(PAGES_MAX):
        try:
            recs, total = lire_page(offset, fetch=fetch)
        except Exception as e:
            print("  (info) lecture page Proparco interrompue : {}".format(str(e)[:80]))
            break
        if not recs:
            break
        for rec in recs:
            c["records_vus"] += 1
            pays_nom = rec.get("pays_de_realisation") or rec.get("pays_du_siege_social") or ""
            iso3 = iso3_depuis_nom(pays_nom)
            if not iso3 or not ted.dans_le_perimetre(iso3):
                c["hors_perimetre"] += 1
                if pays_nom and not iso3:
                    c["pays_non_mappes"][pays_nom] = c["pays_non_mappes"].get(pays_nom, 0) + 1
                continue
            if est_client_financier(rec.get("nature_du_client")):
                c["rejet_fi"] += 1
                continue
            dans, _d = _dans_fenetre(rec, aujourd_hui)
            if not dans:
                c["hors_fenetre"] += 1
                continue
            a = rec_vers_avis(rec)
            if not a["publication_number"]:
                c["sans_id"] += 1
                continue
            if a["publication_number"] in deja_vus:
                c["deja_connus"] += 1
                continue
            avis.append(a)
        offset += PAGE
        if total is not None and offset >= total:
            break
        time.sleep(0.2)
    c["retenus"] = len(avis)
    c["total"] = total
    # Plus gros montants d'abord : le budget LLM va aux plus gros deploiements.
    def _montant(a):
        m = re.sub(r"[^\d]", "", a.get("valeur_estimee") or "")
        return int(m) if m else 0
    avis.sort(key=_montant, reverse=True)
    return avis, c


# ===========================================================================
# SCORING (coeur TED) + PROMPT
# ===========================================================================
def avis_pour_scoring(avis):
    copie = dict(avis)
    copie["pays_execution"] = avis.get("pays_iso3") or avis.get("pays_execution")
    return copie


def cible_commerciale(avis, extraction):
    ent = avis.get("acheteur") or "l'entreprise financee"
    pays = avis.get("pays_execution") or "le pays hote"
    return ("Entreprise privee financee par Proparco (groupe AFD) : {} en {}. "
            "Cible : le client et ses equipes deployees sur zone a risque. "
            "Bailleur francais, souvent plus accessible qu'un marche multilateral."
            ).format(ent, pays)


PROMPT_PROPARCO = """Tu es analyste sûreté pour une société française de protection de personnes en zones à risque (escorte, protection rapprochée CPO/CPD, chauffeur sécurité, véhicule sécurisé, sécurisation de déplacements terrain). Elle ne vend PAS de conseil voyage générique.

On te donne un PROJET D'INVESTISSEMENT PRIVÉ financé par Proparco (filiale secteur privé de l'AFD) dans un pays hôte à risque. Le client est une entreprise privée qui déploie souvent cadres, techniciens et actifs sur le terrain. Détermine si le projet implique une présence PHYSIQUE de personnel sur zone, créant un besoin probable de prestations opérationnelles de sûreté.

RÈGLE DÉPLOIEMENT : un projet industriel, énergétique, minier, d'infrastructure, agro-industriel ou de construction implique des actifs et des équipes sur site (déploiement réel). Un projet purement financier (intermédiation, microcrédit, fonds) sans chantier n'expose pas de personnel.

RÈGLE MOBILITÉ TERRAIN : classe dans UNE catégorie : aucune | capitale | multi_sites | chantier | terrain_isole | frontiere.

RÈGLE SÉCURITÉ EXISTANTE : "securite_existante" = aucune | interne_client | prestataire_tiers | inconnu. "prestataire_tiers" n'est PAS un motif d'exclusion (opportunité de déplacement concurrentiel).

RÈGLE CLIENT : le client est un acteur PRIVÉ, commercialement accessible. Proparco est un bailleur bilatéral français, souvent plus accessible qu'un marché ONU/UE.

RÈGLE PROFILS : ne cite JAMAIS d'entreprise réelle en plus de celle donnée, décris des PROFILS d'acteur.

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
Client (entreprise) : {acheteur}
Nature du client : {nature_client}
Pays hôte (exécution) : {pays_execution}
Secteur : {secteur}
Contexte : {description}
"""


def analyser(avis, modele=None):
    prompt = PROMPT_PROPARCO.format(
        acheteur=avis.get("acheteur", ""),
        nature_client=avis.get("nature_client", "") or "n.c.",
        pays_execution=avis.get("pays_execution", ""),
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
# SORTIE (onglet proparco_radar) + miroir Postgres
# ===========================================================================
COLONNES = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "nature_client", "secteur",
    "categorie_es", "statut", "date_publication",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance", "modele", "raffine", "divergence",
    "valeur_estimee", "publication_number", "lien_avis",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_cs)
    try:
        return classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000, cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f


def ligne_depuis_resultat(r):
    avis, e = r["avis"], (r["extraction"] or {})
    modele = ted.MODELE_RAFFINEMENT if r["raffine"] else ted.MODELE
    v = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"], "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.calculer_action_recommandee(
            r["score"], r["extraction"], surete=r["surete"]),
        "fenetre_action": ted.calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": e.get("niveau_opportunite_amarante", ""),
        "titre": avis.get("titre", ""), "acheteur": avis.get("acheteur", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "nature_client": avis.get("nature_client", ""),
        "secteur": avis.get("secteur", ""),
        "categorie_es": avis.get("categorie_es", ""),
        "statut": avis.get("statut", ""),
        "date_publication": avis.get("date_publication", ""),
        "type_client": e.get("type_client", ""),
        "type_mobilite": e.get("type_mobilite", ""),
        "profil_personnes_exposees": e.get("profil_personnes_exposees", ""),
        "duree_estimee": e.get("duree_estimee", ""),
        "accessibilite_commerciale": e.get("accessibilite_commerciale", ""),
        "securite_existante_detectee": e.get("securite_existante_detectee", ""),
        "profils_acteurs_probables": ", ".join(e.get("profils_acteurs_probables") or []),
        "cible_commerciale_reelle": cible_commerciale(avis, r["extraction"]),
        "justification": e.get("justification", ""),
        "confiance": e.get("confiance", ""),
        "modele": modele, "raffine": r["raffine"], "divergence": r["divergence"],
        "valeur_estimee": avis.get("valeur_estimee", ""),
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
# POINT D'ENTREE
# ===========================================================================
def _afficher_entonnoir(c):
    print("\n--- ENTONNOIR PROPARCO (fenetre {} j) ---".format(FENETRE_JOURS))
    print("  Total dataset          : {}".format(c.get("total")))
    print("  Records vus            : {}".format(c["records_vus"]))
    print("  Hors perimetre (pays)  : {}".format(c["hors_perimetre"]))
    print("  Rejetes -- client FI   : {}".format(c["rejet_fi"]))
    print("  Hors fenetre           : {}".format(c["hors_fenetre"]))
    print("  Deja connus (sautes)   : {}".format(c["deja_connus"]))
    print("  Sans identifiant       : {}".format(c["sans_id"]))
    print("  RETENUS                : {}".format(c["retenus"]))
    if c.get("pays_non_mappes"):
        print("  /!\\ pays NON mappes (a ajouter en alias) :")
        for nom, n in sorted(c["pays_non_mappes"].items(), key=lambda x: -x[1])[:15]:
            print("      {:6}x  {}".format(n, nom))


def main():
    if not ACTIVER:
        print("(info) Collecteur Proparco desactive (RADAR_PROPARCO=0).")
        return
    if not DEBUG and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente (ou lance RADAR_PROPARCO_DEBUG=1).")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    deja_vus = set()
    if not DEBUG and sheet_id and fichier:
        deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES) or set()

    print("Etape 1/2 -- Collecte Proparco (API Opendatasoft)...")
    try:
        avis, compteurs = collecte(deja_vus=deja_vus)
    except Exception as e:
        print("ERREUR : collecte Proparco impossible ({}).".format(str(e)[:200]))
        print("(info) Les autres collecteurs et le dashboard ne sont pas affectes.")
        return
    _afficher_entonnoir(compteurs)

    if DEBUG:
        print("\n=== MODE DEBUG : aucun LLM, aucune ecriture ===")
        for i, a in enumerate(avis[:30], start=1):
            print("  {:2}. {} | {} ({}) | {} | {}".format(
                i, (a.get("acheteur") or "?")[:32], (a.get("pays_execution") or "?")[:16],
                a.get("pays_iso3") or "?", (a.get("secteur") or "")[:24],
                a.get("valeur_estimee") or "montant n.c."))
        print("\nRetire RADAR_PROPARCO_DEBUG pour lancer l'analyse reelle.")
        return

    if not avis:
        print("Aucun projet Proparco nouveau a analyser ce run.")
        return
    if len(avis) > MAX_AVIS_LLM:
        print("    (plafond {} : {} en attente).".format(MAX_AVIS_LLM, len(avis) - MAX_AVIS_LLM))
        avis = avis[:MAX_AVIS_LLM]

    print("\nEtape 2/2 -- Extraction LLM et score ({} projets, {})...\n".format(len(avis), ted.MODELE))
    resultats = []
    for i, a in enumerate(avis, start=1):
        arret = ted.sortie_selon_sante_llm("proparco")
        if arret:
            print("  " + arret)
            break
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
            print("(proparco) ecriture impossible ({}). Le run continue.".format(e))
    else:
        print("\n(Pas de Sheet : {} projets analyses, affichage seul.)".format(len(resultats)))
        for r in resultats[:15]:
            print("  {:4} | {:28} | {} | {}".format(
                r["score"], (r["avis"].get("acheteur") or "?")[:28],
                (r["avis"].get("pays_execution") or "?")[:16], r["avis"].get("valeur_estimee") or ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Collecteur Proparco interrompu : {}".format(e))
    sys.exit(0)
