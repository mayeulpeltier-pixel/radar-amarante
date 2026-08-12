# -*- coding: utf-8 -*-
"""Alerte source silencieuse (regression, 12/08/2026).

Le mode d'echec vicieux : une source qui produisait s'arrete sans erreur. On
detecte la REGRESSION sur la tendance persistee (pas le vide chronique) et on
alerte fort (annotation GitHub Actions). Fonctions pures, aucun reseau.
"""

import unittest
import radar_runs as rr


def _run(horo, **vols):
    return {"type": "sante", "horodatage": horo,
            "sources": [{"src": k, "n": v} for k, v in vols.items()]}


class TestSourcesMuettes(unittest.TestCase):
    def _hist(self):
        # TED : produisait puis 0 x3 (regression). BM : toujours 0 (chronique).
        # IFC : produit encore.
        return [
            _run("2026-08-12", TED=0, BM=0, IFC=4),
            _run("2026-08-08", TED=0, BM=0, IFC=3),
            _run("2026-08-05", TED=0, BM=0, IFC=5),
            _run("2026-08-01", TED=12, BM=0, IFC=4),
            _run("2026-07-28", TED=9, BM=0, IFC=6),
        ]

    def test_regression_detectee(self):
        m = {x["src"]: x["runs_muets"] for x in rr.sources_muettes(self._hist(), 3)}
        self.assertEqual(m.get("TED"), 3)

    def test_vide_chronique_ignore(self):
        """BM n'a jamais produit -> jamais alertee (pas de faux positif)."""
        srcs = [x["src"] for x in rr.sources_muettes(self._hist(), 3)]
        self.assertNotIn("BM", srcs)

    def test_source_active_ignoree(self):
        srcs = [x["src"] for x in rr.sources_muettes(self._hist(), 3)]
        self.assertNotIn("IFC", srcs)

    def test_pas_assez_dhistorique(self):
        """Moins de `seuil` runs -> on ne conclut pas."""
        self.assertEqual(rr.sources_muettes(self._hist()[:2], 3), [])

    def test_silence_trop_court_pas_alerte(self):
        """Muette seulement 2 runs (< seuil 3) -> pas encore alertee."""
        hist = [
            _run("2026-08-12", TED=0, IFC=4),
            _run("2026-08-08", TED=0, IFC=3),
            _run("2026-08-05", TED=7, IFC=5),   # produisait il y a 3 runs
            _run("2026-08-01", TED=12, IFC=4),
        ]
        self.assertEqual([x["src"] for x in rr.sources_muettes(hist, 3)], [])

    def test_produit_dans_la_fenetre_pas_alerte(self):
        hist = [
            _run("2026-08-12", TED=5, IFC=4),   # a produit ce run
            _run("2026-08-08", TED=0, IFC=3),
            _run("2026-08-05", TED=0, IFC=5),
            _run("2026-08-01", TED=12, IFC=4),
        ]
        self.assertNotIn("TED", [x["src"] for x in rr.sources_muettes(hist, 3)])

    def test_entree_non_dict_ignoree(self):
        hist = self._hist() + ["bruit", None, 42]
        # ne leve pas, et detecte toujours TED
        self.assertIn("TED", [x["src"] for x in rr.sources_muettes(hist, 3)])


class TestAlerteEmission(unittest.TestCase):
    def test_annotation_github_emise(self):
        hist = [
            _run("2026-08-12", TED=0), _run("2026-08-08", TED=0),
            _run("2026-08-05", TED=0), _run("2026-08-01", TED=8),
            _run("2026-07-28", TED=9),
        ]
        out = []
        res = rr.alerter_sources_muettes(seuil_runs=3, emettre=out.append, hist=hist)
        self.assertEqual([x["src"] for x in res], ["TED"])
        self.assertTrue(any("::warning title=Source muette::" in l for l in out))

    def test_rien_a_signaler_silencieux(self):
        hist = [_run("2026-08-12", TED=5), _run("2026-08-08", TED=6),
                _run("2026-08-05", TED=4), _run("2026-08-01", TED=8)]
        out = []
        res = rr.alerter_sources_muettes(seuil_runs=3, emettre=out.append, hist=hist)
        self.assertEqual(res, [])
        self.assertEqual(out, [])          # aucun bruit si tout va bien

    def test_best_effort_ne_leve_jamais(self):
        # hist volontairement pourri : ne doit pas lever
        self.assertEqual(rr.alerter_sources_muettes(hist="pas une liste",
                                                    emettre=lambda *a: None), [])


if __name__ == "__main__":
    unittest.main()
