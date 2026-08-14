# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DU PERIMETRE COMMERCIAL (Amerique latine / Asie).
==========================================================================

CONTEXTE (22/07/2026)
---------------------
Constat de l'analyste : tres peu de leads en Asie et en Amerique latine. Cause
trouvee : sur 120 pays de l'univers, 34 seulement etaient classes
individuellement ; les 86 autres restaient a 0.3, et le filtre de la Banque
Mondiale (TIER_RISQUE_MINIMAL = 0.6) les ecartait AVANT toute analyse. Cote
Amerique latine, seuls Haiti, le Mexique et la Jamaique passaient.

LE POINT DE CONCEPTION QUE CES TESTS PROTEGENT
----------------------------------------------
Deux notions ont ete DECOUPLEES, et doivent le rester :
  - MULTIPLICATEUR_ZONE     : evaluation FACTUELLE du risque, gonfle le score.
  - PAYS_COUVERTS_AMARANTE  : perimetre COMMERCIAL, ouvre la collecte.

La tentation est d'ouvrir un pays en le classant plus dangereux qu'il n'est.
Ce serait detruire la hierarchie du radar : un lead chilien scorerait comme un
lead malien. Les tests ci-dessous echouent si quelqu'un refait ce raccourci.
"""

import unittest

import ted_complet_v14 as ted


ROUGES = {"MEX": "Mexique", "VEN": "Venezuela", "ECU": "Equateur",
          "HND": "Honduras"}
ORANGES = {"COL": "Colombie", "GTM": "Guatemala", "PER": "Perou",
           "BOL": "Bolivie", "IDN": "Indonesie",
           "SLV": "Salvador", "NIC": "Nicaragua"}
COUVERTS_RISQUE_FAIBLE = {"BRA": "Bresil", "ARG": "Argentine", "CHL": "Chili",
                          "MNG": "Mongolie", "PAN": "Panama"}


class TestGrilleDeRisque(unittest.TestCase):
    """La grille validee par l'analyste, pays par pays."""

    def test_rouges(self):
        for iso, nom in ROUGES.items():
            self.assertEqual(ted.MULTIPLICATEUR_ZONE.get(iso), 1.0,
                             "{} devrait etre rouge (1.0)".format(nom))

    def test_oranges(self):
        for iso, nom in ORANGES.items():
            self.assertEqual(ted.MULTIPLICATEUR_ZONE.get(iso), 0.6,
                             "{} devrait etre orange (0.6)".format(nom))

    def test_pays_couverts_a_risque_faible_restent_bas(self):
        """LE test qui protege la hierarchie : etre dans le perimetre
        commercial ne doit JAMAIS surevaluer le risque."""
        for iso, nom in COUVERTS_RISQUE_FAIBLE.items():
            self.assertEqual(
                ted.MULTIPLICATEUR_ZONE.get(iso), 0.3,
                "{} est couvert commercialement mais ne doit pas scorer "
                "comme une zone de conflit".format(nom))

    def test_hierarchie_preservee(self):
        """Un lead malien doit continuer de primer sur un lead chilien."""
        self.assertGreater(ted.MULTIPLICATEUR_ZONE["MLI"],
                           ted.MULTIPLICATEUR_ZONE["CHL"])
        self.assertGreater(ted.MULTIPLICATEUR_ZONE["ECU"],
                           ted.MULTIPLICATEUR_ZONE["COL"])
        self.assertGreater(ted.MULTIPLICATEUR_ZONE["COL"],
                           ted.MULTIPLICATEUR_ZONE["BRA"])


class TestPerimetreCommercial(unittest.TestCase):

    def test_les_treize_pays_sont_collectes(self):
        """Le but de tout l'exercice : ils passent desormais le filtre."""
        for iso in list(ROUGES) + list(ORANGES) + list(COUVERTS_RISQUE_FAIBLE):
            self.assertTrue(ted.dans_le_perimetre(iso),
                            "{} devrait etre collecte".format(iso))

    def test_un_pays_calme_hors_perimetre_reste_ecarte(self):
        """Le filtre doit continuer de filtrer : sans quoi le radar se noie."""
        for iso in ("URY", "PRY", "LKA"):     # dans l'univers, tier 0.3
            self.assertFalse(ted.dans_le_perimetre(iso),
                             "{} ne devrait pas etre collecte".format(iso))

    def test_pays_inconnu_ecarte(self):
        self.assertFalse(ted.dans_le_perimetre("CHE"))   # Suisse
        self.assertFalse(ted.dans_le_perimetre(""))
        self.assertFalse(ted.dans_le_perimetre(None))

    def test_zone_a_risque_passe_sans_etre_dans_le_perimetre(self):
        """Les pays a risque eleve restent collectes par leur seul tier."""
        for iso in ("MLI", "SOM", "UKR", "AFG"):
            self.assertNotIn(iso, ted.PAYS_COUVERTS_AMARANTE)
            self.assertTrue(ted.dans_le_perimetre(iso))

    def test_perimetre_pilotable_par_env(self):
        """Fondation multi-client : chaque client aura son perimetre, sur la
        meme evaluation de risque."""
        import importlib
        import os
        avant = os.environ.get("RADAR_PAYS_COUVERTS")
        os.environ["RADAR_PAYS_COUVERTS"] = "chl, arg"
        try:
            importlib.reload(ted)
            self.assertEqual(ted.PAYS_COUVERTS_AMARANTE, {"CHL", "ARG"})
            self.assertTrue(ted.dans_le_perimetre("CHL"))
            self.assertFalse(ted.dans_le_perimetre("BRA"))
        finally:
            if avant is None:
                os.environ.pop("RADAR_PAYS_COUVERTS", None)
            else:
                os.environ["RADAR_PAYS_COUVERTS"] = avant
            importlib.reload(ted)


class TestAffichageDashboard(unittest.TestCase):

    def test_aucun_pays_du_perimetre_en_non_classe(self):
        """Un lead qui remonte mais tombe dans 'Non classe' est invisible."""
        import radar_dashboard as dash
        for iso in list(ROUGES) + list(ORANGES) + list(COUVERTS_RISQUE_FAIBLE):
            self.assertIn(iso, dash.ZONE_PAR_ISO3,
                          "{} tomberait dans 'Non classe'".format(iso))


class TestCorrespondanceDesNoms(unittest.TestCase):
    """Piege decouvert le 22/07/2026 en preparant le collecteur IDB : ouvrir
    un pays dans PAYS_COUVERTS_AMARANTE ne sert A RIEN si son NOM ne se
    traduit pas en ISO3. La Banque Mondiale recoit des noms ("Argentina"),
    pas des codes : sans correspondance, code_iso3_pays renvoie "" -> tier 0.2
    -> pays ecarte, en silence, malgre son ajout au perimetre."""

    NOMS = {"Argentina": "ARG", "Brazil": "BRA", "Chile": "CHL",
            "Venezuela": "VEN", "Mongolia": "MNG", "Mexico": "MEX",
            "Colombia": "COL", "Peru": "PER", "Bolivia": "BOL",
            "Honduras": "HND", "Guatemala": "GTM", "Ecuador": "ECU",
            "Indonesia": "IDN"}

    def test_chaque_nom_se_traduit(self):
        import ted_complet_bm as bm
        for nom, iso in self.NOMS.items():
            self.assertEqual(bm.code_iso3_pays(nom), iso,
                             "{} ne se traduit pas en {}".format(nom, iso))

    def test_chaque_nom_franchit_le_filtre(self):
        """Le bout de la chaine : nom -> ISO3 -> perimetre -> collecte."""
        import ted_complet_bm as bm
        for nom in self.NOMS:
            self.assertTrue(ted.dans_le_perimetre(bm.code_iso3_pays(nom)),
                            "{} serait ecarte a la collecte".format(nom))

    def test_pas_de_repli_par_sous_chaine(self):
        """La garde historique tient : 'mali' est contenu dans 'somalia'."""
        import ted_complet_bm as bm
        self.assertEqual(bm.code_iso3_pays("Somalia"), "SOM")
        self.assertEqual(bm.code_iso3_pays("Romania"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
