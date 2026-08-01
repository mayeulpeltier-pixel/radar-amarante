# -*- coding: utf-8 -*-
"""Tests IFC : parsing d'un record Azure (format reel), cribles categorie FI +
perimetre pays, dedup par Project_Number, resolution pays -> ISO3, schema Sheet.
Aucun reseau (fetch_page injecte), aucun LLM reel."""

import json
import unittest

import ted_complet_v14 as ted
import ifc_radar as ic


REC_CIV = {
    "Project_Number": "51919", "Project_Name": "Cote Ivoire Solar Park",
    "Company_Name": "Solar CI SA", "Country_Description": "Cote D'Ivoire",
    "Environmental_Category_Description": "B - Limited",
    "Document_Type_Description": "Summary of Proposed Investment",
    "Type_Description": "Investment", "Status_Description": "Active",
    "Sector": "Renewable Energy", "Sponsor": "Meridiam (France)",
    "Projected_Board_Date": "2026-09-01T00:00:00Z",
    "Disclosed_Date": "2026-07-30T00:00:00Z",
    "Estimated_Total_Budget": "$120,000,000.00",
    "Project_Description": "Construction and operation of a 50 MW solar plant with expatriate supervision on site.",
}
REC_FI = {
    "Project_Number": "51625", "Project_Name": "Bank Facility",
    "Company_Name": "Some Bank", "Country_Description": "Malaysia",
    "Environmental_Category_Description": "FI", "Type_Description": "Investment",
    "Project_Description": "Credit line to a financial intermediary.",
}
REC_MALAISIE = {
    "Project_Number": "51625b", "Project_Name": "Malaysia Factory",
    "Company_Name": "MFG Sdn Bhd", "Country_Description": "Malaysia",
    "Environmental_Category_Description": "B - Limited", "Type_Description": "Investment",
    "Project_Description": "A manufacturing plant.",
}


class TestParsing(unittest.TestCase):
    def test_champs_et_iso3(self):
        a = ic.parser_record(REC_CIV)
        self.assertEqual(a["publication_number"], "IFC:51919")
        self.assertEqual(a["acheteur"], "Solar CI SA")
        self.assertEqual(a["pays_iso3"], "CIV")
        self.assertEqual(a["categorie_es"], "B - Limited")
        self.assertIn("solar plant", a["description"])
        self.assertIn("Meridiam", a["description"])
        self.assertEqual(a["valeur_estimee"], "$120,000,000.00")

    def test_lien_best_effort(self):
        a = ic.parser_record(REC_CIV)
        self.assertIn("disclosures.ifc.org/project-detail/", a["lien_avis"])
        self.assertIn("51919", a["lien_avis"])

    def test_scoring_utilise_iso3(self):
        a = ic.parser_record(REC_CIV)
        self.assertEqual(ic.avis_pour_scoring(a)["pays_execution"], "CIV")


class TestEntonnoir(unittest.TestCase):
    def test_cribles_et_dedup(self):
        page0 = [REC_CIV, REC_CIV, REC_FI, REC_MALAISIE]   # REC_CIV en double
        avis, c = ic.collecte(session=object(),
                              fetch_page=lambda skip: page0 if skip == 0 else [])
        self.assertEqual(c["doublons"], 1)           # 2e REC_CIV
        self.assertEqual(c["rejet_fi"], 1)           # REC_FI
        self.assertEqual(c["hors_perimetre"], 1)     # Malaysia (hors carte risque)
        self.assertEqual(c["retenus"], 1)            # REC_CIV
        self.assertEqual(len(avis), 1)
        self.assertEqual(avis[0]["pays_iso3"], "CIV")

    def test_deja_vus(self):
        avis, c = ic.collecte(session=object(),
                              fetch_page=lambda skip: [REC_CIV] if skip == 0 else [],
                              deja_vus={"IFC:51919"})
        self.assertEqual(c["deja_connus"], 1)
        self.assertEqual(c["retenus"], 0)

    def test_crible_advisory(self):
        rec_adv = {"Project_Number": "70001", "Project_Name": "DRC Capital Market Dev",
                   "Country_Description": "Congo, Democratic Republic of",
                   "Type_Description": "Advisory Services",
                   "Project_Description": "Advisory to develop capital markets."}
        avis, c = ic.collecte(session=object(),
                              fetch_page=lambda skip: [rec_adv] if skip == 0 else [])
        self.assertEqual(c["rejet_advisory"], 1)     # conseil IFC ecarte
        self.assertEqual(c["retenus"], 0)


class TestSchema(unittest.TestCase):
    def test_ligne_alignee(self):
        original = ted.appeler_modele
        self.addCleanup(setattr, ted, "appeler_modele", original)
        ted.appeler_modele = lambda prompt, modele=None: json.dumps({
            "deploiement_terrain_reel": True, "type_mobilite": "chantier",
            "profil_personnes_exposees": "expert_international", "securite_existante": "aucune",
            "type_activite": "supervision_chantier", "type_client": "entreprise_privee",
            "duree_estimee": "longue_ou_residente", "accessibilite_commerciale": "facile",
            "profils_acteurs_probables": ["developpeur energie"],
            "besoin_securite_operationnel_probable": True,
            "niveau_opportunite_amarante": "fort",
            "justification": "Chantier solaire, personnel expatrie expose.",
            "confiance": 0.8})
        a = ic.parser_record(REC_CIV)
        extraction = ic.analyser(a)
        self.assertIsNotNone(extraction)
        s, c, f = ted.calculer_scores(ic.avis_pour_scoring(a), extraction)
        r = {"avis": a, "extraction": extraction, "surete": s, "commercial": c,
             "score": f, "raffine": False, "divergence": False}
        self.assertEqual(len(ic.ligne_depuis_resultat(r)), len(ic.COLONNES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
