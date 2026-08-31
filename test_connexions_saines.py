# -*- coding: utf-8 -*-
"""Santé des connexions Postgres et réveil du service (26/08/2026).

L'INCIDENT
----------
Journaux Render :

    psycopg.errors.IdleInTransactionSessionTimeout:
        terminating connection due to idle-in-transaction timeout
    INFO: "HEAD /?frais=1 HTTP/1.1" 405 Method Not Allowed

DÉFAUT 1 -- UNE ERREUR AVALÉE N'EST PAS UNE ERREUR TRAITÉE
-----------------------------------------------------------
En Postgres, une requête qui échoue ABANDONNE la transaction : toute requête
suivante sur la même connexion échoue, et le COMMIT de sortie aussi. La
connexion reste ouverte, en transaction, jusqu'à ce que le serveur la tue.

Les lectures best-effort ajoutées les 25 et 26/08 (`lire_outcomes`,
`compter_issues`, `lire_entreprises`) attrapaient l'exception et renvoyaient
une valeur vide -- sans jamais annuler la transaction.

Or elles interrogent des tables CRÉÉES PAR LA MIGRATION. Tant qu'aucun
collecteur n'avait tourné (le run était rouge sur les tests), `radar_outcomes`,
`radar_entreprises` et `radar_opportunites` n'existaient pas en production :
chaque rendu de page empoisonnait donc une connexion.

Deux corrections, volontairement indépendantes :
  - `_annuler(conn)` rend la connexion utilisable après un échec ;
  - `sante_detaillee` appelle `initialiser` AVANT de lire, pour que les tables
    existent au lieu de compter sur le rattrapage.

DÉFAUT 2 -- LE RÉVEIL RENVOYAIT 405
------------------------------------
FastAPI n'expose pas HEAD sur une route déclarée en GET. Le ping de réveil
échouait à chaque appel, et un pinger qui reçoit 405 ne maintient rien
éveillé.

Tests OFFLINE : connexion factice, aucun réseau.
"""

import os
import unittest

import radar_stockage as st

try:
    from fastapi.testclient import TestClient
    import radar_app
    PRET = True
except Exception:                       # fastapi absent en local
    PRET = False


class ConnexionQuiEchoue:
    """Connexion factice qui reproduit le comportement de Postgres : une
    requête ratée abandonne la transaction, et tout ce qui suit échoue."""

    def __init__(self, tables_absentes=("radar_outcomes",)):
        self.absentes = tables_absentes
        self.abandonnee = False
        self.annulations = 0

    def cursor(self):
        return _Curseur(self)

    def rollback(self):
        self.annulations += 1
        self.abandonnee = False

    def commit(self):
        if self.abandonnee:
            raise RuntimeError("commit sur transaction abandonnée")


class _Curseur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, sql, params=None):
        if self.conn.abandonnee:
            raise RuntimeError("current transaction is aborted")
        for table in self.conn.absentes:
            if table in sql:
                self.conn.abandonnee = True
                raise RuntimeError('relation "%s" does not exist' % table)

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class TestTransactionRendueUtilisable(unittest.TestCase):
    """LE test de l'incident : après un échec, la connexion doit repartir."""

    def test_compter_issues_annule_avant_de_renoncer(self):
        conn = ConnexionQuiEchoue(("radar_outcomes",))
        self.assertEqual(st.compter_issues(conn), {"gagne": 0, "perdu": 0})
        self.assertFalse(conn.abandonnee, "transaction laissée abandonnée")
        self.assertEqual(conn.annulations, 1)

    def test_lire_outcomes_annule_avant_de_renoncer(self):
        conn = ConnexionQuiEchoue(("radar_outcomes",))
        self.assertEqual(st.lire_outcomes(conn), [])
        self.assertFalse(conn.abandonnee)

    def test_lire_entreprises_annule_avant_de_renoncer(self):
        conn = ConnexionQuiEchoue(("radar_entreprises",))
        self.assertEqual(st.lire_entreprises(conn), {})
        self.assertFalse(conn.abandonnee)

    def test_le_commit_de_sortie_ne_leve_plus(self):
        """C'est ce commit raté qui laissait la connexion ouverte jusqu'au
        timeout du serveur."""
        conn = ConnexionQuiEchoue(("radar_outcomes",))
        st.compter_issues(conn)
        conn.commit()                   # ne doit pas lever

    def test_une_lecture_reste_possible_apres_l_echec(self):
        """Sans rollback, la requête suivante échouait aussi : l'erreur se
        propageait à tout le bloc."""
        conn = ConnexionQuiEchoue(("radar_outcomes",))
        st.compter_issues(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")     # ne doit pas lever

    def test_annuler_ne_leve_jamais(self):
        """Fonction défensive : elle est appelée dans un gestionnaire
        d'erreur, elle ne doit pas en produire une seconde."""
        class Muette:
            def rollback(self):
                raise RuntimeError("connexion déjà fermée")
        st._annuler(Muette())
        st._annuler(None)

    def test_l_echec_est_journalise(self):
        """Un best-effort muet a déjà masqué deux défauts aujourd'hui."""
        with open("radar_stockage.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("compter_issues indisponible", src)


class TestTablesCreeesAvantLecture(unittest.TestCase):
    """Corriger le symptôme ne suffit pas : les tables doivent exister."""

    def test_sante_detaillee_initialise_avant_de_lire(self):
        with open("radar_cockpit.py", encoding="utf-8") as f:
            src = f.read()
        bloc = src.split("def sante_detaillee")[1].split("\ndef ")[0]
        i_init = bloc.index("st.initialiser(conn)")
        i_lire = bloc.index("st.inventaire(conn)")
        self.assertLess(i_init, i_lire)

    def test_les_nouvelles_tables_sont_dans_la_migration(self):
        for table in ("radar_outcomes", "radar_entreprises", "radar_projets",
                      "radar_opportunites"):
            self.assertIn("CREATE TABLE IF NOT EXISTS " + table,
                          st.SCHEMA_SQL, table)


@unittest.skipUnless(PRET, "fastapi absent")
class TestReveilDuService(unittest.TestCase):

    def setUp(self):
        os.environ["RADAR_APP_MOT_DE_PASSE"] = "x"
        self.client = TestClient(radar_app.app)

    def test_head_racine_repond_200(self):
        """405 en boucle dans les journaux : le ping de réveil échouait."""
        self.assertEqual(self.client.head("/").status_code, 200)

    def test_head_avec_parametre_repond_aussi(self):
        self.assertEqual(self.client.head("/?frais=1").status_code, 200)

    def test_le_reveil_ne_genere_pas_la_page(self):
        """Réveiller le service ne doit pas coûter la génération d'une page de
        plusieurs mégaoctets : on le réveillerait pour le saturer aussitôt."""
        r = self.client.head("/")
        self.assertEqual(r.content, b"")

    def test_le_reveil_ne_demande_pas_d_authentification(self):
        """Un pinger n'a pas de mot de passe. Il ne lit aucune donnée non
        plus : c'est ce qui rend l'exception acceptable."""
        r = self.client.head("/")
        self.assertNotEqual(r.status_code, 401)

    def test_la_page_reste_protegee(self):
        self.assertIn(self.client.get("/").status_code, (401, 503))

    def test_sante_repond_toujours(self):
        self.assertEqual(self.client.get("/sante").status_code, 200)


if __name__ == "__main__":
    unittest.main()
