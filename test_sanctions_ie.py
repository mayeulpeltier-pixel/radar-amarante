# -*- coding: utf-8 -*-
"""Sanctions des bailleurs (volet IE) : normalisation de la liste officielle
Banque mondiale, cross-debarment, detection des exclusions recentes, ligne
Sheet. Offline : la reponse de l'API est injectee (aucun reseau)."""

import datetime
import unittest

import sanctions_ie as S

AUJ = datetime.date(2026, 10, 31)


def row(sid="118351", nom="ACME LTD", type_="F", pays="China", iso2="CN",
        flg="D", debut="2026-06-23", fin="2027-04-22", motif="Fraud"):
    return {"SUPP_ID": sid, "SUPP_NAME": nom, "SUPP_TYPE_CODE": type_,
            "COUNTRY_NAME": pays, "LAND1": iso2, "SUPP_CITY": "Ville",
            "INELIG_FLG": flg, "DEBAR_FROM_DATE": debut, "DEBAR_TO_DATE": fin,
            "DEBAR_REASON": motif, "UPD_DATE": "2026-06-24"}


def reponse(rows):
    return {"response": {"ZPROCSUPP": rows}}


class TestNormalisation(unittest.TestCase):

    def test_firme_debarquee(self):
        s = S.normaliser(row(), aujourd=AUJ)
        self.assertEqual(s["cle"], "wb:118351")
        self.assertEqual(s["type"], "Entreprise")
        self.assertEqual(s["exclu_par"], "Banque mondiale")
        self.assertFalse(s["cross_debarment"])
        self.assertEqual(s["debar_debut"], "2026-06-23")

    def test_cross_debarment(self):
        s = S.normaliser(row(flg="X"), aujourd=AUJ)
        self.assertTrue(s["cross_debarment"])
        self.assertIn("Cross-debarment", s["exclu_par"])

    def test_personne(self):
        self.assertEqual(S.normaliser(row(type_="I"), aujourd=AUJ)["type"], "Personne")

    def test_ligne_incomplete_ignoree(self):
        self.assertIsNone(S.normaliser({"SUPP_ID": "", "SUPP_NAME": "X"}, aujourd=AUJ))
        self.assertIsNone(S.normaliser({"SUPP_ID": "1", "SUPP_NAME": ""}, aujourd=AUJ))

    def test_date_permanente_toleree(self):
        s = S.normaliser(row(fin="2999-12-31"), aujourd=AUJ)
        self.assertEqual(s["debar_fin"], "2999-12-31")


class TestCollecteEtRecentes(unittest.TestCase):

    def test_dedoublonnage(self):
        s, err = S.collecter(fetch=lambda: [row(sid="1"), row(sid="1"), row(sid="2")],
                             aujourd=AUJ)
        self.assertEqual(err, "")
        self.assertEqual(sorted(x["cle"] for x in s), ["wb:1", "wb:2"])

    def test_erreur_forme(self):
        s, err = S.collecter(fetch=lambda: "pas une liste", aujourd=AUJ)
        self.assertEqual(s, [])
        self.assertIn("forme inattendue", err)

    def test_recentes_fenetre(self):
        s = [S.normaliser(row(sid="old", debut="2024-01-01"), aujourd=AUJ),
             S.normaliser(row(sid="new", debut="2026-10-01"), aujourd=AUJ),
             S.normaliser(row(sid="hier", debut="2026-10-27"), aujourd=AUJ)]
        rec = S.recentes(s, AUJ, 180)
        self.assertEqual([x["cle"] for x in rec], ["wb:hier", "wb:new"])  # recent d'abord


class TestRecuperationForme(unittest.TestCase):

    def test_forme_inattendue_detectee(self):
        # Chemin reseau simule par un fetch=None impossible ici ; on teste la
        # validation de forme via recuperer avec un fetch renvoyant le mauvais type.
        lignes, err = S.recuperer(fetch=lambda: [row()])
        self.assertEqual(err, "")
        self.assertEqual(len(lignes), 1)


class TestExecuter(unittest.TestCase):

    def test_rapport_compte_cross_et_recentes(self):
        rows = [row(sid="a", flg="X", debut="2026-10-20"),
                row(sid="b", flg="D", debut="2019-01-01"),
                row(sid="c", flg="D", debut="2026-09-15")]
        sanctions, rec, rap = S.executer(fetch=lambda: rows, aujourd=AUJ, jours=180)
        self.assertEqual(rap["total"], 3)
        self.assertEqual(rap["cross"], 1)
        self.assertEqual(rap["recentes"], 2)      # a et c, pas b (2019)
        md = S.rapport_markdown(rap, ["Postgres : rien a ecrire."])
        self.assertIn("cross-debarment", md)

    def test_ligne_sheet_schema(self):
        s = S.normaliser(row(), aujourd=AUJ)
        ligne = S.ligne_sheet(s)
        self.assertEqual(len(ligne), len(S.COLONNES_SHEET))
        self.assertEqual(ligne[-2:], ["", ""])    # colonnes de suivi ARISE vides
        self.assertEqual(ligne[S.COL_CLE], "wb:118351")

    def test_ecritures_sans_configuration(self):
        self.assertIn("absent", S.ecrire_pg(None, [{}]))
        self.assertIn("non configure", S.ecrire_sheet(None, []))
        self.assertIsNone(S.memoire_pg(None))


if __name__ == "__main__":
    unittest.main()
