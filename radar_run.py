# -*- coding: utf-8 -*-
"""
Radar Amarante -- Master d'orchestration
=========================================

Lance les collecteurs en sequence en un seul appel. Point d'entree unique
pour la routine automatisee (GitHub Actions) comme pour un lancement manuel.

PRINCIPE DE CONCEPTION : ce script n'installe RIEN et ne configure aucun
secret. Il suppose que l'environnement a deja ete prepare AVANT son
lancement :
  - ANTHROPIC_API_KEY            (obligatoire)
  - TED_SHEET_ID                 (pour ecrire dans le Google Sheet ; sinon console)
  - GOOGLE_SERVICE_ACCOUNT_FILE  (idem)
  - RADAR_LOG_FILE               (optionnel ; chemin du journal, defaut "radar.log")

CHAQUE collecteur optionnel est ISOLE : s'il manque ou echoue, les autres
tournent quand meme (on ne perd pas une source a cause d'une autre).

LANCEMENT :
    python radar_run.py
"""

import logging
import os
import sys
import time
import traceback
from datetime import datetime


# ===========================================================================
# CONTRAT D'INTERFACE : ce que le collecteur BM emprunte au coeur TED.
# ===========================================================================
SYMBOLES_REQUIS_TED = [
    "MAX_CARACTERES_DESCRIPTION", "MODELE", "MODELE_RAFFINEMENT",
    "MULTIPLICATEUR_ZONE", "SEUIL_ALERTE", "SEUIL_SURVEILLANCE",
    "_nettoyer_html", "appeler_llm", "calculer_action_recommandee",
    "calculer_fenetre_action", "calculer_scores", "charger_index_publication",
    "lettre_colonne", "session_robuste",
]

logger = logging.getLogger("radar")


def configurer_logging():
    """Journalisation : console + fichier horodate (mode ajout). Idempotent."""
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)
    chemin_log = os.environ.get("RADAR_LOG_FILE", "radar.log")
    try:
        fichier = logging.FileHandler(chemin_log, mode="a", encoding="utf-8")
        fichier.setFormatter(fmt)
        logger.addHandler(fichier)
    except OSError as e:
        logger.warning("Journal fichier indisponible (%s) : %s. "
                       "On continue en console seule.", chemin_log, e)


def verifier_interface(ted):
    """Renvoie la liste des symboles manquants (vide si tout va bien)."""
    return [s for s in SYMBOLES_REQUIS_TED if not hasattr(ted, s)]


def lancer_collecteur(nom, module):
    """Lance le main() d'un collecteur, isole des erreurs.
    Renvoie (succes: bool, duree_secondes: float)."""
    logger.info("=" * 60)
    logger.info(">>> %s", nom)
    logger.info("=" * 60)
    debut = time.time()
    try:
        module.main()
        duree = time.time() - debut
        logger.info("%s termine en %.0fs.", nom, duree)
        return True, duree
    except KeyboardInterrupt:
        raise
    except Exception as e:
        duree = time.time() - debut
        logger.error("%s a ECHOUE apres %.0fs : %s", nom, duree, e)
        logger.error("Detail technique :\n%s", traceback.format_exc())
        return False, duree


def main():
    configurer_logging()
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info("#" * 60)
    logger.info("RADAR AMARANTE -- run du %s", horodatage)
    logger.info("#" * 60)

    # --- 1. Import du coeur TED + verification d'interface --------------------
    try:
        import ted_complet_v14 as ted
    except ModuleNotFoundError:
        logger.error("ted_complet_v14.py est introuvable. Les fichiers du radar "
                     "doivent etre dans le MEME dossier.")
        sys.exit(1)

    manquants = verifier_interface(ted)
    if manquants:
        logger.error("L'interface du coeur TED a change. Symbole(s) "
                     "manquant(s) : %s. Corrige avant de relancer.",
                     ", ".join(manquants))
        sys.exit(1)
    logger.info("Interface TED verifiee : les %d symboles attendus sont presents.",
                len(SYMBOLES_REQUIS_TED))

    # --- 2. Import du collecteur BM ------------------------------------------
    try:
        import ted_complet_bm as bm
    except ModuleNotFoundError:
        logger.error("ted_complet_bm.py est introuvable (meme dossier requis).")
        sys.exit(1)

    # --- 2 bis-a. Collecteur BOAMP (marches publics FR), optionnel -----------
    boamp = None
    try:
        import ted_complet_boamp as boamp
    except Exception as e:
        logger.info("(info) Collecteur BOAMP indisponible (%s). Etape ignoree.", e)

    # --- 2 bis. Moteur de signaux prives (BITD), optionnel -------------------
    bitd = None
    try:
        import bitd_signaux as bitd
    except Exception as e:
        logger.info("(info) Moteur BITD indisponible (%s). Etape ignoree.", e)

    # --- 2 ter. Enrichissement firmographique, optionnel ---------------------
    enrich = None
    try:
        import enrichir_entreprises as enrich
    except Exception as e:
        logger.info("(info) Enrichissement indisponible (%s). Etape ignoree.", e)

    # --- 2 quater. Collecteur ReliefWeb (offres terrain), optionnel ----------
    reliefweb = None
    try:
        import ted_complet_reliefweb as reliefweb
    except Exception as e:
        logger.info("(info) Collecteur ReliefWeb indisponible (%s). Etape ignoree.", e)

    # --- 2 quinquies. Collecteur ATTRIBUTIONS TED (qui a gagne), optionnel ----
    # Levier 1 : recolte les marches attribues en zones a risque et en extrait
    # le titulaire (cible de prospection). Sans LLM, cout zero. Isole.
    attributions = None
    try:
        import ted_complet_attributions as attributions
    except Exception as e:
        logger.info("(info) Collecteur attributions indisponible (%s). Etape ignoree.", e)

    # --- 3. Garde commune : cle API ------------------------------------------
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY n'est pas definie. Definis-la avant de "
                     "lancer (secret GitHub Actions).")
        sys.exit(1)

    if not os.environ.get("TED_SHEET_ID"):
        logger.info("(info) TED_SHEET_ID absent : les collecteurs tourneront "
                    "en mode console (pas d'ecriture Google Sheet).")

    # --- 4. Lancement sequentiel, chacun isole -------------------------------
    resultats = {}
    resultats["TED"] = lancer_collecteur("ETAPE -- COLLECTEUR TED", ted)
    resultats["Banque Mondiale"] = lancer_collecteur(
        "ETAPE -- COLLECTEUR BANQUE MONDIALE", bm)
    if boamp is not None:
        resultats["BOAMP"] = lancer_collecteur(
            "ETAPE -- COLLECTEUR BOAMP (marches publics FR)", boamp)
    if reliefweb is not None:
        resultats["ReliefWeb"] = lancer_collecteur(
            "ETAPE -- COLLECTEUR RELIEFWEB (offres terrain)", reliefweb)
    if attributions is not None:
        resultats["Attributions"] = lancer_collecteur(
            "ETAPE -- COLLECTEUR ATTRIBUTIONS TED (qui a gagne)", attributions)
    if bitd is not None:
        resultats["Signaux BITD"] = lancer_collecteur(
            "ETAPE -- MOTEUR SIGNAUX PRIVES (BITD)", bitd)
    if enrich is not None:
        resultats["Enrichissement"] = lancer_collecteur(
            "ETAPE -- ENRICHISSEMENT ENTREPRISES", enrich)

    # --- 5. Bilan global ------------------------------------------------------
    logger.info("#" * 60)
    logger.info("BILAN GLOBAL DU RUN")
    logger.info("#" * 60)
    duree_totale = 0.0
    au_moins_un_echec = False
    for nom, (ok, duree) in resultats.items():
        duree_totale += duree
        etat = "OK" if ok else "ECHEC"
        if not ok:
            au_moins_un_echec = True
        logger.info("  %-18s : %-5s (%.0fs)", nom, etat, duree)
    logger.info("  %-18s : %.0fs", "Duree totale", duree_totale)

    # --- 6. Code de sortie pour l'ordonnanceur -------------------------------
    if au_moins_un_echec:
        logger.error("Au moins un collecteur a echoue : code de sortie 1.")
        sys.exit(1)
    logger.info("Run complet.")


if __name__ == "__main__":
    main()
