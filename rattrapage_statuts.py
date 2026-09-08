# -*- coding: utf-8 -*-
"""Rattrapage des statuts orphelins (suite de P3.4).

CE QUI S'EST PASSE
------------------
La table `ONGLET_SRC` du cockpit ne couvrait que 4 sources sur 15. Les onze
autres -- AFDB, ADB, EBRD, UNGM, RW, MIGA, IFC, IDB, BMP, PROPARCO, DFC --
tombaient sur `ONGLET_SRC[l.src] || ""`.

Or `superposer_statuts` relit les statuts sur la cle (onglet,
publication_number). Un statut ecrit avec un onglet VIDE est ecrit reellement,
et n'est JAMAIS retrouve. Marquer « a contacter » un avis EBRD partait dans le
vide, et le lead reapparaissait « nouveau » au rechargement.

Le defaut est corrige depuis le 26/08. Ce script recupere ce qui a ete perdu
avant.

COMMENT ON RETROUVE LE BON ONGLET
---------------------------------
`radar_lignes` porte (onglet, publication_number). Un numero de publication
suffit donc a designer son onglet -- SI ce numero n'apparait que dans un seul.
Quand il apparait dans plusieurs, on NE DEVINE PAS : on le signale. Un statut
pose sur le mauvais lead serait pire que le statut perdu, parce que personne
ne le remettrait en question.

TROIS REGLES QUI COMPTENT
-------------------------
1. ON N'ECRASE JAMAIS un statut existant. L'orphelin date d'AVANT le
   correctif ; si le lead a ete remarque depuis, la valeur en place est plus
   recente et fait autorite.
2. ON N'ECRIT PAS DANS `radar_outcomes`. C'est une REPARATION, pas une
   transition commerciale : la journaliser polluerait la boucle
   d'apprentissage avec des issues fictives datees d'aujourd'hui. On passe
   donc par du SQL direct, PAS par `definir_statut`.
3. LES ORPHELINS SONT CONSERVES apres recuperation. Rien n'est supprime, et
   le script reste idempotent : une seconde execution constate simplement que
   la cible porte deja un statut.

USAGE
-----
    python rattrapage_statuts.py                # sonde, n'ecrit rien
    python rattrapage_statuts.py --appliquer    # ecrit
"""

import argparse
import os
import sys

import radar_stockage as st


def lire_orphelins(conn):
    """[(publication_number, statut, motif, maj)] des statuts sans onglet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT publication_number, statut, motif, maj"
            " FROM radar_statuts WHERE onglet = '' AND publication_number <> ''"
            " ORDER BY maj")
        return list(cur.fetchall())


def onglets_du_numero(conn, publications):
    """{publication_number: [onglets]} depuis `radar_lignes`.

    Une seule requete pour tout le lot : autant d'aller-retours que
    d'orphelins saturerait une base gratuite."""
    if not publications:
        return {}
    out = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT publication_number, onglet FROM radar_lignes"
            " WHERE publication_number = ANY(%s)", (list(publications),))
        for pub, onglet in cur.fetchall():
            out.setdefault(pub, []).append(onglet)
    return out


def statuts_deja_poses(conn, publications):
    """{(onglet, publication_number)} des statuts NON orphelins deja en place.

    C'est ce qui empeche d'ecraser un travail plus recent que la panne."""
    if not publications:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT onglet, publication_number FROM radar_statuts"
            " WHERE onglet <> '' AND publication_number = ANY(%s)",
            (list(publications),))
        return {(o, p) for o, p in cur.fetchall()}


def classer(orphelins, par_numero, deja):
    """Repartit les orphelins. Fonction PURE, aucun acces base.

    Renvoie (recuperables, ambigus, introuvables, deja_traites) ou
    `recuperables` = [(onglet, pub, statut, motif)]."""
    recuperables, ambigus, introuvables, deja_traites = [], [], [], []
    for pub, statut, motif, _maj in orphelins:
        onglets = sorted(set(par_numero.get(pub) or []))
        if not onglets:
            introuvables.append((pub, statut))
        elif len(onglets) > 1:
            ambigus.append((pub, statut, onglets))
        elif (onglets[0], pub) in deja:
            deja_traites.append((onglets[0], pub, statut))
        else:
            recuperables.append((onglets[0], pub, statut, motif or ""))
    return (recuperables, ambigus, introuvables, deja_traites)


def appliquer(conn, recuperables):
    """Ecrit les statuts recuperes. SQL direct, PAS `definir_statut`.

    `definir_statut` journaliserait chaque reparation dans `radar_outcomes`
    comme une transition datee d'aujourd'hui. La boucle d'apprentissage y
    verrait des dizaines d'issues fictives : on repare l'etat, on n'invente
    pas d'histoire.

    `ON CONFLICT DO NOTHING` : seconde barriere contre l'ecrasement, en plus
    du filtrage de `classer`."""
    n = 0
    with conn.cursor() as cur:
        for onglet, pub, statut, motif in recuperables:
            cur.execute(
                "INSERT INTO radar_statuts (onglet, publication_number,"
                " statut, motif) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (onglet, publication_number) DO NOTHING",
                (onglet, pub, statut, motif))
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--appliquer", action="store_true",
                    help="ecrit reellement (defaut : sonde, aucune ecriture)")
    args = ap.parse_args()

    if not st.actif():
        print("DATABASE_URL absent ou pilote manquant : rien a faire.")
        return 1

    print("Rattrapage des statuts orphelins -- mode {}\n".format(
        "ECRITURE" if args.appliquer else "SONDE (aucune ecriture)"))

    with st.connexion() as conn:
        st.initialiser(conn)
        orphelins = lire_orphelins(conn)
        if not orphelins:
            print("Aucun statut orphelin : rien a recuperer.")
            return 0
        pubs = {o[0] for o in orphelins}
        recup, ambigus, introuvables, deja = classer(
            orphelins, onglets_du_numero(conn, pubs),
            statuts_deja_poses(conn, pubs))

        print("{} statut(s) orphelin(s) trouve(s) :".format(len(orphelins)))
        print("   {:4d} recuperable(s)".format(len(recup)))
        print("   {:4d} deja repose(s) depuis (on ne touche pas)".format(len(deja)))
        print("   {:4d} ambigu(s) : le numero existe dans plusieurs onglets"
              .format(len(ambigus)))
        print("   {:4d} introuvable(s) : plus aucune ligne pour ce numero"
              .format(len(introuvables)))

        par_onglet = {}
        for onglet, _p, statut, _m in recup:
            par_onglet.setdefault(onglet, []).append(statut)
        if par_onglet:
            print("\nRepartition des recuperables :")
            for onglet, statuts in sorted(par_onglet.items()):
                detail = {}
                for s in statuts:
                    detail[s] = detail.get(s, 0) + 1
                print("   {:<24} {:3d}  ({})".format(
                    onglet, len(statuts),
                    ", ".join("{} {}".format(v, k)
                              for k, v in sorted(detail.items()))))
        for pub, statut, onglets in ambigus[:5]:
            print("   ambigu : {} ({}) present dans {}".format(
                pub, statut, ", ".join(onglets)))

        if args.appliquer and recup:
            n = appliquer(conn, recup)
            conn.commit()
            print("\n{} statut(s) recupere(s).".format(n))
        elif recup:
            print("\nRelance avec --appliquer pour ecrire. Rappel : aucun "
                  "statut existant ne sera ecrase, et rien n'est journalise "
                  "dans radar_outcomes (c'est une reparation, pas une "
                  "transition commerciale).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
