# -*- coding: utf-8 -*-
"""Confidentialite et authentification de l'application web.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Deux constats en relisant `radar_app`, l'un concret, l'autre latent.

1. AUCUNE DIRECTIVE DE CACHE SUR LA PAGE DE LEADS
   En-tetes reellement servis avant correction :

       content-type, content-encoding, vary

   C'est tout. Or cette page porte l'integralite du renseignement commercial
   (~2 500 leads, 2,6 Mo avant compression). Sans `Cache-Control`, le
   navigateur la conserve dans son cache disque : sur un portable partage ou
   emprunte, la liste reste consultable APRES la session, sans avoir a se
   reauthentifier. L'authentification Basic placee devant ne protege alors
   plus rien.

2. `compare_digest` SUR DES CHAINES, ET UN COURT-CIRCUIT
   - `compare_digest` LEVE une TypeError des qu'un caractere n'est pas ASCII.
     Aujourd'hui starlette rejette le non-ASCII avant d'arriver la, donc le
     defaut ne se voit pas -- mais on depend d'un detail d'implementation
     d'une dependance epinglee. Si starlette suivait la RFC 7617 (qui
     autorise UTF-8), un mot de passe accentue deviendrait inutilisable et
     produirait des 500 au lieu de 401.
   - `compare_digest(user) and compare_digest(mdp)` : quand l'identifiant est
     faux, la comparaison du mot de passe n'a pas lieu. L'ecart de temps
     mesurable revele si un identifiant existe, ce que `compare_digest` sert
     precisement a empecher. Le `and` annulait le benefice.

CE QUE CE FICHIER NE TESTE PAS, ET POURQUOI
-------------------------------------------
Il ne teste aucun cloisonnement multi-client, parce qu'il n'y en a pas et
qu'il ne faut pas faire croire le contraire. Le cache est global, mais ce
n'est pas la vulnerabilite : `lire_onglets_pg` lit TOUTES les lignes sans
notion de client, donc deux comptes verraient les memes donnees meme avec des
caches separes. Le detail de ce qu'un vrai multi-client exige est documente en
tete de la section CACHE de `radar_app`.
"""

import os
import unittest

try:
    from fastapi.testclient import TestClient
    from fastapi import HTTPException
    from fastapi.security import HTTPBasicCredentials
    import radar_app
    PRET = True
except Exception:                       # fastapi/httpx absents en local
    PRET = False

URL_TEST = os.environ.get("RADAR_TEST_DATABASE_URL", "")


class _Identifiants:
    """Installe un couple utilisateur/mot de passe et le retire proprement."""

    def __init__(self, utilisateur="radar", mot_de_passe="secret"):
        self.utilisateur, self.mot_de_passe = utilisateur, mot_de_passe

    def __enter__(self):
        self._avant = {c: os.environ.get(c) for c in
                       ("RADAR_APP_UTILISATEUR", "RADAR_APP_MOT_DE_PASSE")}
        os.environ["RADAR_APP_UTILISATEUR"] = self.utilisateur
        os.environ["RADAR_APP_MOT_DE_PASSE"] = self.mot_de_passe
        return self

    def __exit__(self, *a):
        for cle, valeur in self._avant.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        return False


# ===========================================================================
# AUTHENTIFICATION
# ===========================================================================

@unittest.skipUnless(PRET, "fastapi/httpx indisponibles")
class TestAuthentification(unittest.TestCase):

    def _verifier(self, utilisateur, mot_de_passe):
        return radar_app._verifier(
            HTTPBasicCredentials(username=utilisateur, password=mot_de_passe))

    def test_identifiants_valides(self):
        with _Identifiants():
            self.assertEqual(self._verifier("radar", "secret"), "radar")

    def test_l_identite_authentifiee_est_renvoyee(self):
        """Elle ne sert a rien aujourd'hui, mais c'est elle qui servira de
        portee de cache le jour d'un vrai multi-client. Autant que le point
        d'accroche existe et soit teste plutot que d'etre invente dans
        l'urgence."""
        with _Identifiants(utilisateur="client_a"):
            self.assertEqual(self._verifier("client_a", "secret"), "client_a")

    def test_mot_de_passe_faux(self):
        with _Identifiants():
            with self.assertRaises(HTTPException) as ctx:
                self._verifier("radar", "faux")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_identifiant_faux(self):
        with _Identifiants():
            with self.assertRaises(HTTPException) as ctx:
                self._verifier("intrus", "secret")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_identifiant_non_ascii_donne_401_et_non_500(self):
        """LA correction. Avant : TypeError, donc 500 -- une erreur serveur
        pour ce qui est un simple echec d'authentification."""
        with _Identifiants():
            with self.assertRaises(HTTPException) as ctx:
                self._verifier("radaré", "secret")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_mot_de_passe_non_ascii_utilisable(self):
        """Un mot de passe accentue est parfaitement legitime, surtout sur un
        projet francais. Avant, il faisait lever la comparaison."""
        with _Identifiants(mot_de_passe="mot_de_passé_très_sûr"):
            self.assertEqual(self._verifier("radar", "mot_de_passé_très_sûr"),
                             "radar")
            with self.assertRaises(HTTPException):
                self._verifier("radar", "autre_chose")

    def test_verrouille_sans_mot_de_passe_configure(self):
        """Ferme par defaut : mieux vaut une application indisponible qu'une
        application ouverte."""
        avant = os.environ.get("RADAR_APP_MOT_DE_PASSE")
        os.environ.pop("RADAR_APP_MOT_DE_PASSE", None)
        try:
            with self.assertRaises(HTTPException) as ctx:
                self._verifier("radar", "peu importe")
            self.assertEqual(ctx.exception.status_code, 503)
        finally:
            if avant is not None:
                os.environ["RADAR_APP_MOT_DE_PASSE"] = avant

    def test_les_deux_comparaisons_sont_toujours_evaluees(self):
        """Contre l'oracle de temps : avec un `and` court-circuitant, un
        identifiant faux evitait la comparaison du mot de passe, et l'ecart
        mesurable revelait quels identifiants existent.

        On inspecte le source plutot que de chronometrer : une mesure de temps
        en CI est bruyante au point d'etre inexploitable, alors que la
        propriete recherchee est structurelle."""
        import inspect
        source = inspect.getsource(radar_app._verifier)
        self.assertIn("util_ok = ", source)
        self.assertIn("mdp_ok = ", source)
        self.assertNotIn("compare_digest(identifiants.username, attendu_util) and",
                         source)

    def test_comparaison_sur_octets(self):
        """La cause racine : `compare_digest` sur des `str` leve sur du
        non-ASCII. Comparer des octets supprime toute hypothese sur la couche
        qui a decode l'en-tete Basic."""
        import inspect
        source = inspect.getsource(radar_app._verifier)
        self.assertIn('.encode("utf-8")', source)


# ===========================================================================
# EN-TETES DE CONFIDENTIALITE
# ===========================================================================

@unittest.skipUnless(PRET, "fastapi/httpx indisponibles")
class TestEnTetesPrives(unittest.TestCase):

    ATTENDUS = {
        "cache-control": "private, no-store, max-age=0",
        "referrer-policy": "no-referrer",
        "x-frame-options": "DENY",
        "x-content-type-options": "nosniff",
    }

    def test_la_constante_couvre_les_quatre_directives(self):
        poses = {k.lower() for k in radar_app.EN_TETES_PRIVES}
        self.assertEqual(poses, set(self.ATTENDUS),
                         "la liste des en-tetes a change sans que le test suive")

    def test_cache_control_interdit_toute_conservation(self):
        """`no-store` est la directive qui compte : `no-cache` autoriserait
        encore l'ecriture sur disque, avec revalidation. Ici on ne veut aucune
        trace de la page apres la session."""
        valeur = radar_app.EN_TETES_PRIVES["Cache-Control"]
        self.assertIn("no-store", valeur)
        self.assertIn("private", valeur)

    @unittest.skipUnless(URL_TEST, "RADAR_TEST_DATABASE_URL non defini")
    def test_page_de_leads_servie_avec_les_en_tetes(self):
        avant = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = URL_TEST
        try:
            with _Identifiants():
                client = TestClient(radar_app.app)
                reponse = client.get("/", auth=("radar", "secret"))
                self.assertEqual(reponse.status_code, 200)
                for entete, valeur in self.ATTENDUS.items():
                    self.assertEqual(reponse.headers.get(entete), valeur,
                                     "en-tete {} absent ou incorrect".format(entete))
        finally:
            if avant is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = avant

    @unittest.skipUnless(URL_TEST, "RADAR_TEST_DATABASE_URL non defini")
    def test_api_statut_servie_avec_les_en_tetes(self):
        """La reponse JSON est moins sensible, mais une exception dans la
        regle est une exception a expliquer : autant n'en pas avoir."""
        avant = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = URL_TEST
        try:
            with _Identifiants():
                client = TestClient(radar_app.app)
                reponse = client.post(
                    "/api/statut",
                    json={"onglet": "test_entetes",
                          "publication_number": "P-ENTETES",
                          "statut": "contacte"},
                    auth=("radar", "secret"))
                self.assertEqual(reponse.status_code, 200)
                self.assertEqual(reponse.headers.get("cache-control"),
                                 self.ATTENDUS["cache-control"])
        finally:
            if avant is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = avant


if __name__ == "__main__":
    unittest.main()
