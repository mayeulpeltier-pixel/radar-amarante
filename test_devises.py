"""Tests de la normalisation des montants en EUR (dashboard._valeur_en_millions).

Le mini-score des attributions pondere par la valeur du marche. Sans conversion,
les marches en devise faible (francs CFA du Sahel) etaient sur-priorises. On
verifie ici que la valeur est ramenee en EUR au point de scoring, que l'euro
(majorite des marches TED) reste inchange, et que les devises inconnues ne sont
pas alterees.
"""

import unittest

import radar_dashboard as dash


class TestNormalisationEuro(unittest.TestCase):

    def test_franc_cfa_ted_ramene_au_minimum(self):
        # 50 000 000 XOF ~ 76 000 EUR : doit peser < 1 M (avant : lu comme 50 M).
        self.assertLess(dash._valeur_en_millions("50000000 XOF"), 1.0)
        self.assertLess(dash._valeur_en_millions("50000000 XAF"), 1.0)

    def test_euro_inchange(self):
        # Pivot EUR : les marches en euros ne bougent pas.
        self.assertAlmostEqual(dash._valeur_en_millions("5000000 EUR"), 5.0, places=3)

    def test_dollar_converti(self):
        # USD -> EUR (~0.92).
        v = dash._valeur_en_millions("5000000 USD")
        self.assertTrue(4.4 < v < 4.8, v)

    def test_livre_convertie(self):
        # GBP est plus forte que l'EUR -> > 1 pour 1 M GBP.
        self.assertGreater(dash._valeur_en_millions("1000000 GBP"), 1.0)

    def test_devise_inconnue_non_alteree(self):
        # QQQ absent de la table -> facteur 1.0, montant inchange.
        self.assertAlmostEqual(dash._valeur_en_millions("1234567 QQQ"),
                               1.234567, places=3)

    def test_devise_ue_complement_repo(self):
        # DKK a ete ajoutee depuis le TED Open Data Service -> doit convertir.
        v = dash._valeur_en_millions("10000000 DKK")
        self.assertTrue(0.9 < v < 1.8, v)   # ~1.34 M EUR

    def test_montant_absent(self):
        self.assertEqual(dash._valeur_en_millions("n.c."), 0.0)
        self.assertEqual(dash._valeur_en_millions(""), 0.0)

    def test_coherence_avec_bm_en_usd(self):
        # La Banque Mondiale publie deja ses montants en "USD X million" :
        # detecte comme USD -> converti en EUR, les inegalites de score tiennent.
        petit = dash._valeur_en_millions("USD 0.014 million")
        gros = dash._valeur_en_millions("USD 205 million")
        self.assertLess(petit, 1.0)
        self.assertGreater(gros, 20.0)


if __name__ == "__main__":
    unittest.main()
