"""Tests du backfill par tranches de dates (pepite 2, alternative a ITERATION).

collecter_backfill decoupe une plage en fenetres bornees et appelle
interroger_ted par tranche. On verifie : bornes correctes, decoupage disjoint,
dedup par publication-number, inversion depuis/jusqu toleree. Tout hors-ligne
(interroger_ted simule).
"""

import unittest

import ted_complet_v14 as ted


class _Base(unittest.TestCase):
    def setUp(self):
        self._interroger_avant = ted.interroger_ted
        self._flag_avant = ted.os.environ.get("RADAR_TED_FILTRE_DATE")

    def tearDown(self):
        ted.interroger_ted = self._interroger_avant
        if self._flag_avant is None:
            ted.os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        else:
            ted.os.environ["RADAR_TED_FILTRE_DATE"] = self._flag_avant


# --- construire_requete : borne haute + forcer_bornes ----------------------

class TestBornesRequete(_Base):

    def test_jusqu_date_ajoute_borne_haute(self):
        ted.os.environ["RADAR_TED_FILTRE_DATE"] = "1"
        corps = ted.construire_requete("20260101", "20260107")
        self.assertIn("publication-date>=20260101", corps["query"])
        self.assertIn("publication-date<=20260107", corps["query"])

    def test_forcer_bornes_ignore_le_flag(self):
        """Le backfill impose ses bornes meme flag OFF."""
        ted.os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        corps = ted.construire_requete("20260101", "20260107", forcer_bornes=True)
        self.assertIn("publication-date>=20260101", corps["query"])
        self.assertIn("publication-date<=20260107", corps["query"])

    def test_sans_forcer_ni_flag_pas_de_borne(self):
        """Garde-fou pepite 1 preserve : rien sans flag ni forcer_bornes."""
        ted.os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        corps = ted.construire_requete("20260101", "20260107")
        self.assertNotIn("publication-date", corps["query"])


# --- collecter_backfill : decoupage, bornes, dedup -------------------------

class TestCollecterBackfill(_Base):

    def test_decoupage_en_tranches_disjointes(self):
        captures = []

        def faux(corps, max_pages=None):
            captures.append(corps["query"])
            return [{"publication-number": "P{}".format(len(captures))}]

        ted.interroger_ted = faux
        # 14 jours, pas 7 -> 2 tranches : [01-01,01-07], [01-08,01-14]
        res = ted.collecter_backfill("2026-01-01", "2026-01-14", pas_jours=7)
        self.assertEqual(len(captures), 2)
        self.assertIn("publication-date>=20260101", captures[0])
        self.assertIn("publication-date<=20260107", captures[0])
        self.assertIn("publication-date>=20260108", captures[1])   # disjoint
        self.assertIn("publication-date<=20260114", captures[1])
        self.assertEqual(len(res), 2)

    def test_derniere_tranche_bornee_a_jusqu(self):
        captures = []

        def faux(corps, max_pages=None):
            captures.append(corps["query"])
            return []

        ted.interroger_ted = faux
        # 10 jours, pas 7 -> [01-01,01-07], [01-08,01-10] (tronquee a jusqu)
        ted.collecter_backfill("2026-01-01", "2026-01-10", pas_jours=7)
        self.assertEqual(len(captures), 2)
        self.assertIn("publication-date<=20260110", captures[1])

    def test_dedup_par_publication_number(self):
        def faux(corps, max_pages=None):
            return [{"publication-number": "MEME"}]   # meme avis a chaque tranche

        ted.interroger_ted = faux
        res = ted.collecter_backfill("2026-01-01", "2026-01-21", pas_jours=7)
        self.assertEqual(len(res), 1)   # 3 tranches, mais dedup -> 1

    def test_avis_sans_numero_conserves(self):
        def faux(corps, max_pages=None):
            return [{"notice-title": "sans numero"}]

        ted.interroger_ted = faux
        res = ted.collecter_backfill("2026-01-01", "2026-01-07", pas_jours=7)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["notice-title"], "sans numero")

    def test_depuis_jusqu_inverses_tolere(self):
        captures = []

        def faux(corps, max_pages=None):
            captures.append(corps["query"])
            return []

        ted.interroger_ted = faux
        # jusqu < depuis : la fonction remet dans l'ordre
        ted.collecter_backfill("2026-01-14", "2026-01-08", pas_jours=7)
        self.assertTrue(captures)
        self.assertIn("publication-date>=20260108", captures[0])


if __name__ == "__main__":
    unittest.main()
