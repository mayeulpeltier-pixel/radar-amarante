# -*- coding: utf-8 -*-
"""Extracteur de montant des avis BM (texte libre). On teste les garde-fous
sur les fragments REELS remontes par la sonde montant :
  acceptes  : "$ 120,000", "14.15 million", "7.1 Million"
  rejetes   : "USD 400", "$ 100", "USD 150"  (numeros/seuils, pas des montants)
Plus le cablage : valeur_estimee est peuplee et exposee en colonne.
"""

import unittest

import ted_complet_bm as bm


class TestExtraireMontant(unittest.TestCase):

    def test_rejette_petit_montant_sans_magnitude(self):
        for t in ("Reference USD 400 du dossier", "voir $ 100 au chapitre",
                  "USD 150 pages"):
            self.assertEqual(bm.extraire_montant(t), "inconnu", t)

    def test_accepte_montant_groupe_avec_devise(self):
        self.assertEqual(bm.extraire_montant("Budget estime $ 120,000 pour l'etude"),
                         "120000 USD")

    def test_accepte_magnitude_million(self):
        self.assertEqual(bm.extraire_montant("cost about 14.15 million"),
                         "14150000 USD")
        self.assertEqual(bm.extraire_montant("estimated 7.1 Million USD"),
                         "7100000 USD")

    def test_accepte_milliard(self):
        self.assertEqual(bm.extraire_montant("total project 1.2 billion USD"),
                         "1200000000 USD")

    def test_retient_le_plus_gros(self):
        # Un petit chiffre parasite + un vrai montant -> on garde le gros.
        self.assertEqual(
            bm.extraire_montant("lot 3, USD 250 million de travaux, page 4"),
            "250000000 USD")

    def test_devise_euro_preservee(self):
        self.assertEqual(bm.extraire_montant("marche de 5 000 000 EUR"),
                         "5000000 EUR")

    def test_annee_seule_ignoree(self):
        self.assertEqual(bm.extraire_montant("publie en 2026 pour la region"),
                         "inconnu")

    def test_vide_donne_inconnu(self):
        self.assertEqual(bm.extraire_montant("", None, "supervision"), "inconnu")

    def test_borne_haute_ignore_bruit(self):
        self.assertEqual(bm.extraire_montant("code 999999999999 USD interne"),
                         "inconnu")


class TestCablageColonne(unittest.TestCase):

    def test_valeur_estimee_dans_colonnes(self):
        self.assertEqual(bm.COLONNES_BM[-1], "valeur_estimee")

    def test_normaliser_peuple_valeur_estimee(self):
        rec = {"id": "1", "project_id": "P100000",
               "bid_description": "Works, budget 30 million USD",
               "project_name": "Route", "project_ctry_name": "Mali",
               "notice_text": "Estimated contract value 30 million USD."}
        avis = bm.normaliser_bm(rec)
        self.assertEqual(avis["valeur_estimee"], "30000000 USD")

    def test_normaliser_inconnu_si_rien(self):
        rec = {"id": "2", "project_name": "Supervision", "bid_description": "",
               "project_ctry_name": "Niger", "notice_text": "Consulting services."}
        self.assertEqual(bm.normaliser_bm(rec)["valeur_estimee"], "inconnu")


if __name__ == "__main__":
    unittest.main()
