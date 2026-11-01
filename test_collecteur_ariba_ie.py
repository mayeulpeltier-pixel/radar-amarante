# -*- coding: utf-8 -*-
"""Collecteur IE Ariba Discovery (appels d'offres privés) : normalisation,
fenêtre/ouverture, file d'analyse (pré-filtre lentille, mémoire avant plafond,
hors France), collecte paginée et bout-en-bout. Offline : API et LLM injectés.
Réutilise le schéma/écriture du collecteur BOAMP (collecteur_ie)."""

import datetime
import json
import unittest

import collecteur_ariba_ie as A
import collecteur_ie as C

A.PAUSE = 0.0
AUJ = datetime.date(2026, 11, 1)
DEBUT = AUJ - datetime.timedelta(days=A.JOURS)


def record(rfx="1110027394", title="Integrity due diligence services",
           desc="Third party integrity checks", cats=None, lieux=None,
           client=None, poste="2026-10-20", fin="2026-12-01"):
    return {"rfxID": rfx, "title": title, "description": desc,
            "productsAndServicesCategories": cats if cats is not None
            else ["Business regulatory and compliance research service"],
            "shipToOrServiceLocations": lieux if lieux is not None else ["United Kingdom"],
            "customerName": client, "datePosted": poste + "T10:00:00Z",
            "endDate": fin + "T17:00:00Z", "adRfxType": "RFP"}


class TestNormalisation(unittest.TestCase):

    def test_champs(self):
        a = A.normaliser(record())
        self.assertEqual(a["source"], "Ariba")
        self.assertEqual(a["id_avis"], "1110027394")
        self.assertEqual(a["date_publication"], "2026-10-20")
        self.assertEqual(a["date_limite"], "2026-12-01")
        self.assertIn("compliance research", a["corps"])     # catégorie reprise
        self.assertTrue(a["lien"].endswith("preview/1110027394?anId=ANONYMOUS"))
        self.assertFalse(a["francais"])

    def test_francais_detecte(self):
        self.assertTrue(A.normaliser(record(lieux=["France"]))["francais"])

    def test_lieux_et_cats_en_chaine(self):
        a = A.normaliser(record(cats="Audit", lieux="Canada"))
        self.assertIn("Audit", a["corps"])
        self.assertEqual(a["pays"], "Canada")


class TestFenetreOuverture(unittest.TestCase):

    def test_est_ouvert(self):
        self.assertTrue(A.est_ouvert({"date_limite": "2026-12-01"}, AUJ))
        self.assertFalse(A.est_ouvert({"date_limite": "2026-10-01"}, AUJ))
        self.assertTrue(A.est_ouvert({"date_limite": ""}, AUJ))

    def test_dans_fenetre(self):
        self.assertTrue(A.dans_fenetre({"date_publication": "2026-10-20"}, DEBUT))
        self.assertFalse(A.dans_fenetre({"date_publication": "2026-01-01"}, DEBUT))


class TestFileAnalyse(unittest.TestCase):

    def test_france_ecartee_et_prefiltre(self):
        avis = [A.normaliser(record(rfx="1", lieux=["France"])),            # France -> écarté
                A.normaliser(record(rfx="2", title="Boiler repair services")),  # pas IE
                A.normaliser(record(rfx="3", title="Integrity due diligence"))]  # IE titre
        file_, ent = A.file_d_analyse(avis, set(), AUJ, DEBUT)
        self.assertEqual([a["id_avis"] for a in file_], ["3"])
        self.assertEqual(ent["francais"], 1)

    def test_memoire_avant_plafond(self):
        avis = [A.normaliser(record(rfx=str(i), title="Due diligence des tiers"))
                for i in range(5)]
        deja = {C.cle_avis(dict(A.normaliser(record(rfx="0")), source="Ariba"))}
        file_, ent = A.file_d_analyse(avis, deja, AUJ, DEBUT, budget=2)
        self.assertEqual(ent["deja_vus"], 1)        # rfx 0 connu, sauté avant plafond
        self.assertEqual(len(file_), 2)             # plafond applique au reste
        self.assertEqual(ent["hors_budget"], 2)     # 4 restants - 2

    def test_ferme_ecarte(self):
        avis = [A.normaliser(record(rfx="9", title="Due diligence", fin="2026-09-01"))]
        file_, ent = A.file_d_analyse(avis, set(), AUJ, DEBUT)
        self.assertEqual(file_, [])
        self.assertEqual(ent["fermes"], 1)


class TestCollecter(unittest.TestCase):

    def test_pagination_et_dedup(self):
        appels = []
        def fetch(terme, page, cursor):
            appels.append((terme, page))
            if page == 1:
                return {"records": [record(rfx="1"), record(rfx="2")],
                        "totalNumberOfRecords": 3, "searchAfter": ["c1"]}
            return {"records": [record(rfx="2")],   # doublon
                    "totalNumberOfRecords": 3, "searchAfter": None}
        avis, j = A.collecter(fetch=fetch, requetes=["due diligence"], pages=3)
        self.assertEqual(sorted(a["id_avis"] for a in avis), ["1", "2"])
        self.assertEqual(j["requetes"], 1)

    def test_erreur_par_terme_isolee(self):
        def fetch(terme, page, cursor):
            if terme == "kyc":
                raise RuntimeError("503")
            return {"records": [record(rfx="7")], "totalNumberOfRecords": 1, "searchAfter": None}
        avis, j = A.collecter(fetch=fetch, requetes=["due diligence", "kyc"], pages=1)
        self.assertEqual([a["id_avis"] for a in avis], ["7"])
        self.assertEqual(len(j["erreurs"]), 1)


class TestExecuter(unittest.TestCase):

    def _appel(self, prompt, modele):
        return json.dumps({
            "famille_offre": "due_diligence", "type_acheteur": "entreprise",
            "format": "accord_cadre", "barrieres_entree": "faibles",
            "achat_outil": False, "dimension_internationale": True,
            "sensibilite": "haute", "resume": "DD tiers", "confiance": 0.9})

    def test_bout_en_bout(self):
        def fetch(terme, page, cursor):
            if terme == "due diligence" and page == 1:
                return {"records": [record(rfx="1", client="Severn Trent")],
                        "totalNumberOfRecords": 1, "searchAfter": None}
            return {"records": [], "totalNumberOfRecords": 0, "searchAfter": None}
        lignes_pg, lignes_sheet, rap = A.executer(
            aujourd=AUJ, fetch=fetch, appel=self._appel, deja_vus=set(), debug=False)
        self.assertEqual(len(lignes_pg), 1)
        self.assertEqual(lignes_pg[0]["source"], "Ariba")
        if lignes_sheet:
            self.assertEqual(len(lignes_sheet[0]), len(C.COLONNES_SHEET))

    def test_debug_sans_llm(self):
        def fetch(terme, page, cursor):
            return ({"records": [record(rfx="1")], "totalNumberOfRecords": 1,
                     "searchAfter": None} if page == 1 else {"records": []})
        lignes_pg, lignes_sheet, rap = A.executer(
            aujourd=AUJ, fetch=fetch, deja_vus=set(), debug=True)
        self.assertEqual((lignes_pg, lignes_sheet), ([], []))
        self.assertTrue(rap["debug"])


if __name__ == "__main__":
    unittest.main()
