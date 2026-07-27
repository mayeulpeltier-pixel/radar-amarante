# -*- coding: utf-8 -*-
"""Tests de radar_resilience : detection des erreurs transitoires, retry avec
backoff (sleep injecte, aucune attente reelle), non-retry des vraies erreurs,
epuisement des tentatives. Aucune dependance reseau/gspread."""

import unittest

import radar_resilience as rr


class FausseReponse:
    def __init__(self, code):
        self.status_code = code


class FausseAPIError(Exception):
    """Imite gspread.exceptions.APIError (.response.status_code + message).
    Message neutre par code (la detection doit s'appuyer sur le code HTTP)."""
    def __init__(self, code):
        super().__init__("APIError: [{}]".format(code))
        self.response = FausseReponse(code)


class TestDetection(unittest.TestCase):
    def test_transitoire_par_code(self):
        for c in (429, 500, 502, 503):
            self.assertTrue(rr.est_transitoire(FausseAPIError(c)))

    def test_non_transitoire(self):
        self.assertFalse(rr.est_transitoire(FausseAPIError(404)))
        self.assertFalse(rr.est_transitoire(ValueError("boom")))
        self.assertFalse(rr.est_transitoire(FausseAPIError(400)))

    def test_transitoire_par_message(self):
        # Sans .response, on retombe sur le message.
        self.assertTrue(rr.est_transitoire(Exception("APIError: [503]: unavailable")))
        self.assertTrue(rr.est_transitoire(Exception("The service is currently unavailable")))


class TestRetry(unittest.TestCase):
    def test_succes_apres_deux_echecs(self):
        essais = {"n": 0}
        attentes = []

        def op():
            essais["n"] += 1
            if essais["n"] < 3:
                raise FausseAPIError(503)
            return "ok"

        res = rr.avec_retry(op, tentatives=4, dormir=attentes.append)
        self.assertEqual(res, "ok")
        self.assertEqual(essais["n"], 3)            # 2 echecs + 1 succes
        self.assertEqual(attentes, [2.0, 4.0])      # backoff exponentiel

    def test_erreur_non_transitoire_relancee_sans_retry(self):
        essais = {"n": 0}

        def op():
            essais["n"] += 1
            raise FausseAPIError(404)

        with self.assertRaises(FausseAPIError):
            rr.avec_retry(op, tentatives=4, dormir=lambda s: None)
        self.assertEqual(essais["n"], 1)            # aucun retry

    def test_epuisement_relance_derniere_erreur(self):
        essais = {"n": 0}

        def op():
            essais["n"] += 1
            raise FausseAPIError(503)

        with self.assertRaises(FausseAPIError):
            rr.avec_retry(op, tentatives=3, dormir=lambda s: None)
        self.assertEqual(essais["n"], 3)            # 3 tentatives puis abandon

    def test_succes_immediat_sans_attente(self):
        attentes = []
        res = rr.avec_retry(lambda: 42, dormir=attentes.append)
        self.assertEqual(res, 42)
        self.assertEqual(attentes, [])              # aucun sommeil si succes direct


if __name__ == "__main__":
    unittest.main(verbosity=2)
