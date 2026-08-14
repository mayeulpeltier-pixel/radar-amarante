"""Tests du referentiel CPV officiel (divisions) et de son cablage.

Le referentiel vient de la codelist cpv.gc du eForms-SDK (OP-TED), reduit aux
45 divisions officielles (niveau utilise par secteur_lisible). Objectif : rendre
lisible un secteur dont la division n'est pas dans la table metier Amarante,
sans jamais alterer les libelles metier existants.
"""

import unittest

import cpv_reference as cr
import ted_complet_attributions as attr


class TestReferentiel(unittest.TestCase):

    def test_45_divisions(self):
        self.assertEqual(len(cr.DIVISIONS), 45)

    def test_division_lisible(self):
        self.assertEqual(cr.division_lisible("66510000"),
                         "Services financiers et d'assurance")
        self.assertEqual(cr.division_lisible("48000000"),
                         "Logiciels et systèmes d'information")

    def test_division_inconnue(self):
        self.assertIsNone(cr.division_lisible("99999999"))
        self.assertIsNone(cr.division_lisible(""))
        self.assertIsNone(cr.division_lisible(None))

    def test_prend_les_deux_premiers_chiffres(self):
        # Code complet ou juste la division -> meme libelle.
        self.assertEqual(cr.division_lisible("45000000"), cr.division_lisible("45"))


class TestCablageSecteurLisible(unittest.TestCase):

    def test_table_metier_prioritaire(self):
        # Division 71 est dans la table metier -> libelle COURT conserve.
        self.assertEqual(attr.secteur_lisible(["71520000"]), "Ingenierie / etudes")

    def test_fallback_officiel_hors_table_metier(self):
        # Division 66 absente de la table metier -> libelle officiel, pas "Autre".
        self.assertEqual(attr.secteur_lisible(["66510000"]),
                         "Services financiers et d'assurance")

    def test_multi_cpv_metier_gagne(self):
        # Meme si un code hors table vient en premier, un code metier l'emporte.
        self.assertEqual(attr.secteur_lisible(["48000000", "71520000"]),
                         "Ingenierie / etudes")

    def test_aucune_division_connue_reste_autre(self):
        self.assertEqual(attr.secteur_lisible(["99999999"]), "Autre")
        self.assertEqual(attr.secteur_lisible([]), "Autre")


if __name__ == "__main__":
    unittest.main()
