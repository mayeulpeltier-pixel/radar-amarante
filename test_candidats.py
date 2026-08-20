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
