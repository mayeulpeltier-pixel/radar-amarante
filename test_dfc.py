# -*- coding: utf-8 -*-
"""
Tests du collecteur DFC. HORS-LIGNE : lignes injectees (pas de reseau, pas
d'openpyxl, pas de LLM). Verifie le mapping pays anglais->ISO3, le crible FI
(NAICS), le filtre FY (legacy), la resolution de colonnes par en-tete, le
mapping d'un enregistrement et l'arite des lignes.
"""

import unittest
import dfc_radar as d


ENTETE = ["Fiscal Year", "Project Number", "Project Type", "Region", "Country",
          "Department", "Framework", "Project Name", "Committed", "NAICS Sector",
          "Project Description", "Project Profile URL", "Exposure",
          "Originating Agency", "Country Income Level", "Support Type",
          "Source of Funding", "Currency", "Sovereign (Yes/No)",
          "Estimated Term (Years)", "Environmental and Social Risk Category",
          "2X identifier", "2X Qualifying Criteria", "IQ Tier"]


def _ligne(fy="2023", num="9000200001", pays="Mali", nom="Sahel Energy Ltd",
           committed="25000000", naics="Utilities", desc="Solar plant construction",
           sovereign="No", url="https://www.dfc.gov/x"):
    m = {"Fiscal Year": fy, "Project Number": num, "Country": pays,
         "Project Name": nom, "Committed": committed, "NAICS Sector": naics,
         "Project Description": desc, "Sovereign (Yes/No)": sovereign,
         "Project Profile URL": url, "Region": "Africa"}
    return [m.get(col, "") for col in ENTETE]


def faux_fetch(lignes_data):
    # Une ligne titre parasite au-dessus de l'en-tete, comme le vrai fichier.
    return lambda: [["Project Data"] + [""] * 23, ENTETE] + lignes_data


class TestMappingPays(unittest.TestCase):
    def test_anglais_vers_iso3(self):
        self.assertEqual(d.iso3_depuis_nom("Ukraine"), "UKR")
        self.assertEqual(d.iso3_depuis_nom("Syria"), "SYR")
        self.assertEqual(d.iso3_depuis_nom("Democratic Republic of the Congo"), "COD")
        self.assertEqual(d.iso3_depuis_nom("Cote d'Ivoire"), "CIV")

    def test_inconnu(self):
        self.assertEqual(d.iso3_depuis_nom("Neverland"), "")


class TestCribleFI(unittest.TestCase):
    def test_finance_ecarte(self):
        self.assertTrue(d.est_secteur_financier("Finance and Insurance"))
        self.assertTrue(d.est_secteur_financier("Funds and Trusts"))

    def test_industrie_gardee(self):
        self.assertFalse(d.est_secteur_financier("Utilities"))
        self.assertFalse(d.est_secteur_financier("Mining"))


class TestResolutionColonnes(unittest.TestCase):
    def test_entete_et_valeurs(self):
        lignes = faux_fetch([_ligne()])()
        i = d._index_entete(lignes)
        self.assertEqual(i, 1)                         # saute la ligne titre
        idx = d._cols(lignes[i])
        self.assertEqual(d._val(lignes[2], idx, "Country"), "Mali")
        self.assertEqual(d._val(lignes[2], idx, "Project Name"), "Sahel Energy Ltd")


class TestMappingAvis(unittest.TestCase):
    def test_rec_vers_avis(self):
        lignes = faux_fetch([_ligne()])()
        idx = d._cols(lignes[1])
        a = d.rec_vers_avis(lignes[2], idx)
        self.assertEqual(a["publication_number"], "9000200001")
        self.assertEqual(a["acheteur"], "Sahel Energy Ltd")
        self.assertEqual(a["pays_iso3"], "MLI")
        self.assertIn("USD", a["valeur_estimee"])
        self.assertEqual(a["sovereign"], "No")
        self.assertIn("solar", a["description"].lower())


class TestCollecteEntonnoir(unittest.TestCase):
    def test_filtres(self):
        lignes = [
            _ligne(num="A", pays="Mali", naics="Utilities"),              # retenu
            _ligne(num="B", pays="France"),                               # hors perimetre (non mappe)
            _ligne(num="C", naics="Finance and Insurance"),               # FI
            _ligne(num="D", fy="1962"),                                   # legacy hors FY
            _ligne(num="E", pays="Neverland"),                            # non mappe
        ]
        avis, c = d.collecte(deja_vus=set(), fetch=faux_fetch(lignes))
        ids = {a["publication_number"] for a in avis}
        self.assertEqual(ids, {"A"})
        self.assertEqual(c["rejet_fi"], 1)
        self.assertEqual(c["hors_fy"], 1)
        self.assertGreaterEqual(c["hors_perimetre"], 2)   # France + Neverland
        self.assertIn("Neverland", c["pays_non_mappes"])

    def test_memoire(self):
        lignes = [_ligne(num="A"), _ligne(num="B")]
        avis, c = d.collecte(deja_vus={"A"}, fetch=faux_fetch(lignes))
        self.assertEqual([a["publication_number"] for a in avis], ["B"])
        self.assertEqual(c["deja_connus"], 1)

    def test_tri_montant(self):
        lignes = [_ligne(num="petit", committed="1000000"),
                  _ligne(num="gros", committed="90000000")]
        avis, _c = d.collecte(deja_vus=set(), fetch=faux_fetch(lignes))
        self.assertEqual(avis[0]["publication_number"], "gros")


class TestArite(unittest.TestCase):
    def test_ligne_bonne_longueur(self):
        lignes = faux_fetch([_ligne()])()
        idx = d._cols(lignes[1])
        a = d.rec_vers_avis(lignes[2], idx)
        r = {"avis": a, "extraction": None, "score": 0.0, "surete": 0.0,
             "commercial": 0.0, "raffine": False, "divergence": False}
        ligne = d.ligne_depuis_resultat(r)
        self.assertEqual(len(ligne), len(d.COLONNES))
        self.assertTrue(all(isinstance(x, str) for x in ligne))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRedactedEtAlias(unittest.TestCase):
    def test_alias_hors_perimetre(self):
        self.assertEqual(d.iso3_depuis_nom("India"), "IND")
        self.assertEqual(d.iso3_depuis_nom("El Salvador"), "SLV")

    def test_redacted_ecarte(self):
        lignes = [
            _ligne(num="OK", pays="Ukraine", nom="MHP SE", naics="Manufacturing"),
            _ligne(num="R1", pays="Ukraine", nom="Redacted", naics="Redacted"),
        ]
        avis, c = d.collecte(deja_vus=set(), fetch=faux_fetch(lignes))
        self.assertEqual({a["publication_number"] for a in avis}, {"OK"})
        self.assertEqual(c["rejet_redacted"], 1)

    def test_inde_mappe_reste_hors_perimetre(self):
        lignes = [_ligne(num="X", pays="India")]
        avis, c = d.collecte(deja_vus=set(), fetch=faux_fetch(lignes))
        self.assertEqual(avis, [])
        self.assertEqual(c["hors_perimetre"], 1)
        self.assertNotIn("India", c.get("pays_non_mappes", {}))
