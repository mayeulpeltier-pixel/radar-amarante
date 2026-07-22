# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DE L'APPLICATION WEB.
==============================================

Deux etages, comme test_stockage :
  1. SANS BASE : verrouillage par defaut (503 sans mot de passe), 401 sans
     identifiants, /sante toujours accessible. Executes des que fastapi et
     httpx sont installes (le workflow radar.yml les installe).
  2. AVEC BASE (RADAR_TEST_DATABASE_URL) : la page se genere depuis Postgres
     avec le moteur du dashboard, la superposition des statuts fonctionne, et
     l'API /api/statut ecrit un upsert reel.
"""

import os
import unittest

try:
    from fastapi.testclient import TestClient
    import radar_app
    import radar_stockage as st
    PRET = True
except Exception:                       # fastapi/httpx absents en local
    PRET = False

URL_TEST = os.environ.get("RADAR_TEST_DATABASE_URL", "")


def _client():
    return TestClient(radar_app.app)


try:
    import radar_dashboard as _dash
    DASH = True
except Exception:
    DASH = False


@unittest.skipUnless(DASH, "radar_dashboard indisponible")
class TestBoutonJeContacte(unittest.TestCase):
    """Le bouton « Je contacte » selon le contexte de service. Purs : aucune
    base, aucun reseau, on inspecte le HTML produit."""

    def _page(self, api_statut):
        return _dash.generer_html([], [], api_statut=api_statut)

    def test_page_statique_inchangee(self):
        """Cloudflare : le gabarit JS est COMMUN aux deux pages, l'appel y
        figure donc toujours. Ce qui compte est la GARDE : drapeau a false,
        donc le fetch ne part jamais (l'endpoint n'existe pas en statique)."""
        page = self._page(False)
        self.assertIn("const API_STATUT = false;", page)
        self.assertIn("if(API_STATUT&&l.pub&&ONGLET_SRC[l.src])", page)
        # L'ecriture Apps Script reste, elle, conditionnee a son URL.
        self.assertIn("if(SUIVI_URL){fetch(SUIVI_URL", page)

    def test_page_application_cablee_sur_l_api(self):
        page = self._page(True)
        self.assertIn("const API_STATUT = true;", page)
        self.assertIn("fetch('/api/statut'", page)
        self.assertIn("statut:'contacte'", page)

    def test_bouton_visible_sans_apps_script(self):
        """Piege evite : SUIVI_ON ne dependait que d'Apps Script. Sur Render,
        sans ce secret, le bouton aurait purement disparu."""
        self.assertIn("const SUIVI_ON = !!SUIVI_URL || API_STATUT;",
                      self._page(True))

    def test_toutes_les_sources_ont_un_onglet(self):
        """Chaque source affichable doit savoir dans quel onglet ecrire son
        statut, sinon le bouton serait muet pour elle."""
        page = self._page(True)
        for src in ("TED", "BM", "AFDB", "EBRD", "UNGM", "RW", "ATTRIB"):
            self.assertIn(src + ":'", page.split("ONGLET_SRC = {")[1][:400],
                          "source {} sans onglet".format(src))


@unittest.skipUnless(PRET, "fastapi/httpx indisponibles")
class TestVerrouillage(unittest.TestCase):
    """La securite d'abord : rien ne sort sans configuration ni identifiants."""

    def setUp(self):
        self._mdp = os.environ.pop("RADAR_APP_MOT_DE_PASSE", None)

    def tearDown(self):
        if self._mdp is not None:
            os.environ["RADAR_APP_MOT_DE_PASSE"] = self._mdp

    def test_sans_mot_de_passe_tout_est_verrouille(self):
        r = _client().get("/", auth=("radar", "nimportequoi"))
        self.assertEqual(r.status_code, 503)
        self.assertIn("verrouillee", r.json()["detail"])

    def test_sante_repond_sans_authentification_ni_donnees(self):
        r = _client().get("/sante")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["mot_de_passe_configure"])

    def test_mauvais_identifiants_401(self):
        os.environ["RADAR_APP_MOT_DE_PASSE"] = "bon-mdp"
        try:
            r = _client().get("/", auth=("radar", "mauvais"))
            self.assertEqual(r.status_code, 401)
            r2 = _client().get("/")     # aucun identifiant
            self.assertEqual(r2.status_code, 401)
        finally:
            os.environ.pop("RADAR_APP_MOT_DE_PASSE", None)


@unittest.skipUnless(PRET and URL_TEST,
                     "pas de base de test (RADAR_TEST_DATABASE_URL absent)")
class TestApplicationIntegration(unittest.TestCase):
    """Contre un vrai Postgres : page complete et statuts."""

    AUTH = ("radar", "mdp-de-test")

    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = URL_TEST
        os.environ["RADAR_APP_MOT_DE_PASSE"] = "mdp-de-test"
        cls.conn = st.connexion(URL_TEST)
        st.initialiser(cls.conn)
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        os.environ.pop("RADAR_APP_MOT_DE_PASSE", None)

    def setUp(self):
        radar_app.invalider_cache()
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE radar_lignes")
            cur.execute("TRUNCATE radar_statuts")
        self.conn.commit()
        # Un avis TED plat minimal mais realiste (forme canonique).
        st.ajouter_lignes(self.conn, "ted_radar", [{
            "date_maj": "2026-07-21", "score_final": 82, "score_surete": 44,
            "score_commercial": 38, "action_recommandee": "contacter",
            "fenetre_action": "sous 15 jours",
            "titre": "Escorte de convois logistiques Sahel",
            "acheteur": "Delegation UE", "pays_execution": "MLI",
            "publication_number": "TED-APP-1",
            "lien_avis": "https://exemple.invalid/ted1",
            "date_publication": "2026-07-18",
        }])
        self.conn.commit()

    def test_page_generee_depuis_postgres(self):
        r = _client().get("/", auth=self.AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("Escorte de convois logistiques Sahel", r.text)
        self.assertIn("Radar Amarante", r.text)

    def test_statut_upsert_et_superposition(self):
        c = _client()
        r1 = c.post("/api/statut", auth=self.AUTH, json={
            "onglet": "ted_radar", "publication_number": "TED-APP-1",
            "statut": "contacte"})
        self.assertEqual(r1.status_code, 200)
        # Upsert assume : le second POST REMPLACE (zone humaine).
        c.post("/api/statut", auth=self.AUTH, json={
            "onglet": "ted_radar", "publication_number": "TED-APP-1",
            "statut": "gagne"})
        self.assertEqual(
            st.lire_statuts(self.conn)[("ted_radar", "TED-APP-1")], "gagne")
        # Et la page reflete la zone humaine posee en base.
        page = c.get("/", auth=self.AUTH).text
        self.assertIn("gagne", page)

    def test_aller_retour_bouton_contacte(self):
        """Le scenario reel : je clique « Je contacte » (POST), je recharge
        depuis un AUTRE navigateur (aucun localStorage) et le lead doit
        apparaitre comme deja pris en charge."""
        c = _client()
        c.post("/api/statut", auth=self.AUTH, json={
            "onglet": "ted_radar", "publication_number": "TED-APP-1",
            "statut": "contacte"})
        page = c.get("/", auth=self.AUTH).text
        # Le statut serveur voyage dans les donnees de la page (et non plus
        # seulement dans le localStorage du poste qui a clique).
        self.assertIn('"statut": "contacte"', page.replace("'", '"'))

    # -- Performance (mesures du 22/07/2026 : 2,6 Mo -> 96 Ko) -------------
    def test_page_compressee(self):
        """uvicorn ne compresse rien par defaut : sans ce middleware, chaque
        chargement transferait 2,6 Mo."""
        r = _client().get("/", auth=self.AUTH, headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.headers.get("content-encoding"), "gzip")

    def test_cache_evite_de_regenerer(self):
        """Les donnees ne changent que 2 fois par semaine : la seconde demande
        ne doit pas reconstruire la page."""
        appels = []
        original = radar_app.generer_page
        radar_app.generer_page = lambda conn: appels.append(1) or original(conn)
        try:
            c = _client()
            c.get("/", auth=self.AUTH)
            c.get("/", auth=self.AUTH)
            c.get("/", auth=self.AUTH)
            self.assertEqual(len(appels), 1, "la page a ete regeneree inutilement")
        finally:
            radar_app.generer_page = original

    def test_frais_force_la_regeneration(self):
        appels = []
        original = radar_app.generer_page
        radar_app.generer_page = lambda conn: appels.append(1) or original(conn)
        try:
            c = _client()
            c.get("/", auth=self.AUTH)
            c.get("/?frais=1", auth=self.AUTH)
            self.assertEqual(len(appels), 2)
        finally:
            radar_app.generer_page = original

    def test_poser_un_statut_invalide_le_cache(self):
        """Sinon l'utilisateur cliquerait « Je contacte » sans voir sa propre
        action pendant dix minutes."""
        c = _client()
        c.get("/", auth=self.AUTH)
        c.post("/api/statut", auth=self.AUTH, json={
            "onglet": "ted_radar", "publication_number": "TED-APP-1",
            "statut": "gagne"})
        page = c.get("/", auth=self.AUTH).text
        self.assertIn("gagne", page)

    def test_statut_sans_identifiant_refuse(self):
        r = _client().post("/api/statut", auth=self.AUTH, json={
            "onglet": "ted_radar", "publication_number": "  ", "statut": "x"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
