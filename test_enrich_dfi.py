# -*- coding: utf-8 -*-
"""Tests de l'enrichissement des sponsors prives DFI (GLEIF pour toutes, Hunter
sur le reliquat apres la watchlist Haute). Fonctions PURES, sans reseau."""
import unittest
import enrichir_entreprises as ee


ENTETE = ["acheteur", "valeur_estimee", "pays_execution", "secteur"]


def _onglet(rows):
    return [ENTETE] + rows


class TestEntreprisesDFI(unittest.TestCase):
    def test_extraction(self):
        vals = _onglet([
            ["Enerjisa Enerji A.S.", "70 982 396 EUR", "TUR", "Energie"],
            ["Rovuma LNG Phase I", "1 500 000 000 USD", "MOZ", "Mining"],
            ["Redacted", "150 000 000 USD", "UKR", "Redacted"],       # ecarte
            ["Enerjisa Enerji A.S.", "70 982 396 EUR", "TUR", "Energie"],  # dedup
        ])
        out = ee.entreprises_dfi(vals)
        noms = [o["entreprise"] for o in out]
        self.assertEqual(noms, ["Enerjisa Enerji A.S.", "Rovuma LNG Phase I"])
        self.assertTrue(all(o["origine"] == "dfi" and o["etranger"] for o in out))
        self.assertGreater(out[1]["valeur"], out[0]["valeur"])   # Rovuma > Enerjisa

    def test_sans_colonne_acheteur(self):
        self.assertEqual(ee.entreprises_dfi([["autre"], ["x"]]), [])

    def test_plafond(self):
        vals = _onglet([["Soc {}".format(i), "1 M", "MLI", "s"] for i in range(10)])
        self.assertEqual(len(ee.entreprises_dfi(vals, max_comptes=3)), 3)


class TestHunterReliquatDFI(unittest.TestCase):
    def _comptes(self):
        return [
            {"entreprise": "Thales", "priorite_socle": "Haute", "origine": "watchlist"},
            {"entreprise": "Rovuma LNG", "priorite_socle": "Moyenne", "origine": "dfi",
             "etranger": True, "valeur": 1.5e9},
            {"entreprise": "Enerjisa", "priorite_socle": "Moyenne", "origine": "dfi",
             "etranger": True, "valeur": 7e7},
        ]

    def test_haute_puis_dfi_sur_reliquat(self):
        infos = {"thales": ("Patrice C.", "gouv")}   # Haute avec dirigeant -> finder
        cibles = ee.selectionner_cibles_hunter(self._comptes(), infos, set(), budget=3)
        noms = [c[0] for c in cibles]
        self.assertEqual(noms[0], "Thales")                 # Haute d'abord
        self.assertEqual(noms[1:], ["Rovuma LNG", "Enerjisa"])  # DFI, gros montant d'abord
        # DFI en mode generique (RGPD) : 4e element du tuple = True
        self.assertTrue(all(c[3] for c in cibles if c[0] in ("Rovuma LNG", "Enerjisa")))

    def test_budget_epuise_par_haute_exclut_dfi(self):
        infos = {"thales": ("Patrice C.", "gouv")}
        cibles = ee.selectionner_cibles_hunter(self._comptes(), infos, set(), budget=1)
        self.assertEqual([c[0] for c in cibles], ["Thales"])   # reliquat=0 -> pas de DFI

    def test_dfi_jamais_reprises_si_deja_tentees(self):
        infos = {}
        cibles = ee.selectionner_cibles_hunter(
            self._comptes(), infos, deja={"rovuma lng"}, budget=3)
        self.assertNotIn("Rovuma LNG", [c[0] for c in cibles])


if __name__ == "__main__":
    unittest.main(verbosity=2)
