# -*- coding: utf-8 -*-
"""Tests ADB : parsing de la page tenders (format reel colle par l'utilisateur),
resolution pays via Country/Economy en clair, filtrage hors-zone, garde-fou JS,
compatibilite coeur TED. Aucun appel reseau ni LLM."""

import unittest

import adb_radar as adb
import ted_complet_v14 as ted


# Format REEL de la page tenders (colle depuis adb.org), enrichi de 3 cas de
# controle : Afghanistan (suivi), Viet Nam (hors zone), Regional (sans pays).
PAGE_REELLE = """Showing 1 - 12 of 50079 results for "*"
Sort ByRelevanceDeadlineDate Posted
Status: ActiveDeadline: 01 Sep 2026
[58321-001: L4741-SRI: Mahaweli Water Security Investment Program (MWSIP) Stage 2 Project](https://www.adb.org/sites/default/files/tenders/sri4741-ifb-ncpcp-5b.pdf)
Country/Economy: Sri LankaSector: Agriculture, natural resources and rural developmentPosting Date: 30 Jun 2026
Notice Type:Invitation for BidsApproval Number:4741
Status: ActiveDeadline: 31 Aug 2026
[58040-001: 58040-001 PNG - Urban Water Supply and Sanitation Security and Resilience Improvement Project [UWSSSRIP-Plant-01] [Extended]](https://www.adb.org/sites/default/files/tenders/png58040-001-uwsssrip-plant-01-ifb-ext.pdf)
Country/Economy: Papua New GuineaSector: Water and other urban infrastructure and servicesPosting Date: 22 Jun 2026
Notice Type:Invitation for BidsApproval Number:4802
Status: ActiveDeadline: 15 Sep 2026
[59111-002: L1234-AFG: Kabul Resilient Infrastructure - Prequalification of Contractors](https://www.adb.org/sites/default/files/tenders/afg59111-pq.pdf)
Country/Economy: AfghanistanSector: TransportPosting Date: 28 Jun 2026
Notice Type:PrequalificationApproval Number:1234
Status: ActiveDeadline: 20 Sep 2026
[60222-001: L5678-VIE: Ho Chi Minh Urban Rail - Individual Consultant](https://www.adb.org/sites/default/files/tenders/vie60222.pdf)
Country/Economy: Viet NamSector: TransportPosting Date: 25 Jun 2026
Notice Type:Individual - ConsultingApproval Number:5678
Status: ActiveDeadline: 10 Oct 2026
[61333-001: Regional Capacity Building - Firm Consulting](https://www.adb.org/sites/default/files/tenders/reg61333.pdf)
Country/Economy: RegionalSector: Public sector managementPosting Date: 20 Jun 2026
Notice Type:Firm - ConsultingApproval Number:9999
"""


class TestTexteBrut(unittest.TestCase):
    def test_ancre_pdf_html_preservee(self):
        html = '<a href="https://x.org/a.pdf">Titre Projet</a> Country/Economy: Nepal'
        t = adb._texte_brut(html)
        self.assertIn("[Titre Projet](https://x.org/a.pdf)", t)

    def test_balises_retirees(self):
        self.assertNotIn("<div>", adb._texte_brut("<div>x</div>"))


class TestParsingNotices(unittest.TestCase):
    def setUp(self):
        self.notices = adb.parser_notices(PAGE_REELLE)

    def test_nombre_notices(self):
        self.assertEqual(len(self.notices), 5)

    def test_pays_en_clair(self):
        pays = [n["pays_clair"] for n in self.notices]
        self.assertIn("Sri Lanka", pays)
        self.assertIn("Papua New Guinea", pays)

    def test_reference_extraite(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertEqual(sri["reference"], "58321-001")

    def test_lien_pdf_extrait(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertTrue(sri["lien"].endswith("sri4741-ifb-ncpcp-5b.pdf"))

    def test_deadline_et_posting(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertEqual(sri["deadline"], "01 Sep 2026")
        self.assertEqual(sri["posting_date"], "30 Jun 2026")

    def test_type_notice_texte(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertIn("Invitation for Bids", sri["type_notice_txt"])


class TestResolutionPays(unittest.TestCase):
    def test_pays_suivi(self):
        self.assertEqual(adb.resoudre_iso3("Sri Lanka")[0], "LKA")
        self.assertEqual(adb.resoudre_iso3("Papua New Guinea")[0], "PNG")
        self.assertEqual(adb.resoudre_iso3("Afghanistan")[0], "AFG")

    def test_hors_zone_signale(self):
        iso, hz = adb.resoudre_iso3("Viet Nam")
        self.assertEqual(iso, "")
        self.assertEqual(hz, "Viet Nam")

    def test_regional_ignore(self):
        self.assertEqual(adb.resoudre_iso3("Regional"), ("", ""))


class TestTypeNotice(unittest.TestCase):
    def test_amont(self):
        self.assertEqual(adb.type_notice("Prequalification")[1], "amont")
        self.assertEqual(adb.type_notice("Advance notice")[1], "amont")

    def test_tender(self):
        self.assertEqual(adb.type_notice("Invitation for Bids")[1], "tender")
        self.assertEqual(adb.type_notice("Individual - Consulting")[1], "tender")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.avis, self.stats = adb.collecter_et_normaliser(fetch=lambda url=None: PAGE_REELLE)

    def test_garde_fou_non_declenche(self):
        self.assertFalse(self.stats["page_vide"])

    def test_retient_zones_suivies(self):
        pays = sorted(a["pays_execution"] for a in self.avis)
        # Sri Lanka, PNG, Afghanistan gardes ; Viet Nam et Regional exclus.
        self.assertEqual(pays, ["AFG", "LKA", "PNG"])

    def test_vietnam_signale(self):
        self.assertIn("Viet Nam", self.stats["pays_hors_zone"])

    def test_afghanistan_est_amont(self):
        afg = [a for a in self.avis if a["pays_execution"] == "AFG"][0]
        self.assertEqual(afg["phase"], "amont")   # Prequalification

    def test_deadline_convertie_iso(self):
        sri = [a for a in self.avis if a["pays_execution"] == "LKA"][0]
        self.assertEqual(sri["deadline"], "2026-09-01")

    def test_reference_comme_publication_number(self):
        sri = [a for a in self.avis if a["pays_execution"] == "LKA"][0]
        self.assertEqual(sri["publication_number"], "58321-001")

    def test_avis_compatible_coeur_ted(self):
        avis = self.avis[0]
        extraction = {
            "deploiement_terrain_reel": True, "type_mobilite": "terrain_isole",
            "profil_personnes_exposees": "expert_international",
            "securite_existante_detectee": False, "type_client": "bailleur_donateur",
            "accessibilite_commerciale": "moyenne", "duree_estimee": "moyenne",
            "niveau_opportunite_amarante": "moyen", "confiance": 0.8,
        }
        s, c, f = ted.calculer_scores(avis, extraction)
        self.assertGreaterEqual(f, 0.0)
        self.assertLessEqual(f, 10.0)


class TestGardeFouJS(unittest.TestCase):
    def test_page_vide_declenche_garde_fou(self):
        # Page rendue en JS = pas de notices -> garde-fou.
        avis, stats = adb.collecter_et_normaliser(
            fetch=lambda url=None: "<html><body><div id='app'></div></body></html>")
        self.assertTrue(stats["page_vide"])
        self.assertEqual(avis, [])


if __name__ == "__main__":
    unittest.main()
