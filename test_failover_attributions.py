"""Failover TED du collecteur d'ATTRIBUTIONS (ted_complet_attributions).

collecte_attributions route desormais son POST par ted.poster_ted, donc il
herite du failover primaire -> secondaire. On verifie ici la bascule de bout en
bout, sans reseau (session scriptee injectee, fetch=None).

Rappel : quand `fetch` est fourni, poster_ted n'est jamais atteint (court-circuit
avant reseau). Ces tests injectent une SESSION, pas un fetch, pour exercer
justement le chemin reseau + failover.
"""

import unittest

import requests

import ted_complet_v14 as ted
import ted_complet_attributions as attr


PRIMAIRE = ted.TED_ENDPOINT
SECONDAIRE = ted.TED_ENDPOINT_SECONDAIRE


class _Rep:
    def __init__(self, status=200, notices=None):
        self.status_code = status
        self._notices = notices if notices is not None else []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return {"notices": self._notices}


class _SessionScriptee:
    def __init__(self, par_url):
        self.par_url = par_url
        self.urls_appelees = []

    def post(self, url, json=None, timeout=None, **kw):
        self.urls_appelees.append(url)
        return self.par_url[url]()   # peut lever


class TestAttributionsFailover(unittest.TestCase):

    def setUp(self):
        self._niveau_avant = ted._NIVEAU_ENRICHISSEMENT
        ted._NIVEAU_ENRICHISSEMENT = None

    def tearDown(self):
        ted._NIVEAU_ENRICHISSEMENT = self._niveau_avant

    def test_bascule_primaire_vers_secondaire(self):
        """Primaire en timeout -> la collecte doit servir les attributions via
        la secondaire, de facon transparente."""
        notice = {"publication-number": "302871-2026", "notice-type": "can-standard"}

        def _timeout():
            raise requests.exceptions.Timeout("primaire down")

        faux = _SessionScriptee({
            PRIMAIRE: _timeout,
            SECONDAIRE: lambda: _Rep(200, [notice]),  # 1 < LIMITE -> stop page 1
        })
        bruts = attr.collecte_attributions(session=faux)
        self.assertEqual(len(bruts), 1)
        self.assertEqual(bruts[0]["publication-number"], "302871-2026")
        self.assertIn(SECONDAIRE, faux.urls_appelees)

    def test_primaire_ok_pas_de_bascule(self):
        """Cas nominal : la primaire repond, la secondaire n'est jamais appelee."""
        notice = {"publication-number": "1-2026", "notice-type": "can-standard"}
        faux = _SessionScriptee({
            PRIMAIRE: lambda: _Rep(200, [notice]),
            SECONDAIRE: lambda: _Rep(200, [{"publication-number": "NE_DOIT_PAS"}]),
        })
        bruts = attr.collecte_attributions(session=faux)
        self.assertEqual(len(bruts), 1)
        self.assertEqual(faux.urls_appelees, [PRIMAIRE])

    def test_fetch_court_circuite_le_reseau(self):
        """Contrat preserve : si fetch est fourni, aucun endpoint n'est touche."""
        appels = []

        def faux_fetch(payload):
            appels.append(payload)
            return {"notices": []}   # vide -> arret immediat

        bruts = attr.collecte_attributions(fetch=faux_fetch)
        self.assertEqual(bruts, [])
        self.assertTrue(appels, "fetch aurait du etre appele")


if __name__ == "__main__":
    unittest.main()
