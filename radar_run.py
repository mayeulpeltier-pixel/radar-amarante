# -*- coding: utf-8 -*-
"""
Radar Amarante -- Master d'orchestration
=========================================

Lance les deux collecteurs en sequence (TED puis Banque Mondiale) en un seul
appel. Point d'entree unique pour la routine hebdomadaire et pour n'importe
quel ordonnanceur (Colab a la main aujourd'hui, GitHub Actions demain).

PRINCIPE DE CONCEPTION : ce script n'installe RIEN et ne configure aucun
secret. Il suppose que l'environnement a deja ete prepare AVANT son
lancement :
  - ANTHROPIC_API_KEY            (obligatoire, les deux collecteurs en ont besoin)
  - TED_SHEET_ID                 (pour ecrire dans le Google Sheet ; sinon console)
  - GOOGLE_SERVICE_ACCOUNT_FILE  (idem)
  - RADAR_LOG_FILE               (optionnel ; chemin du journal, defaut "radar.log")
C'est la cellule Colab (aujourd'hui) ou le workflow GitHub Actions (demain)
qui fournit ces variables. Le master reste identique dans les deux mondes :
c'est ce qui le rend portable et automatisable.

JOURNALISATION (logging) : le master ecrit le DEROULE du run (debut, fin, etat
de chaque collecteur, durees, echecs) a la fois dans la console ET dans un
fichier horodate (radar.log, en mode ajout). C'est le suivi historique utile
sur un serveur permanent : "le run a-t-il reussi ? quelle partie a echoue ?
combien de temps ?". Les deux collecteurs, eux, gardent leurs print pour le
detail des leads (visibles dans la console et, sur GitHub Actions, dans les
logs du run). On ne touche donc pas aux fichiers collecteurs.

CE QU'IL FAIT :
  1. Verifie que le coeur TED est importable et que son interface est intacte
     (les 14 symboles que le collecteur BM lui emprunte). Filet anti-casse
     silencieuse : si une fonction du coeur est renommee, on s'arrete avec un
     message clair AVANT de lancer quoi que ce soit.
  2. Verifie la presence de la cle API (garde commune).
  3. Lance TED, puis BM, chacun isole : si l'un echoue, l'autre tourne quand
     meme (on ne perd pas une source a cause de l'autre).
  4. Journalise un bilan global avec les durees.
  5. Sort avec un code != 0 si un collecteur a echoue, pour qu'un ordonnanceur
     (GitHub Actions) marque le run en echec et envoie une alerte.

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
# Si l'un de ces symboles disparait (renommage, suppression), BM casserait
# silencieusement. On le verifie ici, une fois, au demarrage.
# ===========================================================================
SYMBOLES_REQUIS_TED = [
    # Constantes
    "MAX_CARACTERES_DESCRIPTION", "MODELE", "MODELE_RAFFINEMENT",
    "MULTIPLICATEUR_ZONE", "SEUIL_ALERTE", "SEUIL_SURVEILLANCE",
    # Fonctions
    "_nettoyer_html", "appeler_llm", "calculer_action_recommandee",
    "calculer_fenetre_action", "calculer_scores", "charger_index_publication",
    "lettre_colonne", "session_robuste",
]

logger = logging.getLogger("radar")


def configurer_logging():
    """Configure la journalisation : console + fichier horodate (mode ajout).
    Idempotent : ne double pas les handlers si appele plusieurs fois."""
    if logger.handlers:  # deja configure
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Console (stdout) : visible en direct dans Colab et dans les logs GitHub.
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)
    # Fichier : suivi historique persistant. Chemin configurable.
    chemin_log = os.environ.get("RADAR_LOG_FILE", "radar.log")
    try:
        fichier = logging.FileHandler(chemin_log, mode="a", encoding="utf-8")
        fichier.setFormatter(fmt)
        logger.addHandler(fichier)
    except OSError as e:
        # Si le fichier n'est pas accessible (droits, chemin), on continue en
        # console seule plutot que de planter tout le run pour un log.
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
        # Interruption manuelle (ou coupure reseau Colab sur Android) : on
        # n'avale pas, on remonte pour que l'utilisateur sache que c'est lui
        # (ou le reseau), pas un bug.
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
        logger.error("ted_complet_v14.py est introuvable. Les trois fichiers "
                     "(ted_complet_v14.py, ted_complet_bm.py, radar_run.py) "
                     "doivent etre dans le MEME dossier.")
        sys.exit(1)

    manquants = verifier_interface(ted)
    if manquants:
        logger.error("L'interface du coeur TED a change. Symbole(s) "
                     "manquant(s) que le collecteur BM attend : %s. "
                     "Le collecteur BM casserait. Corrige avant de relancer.",
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

    # --- 2 bis. Import du moteur de signaux prives (BITD), optionnel ----------
    # S'il manque, ou si la whitelist n'est pas encore importee dans le Sheet,
    # cette etape ne fait rien : elle n'empeche jamais le radar public de tourner.
    bitd = None
    try:
        import bitd_signaux as bitd
    except Exception as e:
        logger.info("(info) Moteur BITD indisponible (%s). Etape ignoree.", e)

    # --- 2 ter. Import de l'enrichissement firmographique, optionnel ---------
    enrich = None
    try:
        import enrichir_entreprises as enrich
    except Exception as e:
        logger.info("(info) Enrichissement indisponible (%s). Etape ignoree.", e)

    # --- 3. Garde commune : cle API ------------------------------------------
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY n'est pas definie. Les deux "
                     "collecteurs en ont besoin. Definis-la avant de lancer le "
                     "master (cellule Colab, ou secret GitHub Actions).")
        sys.exit(1)

    if not os.environ.get("TED_SHEET_ID"):
        logger.info("(info) TED_SHEET_ID absent : les collecteurs tourneront "
                    "en mode console (pas d'ecriture Google Sheet).")

    # --- 4. Lancement sequentiel, chacun isole -------------------------------
    resultats = {}
    resultats["TED"] = lancer_collecteur("ETAPE -- COLLECTEUR TED", ted)
    resultats["Banque Mondiale"] = lancer_collecteur(
        "ETAPE -- COLLECTEUR BANQUE MONDIALE", bm)
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
        logger.error("Au moins un collecteur a echoue : code de sortie 1 "
                     "(un ordonnanceur marquera le run en echec et alertera).")
        sys.exit(1)
    logger.info("Run complet. Les deux onglets (ted_radar, bm_radar) sont a jour.")


if __name__ == "__main__":
    main()
