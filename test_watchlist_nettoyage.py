# -*- coding: utf-8 -*-
"""Nettoyage des noms de titulaires dans l'auto-alimentation de la watchlist.

Le parsing PDF/TED collait parfois des residus au nom du gagnant (numero d'avis,
pagination, lot). On verifie que nettoyer_nom_titulaire les retire, rejette les
noms devenus vides, et preserve les noms propres. On verifie aussi que le seed
utilise bien ce nettoyage (le compte cree porte le nom propre).
"""

import unittest

import signaux_prives as sp


class TestNettoyageNom(unittest.TestCase):

    def test_residu_avis_et_pagination(self):
        self.assertEqual(
            sp.nettoyer_nom_titulaire("AQUASEARCH 228638-2026 Page 14/17"),
            "AQUASEARCH")

    def test_residu_lot(self):
        self.assertEqual(
            sp.nettoyer_nom_titulaire("Consortium ABC - Lot 3"), "Consortium ABC")

    def test_nom_propre_inchange(self):
        for n in ["Onur Group", "Yandalux Solar GmbH",
                  "SOCIETE D OBSERVATION MULTIMODALE"]:
            self.assertEqual(sp.nettoyer_nom_titulaire(n), n)

    def test_espaces_normalises(self):
        self.assertEqual(
            sp.nettoyer_nom_titulaire("  Vinci   Construction  "),
            "Vinci Construction")

    def test_residu_pur_rejete(self):
        for n in ["228638-2026 Page 14/17", "Page 3/5", "12345", ""]:
            self.assertEqual(sp.nettoyer_nom_titulaire(n), "")


class TestSeedNettoie(unittest.TestCase):

    def test_seed_produit_nom_propre(self):
        valeurs = [
            ["gagnant", "pays_execution", "date_publication"],
            ["AQUASEARCH 228638-2026 Page 14/17", "HTI", "2026-08-01"],
        ]
        comptes = sp.seed_depuis_attributions(valeurs)
        self.assertEqual(len(comptes), 1)
        self.assertEqual(comptes[0]["entreprise"], "AQUASEARCH")

    def test_seed_rejette_nom_corrompu_vide(self):
        valeurs = [
            ["gagnant", "pays_execution", "date_publication"],
            ["228638-2026 Page 14/17", "HTI", "2026-08-01"],
        ]
        self.assertEqual(sp.seed_depuis_attributions(valeurs), [])


if __name__ == "__main__":
    unittest.main()
