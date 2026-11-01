# -*- coding: utf-8 -*-
"""Sonde sûreté Ariba : classement (physique / cyber / matériel / pertinent),
zone géographique, verdict, et collecte paginée. Offline : API injectée."""

import unittest

import sonde_ariba_surete as S

S.PAUSE = 0.0


class TestClasser(unittest.TestCase):

    def test_securite_physique_pertinente(self):
        c = S.classer("Manned Guarding Services", "Provision of security guards", [])
        self.assertTrue(c["physique"])
        self.assertTrue(c["pertinent"])

    def test_cyber_ecarte(self):
        c = S.classer("Cyber Security Managed Services", "SOC and SIEM", [])
        self.assertTrue(c["cyber"])
        self.assertFalse(c["pertinent"])      # cyber -> non pertinent

    def test_cyber_meme_avec_mot_physique(self):
        # Un avis qui parle de gardiennage ET de cyber : le cyber l'ecarte (prudence).
        c = S.classer("Security guard and cyber monitoring", "", [])
        self.assertTrue(c["physique"])
        self.assertFalse(c["pertinent"])

    def test_materiel_marque(self):
        c = S.classer("Supply and Delivery of CCTV Cameras", "", [])
        self.assertTrue(c["bien"])

    def test_generique_sans_physique(self):
        c = S.classer("Security Services", "", [])
        self.assertTrue(c["generique"])
        self.assertFalse(c["pertinent"])      # pas de terme physique explicite

    def test_security_guard_non_generique(self):
        # Contient "security" ET un terme physique -> physique, PAS generique.
        c = S.classer("Security Guard Services", "", [])
        self.assertTrue(c["physique"])
        self.assertFalse(c["generique"])
        self.assertTrue(c["pertinent"])

    def test_categorie_prise_en_compte(self):
        c = S.classer("RFP 2026-114", "", ["Manned guarding and patrol services"])
        self.assertTrue(c["pertinent"])


class TestZone(unittest.TestCase):

    def test_zone_amarante(self):
        self.assertEqual(S.zone(["Pakistan"]), "zone_amarante")
        self.assertEqual(S.zone(["United Arab Emirates"]), "zone_amarante")
        self.assertEqual(S.zone(["Bamako - Mali"]), "zone_amarante")

    def test_autre(self):
        self.assertEqual(S.zone(["Nova Scotia"]), "autre")
        self.assertEqual(S.zone([]), "autre")


class TestVerdict(unittest.TestCase):

    def test_exploitable(self):
        self.assertEqual(S.verdict({"pertinents": 30, "jours": 90})[0], "EXPLOITABLE")  # 10/mois

    def test_marginal(self):
        self.assertEqual(S.verdict({"pertinents": 3, "jours": 90})[0], "MARGINAL")      # 1/mois

    def test_seuil_pile(self):
        # 8/mois sur 30 j = seuil inclus.
        self.assertEqual(S.verdict({"pertinents": 8, "jours": 30})[0], "EXPLOITABLE")


def rec(rfx, titre, lieux=None, desc=""):
    return {"rfxID": rfx, "title": titre, "description": desc,
            "productsAndServicesCategories": [],
            "shipToOrServiceLocations": lieux if lieux is not None else ["Canada"]}


class TestSonder(unittest.TestCase):

    def test_pagination_dedup_et_comptage(self):
        appels = []
        def fetch(terme, page, cursor):
            appels.append((terme, page))
            if page == 1:
                return {"records": [rec("1", "Manned Guarding", ["Pakistan"]),
                                    rec("2", "Cyber Security SOC")],
                        "totalNumberOfRecords": 3, "searchAfter": ["c"]}
            return {"records": [rec("1", "Manned Guarding")],   # doublon
                    "totalNumberOfRecords": 3, "searchAfter": None}
        j = S.sonder(fetch=fetch, termes=["security guard services"], pages=3)
        self.assertEqual(j["uniques"], 2)
        self.assertEqual(j["pertinents"], 1)                    # guarding ; pas le cyber
        self.assertEqual(j["geo"]["zone_amarante"], 1)          # Pakistan

    def test_erreur_par_terme_isolee(self):
        def fetch(terme, page, cursor):
            if terme == "gardiennage":
                raise RuntimeError("503")
            return {"records": [rec("7", "Static Guarding Services", ["Qatar"])],
                    "totalNumberOfRecords": 1, "searchAfter": None}
        j = S.sonder(fetch=fetch, termes=["manned guarding", "gardiennage"], pages=1)
        self.assertEqual(j["pertinents"], 1)
        self.assertEqual(len(j["erreurs"]), 1)


if __name__ == "__main__":
    unittest.main()
