# -*- coding: utf-8 -*-
"""Rattrapage des statuts orphelins (suite de P3.4, 26/08/2026).

CE QUI EST REPARE
-----------------
`ONGLET_SRC` ne couvrait que 4 sources sur 15. Les onze autres tombaient sur
`ONGLET_SRC[l.src] || ""`, et `superposer_statuts` relit sur la clé (onglet,
publication_number) : un statut écrit avec un onglet VIDE est écrit réellement
et n'est jamais retrouvé. Marquer « à contacter » un avis EBRD partait dans le
vide, et le lead réapparaissait « nouveau » au rechargement.

Le défaut est corrigé. Ce script récupère ce qui a été perdu avant.

LES TROIS RÈGLES QUE CES TESTS GARDENT
--------------------------------------
1. **On ne devine pas.** Si un numéro de publication existe dans plusieurs
   onglets, on le signale au lieu de choisir. Un statut posé sur le mauvais
   lead serait pire que le statut perdu : personne ne le remettrait en
   question.
2. **On n'écrase jamais.** L'orphelin date d'AVANT le correctif ; si le lead a
   été remarqué depuis, la valeur en place est plus récente et fait autorité.
3. **On n'écrit pas dans `radar_outcomes`.** C'est une réparation, pas une
   transition commerciale : la journaliser polluerait la boucle
   d'apprentissage avec des issues fictives datées d'aujourd'hui.

Tests OFFLINE : fonctions pures et base factice, aucun réseau.
"""

import unittest

import rattrapage_statuts as rs


def _orph(pub, statut="contacte", motif=""):
    return (pub, statut, motif, None)


class TestClassement(unittest.TestCase):
    """Fonction pure : répartit sans toucher à la base."""

    def setUp(self):
        self.orphelins = [_orph("EBRD-1"), _orph("UNGM-2", "non_pertinent",
                                                 "prix"),
                          _orph("AMBIGU"), _orph("DISPARU"),
                          _orph("DEJA", "surveille")]
        self.par_numero = {"EBRD-1": ["ebrd_radar"], "UNGM-2": ["ungm_radar"],
                           "AMBIGU": ["ted_radar", "bm_radar"],
                           "DEJA": ["miga_radar"]}
        self.deja = {("miga_radar", "DEJA")}

    def _classer(self):
        return rs.classer(self.orphelins, self.par_numero, self.deja)

    def test_un_numero_dans_un_seul_onglet_est_recuperable(self):
        recup, _, _, _ = self._classer()
        self.assertIn(("ebrd_radar", "EBRD-1", "contacte", ""), recup)

    def test_le_motif_est_conserve(self):
        """Le motif d'écartement sert à l'apprentissage : le perdre au
        rattrapage reviendrait à ne récupérer qu'à moitié."""
        recup, _, _, _ = self._classer()
        self.assertIn(("ungm_radar", "UNGM-2", "non_pertinent", "prix"), recup)

    def test_un_numero_dans_plusieurs_onglets_n_est_PAS_devine(self):
        """LA règle centrale. Un statut posé sur le mauvais lead serait pire
        que le statut perdu."""
        recup, ambigus, _, _ = self._classer()
        self.assertNotIn("AMBIGU", [p for _, p, _, _ in recup])
        self.assertEqual(ambigus[0][0], "AMBIGU")
        self.assertEqual(ambigus[0][2], ["bm_radar", "ted_radar"])

    def test_un_numero_sans_ligne_est_signale_pas_ignore(self):
        """Un orphelin sans foyer est une information : peut-être le lead
        a-t-il été purgé, peut-être le numéro est-il corrompu."""
        _, _, introuvables, _ = self._classer()
        self.assertEqual(introuvables, [("DISPARU", "contacte")])

    def test_un_statut_deja_repose_n_est_pas_ecrase(self):
        """L'orphelin est ANTÉRIEUR au correctif : ce qui a été posé depuis
        est plus récent et fait autorité."""
        recup, _, _, deja = self._classer()
        self.assertNotIn("DEJA", [p for _, p, _, _ in recup])
        self.assertEqual(deja, [("miga_radar", "DEJA", "surveille")])

    def test_aucun_orphelin_n_est_perdu_au_classement(self):
        """Chaque orphelin doit se retrouver dans exactement une catégorie."""
        recup, ambigus, introuvables, deja = self._classer()
        self.assertEqual(len(recup) + len(ambigus) + len(introuvables)
                         + len(deja), len(self.orphelins))

    def test_liste_vide(self):
        self.assertEqual(rs.classer([], {}, set()), ([], [], [], []))


class TestEcriture(unittest.TestCase):

    class Conn:
        def __init__(self):
            self.requetes = []

        def cursor(self):
            return TestEcriture.Cur(self.requetes)

        def commit(self):
            pass

    class Cur:
        def __init__(self, journal):
            self.journal = journal

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def execute(self, sql, params=None):
            self.journal.append((sql, params))

        def fetchall(self):
            return []

    def test_ecrit_bien_les_recuperables(self):
        conn = self.Conn()
        n = rs.appliquer(conn, [("ebrd_radar", "E-1", "contacte", "")])
        self.assertEqual(n, 1)
        sql, params = conn.requetes[0]
        self.assertIn("INSERT INTO radar_statuts", sql)
        self.assertEqual(params, ("ebrd_radar", "E-1", "contacte", ""))

    def test_seconde_barriere_contre_l_ecrasement(self):
        """`classer` filtre déjà, mais une base modifiée entre la sonde et
        l'application doit rester protégée."""
        conn = self.Conn()
        rs.appliquer(conn, [("ebrd_radar", "E-1", "contacte", "")])
        self.assertIn("ON CONFLICT (onglet, publication_number) DO NOTHING",
                      conn.requetes[0][0])

    def test_n_ecrit_JAMAIS_dans_radar_outcomes(self):
        """Passer par `definir_statut` journaliserait chaque réparation comme
        une transition datée d'aujourd'hui : la boucle d'apprentissage y
        verrait des dizaines d'issues fictives."""
        conn = self.Conn()
        rs.appliquer(conn, [("ebrd_radar", "E-1", "contacte", ""),
                            ("ungm_radar", "U-2", "perdu", "prix")])
        for sql, _ in conn.requetes:
            self.assertNotIn("radar_outcomes", sql)

    def test_le_script_n_appelle_pas_definir_statut(self):
        """Vérification par l'AST, pas par recherche de texte : le nom apparaît
        légitimement dans les commentaires qui expliquent pourquoi on ne
        l'utilise PAS. Un premier jet cherchait la chaîne et échouait sur sa
        propre justification."""
        import ast
        with open("rattrapage_statuts.py", encoding="utf-8") as f:
            arbre = ast.parse(f.read())
        appels = set()
        for n in ast.walk(arbre):
            if isinstance(n, ast.Call):
                f_ = n.func
                if isinstance(f_, ast.Attribute):
                    appels.add(f_.attr)
                elif isinstance(f_, ast.Name):
                    appels.add(f_.id)
        self.assertNotIn("definir_statut", appels)
        self.assertNotIn("enregistrer_opportunites", appels)

    def test_rien_a_ecrire_ne_produit_aucune_requete(self):
        conn = self.Conn()
        self.assertEqual(rs.appliquer(conn, []), 0)
        self.assertEqual(conn.requetes, [])


class TestIdempotence(unittest.TestCase):
    """Le script doit pouvoir être relancé sans crainte."""

    def test_une_seconde_passe_ne_repropose_rien(self):
        orphelins = [_orph("E-1")]
        par_numero = {"E-1": ["ebrd_radar"]}
        recup, _, _, _ = rs.classer(orphelins, par_numero, set())
        self.assertEqual(len(recup), 1)
        # Après application, le statut existe : la passe suivante l'ignore.
        recup2, _, _, deja = rs.classer(orphelins, par_numero,
                                        {("ebrd_radar", "E-1")})
        self.assertEqual(recup2, [])
        self.assertEqual(len(deja), 1)

    def test_les_orphelins_ne_sont_pas_supprimes(self):
        """Rien n'est détruit : la table d'origine reste consultable."""
        with open("rattrapage_statuts.py", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("DELETE", src.upper().replace("DELETE_", ""))


class TestSondeParDefaut(unittest.TestCase):
    """Même discipline que le back-fill : mesurer avant d'écrire."""

    def _src(self):
        with open("rattrapage_statuts.py", encoding="utf-8") as f:
            return f.read()

    def test_ecriture_conditionnee_a_un_drapeau(self):
        src = self._src()
        self.assertIn('ap.add_argument("--appliquer", action="store_true"', src)
        self.assertIn("if args.appliquer and recup:", src)

    def test_le_mode_est_annonce(self):
        self.assertIn('"ECRITURE" if args.appliquer else "SONDE', self._src())

    def test_le_workflow_est_manuel_et_jamais_planifie(self):
        import os
        chemin = None
        for d in (".github/workflows", ".", "workflows"):
            c = os.path.join(d, "rattrapage_statuts.yml")
            if os.path.exists(c):
                chemin = c
                break
        if chemin is None:
            self.skipTest("rattrapage_statuts.yml introuvable")
        with open(chemin, encoding="utf-8") as f:
            y = f.read()
        self.assertIn("workflow_dispatch", y)
        self.assertNotIn("schedule:", y)
        self.assertIn("default: false", y)

    def test_sans_base_sort_proprement(self):
        import radar_stockage as st
        avant = st.actif
        st.actif = lambda: False
        try:
            import sys
            argv = sys.argv
            sys.argv = ["rattrapage_statuts.py"]
            try:
                self.assertEqual(rs.main(), 1)
            finally:
                sys.argv = argv
        finally:
            st.actif = avant


if __name__ == "__main__":
    unittest.main()
