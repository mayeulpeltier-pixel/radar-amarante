# -*- coding: utf-8 -*-
"""Tests ADB : resolution pays multi-format (code avant/apres deux-points,
entre parentheses, regional), filtrage hors-zone, canari de derive, pipeline.
Aucun appel reseau ni LLM."""

import unittest

import adb_radar as adb
import ted_complet_v14 as ted


FLUX_SIMULE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>ADB Tenders</title>
  <item>
    <title>TA-10693 ARM: Armenia Multisector Reform - National Capital Markets Legal Expert (59351-001)</title>
    <link>https://www.adb.org/projects/tenders/59351-001</link>
    <pubDate>Mon, 06 Jul 2026 09:00:00 +0000</pubDate>
    <description>Individual - Consulting. Deadline: 30 Jul 2026.</description>
  </item>
  <item>
    <title>Loan 4534: NEP - Kathmandu Valley Water Supply Improvement Project (55074-002)</title>
    <link>https://www.adb.org/projects/tenders/55074-002</link>
    <pubDate>Sun, 05 Jul 2026 09:00:00 +0000</pubDate>
    <description>Works. Construction of distribution network.</description>
  </item>
  <item>
    <title>TA-10041 REG: National Project Development Coordinator (AZE: Wastewater Treatment) (56136-001)</title>
    <link>https://www.adb.org/projects/tenders/56136-001</link>
    <pubDate>Sat, 04 Jul 2026 09:00:00 +0000</pubDate>
    <description>Advance notice. Individual - Consulting.</description>
  </item>
  <item>
    <title>Institutional and Commercial Specialist (58369-001)</title>
    <link>https://www.adb.org/projects/tenders/58369-001</link>
    <pubDate>Fri, 03 Jul 2026 09:00:00 +0000</pubDate>
    <description>Regional; Water and other urban infrastructure.</description>
  </item>
  <item>
    <title>SC 126806 VIE: Viet Nam Country Partnership Strategy - National Economics Analyst</title>
    <link>https://www.adb.org/projects/tenders/vie-1</link>
    <pubDate>Thu, 02 Jul 2026 09:00:00 +0000</pubDate>
    <description>Individual - Consulting.</description>
  </item>
  <item>
    <title>TA-99999 PAK: Balochistan Water Resources - Firm Consulting (59999-001)</title>
    <link>https://www.adb.org/projects/tenders/59999-001</link>
    <pubDate>Wed, 01 Jul 2026 09:00:00 +0000</pubDate>
    <description>Firm - Consulting. Prequalification.</description>
  </item>
</channel></rss>"""


class TestResolutionPays(unittest.TestCase):
    def test_code_avant_deux_points(self):
        iso, hz = adb.resoudre_iso3("TA-10693 ARM: Armenia Reform (59351-001)")
        self.assertEqual(iso, "ARM")

    def test_code_apres_deux_points(self):
        iso, hz = adb.resoudre_iso3("Loan 4534: NEP - Kathmandu Valley")
        self.assertEqual(iso, "NPL")

    def test_code_entre_parentheses_prime_sur_reg(self):
        # REG present mais AZE (suivi) doit gagner.
        iso, hz = adb.resoudre_iso3("TA-10041 REG: Coordinator (AZE: Wastewater)")
        self.assertEqual(iso, "AZE")

    def test_regional_seul_non_resolu(self):
        iso, hz = adb.resoudre_iso3("Institutional Specialist Regional Water")
        self.assertEqual(iso, "")
        self.assertEqual(hz, "")

    def test_hors_zone_reconnu_puis_rejete(self):
        # VIE reconnu (Vietnam) mais hors zone suivie -> iso vide + code signale.
        iso, hz = adb.resoudre_iso3("SC 126806 VIE: Viet Nam Strategy")
        self.assertEqual(iso, "")
        self.assertEqual(hz, "VIE")

    def test_code_minuscule_ne_matche_pas(self):
        # 'ban' en minuscule (mot courant) ne doit pas matcher le code BAN.
        iso, hz = adb.resoudre_iso3("proposal to ban single-use plastics")
        self.assertEqual(iso, "")

    def test_mapping_adb_non_iso3(self):
        self.assertEqual(adb.CODE_ADB_VERS_ISO3["VIE"], "VNM")
        self.assertEqual(adb.CODE_ADB_VERS_ISO3["NEP"], "NPL")
        self.assertEqual(adb.CODE_ADB_VERS_ISO3["BAN"], "BGD")
        self.assertEqual(adb.CODE_ADB_VERS_ISO3["INO"], "IDN")


class TestTypeNotice(unittest.TestCase):
    def test_amont(self):
        self.assertEqual(adb.type_notice("Advance notice consulting")[1], "amont")
        self.assertEqual(adb.type_notice("Prequalification of firms")[1], "amont")

    def test_tender(self):
        self.assertEqual(adb.type_notice("Works civil construction")[1], "tender")
        self.assertEqual(adb.type_notice("Individual - Consulting")[1], "tender")


class TestExtraction(unittest.TestCase):
    def test_reference_extraite(self):
        m = adb._RE_REFERENCE.search("... Legal Expert (59351-001)")
        self.assertEqual(m.group(1), "59351-001")

    def test_deadline_parsee(self):
        self.assertEqual(adb._extraire_deadline("Deadline: 30 Jul 2026."), "2026-07-30")

    def test_deadline_absente(self):
        self.assertEqual(adb._extraire_deadline("no date here"), "")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.avis, self.stats = adb.collecter_et_normaliser(fetch=lambda: FLUX_SIMULE)

    def test_parse_tous(self):
        self.assertEqual(self.stats["items"], 6)

    def test_retient_zones_suivies(self):
        pays = sorted(a["pays_execution"] for a in self.avis)
        # ARM, NPL, AZE, PAK attendus ; VIE (hors zone) et Regional exclus.
        self.assertEqual(pays, ["ARM", "AZE", "NPL", "PAK"])

    def test_vietnam_signale_hors_zone(self):
        self.assertIn("VIE", self.stats["prefixes_hors_zone"])

    def test_reference_comme_publication_number(self):
        arm = [a for a in self.avis if a["pays_execution"] == "ARM"][0]
        self.assertEqual(arm["publication_number"], "59351-001")

    def test_deadline_sur_arm(self):
        arm = [a for a in self.avis if a["pays_execution"] == "ARM"][0]
        self.assertEqual(arm["deadline"], "2026-07-30")

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


if __name__ == "__main__":
    unittest.main()
