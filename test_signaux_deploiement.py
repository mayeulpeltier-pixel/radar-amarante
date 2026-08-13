# -*- coding: utf-8 -*-
"""Decouverte de deploiements prives (12/08/2026).

Comble le gap : les collecteurs prives SUIVENT une watchlist ; ici on DECOUVRE
de nouvelles cibles par pays x mots-cles. Reutilise la plomberie bitd/signaux_
prives. On verrouille : requetes, pre-filtre, extraction (parsing), validation,
mapping vers prive_radar, dedup watchlist/evenement, budget. RSS + LLM simules.
"""

import unittest
from datetime import datetime, timezone, timedelta

import signaux_deploiement as sd


MAINTENANT = datetime.now(timezone.utc)


def _pubdate(jours):
    return (MAINTENANT - timedelta(days=jours)).strftime("%a, %d %b %Y 00:00:00 GMT")


def _rss(items):
    corps = "".join(
        "<item><title>{t}</title><link>{l}</link>"
        "<description>{d}</description><pubDate>{p}</pubDate></item>".format(**i)
        for i in items)
    return "<rss><channel>{}</channel></rss>".format(corps)


class TestRequetes(unittest.TestCase):
    def test_url_contient_pays_et_declencheurs(self):
        u = sd.url_pays("Mali", "fr")
        self.assertIn("news.google.com", u)
        self.assertIn("Mali", u)
        self.assertIn("FID", u)

    def test_rotation_pays(self):
        a = sd.pays_du_run(0)
        b = sd.pays_du_run(1)
        self.assertLessEqual(len(a), sd.NB_PAYS_PAR_RUN)
        self.assertNotEqual(a[0], b[0])          # la fenetre glisse


class TestPreFiltre(unittest.TestCase):
    def _fetch(self, items):
        return lambda url: _rss(items)

    def test_vieux_ecarte_frais_garde(self):
        items = [
            {"t": "Frais FID Mali", "l": "http://a?x=1", "d": "deploy", "p": _pubdate(2)},
            {"t": "Vieux", "l": "http://b", "d": "x", "p": _pubdate(200)},
        ]
        cand = sd.collecter_candidats(fetch=self._fetch(items))
        liens = [c["lien"] for c in cand]
        self.assertTrue(any("http://a" in l for l in liens))
        self.assertFalse(any("http://b" in l for l in liens))

    def test_iso3_de_requete_attache(self):
        cand = sd.collecter_candidats(
            fetch=self._fetch([{"t": "x FID", "l": "http://a", "d": "y", "p": _pubdate(1)}]))
        self.assertTrue(all("_iso3_requete" in c for c in cand))

    def test_dedup_par_lien(self):
        items = [{"t": "A", "l": "http://z?u=1", "d": "y", "p": _pubdate(1)}]
        # meme lien renvoye pour chaque requete -> dedup
        cand = sd.collecter_candidats(fetch=lambda u: _rss(items))
        self.assertEqual(len(cand), 1)


class TestExtraction(unittest.TestCase):
    def test_parsing_et_repli_iso3(self):
        art = {"titre": "t", "resume": "r", "_iso3_requete": "MLI", "_pays_requete": "Mali"}
        appel = lambda p: '{"signal": true, "entreprise": "X", "iso3": "", "pays": "", "type_activite": "implantation", "imminence": "court_terme", "confiance": 0.9, "resume": "z"}'
        ex = sd.extraire_deploiement(art, appel=appel)
        self.assertEqual(ex["iso3"], "MLI")      # repli sur le pays de la requete
        self.assertTrue(ex["signal"])

    def test_validation(self):
        self.assertTrue(sd._valide({"signal": True, "entreprise": "Acme", "confiance": 0.7}))
        self.assertFalse(sd._valide({"signal": False, "entreprise": "Acme", "confiance": 0.9}))
        self.assertFalse(sd._valide({"signal": True, "entreprise": "", "confiance": 0.9}))
        self.assertFalse(sd._valide({"signal": True, "entreprise": "Acme", "confiance": 0.1}))


class TestMappingEtOrchestration(unittest.TestCase):
    def _art(self, iso="MLI"):
        return {"titre": "FID", "resume": "r", "lien": "http://a?i=1",
                "date": _pubdate(1), "_iso3_requete": iso, "_pays_requete": "Mali"}

    def _appel(self, entreprise="NewCo", iso="MLI"):
        return (lambda p: ('{"signal": true, "entreprise": "%s", "iso3": "%s", '
                           '"pays": "Mali", "type_activite": "implantation", '
                           '"imminence": "court_terme", "confiance": 0.8, "resume": "z"}'
                           % (entreprise, iso)))

    def test_ligne_ecrite_au_schema_prive(self):
        l = sd.ligne_decouverte(self._art(), {"signal": True, "entreprise": "NewCo",
                                "iso3": "MLI", "pays": "Mali", "type_activite": "implantation",
                                "imminence": "court_terme", "confiance": 0.8, "resume": "z"})
        self.assertIsNotNone(l)
        self.assertEqual(len(l), len(sd.bitd.COLONNES_PRIVE))
        d = dict(zip(sd.bitd.COLONNES_PRIVE, l))
        self.assertEqual(d["entreprise"], "NewCo")
        self.assertEqual(d["priorite_compte"], "decouverte")

    def test_iso3_hors_suivi_ecarte(self):
        l = sd.ligne_decouverte(self._art(), {"signal": True, "entreprise": "NewCo",
                                "iso3": "ZZZ", "type_activite": "implantation",
                                "imminence": "court_terme", "confiance": 0.8})
        self.assertIsNone(l)

    def test_dedup_watchlist(self):
        cand = [self._art()]
        l, _ = sd.analyser_candidats(cand, appel=self._appel("KnownCo"),
                                     connues=["KnownCo"])
        self.assertEqual(l, [])                  # deja suivie -> pas redecouverte

    def test_budget_respecte(self):
        cand = [dict(self._art(), lien="http://a?i=%d" % k) for k in range(10)]
        _, nb = sd.analyser_candidats(cand, appel=self._appel(), budget=3)
        self.assertLessEqual(nb, 3)

    def test_dedup_evenement(self):
        # deux articles -> meme entreprise/pays/activite -> un seul evenement
        cand = [dict(self._art(), lien="http://a?i=1"),
                dict(self._art(), lien="http://a?i=2")]
        l, _ = sd.analyser_candidats(cand, appel=self._appel("SameCo"), connues=[])
        self.assertEqual(len(l), 1)


if __name__ == "__main__":
    unittest.main()
