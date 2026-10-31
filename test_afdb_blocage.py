# -*- coding: utf-8 -*-
"""AfDB sous defi Cloudflare (depuis le 29/10/2026) : le blocage de la source
doit etre DISTINCT d'un echec. Le run reste vert (statut BLOQUE), le compteur
de blocages consecutifs persiste, et on re-alerte (ECHEC) au-dela du seuil.
Offline : flux et etat injectes, aucun reseau."""

import os
import tempfile
import unittest

import afdb_radar as A
import radar_run


PAGE_DEFI = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
             "<body><div class='cf-browser-verification'>enable JavaScript and "
             "cookies</div></body></html>")
XML_OK = ('<?xml version="1.0"?><rss><channel>'
          '<item><title>GPN - Mali - Projet routier</title>'
          '<link>http://x/1</link><pubDate>Mon, 27 Oct 2026 10:00:00 GMT</pubDate>'
          '</item></channel></rss>')


class TestDetecteurDefi(unittest.TestCase):

    def test_403_avec_defi(self):
        self.assertTrue(A._est_defi_cloudflare(403, PAGE_DEFI))

    def test_403_sans_marqueur_n_est_pas_un_defi(self):
        # Un vrai 403 d'autorisation (sans page de defi) reste une erreur.
        self.assertFalse(A._est_defi_cloudflare(403, "Forbidden: access denied"))

    def test_200_jamais_un_defi(self):
        self.assertFalse(A._est_defi_cloudflare(200, PAGE_DEFI))


class _Rep:
    def __init__(self, code, texte):
        self.status_code, self.text = code, texte

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP {}".format(self.status_code))


class _Session:
    def __init__(self, rep):
        self._rep = rep

    def get(self, *a, **k):
        return self._rep


class TestCollecterFlux(unittest.TestCase):

    def test_defi_leve_blocage(self):
        s = _Session(_Rep(403, PAGE_DEFI))
        with self.assertRaises(A.BlocageCloudflare):
            A.collecter_flux(session=s)

    def test_403_ordinaire_leve_une_erreur_normale(self):
        s = _Session(_Rep(403, "Forbidden"))
        with self.assertRaises(RuntimeError):
            A.collecter_flux(session=s)

    def test_200_renvoie_le_texte(self):
        s = _Session(_Rep(200, XML_OK))
        self.assertEqual(A.collecter_flux(session=s), XML_OK)


class TestMainBlocage(unittest.TestCase):
    """main() isole le blocage de l'echec, compte les runs consecutifs, et
    re-alerte au seuil. On injecte un fichier d'etat temporaire."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self._orig_fichier = A.AFDB_ETAT_FICHIER
        self._orig_seuil = A.SEUIL_BLOCAGE_RUNS
        self._orig_flux = A.collecter_flux
        A.AFDB_ETAT_FICHIER = self.tmp.name
        A.SEUIL_BLOCAGE_RUNS = 4
        os.environ.pop("AFDB_DRY_RUN", None)

    def tearDown(self):
        A.AFDB_ETAT_FICHIER = self._orig_fichier
        A.SEUIL_BLOCAGE_RUNS = self._orig_seuil
        A.collecter_flux = self._orig_flux
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def _bloquer(self):
        def faux(*a, **k):
            raise A.BlocageCloudflare("defi")
        A.collecter_flux = faux

    def test_premier_blocage_laisse_vert(self):
        self._bloquer()
        self.assertEqual(A.main(), "BLOQUE")
        self.assertEqual(A._lire_blocages(), 1)

    def test_blocages_consecutifs_cumules_puis_alerte(self):
        self._bloquer()
        self.assertEqual(A.main(), "BLOQUE")   # 1
        self.assertEqual(A.main(), "BLOQUE")   # 2
        self.assertEqual(A.main(), "BLOQUE")   # 3
        self.assertEqual(A._lire_blocages(), 3)
        with self.assertRaises(A.BlocageCloudflare):   # 4e : on re-alerte
            A.main()
        self.assertEqual(A._lire_blocages(), 4)

    def test_succes_reinitialise_le_compteur(self):
        A._ecrire_blocages(3)

        def ok(*a, **k):
            return XML_OK
        A.collecter_flux = ok
        A.main()                                # succes (pas de cle LLM -> sort tot)
        self.assertEqual(A._lire_blocages(), 0)

    def test_compteur_absent_vaut_zero(self):
        os.unlink(self.tmp.name)
        self.assertEqual(A._lire_blocages(), 0)


class TestOrchestrateurTroisEtats(unittest.TestCase):
    """lancer_collecteur distingue OK / BLOQUE / ECHEC et n'echoue le run que
    sur ECHEC."""

    class _Mod:
        def __init__(self, retour=None, exc=None):
            self._r, self._e = retour, exc

        def main(self):
            if self._e:
                raise self._e
            return self._r

    def test_bloque_n_est_pas_un_echec(self):
        statut, _ = radar_run.lancer_collecteur("t", self._Mod(retour="BLOQUE"))
        self.assertEqual(statut, "BLOQUE")

    def test_retour_normal_est_ok(self):
        statut, _ = radar_run.lancer_collecteur("t", self._Mod(retour=None))
        self.assertEqual(statut, "OK")

    def test_exception_est_un_echec(self):
        statut, _ = radar_run.lancer_collecteur("t", self._Mod(exc=RuntimeError("x")))
        self.assertEqual(statut, "ECHEC")

    def test_run_echoue_seulement_sur_echec(self):
        # BLOQUE ne fait pas echouer le run ; ECHEC oui.
        self.assertFalse(radar_run.run_echoue(
            {"TED": ("OK", 1.0), "AfDB": ("BLOQUE", 0.0)}))
        self.assertTrue(radar_run.run_echoue(
            {"TED": ("OK", 1.0), "AfDB": ("BLOQUE", 0.0), "BM": ("ECHEC", 2.0)}))
        self.assertFalse(radar_run.run_echoue({}))


if __name__ == "__main__":
    unittest.main()
