# -*- coding: utf-8 -*-
"""Generateur du cockpit (nouvelle interface) : enrichissement + injection.

Le cockpit reutilise le moteur de radar_dashboard et n'ajoute qu'un champ
`valeur_meur`. On verifie ici que l'injection est propre (placeholders remplaces,
JSON valide) et que la conversion de montant est correcte, sans reseau.
"""

import json
import re
import unittest

import radar_cockpit as rc


def lead(**kw):
    base = {"src": "BM", "zone": "Sahel", "pays": "Mali", "titre": "Marché T",
            "agence": "Banque Mondiale", "final": 8.0, "action": "contacter",
            "sect": "Génie civil / BTP", "statut": "nouveau", "valeur": ""}
    base.update(kw)
    return base


class TestEnrichir(unittest.TestCase):

    def test_valeur_meur_depuis_attribution(self):
        out = rc.enrichir([lead(valeur="9000000 EUR")])
        self.assertEqual(out[0]["valeur_meur"], 9.0)

    def test_avis_sans_montant_donne_zero(self):
        self.assertEqual(rc.enrichir([lead(valeur="")])[0]["valeur_meur"], 0.0)

    def test_montant_illisible_ne_plante_pas(self):
        self.assertEqual(rc.enrichir([lead(valeur="n.c.")])[0]["valeur_meur"], 0.0)

    def test_entree_non_mutee(self):
        src = lead()
        rc.enrichir([src])
        self.assertNotIn("valeur_meur", src)


class TestGenerer(unittest.TestCase):

    def test_placeholders_tous_remplaces(self):
        h = rc.generer_cockpit([lead()])
        for p in ("__LEADS_JSON__", "__COORDS_JSON__", "__RISQUE_JSON__"):
            self.assertNotIn(p, h)

    def test_lead_injecte_visible(self):
        self.assertIn("Route RN17", rc.generer_cockpit([lead(titre="Route RN17")]))

    def test_json_leads_valide(self):
        h = rc.generer_cockpit([lead(valeur="9000000 EUR")])
        m = re.search(r"const RAW=(\[.*?\]), COORDS=", h)
        self.assertIsNotNone(m)
        data = json.loads(m.group(1))
        self.assertEqual(data[0]["valeur_meur"], 9.0)

    def test_coords_injectees(self):
        self.assertIn('"Mali"', rc.generer_cockpit([lead()]))

    def test_liste_vide_ne_plante_pas(self):
        h = rc.generer_cockpit([])
        self.assertIn("const RAW=[]", h)


if __name__ == "__main__":
    unittest.main()
