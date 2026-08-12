# -*- coding: utf-8 -*-
"""Collecteur amont BM Projects (12/08/2026).

Schema CONFIRME par sonde. On verrouille : la resolution pays par NOM (le piege
des codes WB non standard est contourne), le scoring deterministe, les filtres
(statut / fenetre / risque), et la pagination bornee. Aucun reseau (fetch simule).
"""

import unittest
from datetime import date, timedelta

import bm_projets as b


AUJ = date(2026, 8, 12)


def _iso(x):
    return (AUJ + timedelta(days=x)).isoformat() + "T00:00:00Z"


def _p(pid, pays, jours, statut="Active", montant="100000000", **k):
    d = {"proj_id": pid, "project_name": "Projet " + pid, "countryshortname": pays,
         "boardapprovaldate": _iso(jours), "status": statut, "totalamt": montant}
    d.update(k)
    return d


class TestResolutionPays(unittest.TestCase):
    def test_nom_simple(self):
        self.assertEqual(b.resoudre_iso3("Mali"), "MLI")
        self.assertEqual(b.resoudre_iso3("Chad"), "TCD")

    def test_rdc_via_alias(self):
        """Le piege du code WB (CD->0) est contourne par le NOM."""
        self.assertEqual(b.resoudre_iso3("Congo, Democratic Republic of"), "COD")
        self.assertEqual(b.resoudre_iso3("Democratic Republic of Congo"), "COD")

    def test_yemen_et_formes_longues(self):
        self.assertEqual(b.resoudre_iso3("Yemen, Republic of"), "YEM")
        self.assertEqual(b.resoudre_iso3("Iran, Islamic Republic of"), "IRN")

    def test_hors_risque_none(self):
        self.assertIsNone(b.resoudre_iso3("Thailand"))
        self.assertIsNone(b.resoudre_iso3("France"))
        self.assertIsNone(b.resoudre_iso3(""))


class TestScoring(unittest.TestCase):
    def test_surete_suit_la_zone(self):
        s_rouge, _, _ = b.scorer("MLI", 0)      # tier 1.0
        s_calme, _, _ = b.scorer("SEN", 0)      # tier 0.3
        self.assertGreater(s_rouge, s_calme)
        self.assertLessEqual(s_rouge, 10.0)

    def test_commercial_suit_le_montant(self):
        self.assertGreater(b._score_commercial(600e6), b._score_commercial(5e6))

    def test_action_par_seuil(self):
        self.assertEqual(b._action(7.0), "contacter")
        self.assertEqual(b._action(5.0), "surveiller")
        self.assertEqual(b._action(3.0), "ignorer")

    def test_final_borne(self):
        _, _, f = b.scorer("MLI", 10e9)
        self.assertLessEqual(f, 10.0)


class TestNormalisation(unittest.TestCase):
    def test_projet_risque_retenu(self):
        a = b.normaliser(_p("P1", "Mali", -30, montant="300000000"), aujourd=AUJ)
        self.assertIsNotNone(a)
        self.assertEqual(a["pays_execution"], "MLI")
        self.assertEqual(a["publication_number"], "BMP-P1")
        self.assertIn("projects.worldbank.org", a["lien_avis"])
        self.assertIn("AMONT", a["justification"])

    def test_statut_dropped_exclu(self):
        self.assertIsNone(b.normaliser(_p("P", "Mali", -10, statut="Dropped"), aujourd=AUJ))

    def test_hors_risque_exclu(self):
        self.assertIsNone(b.normaliser(_p("P", "Thailand", -10), aujourd=AUJ))

    def test_hors_fenetre_exclu(self):
        self.assertIsNone(b.normaliser(_p("P", "Mali", -900), aujourd=AUJ))   # trop vieux
        self.assertIsNone(b.normaliser(_p("P", "Mali", 900), aujourd=AUJ))    # trop futur

    def test_pipeline_dans_fenetre_futur_retenu(self):
        a = b.normaliser(_p("P", "Niger", 120, statut="Pipeline"), aujourd=AUJ)
        self.assertIsNotNone(a)
        self.assertIn("Pipeline", a["type_notice"])


class TestCollecteFlux(unittest.TestCase):
    def _fetch(self, projets_page0):
        def fetch(url, params):
            if params["os"] > 0:
                return {"projects": {}}
            return {"projects": {p["proj_id"]: p for p in projets_page0}}
        return fetch

    def test_saute_futur_garde_fenetre_stoppe_ancien(self):
        page = [
            _p("F", "Mali", 900),      # futur lointain -> saute
            _p("A", "Mali", 30),       # dans la fenetre -> garde
            _p("B", "Niger", -60),     # dans la fenetre -> garde
            _p("V", "Chad", -900),     # trop vieux -> stop (tri desc)
            _p("W", "Mali", -50),      # apres le stop -> jamais atteint
        ]
        gardes = b.collecter_flux(fetch=self._fetch(page), aujourd=AUJ)
        ids = [p["proj_id"] for p in gardes]
        self.assertIn("A", ids)
        self.assertIn("B", ids)
        self.assertNotIn("F", ids)
        self.assertNotIn("V", ids)
        self.assertNotIn("W", ids)   # stop declenche avant

    def test_dedup(self):
        page = [_p("P1", "Mali", -10), _p("P1", "Mali", -10)]
        out = b.collecter_et_normaliser(fetch=self._fetch(page), aujourd=AUJ)
        self.assertEqual(len(out), 1)


class TestSchema(unittest.TestCase):
    def test_ligne_respecte_le_schema(self):
        a = b.normaliser(_p("P1", "Mali", -30), aujourd=AUJ)
        ligne = b.ligne_depuis_avis(a)
        self.assertEqual(len(ligne), len(b.COLONNES_BMP))

    def test_pays_execution_est_iso3(self):
        a = b.normaliser(_p("P1", "Somalia", -30), aujourd=AUJ)
        self.assertEqual(a["pays_execution"], "SOM")


class TestCablageDashboard(unittest.TestCase):
    """BMP branche dans le dashboard comme une source d'avis ISO (pattern IDB)."""

    def test_construire_leads_accepte_bmp(self):
        import radar_dashboard as rd
        avis = b.normaliser(_p("P1", "Mali", -30), aujourd=AUJ)
        leads = rd.construire_leads([], [], [], {}, [], lignes_bmp=[avis])
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["src"], "BMP")
        self.assertEqual(leads[0]["pays"], "Mali")
        self.assertNotEqual(leads[0]["zone"], "Non classé")

    def test_bmp_dans_le_gabarit(self):
        import radar_dashboard as rd
        html = rd.GABARIT_HTML
        for nom, marqueur in (
                ("badge CSS", ".src.bmp{"),
                ("source filtrable", "'IDB','BMP'"),
                ("libelle carte", "BMP:'BM Projet · amont'"),
                ("libelle bandeau", "BMP:'BM Projets (amont)'"),
                ("compteur avis", "l.src==='BMP'")):
            self.assertIn(marqueur, html, "branchement BMP absent : {}".format(nom))

    def test_bmp_dans_catalogue(self):
        import radar_dashboard as rd
        self.assertIn("BMP", rd.CATALOGUE_SOURCES)

    def test_lire_onglets_dix_sept(self):
        import ast
        src = open("radar_dashboard.py", encoding="utf-8").read()
        arbre = ast.parse(src)
        fonc = next(n for n in ast.walk(arbre)
                    if isinstance(n, ast.FunctionDef) and n.name == "lire_onglets")
        ret = next(n for n in ast.walk(fonc)
                   if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple))
        self.assertEqual(len(ret.value.elts), 17)


if __name__ == "__main__":
    unittest.main()
