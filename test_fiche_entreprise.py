# -*- coding: utf-8 -*-
"""Fiche entreprise 360 : le regroupement transverse repose sur ent_cle,
precalcule serveur. Ce test verrouille le CONTRAT DE DONNEES cote generation
(la logique d'agregation JS est exercee dans le navigateur ; ici on garantit
que le JS recevra tout ce qu'il faut, et que l'ancienne vue Watchlist a bien
ete fondue dans Entreprises)."""

import json
import re
import unittest

import radar_cockpit as rc


def _cockpit(leads, watchlist=None):
    return rc.generer_cockpit(leads, watchlist=watchlist or [])


class TestContratFiche(unittest.TestCase):

    def test_leads_portent_entcle(self):
        h = _cockpit([{"src": "ATTRIB", "zone": "Sahel", "pays": "Mali",
                       "titre": "Barrage", "final": 8.0, "action": "contacter",
                       "sect": "Énergie", "entreprise": "Onur Group",
                       "ent_cle": "onur"}])
        raw = json.loads(re.search(r"const RAW=(\[.*?\]), COORDS=", h).group(1))
        self.assertEqual(raw[0]["ent_cle"], "onur")     # porte cote donnees
        self.assertIn("entcle:l.ent_cle", h)            # mappe vers LEADS (JS)

    def test_watchlist_recoit_entcle_meme_cle_que_leads(self):
        # "Onur Group SA" doit produire la meme cle canonique que "Onur Group".
        h = _cockpit([], watchlist=[{"entreprise": "Onur Group SA",
                                     "secteur": "BTP"}])
        wl = json.loads(re.search(r"WATCHLIST=(\[.*?\]), CANDIDATS=", h, re.S).group(1))
        self.assertEqual(wl[0]["ent_cle"], rc.dash._norm_ent("Onur Group"))

    def test_variantes_partagent_la_cle(self):
        # Variantes que _norm_ent canonicalise vraiment (casse + forme juridique
        # + connecteur retires). "Taahhüt" resterait distinctif : ce n'est PAS
        # une fusion automatique, et c'est le comportement voulu du systeme.
        self.assertEqual(rc.dash._norm_ent("ONUR GROUP"),
                         rc.dash._norm_ent("Onur Group SA"))

    def test_fiche_et_fusion_watchlist(self):
        h = _cockpit([{"src": "PRIVÉ", "zone": "Sahel", "pays": "Niger",
                       "titre": "Bureau Niamey", "final": 6.0,
                       "action": "surveiller", "sect": "BTP",
                       "entreprise": "ACME", "ent_cle": "acme"}])
        # La fiche 360 existe, l'ancienne Watchlist a disparu.
        self.assertIn("function openFiche", h)
        self.assertIn("Entreprises 360", h)
        self.assertNotIn("renderWatch", h)
        self.assertNotIn('id="v-watch"', h)
        self.assertNotIn('data-view="watch"', h)


if __name__ == "__main__":
    unittest.main()
