# -*- coding: utf-8 -*-
"""
Radar Amarante -- Sauvegarde quotidienne du Google Sheet (anti-SPOF donnees).
=============================================================================

POURQUOI CE SCRIPT EXISTE
-------------------------
Le Google Sheet est le systeme d'enregistrement de TOUT le pipeline : leads,
statut CRM (statut_suivi), watchlist curee a la main, enrichissement paye
(Hunter/GLEIF). Le vrai risque n'est pas que Google perde la donnee (tres
durable) mais une CORRUPTION HUMAINE : un tri de colonne qui desaligne tout,
une suppression de lignes, un en-tete ecrase, une watchlist videe par erreur.

Ce script lit TOUS les onglets (liste dynamique, rien n'est code en dur) et en
ecrit une copie CSV fidele dans backups/. Un workflow quotidien les commite :
git fait alors l'historique (chaque commit = un point de restauration date,
consultable via `git log backups/`).

PRINCIPE : lecture SEULE, best-effort. Un onglet illisible n'empeche pas les
autres. Aucun appel LLM, aucune ecriture dans le Sheet. Lecture BRUTE
(get_all_values) et non get_all_records : on veut une copie fidele, y compris
d'un eventuel desalignement, pas une interpretation.

CE QUE LE BACKUP NE COUVRE PAS (limite honnete) : l'Apps Script lie au Sheet
(bouton "Je contacte") et la mise en forme conditionnelle. Ce sont des donnees,
pas du code de feuille.

ENV attendues (memes que les collecteurs) :
  - TED_SHEET_ID                 (obligatoire)
  - GOOGLE_SERVICE_ACCOUNT_FILE  (obligatoire)
  - BACKUP_DIR                   (optionnel, defaut "backups")

RESTAURATION : nouveau Google Sheet -> Fichier > Importer > deposer le CSV de
l'onglet -> "Remplacer la feuille de calcul". Le manifeste (_manifest.json)
donne le nom reel de chaque onglet et le nombre de lignes attendu.

LANCEMENT : python backup_sheet.py
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone


def _nom_fichier_sur(titre):
    """Nom d'onglet -> nom de fichier sur. Le vrai nom d'onglet reste dans le
    manifeste, donc aucune information n'est perdue par cette normalisation."""
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", titre).strip("_.") or "onglet"
    return base[:100]


def ouvrir_classeur(sheet_id, fichier_cs):
    """Ouvre le classeur en LECTURE SEULE (portee readonly : ce script ne peut
    structurellement rien modifier dans le Sheet)."""
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    return gspread.authorize(creds).open_by_key(sheet_id)


def sauvegarder(classeur, dossier):
    """Ecrit un CSV par onglet dans `dossier`, plus un manifeste JSON.
    Renvoie (nb_ok, nb_echecs). Tolerant : un onglet en echec est journalise
    et note dans le manifeste, sans interrompre les autres."""
    os.makedirs(dossier, exist_ok=True)

    manifeste = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "onglets": [],
    }
    noms_utilises = set()
    ok, echecs = 0, 0

    for ws in classeur.worksheets():
        titre = ws.title
        # Nom de fichier sur + anti-collision (deux onglets normalises pareil).
        nom = _nom_fichier_sur(titre)
        candidat, i = nom, 2
        while candidat in noms_utilises:
            candidat = "{}_{}".format(nom, i)
            i += 1
        nom = candidat
        noms_utilises.add(nom)
        chemin = os.path.join(dossier, nom + ".csv")

        try:
            valeurs = ws.get_all_values()   # brut, fidele, positionnel
        except Exception as e:
            print("  (attention) onglet '{}' illisible ({}), ignore.".format(titre, e))
            echecs += 1
            manifeste["onglets"].append({
                "onglet": titre, "fichier": nom + ".csv",
                "lignes": None, "colonnes": None, "erreur": str(e)[:200],
            })
            continue

        with open(chemin, "w", encoding="utf-8", newline="") as f:
            csv.writer(f, quoting=csv.QUOTE_MINIMAL).writerows(valeurs)

        n_lignes = len(valeurs)
        n_cols = max((len(r) for r in valeurs), default=0)
        manifeste["onglets"].append({
            "onglet": titre, "fichier": nom + ".csv",
            "lignes": n_lignes, "colonnes": n_cols, "erreur": None,
        })
        print("  {:<28} -> {:<32} ({} lignes, {} colonnes)".format(
            titre[:28], nom + ".csv", n_lignes, n_cols))
        ok += 1

    with open(os.path.join(dossier, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifeste, f, ensure_ascii=False, indent=2)

    print("Sauvegarde : {} onglet(s) OK, {} echec(s). Dossier : {}".format(ok, echecs, dossier))
    return ok, echecs


def main():
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    dossier = os.environ.get("BACKUP_DIR", "backups")
    if not sheet_id or not fichier_cs:
        print("ERREUR : TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE sont requis.")
        sys.exit(1)

    print("Sauvegarde du Sheet {}...".format(sheet_id))
    try:
        classeur = ouvrir_classeur(sheet_id, fichier_cs)
    except Exception as e:
        print("ERREUR : ouverture du Sheet impossible ({}).".format(e))
        sys.exit(1)

    ok, echecs = sauvegarder(classeur, dossier)
    # Un backup PARTIEL vaut mieux qu'aucun : on ne fait pas echouer le run pour
    # un onglet manquant. En revanche, 0 onglet sauvegarde est anormal (probleme
    # d'acces) et doit alerter (code de sortie != 0).
    if ok == 0:
        print("ERREUR : aucun onglet sauvegarde. Verifie l'acces au Sheet.")
        sys.exit(1)


if __name__ == "__main__":
    main()
