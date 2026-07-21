# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- RATTRAPAGE HISTORIQUE SHEET -> POSTGRES.
===========================================================

POURQUOI CE SCRIPT (constat du run du 21/07/2026)
-------------------------------------------------
La double ecriture des collecteurs ne remplit le miroir QUE lorsqu'ils
atteignent leur fonction d'ecriture. Or la famille TED (TED, BM, AfDB, EBRD,
ReliefWeb) s'arrete AVANT quand tout est deja vu ("Aucun NOUVEL avis"), et
UNGM/IsDB pre-filtrent les deja-connus. Consequence structurelle : les onglets
historiques ne se rempliraient JAMAIS d'eux-memes. Ce script comble les trous
en versant TOUT le classeur dans le miroir, une bonne fois -- et reste
rejouable a volonte.

PRINCIPES (les memes que partout ailleurs)
------------------------------------------
  - Sheet en LECTURE SEULE (portee readonly de backup_sheet.ouvrir_classeur :
    ce script ne peut structurellement rien modifier dans le classeur).
  - Postgres en AJOUT SEUL : ON CONFLICT DO NOTHING, une ligne existante
    n'est jamais reecrite. Rejouer le rattrapage est donc sans danger.
  - Best-effort par onglet : un onglet illisible n'empeche pas les autres.

DEDUPLICATION DES LIGNES SANS publication_number
------------------------------------------------
Certains onglets n'ont pas d'identifiant (prive_radar, whitelist, memoire des
vus, risque_pays...). Sans cle, chaque rejeu dupliquerait tout. On forge donc
une cle de CONTENU : "SHA1-<empreinte de la ligne>", calculee HORS zone de
saisie humaine (statut_suivi, statut_prospection) -- ainsi, changer un statut
a la main dans le Sheet ne cree pas de doublon au rejeu suivant. Limite
honnete : si une donnee de la ligne change dans le Sheet, le rejeu ajoute la
nouvelle version a cote de l'ancienne (historique conserve, jamais ecrase).

ENV attendues :
  - TED_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE   (lecture du classeur)
  - DATABASE_URL                                (miroir Postgres)
  - RADAR_RATTRAPAGE_EXCLUS  (optionnel : noms d'onglets a sauter, separes
    par des virgules)

LANCEMENT :  python radar_rattrapage.py   (workflow "Rattrapage Postgres")
"""

import hashlib
import json
import os

import backup_sheet
import radar_stockage as st


# Zone de saisie humaine : exclue de l'empreinte de contenu pour qu'une mise a
# jour manuelle de statut ne fabrique pas de doublon au rejeu.
CLES_HUMAINES = ("statut_suivi", "statut_prospection")


def cle_contenu(donnees):
    """Empreinte stable d'une ligne sans identifiant : SHA1 du JSON canonique
    (cles triees), zone humaine exclue."""
    base = {k: v for k, v in (donnees or {}).items()
            if str(k) not in CLES_HUMAINES}
    brut = json.dumps(base, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(brut.encode("utf-8")).hexdigest()


def preparer_rattrapage(ligne):
    """Ligne du Sheet -> ligne prete pour le miroir.

    Si elle porte deja un publication_number, on n'y touche pas (les onglets
    d'avis et d'attributions se dedupliquent naturellement). Sinon on forge
    la cle de contenu 'SHA1-...' : elle rend le rattrapage idempotent ET
    restera lisible en base (prefixe explicite, tracable)."""
    donnees = dict(ligne or {})
    pub = str(donnees.get("publication_number", "") or "")
    if not pub:
        donnees["publication_number"] = "SHA1-" + cle_contenu(donnees)
    return donnees


def rattraper_classeur(classeur, conn, exclus=()):
    """Verse chaque onglet du classeur dans le miroir. {onglet: (lues,
    ajoutees, deja, erreur|'')} pour le compte-rendu."""
    bilan = {}
    for ws in classeur.worksheets():
        titre = ws.title
        if titre in exclus:
            bilan[titre] = (0, 0, 0, "exclu")
            continue
        try:
            lignes = ws.get_all_records()
        except Exception as e:
            bilan[titre] = (0, 0, 0, "illisible : {}".format(str(e)[:80]))
            continue
        pretes = [preparer_rattrapage(l) for l in lignes
                  if any(str(v).strip() for v in (l or {}).values())]
        try:
            ajoutees, deja = st.ajouter_lignes(conn, titre, pretes)
            conn.commit()
            bilan[titre] = (len(pretes), ajoutees, deja, "")
        except Exception as e:
            conn.rollback()
            bilan[titre] = (len(pretes), 0, 0, "ecriture : {}".format(str(e)[:80]))
    return bilan


def main():
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not (sheet_id and fichier):
        print("(erreur) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents.")
        raise SystemExit(1)
    if not st.actif():
        print("(erreur) Miroir inactif : DATABASE_URL absent, RADAR_PG=0 ou"
              " psycopg introuvable. Rien a rattraper sans destination.")
        raise SystemExit(1)
    exclus = tuple(x.strip() for x in
                   os.environ.get("RADAR_RATTRAPAGE_EXCLUS", "").split(",")
                   if x.strip())

    print("Rattrapage historique Sheet -> Postgres...")
    classeur = backup_sheet.ouvrir_classeur(sheet_id, fichier)
    with st.connexion() as conn:
        st.initialiser(conn)
        bilan = rattraper_classeur(classeur, conn, exclus)
        etat = st.inventaire(conn)

    total_aj = 0
    for titre, (lues, aj, deja, err) in sorted(bilan.items()):
        total_aj += aj
        if err:
            print("  {:24} {} ({} ligne(s) lue(s))".format(titre, err, lues))
        else:
            print("  {:24} {} lue(s) | {} ajoutee(s) | {} deja connue(s)".format(
                titre, lues, aj, deja))
    print("Total : {} ligne(s) ajoutee(s) au miroir.".format(total_aj))
    print("\nInventaire Postgres apres rattrapage :")
    for onglet, nb in etat.items():
        print("  {:24} {} ligne(s)".format(onglet, nb))


if __name__ == "__main__":
    main()
