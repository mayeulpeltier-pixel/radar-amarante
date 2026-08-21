# -*- coding: utf-8 -*-
"""Enveloppe projet BM amont : le montant (totalamt/lendprojectcost) est
expose comme champ DISTINCT du montant marche, jamais melange aux sommes/tri
"valeur". On verifie la chaine complete : collecteur -> dashboard -> cockpit.
"""

import datetime as _dt
import json
import re
import unittest

import bm_projets as bmp
import radar_cockpit as rc
import radar_dashboard as dash


def _projet(**kw):
    base = {"id": "P100000", "proj_id": "P100000", "project_name": "Route RN1",
            "countryshortname": "Mali", "status": "active",
            "boardapprovaldate": _dt.date.today().isoformat() + "T00:00:00Z",
            "totalamt": "150000000", "lendprojectcost": "150000000"}
    base.update(kw)
    return base


class TestBmpEnveloppe(unittest.TestCase):

    def test_colonne_enveloppe_en_fin(self):
        self.assertEqual(bmp.COLONNES_BMP[-1], "enveloppe_usd")

    def test_normaliser_expose_enveloppe(self):
        av = bmp.normaliser(_projet())
        self.assertIsNotNone(av)
        self.assertEqual(av["enveloppe_usd"], "150000000 USD")

    def test_sans_montant_enveloppe_vide(self):
        av = bmp.normaliser(_projet(totalamt="0", lendprojectcost="0"))
        self.assertIsNotNone(av)
        self.assertEqual(av["enveloppe_usd"], "")

    def test_ligne_ecrit_enveloppe(self):
        av = bmp.normaliser(_projet())
        ligne = bmp.ligne_depuis_avis(av)
        self.assertEqual(ligne[bmp.COLONNES_BMP.index("enveloppe_usd")],
                         "150000000 USD")


class TestDashboardSepareEnveloppe(unittest.TestCase):

    def test_enveloppe_distincte_du_montant_marche(self):
        # Lead BMP : enveloppe presente, montant marche (valeur) vide.
        lead = dash.ligne_vers_lead(
            {"titre": "Route", "pays_execution": "ML", "score_final": "7",
             "enveloppe_usd": "150000000 USD"}, "BMP")
        self.assertEqual(lead["enveloppe"], "150000000 USD")
        self.assertEqual(lead["valeur"], "")


class TestCockpitAfficheEnveloppe(unittest.TestCase):

    def test_enrichir_calcule_enveloppe_meur_a_part(self):
        out = rc.enrichir([{"src": "BMP", "valeur": "", "enveloppe": "150000000 USD"}])
        self.assertEqual(out[0]["valeur_meur"], 0.0)
        self.assertGreater(out[0]["enveloppe_meur"], 0.0)

    def test_rendu_porte_enveloppe_et_helper(self):
        h = rc.generer_cockpit([{"src": "BMP", "zone": "Sahel", "pays": "Mali",
                                 "titre": "Route", "final": 7.0,
                                 "action": "surveiller", "sect": "Génie civil / BTP",
                                 "valeur": "", "enveloppe": "150000000 USD"}])
        self.assertIn("cellMontant", h)      # helper present
        self.assertIn("enveloppe_meur", h)   # champ injecte
        m = re.search(r"const RAW=(\[.*?\]), COORDS=", h)
        data = json.loads(m.group(1))
        self.assertGreater(data[0]["enveloppe_meur"], 0.0)
        self.assertEqual(data[0]["valeur_meur"], 0.0)


if __name__ == "__main__":
    unittest.main()
