"""Tests du failover d'endpoint TED (primaire -> secondaire officiel).

La doc TED publie deux URLs equivalentes. poster_ted tente la primaire et
bascule sur la secondaire UNIQUEMENT sur echec reseau/timeout ou 5xx persistant.
Un 4xx ne declenche PAS de bascule (la degradation de champs d'interroger_ted
doit voir le 400).

Tout est hors-ligne : la session TED est simulee, aucun reseau.
"""

import unittest

import requests

import ted_complet_v14 as ted


class _Rep:
    """Reponse minimale imitant requests.Response."""
    def __init__(self, status=200, notices=None):
        self.status_code = status
        self._notices = notices if notices is not None else []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return {"notices": self._notices}


class _SessionScriptee:
    """Joue un comportement par URL et memorise l'ordre des URLs appelees.

    par_url : dict {url: callable_sans_arg}. Le callable retourne une _Rep ou
    leve une exception (pour simuler timeout / connexion coupee).
    """
    def __init__(self, par_url):
        self.par_url = par_url
        self.urls_appelees = []

    def post(self, url, json=None, timeout=None, **kw):
        self.urls_appelees.append(url)
        return self.par_url[url]()   # peut lever


PRIMAIRE = ted.TED_ENDPOINT
SECONDAIRE = ted.TED_ENDPOINT_SECONDAIRE


class _Base(unittest.TestCase):
    def setUp(self):
        self._session_avant = ted.session_robuste
        self._niveau_avant = ted._NIVEAU_ENRICHISSEMENT
        ted._NIVEAU_ENRICHISSEMENT = None

    def tearDown(self):
        ted.session_robuste = self._session_avant
        ted._NIVEAU_ENRICHISSEMENT = self._niveau_avant

    def _brancher(self, par_url):
        faux = _SessionScriptee(par_url)
        ted.session_robuste = lambda: faux
        return faux


# --- poster_ted : logique de bascule ---------------------------------------

class TestPosterTedFailover(_Base):

    def test_primaire_ok_pas_de_bascule(self):
        """Cas nominal : la primaire repond -> une seule URL, aucune bascule."""
        faux = self._brancher({PRIMAIRE: lambda: _Rep(200, [{"publication-number": "1-2026"}])})
        rep = ted.poster_ted({"q": "x"})
        self.assertEqual(rep.status_code, 200)
        self.assertEqual(faux.urls_appelees, [PRIMAIRE])

    def test_primaire_timeout_bascule_sur_secondaire(self):
        def _timeout():
            raise requests.exceptions.Timeout("primaire injoignable")
        faux = self._brancher({
            PRIMAIRE: _timeout,
            SECONDAIRE: lambda: _Rep(200, [{"publication-number": "9-2026"}]),
        })
        rep = ted.poster_ted({"q": "x"})
        self.assertEqual(rep.status_code, 200)
        self.assertEqual(faux.urls_appelees, [PRIMAIRE, SECONDAIRE])

    def test_primaire_5xx_bascule_sur_secondaire(self):
        faux = self._brancher({
            PRIMAIRE: lambda: _Rep(503),
            SECONDAIRE: lambda: _Rep(200),
        })
        rep = ted.poster_ted({"q": "x"})
        self.assertEqual(rep.status_code, 200)
        self.assertEqual(faux.urls_appelees, [PRIMAIRE, SECONDAIRE])

    def test_4xx_ne_bascule_pas(self):
        """Un 400 doit REMONTER tel quel (degradation de champs), sans toucher
        la secondaire."""
        faux = self._brancher({
            PRIMAIRE: lambda: _Rep(400),
            SECONDAIRE: lambda: _Rep(200),   # ne doit jamais etre appele
        })
        rep = ted.poster_ted({"q": "x"})
        self.assertEqual(rep.status_code, 400)
        self.assertEqual(faux.urls_appelees, [PRIMAIRE])

    def test_les_deux_en_echec_propage_l_exception(self):
        def _timeout():
            raise requests.exceptions.Timeout("injoignable")
        self._brancher({PRIMAIRE: _timeout, SECONDAIRE: _timeout})
        with self.assertRaises(requests.exceptions.Timeout):
            ted.poster_ted({"q": "x"})

    def test_session_injectee_est_utilisee(self):
        """Le param session (utilise par le collecteur d'attributions) doit
        primer sur session_robuste()."""
        # session_robuste ne doit PAS etre appelee si une session est fournie.
        ted.session_robuste = lambda: (_ for _ in ()).throw(
            AssertionError("session_robuste ne devrait pas etre appelee"))
        injectee = _SessionScriptee({PRIMAIRE: lambda: _Rep(200)})
        rep = ted.poster_ted({"q": "x"}, session=injectee)
        self.assertEqual(rep.status_code, 200)
        self.assertEqual(injectee.urls_appelees, [PRIMAIRE])

    def test_dernier_endpoint_5xx_est_retourne(self):
        """Si meme le secondaire renvoie 5xx, on retourne cette reponse (pas de
        bascule possible) : l'appelant fera raise_for_status comme avant."""
        faux = self._brancher({
            PRIMAIRE: lambda: _Rep(502),
            SECONDAIRE: lambda: _Rep(502),
        })
        rep = ted.poster_ted({"q": "x"})
        self.assertEqual(rep.status_code, 502)
        self.assertEqual(faux.urls_appelees, [PRIMAIRE, SECONDAIRE])


# --- Integration : interroger_ted profite du failover de facon transparente -

class TestInterrogerTedAvecFailover(_Base):

    def test_bascule_transparente_sur_page1(self):
        """La primaire tombe en timeout au tout premier appel ; interroger_ted
        doit servir les avis via la secondaire sans que l'appelant le sache."""
        page = [{"publication-number": "1-2026"}]

        def _timeout():
            raise requests.exceptions.Timeout("primaire down")

        faux = self._brancher({
            PRIMAIRE: _timeout,
            SECONDAIRE: lambda: _Rep(200, page),  # 1 notice < limite -> stop page 1
        })
        corps = {"query": "q", "fields": ["publication-number"], "limit": 250,
                 "page": 1, "scope": "ACTIVE"}
        resultats = ted.interroger_ted(corps_requete=corps, max_pages=3)
        self.assertEqual(len(resultats), 1)
        # Primaire tentee puis secondaire : la secondaire a bien servi la page.
        self.assertIn(SECONDAIRE, faux.urls_appelees)


if __name__ == "__main__":
    unittest.main()
