# -*- coding: utf-8 -*-
"""
Radar Amarante - Collecteur BOAMP (Phase 1, #3).

Marches publics francais (Bulletin officiel des annonces des marches publics),
via l'API Opendatasoft de la DILA (gratuite, sans cle, licence ouverte 2.0).
Complement national de TED.

Reutilise INTEGRALEMENT le moteur du collecteur Banque Mondiale
(ted_complet_bm) : extraction LLM sûrete, scoring, escalade Sonnet, schema
Sheet, ecriture, memoire inter-runs. Seules la collecte et la normalisation
sont propres a BOAMP. Ecrit dans l'onglet 'boamp_radar'.

Note de cadrage : BOAMP est majoritairement du marche public DOMESTIQUE
(gardiennage), hors creneau Amarante. Le croisement avec la carte de risque
ecarte automatiquement la France : la source s'auto-filtre et ne remonte que
les rares avis explicitement lies a une zone a risque. Faible rendement assume.
"""

import os
import time
import datetime

import ted_complet_v14 as ted
import ted_complet_bm as bm

# ===========================================================================
# CONFIGURATION
# ===========================================================================
BOAMP_ENDPOINT = ("https://boamp-datadila.opendatasoft.com/api/explore/v2.1/"
                  "catalog/datasets/boamp/records")
LIEN_BOAMP = "https://www.boamp.fr/avis/detail/{}"
NOM_ONGLET_BOAMP = "boamp_radar"
NB_JOURS_FENETRE = 30
MAX_AVIS_LLM = 25
# Interrupteur : RADAR_BOAMP=0 desactive completement le collecteur.
ACTIVER_BOAMP = os.environ.get("RADAR_BOAMP", "1") != "0"
# Seuil d'ecriture : en dessous, l'avis n'est PAS ecrit (evite de polluer le
# Sheet avec du domestique francais a bas score). Le croisement risque ne
# suffit pas : c'est ce seuil qui filtre reellement a l'ecriture.
SEUIL_ECRITURE_BOAMP = float(os.environ.get("RADAR_BOAMP_SEUIL", "3.0"))

# Termes FORTS only : la protection rapprochee / l'escorte de personnes ne se
# confondent pas avec le gardiennage communal. On a retire "sûreté",
# "gardiennage", "agents de sécurité" qui ramenaient du bruit domestique.
TERMES_SURETE = ['"protection rapprochée"', '"escorte"',
                 '"protection de personnalités"', '"protection rapprochee"',
                 '"garde du corps"', '"sûreté des personnes"',
                 '"sécurité des déplacements"']
# Exclusions : si le titre releve clairement du domestique, on jette avant LLM.
TERMES_EXCLUSION = ("fourniture", "mobilier", "papeter", "entretien des locaux",
                    "nettoyage", "voirie", "réhabilitation", "auvent", "sas ",
                    "signalétique", "portage salarial", "restauration scolaire",
                    "espaces verts", "gardiennage", "surveillance des")


# ===========================================================================
# COLLECTE (API Opendatasoft v2.1)
# ===========================================================================
def collecte_boamp(fetch=None, session=None):
    """Renvoie les avis BOAMP recents et pertinents (liste de dicts bruts).
    `fetch` injectable pour tests : callable(url, params) -> dict JSON."""
    where = "(" + " OR ".join(TERMES_SURETE) + ")"
    params = {"where": where, "order_by": "dateparution desc",
              "limit": 100, "lang": "fr"}
    donnees = _get_json(BOAMP_ENDPOINT, params, fetch=fetch, session=session)
    if not donnees:
        return []
    resultats = donnees.get("results") or []
    return [r for r in resultats if _recent(r)]


def _recent(rec, aujourd=None):
    d = _premier(rec, "dateparution", "date_parution")
    if not d:
        return True
    try:
        dt = datetime.date.fromisoformat(str(d)[:10])
    except ValueError:
        return True
    ref = aujourd or datetime.date.today()
    return (ref - dt).days <= NB_JOURS_FENETRE


def _get_json(url, params, fetch=None, session=None):
    if fetch is not None:
        try:
            return fetch(url, params)
        except Exception:
            return None
    session = session or ted.session_robuste()
    try:
        rep = session.get(url, params=params, timeout=30)
        rep.raise_for_status()
        return rep.json()
    except Exception as e:
        print("  (info) API BOAMP indisponible ({}).".format(e))
        return None


# ===========================================================================
# NORMALISATION (vers la structure d'avis attendue par le moteur BM)
# ===========================================================================
def _premier(rec, *cles):
    """Premiere valeur non vide parmi plusieurs noms de champs possibles."""
    for c in cles:
        v = rec.get(c)
        if isinstance(v, dict):
            v = v.get("libelle") or v.get("valeur") or ""
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v if x)
        if v not in (None, "", []):
            return str(v).strip()
    return ""


def normaliser_boamp(rec):
    """Construit un avis compatible avec le moteur BM (ligne_depuis_resultat_bm,
    avis_pour_scoring). BOAMP = marches francais : pays = France par defaut,
    donc auto-filtre par la carte de risque sauf mention explicite d'une zone."""
    idweb = _premier(rec, "idweb", "id")
    objet = _premier(rec, "objet", "OBJET", "objet_complet", "titre_marche")
    acheteur = _premier(rec, "nomacheteur", "acheteur", "nom_acheteur",
                        "denomination", "organisme")
    descripteurs = _premier(rec, "descripteur_libelle", "descripteurs")
    description = ted._nettoyer_html(" ".join([objet, descripteurs]).strip())
    if len(description) > ted.MAX_CARACTERES_DESCRIPTION:
        description = description[:ted.MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"

    return {
        "publication_number": idweb,
        "titre": objet[:300],
        "acheteur": acheteur,
        "pays_acheteur": "FR",
        "pays_execution": "France",
        "pays_iso3": "FRA",
        "pays_execution_incertitude": False,
        "cpv": _premier(rec, "code_cpv", "cpv"),
        "description": description,
        "deadline": _premier(rec, "datelimitereponse", "date_limite_reponse"),
        "date_publication": _premier(rec, "dateparution", "date_parution"),
        "valeur_estimee": "inconnu",
        "source_mode_b": False,
        "lien_avis": LIEN_BOAMP.format(idweb) if idweb else "",
        # Champs BM (Sheet) : BOAMP n'a pas ces notions -> vides.
        "procurement_group": "",
        "procurement_method": _premier(rec, "procedure_categorise", "procedure_libelle"),
        "contact_organization": acheteur,
        "contact_name": "",
        "contact_email": _premier(rec, "email", "courriel"),
        "contact_phone": "",
    }


# ===========================================================================
# ECRITURE (onglet boamp_radar, schema BM reutilise)
# ===========================================================================
def ouvrir_feuille_boamp(sheet_id, fichier_compte_service):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_compte_service, scopes=portee)
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        return classeur.worksheet(NOM_ONGLET_BOAMP)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET_BOAMP, rows=1000,
                                   cols=len(bm.TOUTES_COLONNES_BM))
        f.update([bm.TOUTES_COLONNES_BM])
        return f


# ===========================================================================
# ORCHESTRATION (mirror de bm.main, moteur reutilise)
# ===========================================================================
def pertinent(avis):
    """True si l'avis releve vraiment de la protection de personnes (termes forts),
    et pas du domestique. Filtre applique AVANT tout appel LLM (economie + anti-bruit)."""
    texte = ted._nettoyer_html((avis.get("titre", "") + " " + avis.get("description", ""))).lower()
    if any(x in texte for x in TERMES_EXCLUSION):
        return False
    forts = ("protection rapproch", "escorte", "protection de personnalit",
             "garde du corps", "sûreté des personnes", "surete des personnes",
             "sécurité des déplacement", "securite des deplacement", "close protection")
    return any(f in texte for f in forts)


def _merite_escalade(r):
    if r["extraction"] is None:
        return False
    if r["final_haiku"] >= 5:
        return True
    if r["extraction"].get("confiance", 1.0) < 0.7:
        return True
    if r["extraction"].get("securite_existante_detectee"):
        return True
    return False


def main():
    if not ACTIVER_BOAMP:
        print("BOAMP desactive (RADAR_BOAMP=0).")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY n'est pas definie.")
        return
    print("=" * 60)
    print("COLLECTEUR BOAMP - Radar Amarante")
    print("=" * 60)

    bruts = collecte_boamp()
    print("BOAMP -- avis recuperes : {}".format(len(bruts)))
    if not bruts:
        print("Aucun avis BOAMP. Rien a faire.")
        return

    # Dedup par idweb + normalisation.
    vus, uniques = set(), []
    for r in bruts:
        idw = _premier(r, "idweb", "id")
        if idw and idw not in vus:
            vus.add(idw)
            uniques.append(r)
    avis_normalises = [a for a in (normaliser_boamp(r) for r in uniques) if a["titre"]]
    # Filtre de pertinence (termes forts, hors domestique) AVANT le LLM.
    avant_filtre = len(avis_normalises)
    avis_normalises = [a for a in avis_normalises if pertinent(a)]
    print("Filtre pertinence : {} rejetes (domestique/hors sujet), {} retenus.".format(
        avant_filtre - len(avis_normalises), len(avis_normalises)))
    if len(avis_normalises) > MAX_AVIS_LLM:
        avis_normalises = avis_normalises[:MAX_AVIS_LLM]
    if not avis_normalises:
        print("Aucun avis pertinent pour la protection de personnes.")
        return

    # Memoire inter-runs (schema BM, onglet BOAMP).
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    deja_vus = ted.numeros_publication_existants(
        sheet_id, fichier, NOM_ONGLET_BOAMP, bm.COLONNES_BM)
    if deja_vus:
        avant = len(avis_normalises)
        avis_normalises = [a for a in avis_normalises
                           if str(a.get("publication_number", "")).strip() not in deja_vus]
        print("Memoire : {} avis deja analyses ignores, {} nouveau(x).".format(
            avant - len(avis_normalises), len(avis_normalises)))
    if not avis_normalises:
        print("Aucun NOUVEL avis BOAMP a analyser (tout deja vu).")
        return

    print("\nExtraction LLM ({} avis, modele {})...\n".format(
        len(avis_normalises), ted.MODELE))
    resultats = []
    for i, avis in enumerate(avis_normalises, start=1):
        print("[{}/{}] {}...".format(i, len(avis_normalises), avis["titre"][:60]))
        extraction = ted.appeler_llm(avis)
        s, c, f = ted.calculer_scores(bm.avis_pour_scoring(avis, extraction), extraction)
        resultats.append({"avis": avis, "extraction": extraction,
                          "surete_haiku": s, "commercial_haiku": c, "final_haiku": f,
                          "surete": s, "commercial": c, "score": f,
                          "raffine": False, "divergence": False})
        time.sleep(0.5)

    a_escalader = [r for r in resultats if _merite_escalade(r)]
    if a_escalader:
        print("\n{} avis escalades vers {}...\n".format(
            len(a_escalader), ted.MODELE_RAFFINEMENT))
        for r in a_escalader:
            raffinee = ted.appeler_llm(r["avis"], modele=ted.MODELE_RAFFINEMENT)
            if raffinee is not None:
                s, c, f = ted.calculer_scores(bm.avis_pour_scoring(r["avis"], raffinee), raffinee)
                r["extraction"], r["surete"], r["commercial"], r["score"] = raffinee, s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0
            time.sleep(0.5)

    resultats.sort(key=lambda r: r["score"], reverse=True)
    # Seuil d'ecriture : on n'ecrit PAS le domestique a bas score.
    avant_seuil = len(resultats)
    resultats = [r for r in resultats if r["score"] >= SEUIL_ECRITURE_BOAMP]
    print("Seuil d'ecriture ({}) : {} avis ecartes, {} conserves.".format(
        SEUIL_ECRITURE_BOAMP, avant_seuil - len(resultats), len(resultats)))
    if not resultats:
        print("Aucun avis BOAMP au-dessus du seuil. Rien a ecrire.")
        return

    if not (sheet_id and fichier):
        print("(dry-run) {} avis analyses (pas de Sheet, non ecrit).".format(len(resultats)))
        return
    feuille = ouvrir_feuille_boamp(sheet_id, fichier)
    nouveaux, maj = bm.ecrire_resultats_bm(feuille, resultats)
    print("\nBOAMP -- {} nouveaux, {} mis a jour dans '{}'.".format(
        nouveaux, maj, NOM_ONGLET_BOAMP))


if __name__ == "__main__":
    main()
