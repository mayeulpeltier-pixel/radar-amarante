"""Tests du referentiel pays officiel (pays_reference) et de son cablage.

Le referentiel vient de la codelist country.gc du eForms-SDK (OP-TED). Objectif :
resoudre un nom en ISO3 malgre les variantes d'orthographe (Irak/Iraq, accents,
RDC), et enrichir la detection de mentions pays dans le texte presse.
"""

import unittest

import pays_reference as pr


class TestResolveur(unittest.TestCase):

    def test_forme_officielle_et_usuelle_convergent(self):
        # "Iraq" est la forme UE officielle, "Irak" la forme FR usuelle (alias).
        self.assertEqual(pr.resoudre("Iraq"), "IRQ")
        self.assertEqual(pr.resoudre("Irak"), "IRQ")

    def test_insensible_accents_et_casse(self):
        self.assertEqual(pr.resoudre("Équateur"), "ECU")
        self.assertEqual(pr.resoudre("equateur"), "ECU")
        self.assertEqual(pr.resoudre("ÉTHIOPIE"), "ETH")

    def test_alias_rdc(self):
        self.assertEqual(pr.resoudre("RDC"), "COD")
        self.assertEqual(pr.resoudre("République démocratique du Congo"), "COD")

    def test_code_iso3_direct(self):
        self.assertEqual(pr.resoudre("FRA"), "FRA")
        self.assertEqual(pr.resoudre("mli"), "MLI")   # nom, pas code -> via index

    def test_nom_en(self):
        self.assertEqual(pr.resoudre("Yemen"), "YEM")

    def test_inconnu_renvoie_none(self):
        self.assertIsNone(pr.resoudre("Wakanda"))
        self.assertIsNone(pr.resoudre(""))
        self.assertIsNone(pr.resoudre(None))

    def test_nom_fr_officiel(self):
        self.assertEqual(pr.nom_fr("IRQ"), "Iraq")
        self.assertEqual(pr.nom_fr("COD"), "République démocratique du Congo")
        self.assertEqual(pr.nom_fr("ZZZ"), "ZZZ")     # inconnu -> code

    def test_noms_pour_couvre_fr_accents_et_en(self):
        formes = pr.noms_pour("ECU")
        self.assertIn("équateur", formes)     # FR accentue
        self.assertIn("equateur", formes)     # FR sans accent
        self.assertIn("ecuador", formes)      # EN
        self.assertEqual(pr.noms_pour("ZZZ"), set())

    def test_couverture_referentiel(self):
        # Le referentiel doit couvrir tout le perimetre (echantillon).
        for iso3 in ("LBY", "MLI", "COD", "SSD", "YEM", "SOM", "IRQ",
                     "UKR", "VEN", "ECU", "HTI", "AFG", "SLV"):
            self.assertIn(iso3, pr.PAYS_FR, "ISO3 {} absent".format(iso3))


class TestCablageDetectionPresse(unittest.TestCase):
    """L'enrichissement de signaux_prives._noms_pays_risque doit faire capter
    les formes que la liste manuelle ratait."""

    def setUp(self):
        import signaux_prives
        self.sp = signaux_prives

    def test_forme_anglaise_captee(self):
        self.assertTrue(self.sp._mentionne_pays_risque(
            "Security manager needed for operations in Iraq next quarter"))

    def test_forme_francaise_accentuee_captee(self):
        self.assertTrue(self.sp._mentionne_pays_risque(
            "Mission de supervision au Yémen pour un operateur petrolier"))

    def test_texte_sans_pays_risque(self):
        self.assertFalse(self.sp._mentionne_pays_risque(
            "Ouverture d'un bureau commercial a Lyon, France"))


if __name__ == "__main__":
    unittest.main()
