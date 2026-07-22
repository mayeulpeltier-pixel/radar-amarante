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

    def test_statut_sans_identifiant_refuse(self):
        r = _client().post("/api/statut", auth=self.AUTH, json={
            "onglet": "ted_radar", "publication_number": "  ", "statut": "x"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
