# -*- coding: utf-8 -*-
"""Tests du collecteur AfDB : parsing RSS, extraction type/pays (EN + FR),
filtrage perimetre, normalisation compatible cœur TED. Aucun appel reseau ni
LLM (fetch injecte, scoring teste via une extraction simulee)."""

import unittest

import afdb_radar as afdb
import ted_complet_v14 as ted


# Flux RSS simule, representatif des cas reels observes.
FLUX_SIMULE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>AfDB Projects Procurement</title>
  <item>
    <title>GPN - Rwanda - Muvumba Multipurpose Water Resources Development Program</title>
    <link>https://www.afdb.org/en/notice/gpn-rwanda-1</link>
    <pubDate>Mon, 06 Jul 2026 09:00:00 +0000</pubDate>
    <description>The Government of Rwanda has received financing... &lt;b&gt;field&lt;/b&gt; supervision.</description>
  </item>
  <item>
    <title>AMI - Djibouti - Recrutement d'un Consultant International pour appui terrain</title>
    <link>https://www.afdb.org/fr/notice/ami-djibouti-2</link>
    <pubDate>Sun, 05 Jul 2026 09:00:00 +0000</pubDate>
    <description>La Republique de Djibouti a obtenu un don...</description>
  </item>
  <item>
    <title>EOI - Eritrea - Individual Consultant for Massawa to Tesseney Road</title>
    <link>https://www.afdb.org/en/notice/eoi-eritrea-3</link>
    <pubDate>Sat, 04 Jul 2026 09:00:00 +0000</pubDate>
    <description>Feasibility study, detailed engineering design.</description>
  </item>
  <item>
    <title>PPM - Multinational - Guinee-Guinee Bissau - Projet route Boke-Quebo</title>
    <link>https://www.afdb.org/fr/notice/ppm-multi-4</link>
    <pubDate>Fri, 03 Jul 2026 09:00:00 +0000</pubDate>
    <description>Amenagement de la route.</description>
  </item>
  <item>
    <title>Contract Awards - Senior Capital Markets Operations Specialist</title>
    <link>https://www.afdb.org/en/notice/award-5</link>
    <pubDate>Thu, 02 Jul 2026 09:00:00 +0000</pubDate>
    <description>Attribution.</description>
  </item>
  <item>
    <title>SPN - France - Internal office supplies</title>
    <link>https://www.afdb.org/en/notice/spn-france-6</link>
    <pubDate>Wed, 01 Jul 2026 09:00:00 +0000</pubDate>
    <description>Hors zone a risque.</description>
  </item>
</channel></rss>"""


class TestParsingTitre(unittest.TestCase):
    def test_type_pays_reste(self):
        t, p, r = afdb.parser_titre("GPN - Rwanda - Muvumba Water Program")
        self.assertEqual((t, p), ("GPN", "Rwanda"))
        self.assertEqual(r, "Muvumba Water Program")

    def test_pays_compose_non_coupe(self):
        # 'Cote d'Ivoire' ne doit pas etre coupe (pas de ' - ' interne).
        t, p, r = afdb.parser_titre("EOI - Cote d'Ivoire - Etude filiere")
        self.assertEqual(p, "Cote d'Ivoire")

    def test_type_notice_amont_vs_tender(self):
        self.assertEqual(afdb.type_notice("GPN")[1], "amont")
        self.assertEqual(afdb.type_notice("EOI")[1], "amont")
        self.assertEqual(afdb.type_notice("SPN")[1], "tender")

    def test_attribution_detectee(self):
        self.assertTrue(afdb.type_notice("Contract Awards")[2])
        self.assertTrue(afdb.type_notice("Attribution de contrat")[2])


class TestResolutionPays(unittest.TestCase):
    def test_nom_anglais(self):
        self.assertEqual(afdb.resoudre_iso3("Rwanda", "GPN - Rwanda - X"), "RWA")

    def test_nom_francais_avec_accent(self):
        # 'Guinee' (sans accent) et 'Guinée' (avec) doivent mapper pareil.
        self.assertEqual(afdb.resoudre_iso3("Guinée", "EOI - Guinée - X"), "GIN")

    def test_niger_pas_confondu_avec_nigeria(self):
        self.assertEqual(afdb.resoudre_iso3("Niger", "GPN - Niger - X"), "NER")
        self.assertEqual(afdb.resoudre_iso3("Nigeria", "GPN - Nigeria - X"), "NGA")

    def test_multinational_repli_scan_titre(self):
        # 'Multinational' inconnu -> on scanne le titre, 'Guinee Bissau' trouve.
        iso = afdb.resoudre_iso3("Multinational",
                                 "PPM - Multinational - Guinee-Guinee Bissau - route")
        self.assertIn(iso, ("GNB", "GIN"))  # un pays a risque reconnu dans le titre

    def test_pays_inconnu_renvoie_vide(self):
        self.assertEqual(afdb.resoudre_iso3("Atlantis", "GPN - Atlantis - X"), "")


class TestPipelineComplet(unittest.TestCase):
    def setUp(self):
        self.avis, self.stats = afdb.collecter_et_normaliser(fetch=lambda: FLUX_SIMULE)

    def test_parse_tous_les_items(self):
        self.assertEqual(self.stats["items"], 6)

    def test_attribution_et_france_exclues(self):
        titres = " ".join(a["titre"] for a in self.avis)
        self.assertNotIn("Contract Awards", titres)   # attribution exclue
        self.assertNotIn("France", titres)            # hors zone a risque exclue

    def test_retient_les_bons(self):
        pays = sorted(a["pays_execution"] for a in self.avis)
        # Rwanda, Djibouti, Eritrea, (Multinational -> Guinee ou Guinee-Bissau)
        self.assertIn("RWA", pays)
        self.assertIn("DJI", pays)
        self.assertIn("ERI", pays)
        self.assertEqual(len(self.avis), 4)

    def test_avis_compatible_coeur_ted(self):
        # Un avis normalise doit passer dans ted.calculer_scores sans erreur,
        # avec une extraction simulee (pas d'appel LLM).
        avis = self.avis[0]
        for cle in ("acheteur", "pays_execution", "titre", "cpv", "description"):
            self.assertIn(cle, avis)
        extraction = {
            "deploiement_terrain_reel": True, "type_mobilite": "terrain_isole",
            "profil_personnes_exposees": "expert_international",
            "securite_existante_detectee": False, "type_client": "bailleur_donateur",
            "accessibilite_commerciale": "moyenne", "duree_estimee": "longue_ou_residente",
            "niveau_opportunite_amarante": "fort", "confiance": 0.8,
        }
        surete, commercial, final = ted.calculer_scores(avis, extraction)
        self.assertGreater(final, 0.0)
        self.assertLessEqual(final, 10.0)

    def test_html_nettoye_dans_description(self):
        rwa = [a for a in self.avis if a["pays_execution"] == "RWA"][0]
        self.assertNotIn("<b>", rwa["description"])

    def test_type_notice_et_phase_presents(self):
        for a in self.avis:
            self.assertTrue(a["type_notice"])
            self.assertIn(a["phase"], ("amont", "tender"))


if __name__ == "__main__":
    unittest.main()
