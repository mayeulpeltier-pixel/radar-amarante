# -*- coding: utf-8 -*-
"""Back-fill de `radar_lignes.date_detection` depuis le Google Sheet (P1.5).

LE DEFAUT RESIDUEL QU'ON CORRIGE ICI
------------------------------------
Le 25/08, on a rendu la colonne `date_detection` a l'application : elle
existait en base mais `lire_onglet` ne la selectionnait pas, si bien que tout
ce qui rentrait dans la fenetre de collecte s'affichait « detecte
aujourd'hui ».

Il restait un mensonge, plus discret. `radar_lignes.date_detection` vaut
`CURRENT_DATE` a l'INSERTION EN BASE, pas a la premiere detection reelle. Les
lignes entrees lors du remplissage retroactif du miroir portent donc toutes la
date de ce remplissage, et non celle a laquelle le radar les a vraiment vues.
Faux, mais stable -- alors qu'avant c'etait faux ET glissant.

La vraie date vit dans le Sheet, colonne `date_detection`, ecrite une seule
fois a la creation de la ligne et jamais reecrite. On la rapatrie.

DEUX GARDE-FOUS, ET ILS COMPTENT
--------------------------------
1. ON NE RECULE JAMAIS DANS LE FUTUR. Une date n'est reprise que si elle est
   ANTERIEURE a celle deja en base. Impossible, ainsi, de re-fabriquer un
   « detecte aujourd'hui » a partir d'un Sheet mal rempli : l'operation ne
   peut que vieillir une ligne, jamais la rajeunir.
2. SONDE PAR DEFAUT. Sans `--appliquer`, le script ne fait que COMPTER et
   MONTRER. C'est la discipline du projet : on mesure sur donnees reelles
   avant d'ecrire.

USAGE
-----
    python backfill_date_detection.py                 # sonde, n'ecrit rien
    python backfill_date_detection.py --onglet ted_radar
    python backfill_date_detection.py --appliquer     # ecrit
"""

import argparse
import datetime
import os
import sys

import radar_stockage as st

# Colonne portant la vraie date de premiere detection, cote Sheet. Elle est
# ajoutee en fin de ligne par les collecteurs et jamais reecrite ensuite.
COL_DETECTION = "date_detection"
COL_PUB = "publication_number"


def date_ou_none(valeur):
    """'2026-03-04' -> date(2026, 3, 4). Fonction PURE, tolerante.

    Renvoie None sur tout ce qui n'est pas une date ISO exploitable : cellule
    vide, texte libre, date bricolee a la main. Mieux vaut ignorer une ligne
    que propager une date inventee -- c'est precisement le defaut qu'on
    corrige."""
    txt = str(valeur or "").strip()[:10]
    if not txt:
        return None
    try:
        return datetime.date.fromisoformat(txt)
    except ValueError:
        return None


def a_corriger(date_sheet, date_pg):
    """La ligne doit-elle etre mise a jour ? Fonction PURE.

    GARDE-FOU CENTRAL : seule une date ANTERIEURE est reprise. L'operation ne
    peut que vieillir une ligne. Un Sheet corrompu qui contiendrait des dates
    du jour ne pourrait donc PAS recreer le defaut d'origine."""
    if date_sheet is None:
        return False
    if date_pg is None:
        return True
    return date_sheet < date_pg


def lignes_sheet(sheet_id, fichier, onglet):
    """{publication_number: date} depuis le Sheet. Best-effort : un onglet
    absent ou illisible renvoie {} et le script passe au suivant plutot que
    d'interrompre tout le back-fill."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        portee = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(fichier, scopes=portee)
        feuille = gspread.authorize(creds).open_by_key(sheet_id).worksheet(onglet)
        lignes = feuille.get_all_records()
    except Exception as e:
        print("  ! onglet '{}' illisible ({}) : ignore.".format(
            onglet, str(e)[:80]))
        return {}
    out = {}
    for r in lignes:
        pub = str(r.get(COL_PUB) or "").strip()
        d = date_ou_none(r.get(COL_DETECTION))
        if pub and d:
            # Une meme publication peut apparaitre deux fois (doublon de
            # collecte) : on garde la date la PLUS ANCIENNE, qui est la
            # premiere detection reelle.
            out[pub] = min(d, out[pub]) if pub in out else d
    return out


def dates_pg(conn, onglet):
    """{publication_number: date_detection} depuis la base."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT publication_number, date_detection FROM radar_lignes"
            " WHERE onglet = %s AND publication_number <> ''", (onglet,))
        return {p: d for p, d in cur.fetchall()}


def appliquer(conn, onglet, corrections):
    """Met a jour les lignes. Une seule requete par lot de 500 pour ne pas
    saturer la base avec des milliers d'UPDATE unitaires."""
    items = list(corrections.items())
    total = 0
    with conn.cursor() as cur:
        for i in range(0, len(items), 500):
            lot = items[i:i + 500]
            cur.executemany(
                "UPDATE radar_lignes SET date_detection = %s"
                " WHERE onglet = %s AND publication_number = %s",
                [(d, onglet, p) for p, d in lot])
            total += len(lot)
    return total


def traiter_onglet(conn, sheet_id, fichier, onglet, ecrire):
    """Renvoie (examinees, a_corriger, ecrites, exemples)."""
    sheet = lignes_sheet(sheet_id, fichier, onglet)
    if not sheet:
        return (0, 0, 0, [])
    pg = dates_pg(conn, onglet)
    corrections, exemples = {}, []
    for pub, d_sheet in sheet.items():
        if pub not in pg:
            continue                      # ligne absente du miroir : rien a faire
        if a_corriger(d_sheet, pg[pub]):
            corrections[pub] = d_sheet
            if len(exemples) < 3:
                exemples.append("{} : {} -> {}".format(pub, pg[pub], d_sheet))
    ecrites = appliquer(conn, onglet, corrections) if (ecrire and corrections) else 0
    return (len(pg), len(corrections), ecrites, exemples)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--appliquer", action="store_true",
                    help="ecrit reellement (defaut : sonde, aucune ecriture)")
    ap.add_argument("--onglet", default="",
                    help="limiter a un onglet (defaut : tous ceux du miroir)")
    args = ap.parse_args()

    sheet_id = os.environ.get("TED_SHEET_ID", "")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if not sheet_id:
        print("TED_SHEET_ID absent : impossible de lire la source des dates.")
        return 1
    if not st.actif():
        print("DATABASE_URL absent ou pilote manquant : rien a corriger.")
        return 1

    mode = "ECRITURE" if args.appliquer else "SONDE (aucune ecriture)"
    print("Back-fill date_detection -- mode {}\n".format(mode))

    with st.connexion() as conn:
        onglets = ([args.onglet] if args.onglet
                   else sorted(st.inventaire(conn)))
        tot_ex = tot_corr = tot_ecr = 0
        for onglet in onglets:
            ex, corr, ecr, exemples = traiter_onglet(
                conn, sheet_id, fichier, onglet, args.appliquer)
            tot_ex += ex
            tot_corr += corr
            tot_ecr += ecr
            if not ex:
                continue
            print("{:<26} {:5d} ligne(s), {:5d} a corriger{}".format(
                onglet, ex, corr, "" if not ecr else " -> {} ecrite(s)".format(ecr)))
            for e in exemples:
                print("      {}".format(e))
        if args.appliquer:
            conn.commit()

    print("\nTotal : {} ligne(s) examinee(s), {} date(s) a corriger, "
          "{} ecrite(s).".format(tot_ex, tot_corr, tot_ecr))
    if tot_corr and not args.appliquer:
        print("Relance avec --appliquer pour ecrire. Rappel : l'operation ne "
              "peut que VIEILLIR une ligne, jamais la rajeunir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
