# -*- coding: utf-8 -*-
"""Exposition de l'ancre proj_id (P######) dans les avis BM.

Etape 1 du dossier vivant (Voie A) : le project_id de l'API procnotices est
reporte dans une colonne projet_id, pour relier amont / avis / attribution par
identifiant plutot que par matching textuel. Additif : projet_id en fin de
COLONNES_BM, aucune position existante n'est decalee.
"""

import unittest

import ted_complet_bm as bm
import bm_attributions as bma


def record(pid="P172945"):
    return {"id": "OP00463517", "project_id": pid,
            "project_name": "Punjab Urban Land Systems Enhancement Project",
            "bid_description": "DGPS Survey", "notice_text": "<p>x</p>",
            "project_ctry_name": "Pakistan"}


class TestExpositionProjId(unittest.TestCase):

    def test_normaliser_expose_projet_id(self):
        self.assertEqual(bm.normaliser_bm(record())["projet_id"], "P172945")

    def test_project_id_absent_donne_vide(self):
        rec = record()
        del rec["project_id"]
        self.assertEqual(bm.normaliser_bm(rec)["projet_id"], "")

    def test_colonne_ajoutee_en_fin(self):
        # Les colonnes de donnees sont empilees en fin, dans l'ordre d'ajout :
        # projet_id (Voie A dossiers) puis valeur_estimee (montant BM avis).
        self.assertEqual(bm.COLONNES_BM[-1], "valeur_estimee")
        self.assertEqual(bm.COLONNES_BM[-2], "projet_id")

    def test_publication_number_non_decale(self):
        # publication_number reste avant projet_id : son index est preserve.
        self.assertLess(bm.COLONNES_BM.index("publication_number"),
                        bm.COLONNES_BM.index("projet_id"))

    def test_ligne_ecrit_projet_id(self):
        av = bm.normaliser_bm(record())
        r = {"avis": av, "extraction": None, "raffine": False, "score": 7.0,
             "surete": 7.0, "commercial": 6.0, "divergence": ""}
        ligne = bm.ligne_depuis_resultat_bm(r)
        self.assertEqual(ligne[bm.COLONNES_BM.index("projet_id")], "P172945")


class TestAttributionProjId(unittest.TestCase):

    def test_colonne_ajoutee_en_fin(self):
        self.assertEqual(bma.COLONNES[-1], "projet_id")

    def test_publication_number_non_decale(self):
        self.assertLess(bma.COLONNES.index("publication_number"),
                        bma.COLONNES.index("projet_id"))

    def test_ligne_ecrit_projet_id(self):
        ligne = bma.ligne_pour_sheet({"projet_id": "P172945", "gagnant": "X"})
        self.assertEqual(ligne[bma.COLONNES.index("projet_id")], "P172945")

    def test_projet_id_vide_par_defaut(self):
        ligne = bma.ligne_pour_sheet({"gagnant": "X"})
        self.assertEqual(ligne[bma.COLONNES.index("projet_id")], "")


if __name__ == "__main__":
    unittest.main()
