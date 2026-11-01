# -*- coding: utf-8 -*-
"""Tableau de santé des sources : analyse pure (fraîcheur, état, tri) et
construction des stats depuis un curseur injecté. Offline, aucun réseau."""

import datetime
import unittest

import sante_sources as S

AUJ = datetime.date(2026, 11, 1)


class TestAnalyse(unittest.TestCase):

    def test_jours_depuis(self):
        self.assertEqual(S.jours_depuis("2026-10-22", AUJ), 10)
        self.assertEqual(S.jours_depuis("2026-10-22T09:00:00Z", AUJ), 10)
        self.assertIsNone(S.jours_depuis("", AUJ))
        self.assertIsNone(S.jours_depuis("pas une date", AUJ))

    def test_etat(self):
        self.assertEqual(S.etat(2, 100), "OK")
        self.assertEqual(S.etat(15, 100), "VIEILLIT")      # > 10
        self.assertEqual(S.etat(40, 100), "MUET")          # > 30
        self.assertEqual(S.etat(10, 100), "OK")            # pile seuil vieillit -> encore OK
        self.assertEqual(S.etat(30, 100), "VIEILLIT")      # pile seuil muet -> pas encore muet
        self.assertEqual(S.etat(None, 0), "VIDE")
        self.assertEqual(S.etat(None, None), "INDISPONIBLE")
        self.assertEqual(S.etat(None, 5), "SANS DATE")

    def test_enrichir_et_tri(self):
        stats = [{"source": "a", "lignes": 100, "dernier": "2026-10-31"},   # OK
                 {"source": "b", "lignes": 5, "dernier": "2026-08-01"},     # MUET
                 {"source": "c", "lignes": None, "dernier": None}]          # INDISPONIBLE
        out = S.enrichir(stats, AUJ)
        # Problèmes d'abord : INDISPONIBLE, MUET, puis OK.
        self.assertEqual([x["source"] for x in out], ["c", "b", "a"])
        self.assertEqual(out[-1]["etat"], "OK")
        self.assertEqual(out[1]["etat"], "MUET")

    def test_resume(self):
        c = S.resume([{"etat": "OK"}, {"etat": "OK"}, {"etat": "MUET"}])
        self.assertEqual(c, {"OK": 2, "MUET": 1})


class FauxCurseur:
    """Simule psycopg : renvoie des réponses programmées, ou lève pour une table."""
    def __init__(self, grouped=None, scalaire=None, casse=()):
        self.grouped = grouped or {}      # table -> [(grp, n, dernier), ...]
        self.scalaire = scalaire or {}    # table -> (n, dernier)
        self.casse = set(casse)           # tables qui lèvent
        self._res = None

    def execute(self, sql):
        table = next((t for t in list(self.grouped) + list(self.scalaire) + list(self.casse)
                      if (" " + t + " ") in sql or sql.endswith(t)), None)
        if table in self.casse:
            raise RuntimeError("relation absente")
        if "GROUP BY" in sql:
            self._res = ("g", self.grouped.get(table, []))
        else:
            self._res = ("s", self.scalaire.get(table, (0, None)))

    def fetchall(self):
        return self._res[1]

    def fetchone(self):
        return self._res[1]


class TestStatsTable(unittest.TestCase):

    def test_groupe(self):
        cur = FauxCurseur(grouped={"radar_lignes": [("ted", 120, "2026-10-30"),
                                                    ("bm", 40, "2026-09-01")]})
        out = S._stats_table(cur, "Operations", "radar_lignes", "date_detection", "onglet")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["source"], "Operations / ted")
        self.assertEqual(out[0]["lignes"], 120)
        self.assertEqual(out[0]["dernier"], "2026-10-30")

    def test_scalaire(self):
        cur = FauxCurseur(scalaire={"radar_ie_vigilance": (138, "2026-11-01")})
        out = S._stats_table(cur, "IE vigilance", "radar_ie_vigilance", "detecte_le", None)
        self.assertEqual(out, [{"source": "IE vigilance", "lignes": 138, "dernier": "2026-11-01"}])

    def test_table_absente(self):
        cur = FauxCurseur(casse=("radar_ie_echeances",))
        out = S._stats_table(cur, "IE echeances", "radar_ie_echeances", "cree_le", None)
        self.assertEqual(out, [{"source": "IE echeances", "lignes": None, "dernier": None}])
        self.assertEqual(S.etat(None, out[0]["lignes"]), "INDISPONIBLE")


class TestCollecter(unittest.TestCase):

    def test_sans_url(self):
        self.assertEqual(S.collecter(None), [])


if __name__ == "__main__":
    unittest.main()
