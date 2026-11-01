# -*- coding: utf-8 -*-
"""Cartographie devoir de vigilance : filtre d'assujettissement (sociétés hors
public/association), ligne de sortie, énumération paginée et exécution.
Offline : API injectée (aucun réseau)."""

import datetime
import unittest

import cartographie_vigilance_ie as C

C.PAUSE = 0.0
AUJ = datetime.date(2026, 11, 1)


def rec(nom="ELECTRICITE DE FRANCE", siren="552081317", nj="5599", naf="35.11Z",
        tranche="53", ville="PARIS", cat="GE"):
    return {"siren": siren, "nom_complet": nom, "nature_juridique": nj,
            "tranche_effectif_salarie": tranche, "categorie_entreprise": cat,
            "libelle_activite_principale": "Production d'electricite",
            "siege": {"activite_principale": naf, "libelle_commune": ville}}


class TestAssujettissement(unittest.TestCase):

    def test_societe_commerciale_retenue(self):
        self.assertTrue(C.est_assujettie(rec(nj="5599")))      # SA
        self.assertTrue(C.est_assujettie(rec(nj="5710")))      # SAS
        self.assertTrue(C.est_assujettie(rec(nj="6316")))      # coop

    def test_public_ecarte(self):
        self.assertFalse(C.est_assujettie(rec(nj="7229", naf="84.11Z")))   # collectivite
        self.assertFalse(C.est_assujettie(rec(nj="5599", naf="84.11Z")))   # NAF admin
        # Personne morale de droit public avec NAF commercial : ecartee par la
        # seule regle de nature juridique (7xxx), pas par le NAF.
        self.assertFalse(C.est_assujettie(rec(nj="7389", naf="35.11Z")))

    def test_association_ecartee(self):
        self.assertFalse(C.est_assujettie(rec(nj="9230")))      # Croix-Rouge


class TestLigne(unittest.TestCase):

    def test_ligne_depuis(self):
        v = C.ligne_depuis(rec(), AUJ)
        self.assertEqual(v["cle"], "552081317")
        self.assertEqual(v["effectif"], "10 000 et plus")
        self.assertEqual(v["ville"], "PARIS")
        self.assertEqual(v["detecte_le"], "2026-11-01")

    def test_effectif_tranche_52(self):
        self.assertEqual(C.ligne_depuis(rec(tranche="52"), AUJ)["effectif"], "5 000 a 9 999")

    def test_schemas(self):
        v = C.ligne_depuis(rec(), AUJ)
        self.assertEqual(len(C.ligne_sheet(v)), len(C.COLONNES_SHEET))
        self.assertEqual(len(C.valeurs_pg(v)), len(C.COLONNES_PG))
        self.assertEqual(C.ligne_sheet(v)[C.COL_CLE], "552081317")


class TestEnumeration(unittest.TestCase):

    def _fetch_pages(self, pages_data, total):
        def fetch(page, *a, **k):
            return {"total_results": total, "total_pages": len(pages_data),
                    "results": pages_data[page - 1]}
        return fetch

    def test_pagination(self):
        pages = [[rec(siren="1")], [rec(siren="2")], [rec(siren="3")]]
        records, total = C.enumerer(fetch=self._fetch_pages(pages, 3))
        self.assertEqual(total, 3)
        self.assertEqual([r["siren"] for r in records], ["1", "2", "3"])

    def test_plafond_pages(self):
        C.PAGES_MAX  # borne existante
        gros = [[rec(siren=str(i))] for i in range(100)]
        def fetch(page, *a, **k):
            return {"total_results": 100, "total_pages": 100, "results": gros[page - 1]}
        old = C.PAGES_MAX
        C.PAGES_MAX = 3
        try:
            records, _ = C.enumerer(fetch=fetch)
        finally:
            C.PAGES_MAX = old
        self.assertEqual(len(records), 3)        # borne a 3 pages


class TestExecuter(unittest.TestCase):

    def test_filtre_dedup_comptage(self):
        pages = [[rec(siren="1", nj="5599"), rec(siren="1", nj="5599"),   # doublon
                  rec(siren="2", nj="7229", naf="84.11Z"),               # public
                  rec(siren="3", nj="9230")]]                            # association
        def fetch(page, *a, **k):
            return {"total_results": 4, "total_pages": 1, "results": pages[0]}
        lignes, info = C.executer(fetch=fetch, aujourd=AUJ)
        self.assertEqual([v["siren"] for v in lignes], ["1"])
        self.assertEqual(info["assujetties"], 1)
        self.assertEqual(info["ecartees"], 2)       # public + association (doublon non compte ecarte)

    def test_api_en_echec(self):
        def fetch(page, *a, **k):
            raise RuntimeError("reset")
        lignes, info = C.executer(fetch=fetch, aujourd=AUJ)
        self.assertEqual(lignes, [])
        self.assertEqual(len(info["erreurs"]), 1)


class TestStockage(unittest.TestCase):

    def test_ecritures_sans_configuration(self):
        self.assertIn("absent", C.ecrire_pg(None, [{}]))
        self.assertIn("rien", C.ecrire_pg("postg:///x", []))
        self.assertIn("non configure", C.ecrire_sheet(None, []))


if __name__ == "__main__":
    unittest.main()
