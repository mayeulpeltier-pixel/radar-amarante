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

LECTURE POSITIONNELLE (correction du 22/07/2026)
------------------------------------------------
La premiere version lisait le Sheet PAR EN-TETE (`get_all_records`). C'etait
une faute : tout le reste du projet lit PAR POSITION, justement parce qu'un
en-tete peut etre desaligne. Celui de `bm_radar` l'etait d'exactement une
colonne, et le rattrapage a donc range toutes les valeurs sous de mauvais
noms -- la colonne `publication_number` contenait les NUMEROS DE TELEPHONE
(contact_phone, la colonne juste avant). Diagnostic pose par la phase d'ombre
de la memoire inter-runs, qui a montre '(258) 843031273' cote base contre
'OP00264347' cote Sheet.

Desormais : pour tout onglet dont le schema est connu (les collecteurs le
publient), lecture positionnelle. Pour les onglets libres (watchlist,
referentiels, suivi), lecture par en-tete, qui reste le seul moyen.

PURGE : `RADAR_RATTRAPAGE_PURGE=1` vide les onglets a schema connu AVANT de
les reimporter. Sans danger : `radar_lignes` ne contient que de la donnee de
COLLECTE, integralement reimportable depuis le Sheet ; la zone humaine vit
dans `radar_statuts`, table SEPAREE, jamais touchee ici.

ENV attendues :
  - TED_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE   (lecture du classeur)
  - DATABASE_URL                                (miroir Postgres)
  - RADAR_RATTRAPAGE_PURGE   (optionnel : 1 pour reimporter proprement)
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


def schemas_connus():
    """{onglet: colonnes officielles}. Chaque collecteur publie son schema ;
    on l'importe au lieu de le recopier, pour qu'un ajout de colonne demain
    n'invalide pas le rattrapage. Import best-effort : un module absent retire
    simplement son onglet de la liste (il retombera en lecture par en-tete)."""
    plan = [
        ("ted_complet_v14", "NOM_ONGLET_SHEET", "TOUTES_COLONNES_SHEET"),
        ("ted_complet_bm", "NOM_ONGLET_BM", "TOUTES_COLONNES_BM"),
        ("ted_complet_reliefweb", "NOM_ONGLET_RW", "TOUTES_COLONNES_RW"),
        ("afdb_radar", "NOM_ONGLET", "TOUTES_COLONNES_AFDB"),
        ("ebrd_radar", "NOM_ONGLET", "TOUTES_COLONNES_EBRD"),
        ("adb_radar", "NOM_ONGLET", "TOUTES_COLONNES_ADB"),
        ("ungm_radar", "NOM_ONGLET", "TOUTES_COLONNES_UNGM"),
        ("ted_complet_attributions", "NOM_ONGLET", "TOUTES_COLONNES"),
        ("bitd_signaux", "NOM_ONGLET_PRIVE", "TOUTES_COLONNES_PRIVE"),
        ("enrichir_entreprises", "NOM_ONGLET_ENRICHIES", "COLONNES_ENRICHIES"),
        ("enrichir_entreprises", "NOM_ONGLET_CONTACTS", "COLONNES_CONTACTS"),
    ]
    schemas = {}
    for module, attr_onglet, attr_colonnes in plan:
        try:
            mod = __import__(module)
            schemas[getattr(mod, attr_onglet)] = list(getattr(mod, attr_colonnes))
        except Exception:
            continue
    return schemas


def lignes_positionnelles(valeurs, colonnes):
    """Grille brute (get_all_values) -> dicts, PAR POSITION. Immunise contre
    un en-tete desaligne, absent ou obsolete. Une eventuelle ligne d'en-tete
    est reconnue par son contenu (elle repete les noms de colonnes)."""
    if not valeurs:
        return []
    premiere = [str(c).strip() for c in valeurs[0]]
    debut = 1 if len(set(premiere) & set(colonnes)) >= 3 else 0
    lignes = []
    for row in valeurs[debut:]:
        d = {c: (str(row[i]).strip() if i < len(row) else "")
             for i, c in enumerate(colonnes)}
        if any(v for v in d.values()):
            lignes.append(d)
    return lignes


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


def rattraper_classeur(classeur, conn, exclus=(), purger=False, schemas=None):
    """Verse chaque onglet du classeur dans le miroir. {onglet: (lues,
    ajoutees, deja, erreur|'')} pour le compte-rendu.

    Onglet a schema connu -> lecture POSITIONNELLE (immunisee contre un
    en-tete desaligne). Onglet libre -> lecture par en-tete, faute de mieux."""
    schemas = schemas_connus() if schemas is None else schemas
    bilan = {}
    for ws in classeur.worksheets():
        titre = ws.title
        if titre in exclus:
            bilan[titre] = (0, 0, 0, "exclu")
            continue
        colonnes = schemas.get(titre)
        try:
            if colonnes:
                lignes = lignes_positionnelles(ws.get_all_values(), colonnes)
            else:
                lignes = [l for l in ws.get_all_records()
                          if any(str(v).strip() for v in (l or {}).values())]
        except Exception as e:
            bilan[titre] = (0, 0, 0, "illisible : {}".format(str(e)[:80]))
            continue
        pretes = [preparer_rattrapage(l) for l in lignes]
        try:
            if purger and colonnes:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM radar_lignes WHERE onglet = %s",
                                (titre,))
            ajoutees, deja = st.ajouter_lignes(conn, titre, pretes)
            conn.commit()
            bilan[titre] = (len(pretes), ajoutees, deja,
                            "positionnel" if colonnes else "en-tete")
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
    purger = os.environ.get("RADAR_RATTRAPAGE_PURGE", "") == "1"

    schemas = schemas_connus()
    print("Rattrapage historique Sheet -> Postgres...")
    print("  {} onglet(s) a schema connu : lecture POSITIONNELLE.".format(
        len(schemas)))
    if purger:
        print("  PURGE demandee : ces onglets seront vides puis reimportes"
              " (radar_statuts, la zone humaine, n'est pas touchee).")
    classeur = backup_sheet.ouvrir_classeur(sheet_id, fichier)
    with st.connexion() as conn:
        st.initialiser(conn)
        bilan = rattraper_classeur(classeur, conn, exclus, purger, schemas)
        etat = st.inventaire(conn)

    total_aj = 0
    for titre, (lues, aj, deja, note) in sorted(bilan.items()):
        total_aj += aj
        if note in ("positionnel", "en-tete", "exclu"):
            print("  {:24} {} lue(s) | {} ajoutee(s) | {} deja connue(s)  [{}]".format(
                titre, lues, aj, deja, note))
        else:
            print("  {:24} {} ({} ligne(s) lue(s))".format(titre, note, lues))
    print("Total : {} ligne(s) ajoutee(s) au miroir.".format(total_aj))
    print("\nInventaire Postgres apres rattrapage :")
    for onglet, nb in etat.items():
        print("  {:24} {} ligne(s)".format(onglet, nb))


if __name__ == "__main__":
    main()
