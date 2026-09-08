# -*- coding: utf-8 -*-
"""Sonde ACLED : discriminer la cause d'un refus (08/09/2026).

CE QU'A DONNE LE PREMIER LANCEMENT
----------------------------------
    A. OAUTH  : HTTP 200, access_token reçu (811 car.)  -> OK
    B. DATA   : HTTP 403 {"message":"Access denied"}    -> « à creuser »
    C. AGREG. : HTTP 403                                -> « à creuser »

OAuth réussit et les données sont refusées : c'est une AUTORISATION, pas une
authentification. Les identifiants sont bons, ce compte n'a pas le droit de
lire -- ou bien la requête est mal formée.

LE PROBLÈME DE LA SONDE, PAS DE L'API
-------------------------------------
« À creuser » ne dit pas QUOI creuser. Sans distinction, on repart réécrire
une requête qui n'a peut-être rien à se reprocher, alors que la démarche à
faire est sur le site d'ACLED. Une sonde qui constate sans discriminer envoie
au mauvais chantier.

QUATRE HYPOTHÈSES, TESTÉES DE LA MOINS COÛTEUSE À LA PLUS COÛTEUSE
------------------------------------------------------------------
1. le schéma d'autorisation n'est pas « Bearer » -> une ligne à changer ;
2. ce sont les filtres qui sont refusés -> la requête à revoir ;
3. le compte n'a pas de droit de lecture -> démarche côté ACLED, aucun code ;
4. l'endpoint a bougé.

Aucune écriture, aucun contournement : on établit un fait.

Tests OFFLINE : session HTTP factice, aucun réseau.
"""

import unittest

import sonde_acled as sa


class Reponse:
    def __init__(self, code, corps="", url="http://exemple/api"):
        self.status_code = code
        self.text = corps
        self.url = url
        self.headers = {}

    def json(self):
        import json
        return json.loads(self.text)


OK = '{"status":200,"count":1,"data":[{"country":"Mali"}]}'
REFUS = '{"message":"Access denied"}'


class SessionSchema:
    """Monde où seul un schéma non-Bearer est accepté."""

    def get(self, url, params=None, headers=None, timeout=None):
        schema = (headers or {}).get("Authorization", "").split()[0]
        return Reponse(200, OK) if schema != "Bearer" else Reponse(403, REFUS)


class SessionFiltres:
    """Monde où la requête minimale passe, mais pas la requête filtrée."""

    def get(self, url, params=None, headers=None, timeout=None):
        return Reponse(200, OK) if len(params or {}) <= 2 \
            else Reponse(403, REFUS)


class SessionCompte:
    """Monde où tout est refusé, y compris sans filtre."""

    def __init__(self):
        self.appels = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.appels += 1
        return Reponse(403, REFUS)


class SessionEnTetes(SessionCompte):
    """Refus documenté par les en-têtes de réponse."""

    def get(self, url, params=None, headers=None, timeout=None):
        r = Reponse(403, REFUS)
        r.headers = {"WWW-Authenticate": 'Bearer scope="read:data"',
                     "X-Quota-Remaining": "0", "Content-Type": "text/plain"}
        return r


class TestDiscrimination(unittest.TestCase):
    """Chaque monde doit produire un verdict DIFFÉRENT : c'est tout l'objet."""

    def setUp(self):
        sa.RESULTATS[:] = []

    def test_schema_d_autorisation_incorrect(self):
        c = sa.diagnostiquer_403(SessionSchema(), "TOK", "MAC")
        self.assertTrue(any("schema d'autorisation" in x for x in c))
        self.assertTrue(any("MAC" in x for x in c))

    def test_filtres_refuses_mais_compte_valide(self):
        c = sa.diagnostiquer_403(SessionFiltres(), "TOK", "Bearer")
        self.assertTrue(any("MINIMALE passe" in x for x in c))
        self.assertTrue(any("filtres" in x for x in c))

    def test_compte_sans_droit_de_lecture(self):
        c = sa.diagnostiquer_403(SessionCompte(), "TOK", "Bearer")
        self.assertTrue(any("n'a PAS de droit de lecture" in x for x in c))

    def test_le_verdict_dirige_vers_la_BONNE_action(self):
        """Le point utile : savoir s'il faut toucher au code ou faire une
        démarche. Se tromper coûte une demi-journée."""
        compte = sa.diagnostiquer_403(SessionCompte(), "TOK", "Bearer")
        self.assertTrue(any("pas un probleme de code" in x for x in compte))
        self.assertTrue(any("acleddata.com" in x for x in compte))

        sa.RESULTATS[:] = []
        filtres = sa.diagnostiquer_403(SessionFiltres(), "TOK", "Bearer")
        self.assertFalse(any("pas un probleme de code" in x for x in filtres))

    def test_les_trois_verdicts_sont_distincts(self):
        verdicts = []
        for session, tt in ((SessionSchema(), "MAC"),
                            (SessionFiltres(), "Bearer"),
                            (SessionCompte(), "Bearer")):
            sa.RESULTATS[:] = []
            verdicts.append(tuple(sa.diagnostiquer_403(session, "TOK", tt)))
        self.assertEqual(len(set(verdicts)), 3)


class TestEconomieDesAppels(unittest.TestCase):
    """Un diagnostic ne doit pas marteler une API qui vient de refuser."""

    def setUp(self):
        sa.RESULTATS[:] = []

    def test_pas_plus_d_une_requete_minimale(self):
        s = SessionCompte()
        sa.diagnostiquer_403(s, "TOK", "Bearer")
        self.assertEqual(s.appels, 1)

    def test_bearer_annonce_ne_declenche_pas_d_essai_inutile(self):
        """Si le serveur annonce déjà « Bearer », le réessayer serait rejouer
        exactement la requête qui vient d'échouer."""
        s = SessionCompte()
        sa.diagnostiquer_403(s, "TOK", "Bearer")
        self.assertEqual(s.appels, 1)

    def test_arret_des_qu_une_hypothese_est_confirmee(self):
        """Une fois la cause trouvée, continuer n'apprend rien de plus."""
        c = sa.diagnostiquer_403(SessionSchema(), "TOK", "MAC")
        self.assertEqual(len(c), 1)


class TestEnTetesDeReponse(unittest.TestCase):
    """L'API dit souvent POURQUOI dans ses en-têtes, jamais lus jusqu'ici."""

    def setUp(self):
        sa.RESULTATS[:] = []

    def test_les_en_tetes_utiles_sont_affiches(self):
        import io
        import contextlib
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            sa.interroger(SessionEnTetes(), "TOK", limit=1)
        texte = sortie.getvalue()
        self.assertIn("en-tetes", texte)
        self.assertIn("WWW-Authenticate", texte)
        self.assertIn("X-Quota-Remaining", texte)

    def test_les_en_tetes_sans_interet_sont_ecartes(self):
        """Afficher tous les en-têtes noierait le seul qui compte."""
        import io
        import contextlib
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            sa.interroger(SessionEnTetes(), "TOK", limit=1)
        self.assertNotIn("Content-Type", sortie.getvalue())


class TestAucuneEcriture(unittest.TestCase):
    """La sonde reste une sonde : elle établit, elle ne modifie rien."""

    def test_aucun_appel_en_ecriture(self):
        """Vérifie les appels sur `session`/`requests` UNIQUEMENT.

        Un premier jet cherchait les noms de méthodes partout et échouait sur
        `dict.update` -- confondre une méthode Python avec une écriture
        distante rend le test bruyant et le fait désactiver."""
        import ast
        with open("sonde_acled.py", encoding="utf-8") as f:
            arbre = ast.parse(f.read())
        appels_http = set()
        for n in ast.walk(arbre):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id in ("session", "requests", "s")):
                appels_http.add(n.func.attr)
        for interdit in ("put", "patch", "delete"):
            self.assertNotIn(interdit, appels_http, interdit)
        # POST : uniquement pour OAuth, jamais sur l'endpoint de donnees.
        self.assertTrue(appels_http <= {"get", "post", "Session"},
                        "verbe HTTP inattendu : {}".format(appels_http))

    def test_aucune_ecriture_dans_le_radar(self):
        with open("sonde_acled.py", encoding="utf-8") as f:
            src = f.read()
        for interdit in ("ecrire_miroir", "definir_statut", "append_rows",
                         "enregistrer_entites"):
            self.assertNotIn(interdit + "(", src, interdit)

    def test_sortie_toujours_en_succes(self):
        """Une sonde qui fait échouer le run empêcherait de lire son propre
        rapport dans les journaux."""
        with open("sonde_acled.py", encoding="utf-8") as f:
            self.assertIn("sys.exit(0)", f.read())


class TestVoieCookie(unittest.TestCase):
    """Le 403 sur TOUTES les requêtes OAuth laisse deux explications, qui
    n'appellent pas la même action :

      a. le compte n'a pas de droit de lecture -> démarche côté ACLED ;
      b. le jeton OAuth n'ouvre pas ce droit   -> on change de voie.

    L'authentification par cookie utilise les MÊMES identifiants par une voie
    entièrement différente. Elle tranche donc entre les deux -- et, si elle
    passe, elle fournit la solution de rechange."""

    class Rep:
        def __init__(self, code, corps="", url="http://x"):
            self.status_code = code
            self.text = corps
            self.url = url
            self.headers = {}

        def json(self):
            import json
            return json.loads(self.text)

    class Session:
        def __init__(self, monde):
            self.monde = monde

        def post(self, url, json=None, timeout=None):
            if self.monde == "login_refuse":
                return TestVoieCookie.Rep(403, REFUS)
            return TestVoieCookie.Rep(
                200, '{"current_user":{"uid":"42","name":"x"}}')

        def get(self, url, params=None, timeout=None, headers=None):
            return (TestVoieCookie.Rep(200, OK) if self.monde == "cookie_ok"
                    else TestVoieCookie.Rep(403, REFUS))

    def setUp(self):
        sa.RESULTATS[:] = []

    def test_cookie_accepte_designe_la_voie_oauth(self):
        """Si les mêmes identifiants passent par cookie, le compte a bien le
        droit de lire : c'est OAuth qui est refusé."""
        self.assertIs(sa.sonde_cookie(self.Session("cookie_ok")), True)

    def test_cookie_refuse_confirme_que_c_est_le_compte(self):
        """Deux voies indépendantes refusées : le mode d'authentification est
        hors de cause, aucun code n'y changera rien."""
        self.assertIs(sa.sonde_cookie(self.Session("cookie_ko")), False)

    def test_login_refuse_designe_les_identifiants(self):
        """Troisième cas, distinct des deux autres : ce ne sont plus les
        droits, ce sont les identifiants eux-mêmes."""
        self.assertIsNone(sa.sonde_cookie(self.Session("login_refuse")))

    def test_les_trois_retours_sont_distincts(self):
        retours = []
        for monde in ("cookie_ok", "cookie_ko", "login_refuse"):
            sa.RESULTATS[:] = []
            retours.append(sa.sonde_cookie(self.Session(monde)))
        self.assertEqual(len(set(map(repr, retours))), 3)

    def test_une_session_neuve_est_utilisee(self):
        """La session OAuth porte un en-tête Authorization : la réutiliser
        rendrait la voie cookie non indépendante, donc sans valeur de preuve."""
        with open("sonde_acled.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("sonde_cookie(requests.Session())", src)

    def test_reseau_coupe_ne_leve_pas(self):
        class Morte:
            def post(self, *a, **k):
                raise OSError("reseau coupe")
        self.assertIsNone(sa.sonde_cookie(Morte()))

    def test_la_voie_cookie_reste_en_lecture_seule(self):
        """Le POST sert au login, jamais à écrire des données."""
        import ast
        with open("sonde_acled.py", encoding="utf-8") as f:
            arbre = ast.parse(f.read())
        bloc = [n for n in ast.walk(arbre)
                if isinstance(n, ast.FunctionDef) and n.name == "sonde_cookie"]
        self.assertEqual(len(bloc), 1)
        verbes = {n.func.attr for n in ast.walk(bloc[0])
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)}
        self.assertFalse(verbes & {"put", "patch", "delete"})


if __name__ == "__main__":
    unittest.main()
