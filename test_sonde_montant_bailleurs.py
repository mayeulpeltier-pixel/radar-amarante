# -*- coding: utf-8 -*-
"""Sonde montant bailleurs : le COEUR (scanner de champs / texte, agregation,
verdict) est teste offline, et les probers inject-friendly (fetch injecte)
sont exerces sur des payloads canoniques representatifs de chaque source.

Aucune de ces verifications ne touche le reseau."""

import unittest

import sonde_montant_bailleurs as s


class TestScannerRecord(unittest.TestCase):

    def test_detecte_cle_montant_connue(self):
        rec = {"proj_id": "P1", "totalamt": "125000000", "status": "Active"}
        self.assertEqual(s.scanner_record(rec), [("totalamt", "125000000")])

    def test_detecte_lendprojectcost_et_amount(self):
        rec = {"lendprojectcost": "80000000", "idb_amount": "500000"}
        cles = [c for c, _ in s.scanner_record(rec)]
        self.assertIn("lendprojectcost", cles)
        self.assertIn("idb_amount", cles)

    def test_ignore_valeurs_vides_et_sentinelles(self):
        rec = {"amount": "", "value": "null", "cost": "0", "budget": "inconnu"}
        self.assertEqual(s.scanner_record(rec), [])

    def test_ignore_champ_sans_rapport(self):
        rec = {"titre": "Route", "pays": "Mali", "date": "2026-08-01"}
        self.assertEqual(s.scanner_record(rec), [])

    def test_non_dict_ne_plante_pas(self):
        self.assertEqual(s.scanner_record("pas un dict"), [])
        self.assertEqual(s.scanner_record(None), [])


class TestScannerTexte(unittest.TestCase):

    def test_repere_montant_devise_accolee(self):
        out = s.scanner_texte("Contrat estime a USD 4,500,000 pour la route.")
        self.assertTrue(any("4,500,000" in x for x in out))

    def test_repere_ordre_de_grandeur(self):
        out = s.scanner_texte("Projet de 120 million EUR sur cinq ans.")
        self.assertTrue(out)

    def test_texte_sans_montant_vide(self):
        self.assertEqual(s.scanner_texte("Appel d'offres pour supervision."), [])

    def test_borne_et_dedup(self):
        txt = "USD 10 000 ; USD 10 000 ; USD 20 000 ; USD 30 000"
        out = s.scanner_texte(txt, maxi=2)
        self.assertLessEqual(len(out), 2)


class TestAgregationEtVerdict(unittest.TestCase):

    def test_verdict_exploitable_si_champ_bien_rempli(self):
        recs = [{"totalamt": "100"}, {"totalamt": "200"}, {"totalamt": "300"}]
        rap = s.analyser_echantillon(recs)
        self.assertEqual(rap["n"], 3)
        self.assertEqual(s.verdict(rap)[0], "EXPLOITABLE")

    def test_verdict_partiel_si_champ_rare(self):
        recs = [{"amount": "5"}] + [{"titre": "x"} for _ in range(9)]
        rap = s.analyser_echantillon(recs)
        self.assertEqual(s.verdict(rap)[0], "PARTIEL")

    def test_verdict_texte_libre(self):
        recs = [{"description": "Marche de USD 2,000,000"} for _ in range(3)]
        rap = s.analyser_echantillon(recs)
        # 'description' n'est pas une cle montant -> pas de champ structure,
        # mais la valeur contient un montant -> mention texte.
        self.assertEqual(s.verdict(rap)[0], "TEXTE_LIBRE")

    def test_verdict_absent(self):
        recs = [{"titre": "Supervision", "pays": "Niger"}]
        self.assertEqual(s.verdict(s.analyser_echantillon(recs))[0], "ABSENT")

    def test_exemples_bornes_a_trois(self):
        recs = [{"totalamt": str(i)} for i in range(10)]
        rap = s.analyser_echantillon(recs)
        self.assertLessEqual(len(rap["champs"]["totalamt"]["exemples"]), 3)


class TestProbersInjectes(unittest.TestCase):
    """Probers inject-friendly : on injecte un fetch renvoyant un payload
    canonique, sans reseau. On verifie que le pipeline complet
    (prober -> analyse -> verdict) tient debout."""

    def test_bm_amont_expose_enveloppe(self):
        import bm_projets as bmp
        # collecter_flux(fetch=...) : fetch() renvoie le JSON brut de l'API.
        # Deux projets avec totalamt -> enveloppe exploitable.
        faux = {"projects": {
            "P1": {"id": "P1", "project_name": "Route", "countryshortname": "Mali",
                   "boardapprovaldate": "2026-06-01T00:00:00Z", "status": "Active",
                   "totalamt": "150000000", "lendprojectcost": "150000000"},
            "P2": {"id": "P2", "project_name": "Pont", "countryshortname": "Niger",
                   "boardapprovaldate": "2026-06-01T00:00:00Z", "status": "Active",
                   "totalamt": "90000000", "lendprojectcost": "90000000"}}}
        rap = s.rapport_source("bm_amont", fetch=lambda *a, **k: faux)
        self.assertNotIn("erreur", rap)
        self.assertIn("totalamt", rap["champs"])
        self.assertEqual(rap["verdict"], "EXPLOITABLE")

    def test_afdb_montant_dans_description(self):
        import afdb_radar as afdb
        xml = (u"<?xml version='1.0'?><rss><channel>"
               u"<item><title>Consulting services - Mali</title>"
               u"<link>http://x/1</link><pubDate>2026-08-01</pubDate>"
               u"<description>Estimated contract value USD 3,000,000.</description>"
               u"</item></channel></rss>")
        rap = s.rapport_source("afdb", fetch=lambda *a, **k: xml)
        self.assertNotIn("erreur", rap)
        # RSS = pas de champ structure ; montant en texte libre attendu.
        self.assertEqual(rap["verdict"], "TEXTE_LIBRE")


if __name__ == "__main__":
    unittest.main()
