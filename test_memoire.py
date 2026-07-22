# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DE LA MEMOIRE INTER-RUNS (Sheet -> Postgres).
======================================================================

`ted.numeros_publication_existants` est le POINT UNIQUE par lequel huit
collecteurs decident ce qu'ils ont deja traite. S'y tromper coute cher :
  - memoire trop PETITE  -> reanalyse LLM et lignes en double ;
  - memoire trop GRANDE  -> de vrais leads sautes, en silence.

D'ou la phase d'ombre : les deux sources sont lues, l'ecart est journalise,
mais le Sheet fait foi tant que RADAR_MEMOIRE=pg n'est pas pose. Ces tests
verrouillent les deux regimes ET tous les replis, sans reseau ni base : on
substitue les deux lecteurs internes.
"""

import io
import os
import unittest
from contextlib import redirect_stdout

import ted_complet_v14 as ted


class TestMemoireInterRuns(unittest.TestCase):

    def setUp(self):
        self._sheet, self._pg = ted._memoire_depuis_sheet, ted._memoire_depuis_pg
        self._env = os.environ.pop("RADAR_MEMOIRE", None)

    def tearDown(self):
        ted._memoire_depuis_sheet, ted._memoire_depuis_pg = self._sheet, self._pg
        if self._env is None:
            os.environ.pop("RADAR_MEMOIRE", None)
        else:
            os.environ["RADAR_MEMOIRE"] = self._env

    def _brancher(self, sheet, pg):
        ted._memoire_depuis_sheet = lambda *a, **k: sheet
        ted._memoire_depuis_pg = lambda *a, **k: pg

    def _appeler(self):
        """Renvoie (resultat, journal) : le journal compte, c'est lui qui
        dira a l'analyste si la bascule est sans risque."""
        tampon = io.StringIO()
        with redirect_stdout(tampon):
            res = ted.numeros_publication_existants(
                "sheet-id", "cle.json", "ted_radar", ["publication_number"])
        return res, tampon.getvalue()

    # -- Phase d'ombre (regime par defaut) --------------------------------
    def test_le_sheet_fait_foi_par_defaut(self):
        """Meme quand Postgres connait PLUS de choses, on ne change rien tant
        que la bascule n'est pas demandee."""
        self._brancher({"A", "B"}, {"A", "B", "C"})
        res, _j = self._appeler()
        self.assertEqual(res, {"A", "B"})

    def test_ecart_journalise(self):
        self._brancher({"A", "B"}, {"B", "C"})
        _res, journal = self._appeler()
        self.assertIn("ECART", journal)
        self.assertIn("1 absent(s)", journal)      # A manque en base
        self.assertIn("1 en trop", journal)        # C est en trop

    def test_accord_journalise_comme_sans_risque(self):
        self._brancher({"A", "B"}, {"A", "B"})
        _res, journal = self._appeler()
        self.assertIn("identiques", journal)
        self.assertIn("bascule sans risque", journal)

    # -- Apres bascule ----------------------------------------------------
    def test_bascule_postgres_fait_foi(self):
        os.environ["RADAR_MEMOIRE"] = "pg"
        self._brancher({"A"}, {"A", "B", "C"})
        res, journal = self._appeler()
        self.assertEqual(res, {"A", "B", "C"})
        self.assertIn("Postgres fait foi", journal)

    def test_bascule_ne_lit_meme_plus_le_sheet(self):
        """Interet de la bascule : plus aucun appel a Google Sheets."""
        os.environ["RADAR_MEMOIRE"] = "pg"
        appels = []
        ted._memoire_depuis_sheet = lambda *a, **k: appels.append(1) or set()
        ted._memoire_depuis_pg = lambda *a, **k: {"A"}
        self._appeler()
        self.assertEqual(appels, [])

    # -- Replis (la base ne doit jamais bloquer un run) --------------------
    def test_base_indisponible_repli_sur_le_sheet(self):
        self._brancher({"A", "B"}, None)
        res, journal = self._appeler()
        self.assertEqual(res, {"A", "B"})
        self.assertNotIn("ECART", journal)

    def test_bascule_demandee_mais_base_muette(self):
        """Cas le plus dangereux : bascule active et Postgres injoignable.
        On DOIT retomber sur le Sheet, pas renvoyer un ensemble vide (qui
        ferait tout reanalyser et exploserait le budget LLM)."""
        os.environ["RADAR_MEMOIRE"] = "pg"
        self._brancher({"A", "B"}, None)
        res, journal = self._appeler()
        self.assertEqual(res, {"A", "B"})
        self.assertIn("repli sur le Sheet", journal)

    def test_memoire_pg_avale_les_pannes(self):
        """_memoire_depuis_pg renvoie None, jamais une exception."""
        import radar_stockage
        original = radar_stockage.connexion
        radar_stockage.connexion = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("base morte"))
        avant = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://x:x@127.0.0.1:59999/nulle"
        try:
            with redirect_stdout(io.StringIO()):
                self.assertIsNone(ted._memoire_depuis_pg("ted_radar"))
        finally:
            radar_stockage.connexion = original
            if avant is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = avant


if __name__ == "__main__":
    unittest.main(verbosity=2)
