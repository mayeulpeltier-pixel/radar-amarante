# -*- coding: utf-8 -*-
"""Tests EBRD : parsing du format reel ECEPP (blocs [...] + segments lisibles),
resolution pays, client comme cible, exclusion attributions et hors-zone,
compatibilite coeur TED. Aucun appel reseau ni LLM."""

import unittest
from datetime import date, timedelta

import ebrd_radar as ebrd
import ted_complet_v14 as ted


# Extrait REEL de la page ECEPP (blocs [...] + texte lisible qui les precede),
# avec dates recalees sur aujourd'hui pour tester le filtre de fraicheur.
AUJ = date.today()
def _d(n):  # date JJ/MM/AAAA il y a n jours
    return (AUJ - timedelta(days=n)).strftime("%d/%m/%Y")

PAGE = (
 "Kyrgyz Republic: Kyrgyzstan Climate Resilience Water Supply Project "
 "General Procurement Notice N/A " + _d(3) + " 10:03UK Time N/A Information Only "
 "[Kyrgyzstan Climate Resilience Water Supply Project, 49793, Kyrgyz Republic, "
 "Goods,Works,Consultancy, State Water Resources Agency (SWRA), Natural Resources, "
 "General Procurement Notice] "
 "Serbia: RC Duboko Sanitation Invitation For Tenders Two Stage " + _d(3) + " 06:13UK Time "
 + _d(-45) + " 10:00UK Time Open "
 "[Serbian Solid Waste Programme, 52642, Serbia, RC Duboko Sanitation remediation, "
 "44054880, Works, Open Tender Two Stage, Republic of Serbia, "
 "Municipal and Environmental Infrastructure, Invitation For Tenders Two Stage] "
 "Tajikistan: Construction of Vahdat to Roghun Road Invitation For Prequalification " + _d(4) + " 13:02UK Time "
 + _d(-44) + " 10:00UK Time Open "
 "[Vahdat-Roghun Road, 57623, Tajikistan, Construction of Vahdat to Roghun Road, "
 "44846993, Works, Open Tender Single Stage with PQQ, "
 "The Project Implementation Unit for Roads Rehabilitation (PIURR), Infra Eurasia, "
 "Invitation For Prequalification] "
 "Ukraine: Supply and installation of cogeneration units Invitation For Tenders Single " + _d(5) + " 00:09UK Time "
 + _d(-30) + " 15:00UK Time Open "
 "[Kyiv District Heating, 50839, Ukraine, Supply installation and commissioning, "
 "45067510, Works, Open Tender Single Stage, Kyivteploenergo CE, "
 "Municipal and Environmental Infrastructure, Invitation For Tenders Single] "
 "Moldova: Construction Supervision Contract Award Notice " + _d(6) + " 09:51UK Time N/A Information Only "
 "[Chisinau Water Development Programme, 44027, Moldova, Works, Open Tender, "
 "WATER AND SEWAGE COMPANY, Municipal and Environmental Infrastructure, "
 "Contract Award Notice] "
 "Mongolia: Package 3.1 Smart Metering Invitation For Tenders Single " + _d(5) + " 07:34UK Time "
 + _d(-40) + " 10:00UK Time Open "
 "[GrCF2 W2 - Ulaanbaatar District Heating Project, 49511, Mongolia, Package Smart Metering, "
 "39854815, Works, Open Tender Single Stage, Ulaanbaatar District Heating Company SOE, "
 "Municipal and Environmental Infrastructure, Invitation For Tenders Single]")


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.notices = ebrd.parser_notices(PAGE)

    def test_nombre(self):
        self.assertEqual(len(self.notices), 6)

    def test_ancres_bloc(self):
        kg = [n for n in self.notices if n["pays_clair"] == "Kyrgyz Republic"][0]
        self.assertEqual(kg["projet"], "Kyrgyzstan Climate Resilience Water Supply Project")
        self.assertEqual(kg["id_projet"], "49793")
        self.assertEqual(kg["type_notice"], "Avis general de passation")   # GPN
        self.assertEqual(kg["phase"], "amont")
        self.assertEqual(kg["secteur"], "Natural Resources")
        self.assertEqual(kg["client"], "State Water Resources Agency (SWRA)")

    def test_attribution_detectee(self):
        md = [n for n in self.notices if n["pays_clair"] == "Moldova"][0]
        self.assertTrue(md["est_attribution"])

    def test_dates_parsees(self):
        srb = [n for n in self.notices if n["pays_clair"] == "Serbia"][0]
        self.assertIsNotNone(srb["date_publication"])
        self.assertIsNotNone(srb["date_cloture"])
        self.assertEqual(srb["etat"], "Open")


class TestResolutionPays(unittest.TestCase):
    def test_suivis(self):
        self.assertEqual(ebrd.resoudre_iso3("Ukraine")[0], "UKR")
        self.assertEqual(ebrd.resoudre_iso3("Kyrgyz Republic")[0], "KGZ")
        self.assertEqual(ebrd.resoudre_iso3("Tajikistan")[0], "TJK")

    def test_hors_zone(self):
        """Un pays reellement hors perimetre reste ecarte et signale."""
        iso, hz = ebrd.resoudre_iso3("Romania")
        self.assertEqual(iso, "")
        self.assertEqual(hz, "Romania")

    def test_mongolie_entree_dans_le_perimetre(self):
        """CHANGEMENT ASSUME (22/07/2026) : la Mongolie fait desormais partie
        du perimetre commercial Amarante. L'EBRD la couvre, elle devient donc
        une source de leads asiatiques la ou il n'y en avait aucune."""
        iso, hz = ebrd.resoudre_iso3("Mongolia")
        self.assertEqual(iso, "MNG")
        self.assertEqual(hz, "")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.avis, self.stats = ebrd.collecter_et_normaliser(fetch=lambda url=None: PAGE)

    def test_garde_fou_ok(self):
        self.assertFalse(self.stats["page_vide"])

    def test_retient_suivis_exclut_attrib_et_horszone(self):
        pays = sorted(a["pays_execution"] for a in self.avis)
        # Kyrgyz, Mongolie, Serbia, Tajikistan, Ukraine gardes.
        # Moldova (attribution) exclue.
        self.assertEqual(pays, ["KGZ", "MNG", "SRB", "TJK", "UKR"])

    def test_mongolie_desormais_collectee(self):
        """Elle n'est plus signalee comme hors zone : c'est le gain concret
        de l'elargissement du perimetre."""
        self.assertNotIn("Mongolia", self.stats["pays_hors_zone"])

    def test_client_devient_acheteur(self):
        kg = [a for a in self.avis if a["pays_execution"] == "KGZ"][0]
        self.assertEqual(kg["acheteur"], "State Water Resources Agency (SWRA)")

    def test_gpn_est_amont(self):
        kg = [a for a in self.avis if a["pays_execution"] == "KGZ"][0]
        self.assertEqual(kg["phase"], "amont")

    def test_cible_commerciale_contient_client(self):
        kg = [a for a in self.avis if a["pays_execution"] == "KGZ"][0]
        cible = ebrd.cible_commerciale(kg["client"], kg["phase"])
        self.assertIn("State Water Resources Agency", cible)

    def test_avis_compatible_coeur_ted(self):
        avis = self.avis[0]
        extraction = {
            "deploiement_terrain_reel": True, "type_mobilite": "terrain_isole",
            "profil_personnes_exposees": "expert_international",
            "securite_existante_detectee": False, "type_client": "bailleur_donateur",
            "accessibilite_commerciale": "bonne", "duree_estimee": "longue_ou_residente",
            "niveau_opportunite_amarante": "fort", "confiance": 0.85,
        }
        s, c, f = ted.calculer_scores(avis, extraction)
        self.assertGreater(f, 0.0)
        self.assertLessEqual(f, 10.0)


class TestGardeFou(unittest.TestCase):
    def test_page_vide(self):
        avis, stats = ebrd.collecter_et_normaliser(fetch=lambda url=None: "<html></html>")
        self.assertTrue(stats["page_vide"])
        self.assertEqual(avis, [])


if __name__ == "__main__":
    unittest.main()
