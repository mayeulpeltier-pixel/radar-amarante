# -*- coding: utf-8 -*-
"""Surveiller un projet amont -> verifier l'attribution a chaque run (12/08/2026).

Le run matche un avis surveille contre attributions_radar (similarite titre +
agence). Match -> statut 'attribution_publiee' + gagnant. Matching PUR (teste
ici) ; la colle Postgres est best-effort. Cote client : cablage dans le gabarit.
"""

import unittest

import surveillance_attributions as sv
import radar_dashboard as rd


class TestSimilarite(unittest.TestCase):
    def test_recouvrement_fort(self):
        s = sv.similarite("Mali Electricity Sector Improvement",
                          "Electricity sector improvement works Mali")
        self.assertGreater(s, 0.5)

    def test_sans_recouvrement(self):
        self.assertEqual(sv.similarite("Route RN1 bitume", "Fourniture mobilier"), 0.0)

    def test_mots_vides_ignores(self):
        # 'de/des/the/of' ne doivent pas gonfler le score
        self.assertEqual(sv.similarite("de la des les", "the of and for"), 0.0)


class TestChercherAttribution(unittest.TestCase):
    def _atts(self):
        return [
            {"titre": "Fourniture de mobilier scolaire", "acheteur": "Ville", "gagnant": "Local SARL"},
            {"titre": "Electricity sector improvement - works", "acheteur": "Ministere Energie", "gagnant": "Bouygues Energies"},
        ]

    def test_match_au_dessus_du_seuil(self):
        item = {"titre": "Mali Electricity Sector Improvement Program",
                "acheteur": "Ministere Energie"}
        att, score = sv.chercher_attribution(item, self._atts())
        self.assertIsNotNone(att)
        self.assertEqual(sv.gagnant_de(att), "Bouygues Energies")

    def test_pas_de_match_sous_le_seuil(self):
        item = {"titre": "Construction hopital pediatrique", "acheteur": "Sante"}
        att, score = sv.chercher_attribution(item, self._atts())
        self.assertIsNone(att)

    def test_seuil_reglable(self):
        item = {"titre": "mobilier", "acheteur": "Ville"}
        self.assertIsNone(sv.chercher_attribution(item, self._atts(), seuil=0.9)[0])
        self.assertIsNotNone(sv.chercher_attribution(item, self._atts(), seuil=0.1)[0])


class TestEvaluer(unittest.TestCase):
    def test_evaluation_groupee(self):
        surv = [("bm_projets_radar", "BMP-P1"), ("bm_projets_radar", "BMP-P2")]
        index = {
            ("bm_projets_radar", "BMP-P1"): {"titre": "Mali Electricity Sector Improvement", "acheteur": "Energie"},
            ("bm_projets_radar", "BMP-P2"): {"titre": "Something totally unrelated xyz", "acheteur": "Z"},
        }
        atts = [{"titre": "Electricity sector improvement Mali", "acheteur": "Energie", "gagnant": "Bouygues"}]
        res = sv.evaluer_surveillances(surv, index, atts)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0][:3], ("bm_projets_radar", "BMP-P1", "Bouygues"))

    def test_item_introuvable_saute(self):
        res = sv.evaluer_surveillances([("x", "P")], {}, [{"titre": "a", "gagnant": "G"}])
        self.assertEqual(res, [])

    def test_gagnant_vide_ignore(self):
        surv = [("o", "P")]
        index = {("o", "P"): {"titre": "Electricity sector", "acheteur": ""}}
        atts = [{"titre": "Electricity sector", "acheteur": "", "gagnant": ""}]
        self.assertEqual(sv.evaluer_surveillances(surv, index, atts), [])


class TestCablageDashboard(unittest.TestCase):
    def setUp(self):
        self.html = rd.GABARIT_HTML

    def test_bouton_et_fonctions(self):
        for m in ("data-surveiller", "function marquerSurveille",
                  "function renderSurveillance", "function estSurveille",
                  "data-arreter-surv", "'surveille'"):
            self.assertIn(m, self.html, "cablage surveillance absent : {}".format(m))

    def test_badges(self):
        self.assertIn("Attribution publiée", self.html)
        self.assertIn("👁 Surveillé", self.html)
        self.assertIn("surv-body", self.html)

    def test_lead_attribution_publiee_porte_le_gagnant(self):
        row = {"titre": "x", "pays_execution": "MLI", "score_final": "8",
               "action_recommandee": "contacter", "publication_number": "BMP-1",
               "statut_suivi": "attribution_publiee", "motif_ecart": "Bouygues"}
        lead = rd.ligne_vers_lead(row, "BMP")
        self.assertEqual(lead["statut"], "attribution_publiee")
        self.assertEqual(lead["motif_ecart"], "Bouygues")


if __name__ == "__main__":
    unittest.main()
