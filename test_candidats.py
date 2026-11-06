# -*- coding: utf-8 -*-
"""Candidats probables (incumbents inferes depuis les attributions).

On verifie que l'index agrege bien les titulaires par secteur/zone, que le tri
priorise les etrangers (prospects Amarante), que la cascade secteur_zone ->
secteur -> zone fonctionne, et que les avis (non-ATTRIB) sont ignores.
"""

import unittest

import candidats_probables as cp


def attrib(ent, sect="Génie civil / BTP", zone="Sahel", origine="TUR",
           etr="oui", mois="2026-07"):
    return {"src": "ATTRIB", "entreprise": ent, "sect": sect, "zone": zone,
            "origine": origine, "etranger_titulaire": etr, "mois": mois}


class TestIndex(unittest.TestCase):

    def test_agrege_par_secteur_zone(self):
        idx = cp.construire_index([attrib("Onur"), attrib("Onur", mois="2026-08")])
        cle = "Génie civil / BTP|Sahel"
        self.assertEqual(idx["secteur_zone"][cle][0]["entreprise"], "Onur")
        self.assertEqual(idx["secteur_zone"][cle][0]["nb"], 2)

    def test_avis_ignore(self):
        idx = cp.construire_index([{"src": "BM", "entreprise": "X",
                                    "sect": "A", "zone": "Z"}])
        self.assertEqual(idx["secteur_zone"], {})

    def test_titulaire_trop_court_ignore(self):
        idx = cp.construire_index([attrib("AB")])
        self.assertEqual(idx["secteur"], {})

    def test_etranger_priorise(self):
        leads = [attrib("Locale", origine="MLI", etr="non"),
                 attrib("Etr", origine="TUR", etr="oui")]
        cand = cp.candidats_pour("Génie civil / BTP", "Sahel", cp.construire_index(leads))
        self.assertEqual(cand[0]["entreprise"], "Etr")

    def test_bool_et_string_etranger(self):
        idx = cp.construire_index([attrib("Alpha", etr=True), attrib("Beta", etr="oui")])
        for c in idx["secteur"]["Génie civil / BTP"]:
            self.assertTrue(c["etranger"])


class TestChampConcurrentiel(unittest.TestCase):
    """Resume concurrentiel qui alimente la dimension winability (P2, 01/11)."""

    def test_match_precis_etranger(self):
        idx = cp.construire_index([attrib("Onur", etr="oui")])
        ch = cp.champ_concurrentiel(idx, "Génie civil / BTP", "Sahel")
        self.assertFalse(ch["ouvert"])
        self.assertTrue(ch["etranger"])
        self.assertEqual(ch["n"], 1)
        self.assertEqual(ch["granularite"], "secteur_zone")

    def test_repli_secteur_puis_zone(self):
        idx = cp.construire_index([attrib("Onur")])
        self.assertEqual(cp.champ_concurrentiel(idx, "Génie civil / BTP", "ZoneX")["granularite"],
                         "secteur")
        self.assertEqual(cp.champ_concurrentiel(idx, "AutreSecteur", "Sahel")["granularite"],
                         "zone")

    def test_terrain_ouvert_si_rien(self):
        idx = cp.construire_index([attrib("Onur")])
        ch = cp.champ_concurrentiel(idx, "Inconnu", "Nulle part")
        self.assertTrue(ch["ouvert"])
        self.assertEqual(ch["n"], 0)
        self.assertFalse(ch["etranger"])

    def test_index_absent_ouvert(self):
        ch = cp.champ_concurrentiel(None, "X", "Y")
        self.assertTrue(ch["ouvert"])

    def test_local_seul_non_etranger(self):
        idx = cp.construire_index([attrib("Locale", origine="MLI", etr="non")])
        ch = cp.champ_concurrentiel(idx, "Génie civil / BTP", "Sahel")
        self.assertFalse(ch["etranger"])
        self.assertFalse(ch["ouvert"])


class TestCascade(unittest.TestCase):

    def setUp(self):
        self.idx = cp.construire_index([attrib("Onur")])

    def test_match_precis(self):
        c = cp.candidats_pour("Génie civil / BTP", "Sahel", self.idx)
        self.assertEqual(c[0]["entreprise"], "Onur")

    def test_fallback_secteur(self):
        c = cp.candidats_pour("Génie civil / BTP", "ZoneX", self.idx)
        self.assertEqual(c[0]["entreprise"], "Onur")

    def test_fallback_zone(self):
        c = cp.candidats_pour("SecteurX", "Sahel", self.idx)
        self.assertEqual(c[0]["entreprise"], "Onur")

    def test_rien_ne_matche(self):
        self.assertEqual(cp.candidats_pour("SecteurX", "ZoneX", self.idx), [])

    def test_index_vide(self):
        self.assertEqual(cp.candidats_pour("A", "B", {}), [])


if __name__ == "__main__":
    unittest.main()


class TestCandidatsProuves(unittest.TestCase):
    """« De vraies propositions, pas n'importe quoi » (06/11/2026)."""
    AUJ = __import__("datetime").date(2026, 11, 6)

    @staticmethod
    def _att(ent, pays="RDC", sect="Génie civil / BTP", agence="Fonds social de la RDC",
             marche="Travaux de réhabilitation d'écoles", date="2026-03-01", projet="", etr=False):
        return {"src": "ATTRIB", "entreprise": ent, "pays": pays, "zone": "Afrique centrale",
                "sect": sect, "agence": agence, "marche": marche, "date_det": date,
                "projet_id": projet, "lien": "https://x/" + ent, "etranger_titulaire": etr}

    AVIS = {"src": "AFDB", "pays": "RDC", "zone": "Afrique centrale", "sect": "Génie civil / BTP",
            "agence": "Fonds social de la RDC", "projet_id": "P-CD-PRECAPE"}

    def test_regression_sonde_les_negociants_ne_sortent_plus(self):
        # Le cas reel : fournisseurs ONU, meme zone continentale, secteur vague,
        # financeur en guise d'acheteur. Avant : 55 % chacun. Maintenant : rien.
        avis = {"src": "AFDB", "pays": "RDC", "zone": "Afrique centrale", "sect": "Autre",
                "agence": "African Development Bank"}
        leads = [self._att("Monega Enterprises", pays="Ouganda", sect="Autre",
                           agence="African Development Bank", marche="Supply of medicines"),
                 self._att("Peak International Trade", pays="RDC", sect="Autre",
                           agence="African Development Bank", marche="Supply of IT equipment"),
                 self._att("LA FLEUR SARL", pays="RDC", sect="Autre",
                           agence="African Development Bank", marche="Services de nettoyage")]
        self.assertEqual(cp.candidats_prouves(avis, leads, aujourd=self.AUJ), [])

    def test_meme_projet_preuve_la_plus_forte(self):
        leads = [self._att("SOGEA SATOM", projet="P-CD-PRECAPE", pays="Burundi", agence="x", sect="Eau")]
        c = cp.candidats_prouves(self.AVIS, leads, aujourd=self.AUJ)
        self.assertEqual(c[0]["preuve"], "meme_projet")
        self.assertEqual(c[0]["marches"][0]["lien"], "https://x/SOGEA SATOM")

    def test_meme_acheteur_reel_dans_le_pays(self):
        c = cp.candidats_prouves(self.AVIS, [self._att("GAUFF", sect="Eau")], aujourd=self.AUJ)
        self.assertEqual(c[0]["preuve"], "meme_acheteur")

    def test_financeur_n_est_pas_un_acheteur(self):
        avis = dict(self.AVIS, agence="African Development Bank", sect="Autre", projet_id="")
        leads = [self._att("X Co", agence="African Development Bank", sect="Autre")]
        self.assertEqual(cp.candidats_prouves(avis, leads, aujourd=self.AUJ), [])

    def test_meme_pays_secteur_recent_seulement(self):
        avis = dict(self.AVIS, agence="Autre ministere", projet_id="")
        self.assertEqual(len(cp.candidats_prouves(avis, [self._att("Alpha", agence="y")], aujourd=self.AUJ)), 1)
        vieux = self._att("Bravo", agence="y", date="2021-01-01")
        self.assertEqual(cp.candidats_prouves(avis, [vieux], aujourd=self.AUJ), [])
        autre_pays = self._att("Charlie", agence="y", pays="Rwanda")
        self.assertEqual(cp.candidats_prouves(avis, [autre_pays], aujourd=self.AUJ), [])

    def test_fournitures_exclues_meme_avec_preuve_forte(self):
        leads = [self._att("Pharma SA", projet="P-CD-PRECAPE", marche="Fourniture de médicaments")]
        self.assertEqual(cp.candidats_prouves(self.AVIS, leads, aujourd=self.AUJ), [])

    def test_tri_et_plafond(self):
        leads = [self._att("Local", sect="Génie civil / BTP", agence="y"),
                 self._att("Etranger", sect="Génie civil / BTP", agence="y", etr=True),
                 self._att("Projet", projet="P-CD-PRECAPE", agence="y")]
        c = cp.candidats_prouves(self.AVIS, leads, n=2, aujourd=self.AUJ)
        self.assertEqual([x["entreprise"] for x in c], ["Projet", "Etranger"])
