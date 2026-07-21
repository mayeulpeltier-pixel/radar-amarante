# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DE LA COUCHE DE STOCKAGE POSTGRES.
===========================================================

Deux etages, pour que la CI reste verte partout :
  1. Tests PURS : toujours executes, aucune base requise (interrupteurs,
     preparation des lignes, contrat "ne leve jamais" du miroir).
  2. Tests d'INTEGRATION : executes contre un VRAI Postgres si la variable
     RADAR_TEST_DATABASE_URL est definie (cas du poste de developpement) ;
     sinon sautes proprement. La CI GitHub n'a pas de base : elle valide
     l'etage 1 et saute l'etage 2, c'est voulu.

Le point le plus important verrouille ici : ON CONFLICT DO NOTHING. Une
ligne existante n'est JAMAIS reecrite -- transposition en base de la garde
Sheet qui protege `statut_prospection` (test_bm_ecriture.py).
"""

import json
import os
import unittest
from datetime import date

import radar_stockage as st

URL_TEST = os.environ.get("RADAR_TEST_DATABASE_URL", "")

try:
    import psycopg  # noqa: F401
    PSYCOPG = True
except Exception:
    PSYCOPG = False


# ===========================================================================
# ETAGE 1 : PURS (toujours executes)
# ===========================================================================

class TestInterrupteurs(unittest.TestCase):

    def test_inactif_sans_database_url(self):
        avant = os.environ.pop("DATABASE_URL", None)
        try:
            self.assertFalse(st.actif())
        finally:
            if avant is not None:
                os.environ["DATABASE_URL"] = avant

    def test_miroir_inactif_ne_leve_jamais(self):
        """Contrat central : sans configuration, le miroir repond par une
        phrase de journal, pas par une exception."""
        avant = os.environ.pop("DATABASE_URL", None)
        try:
            msg = st.ecrire_miroir("attributions_radar", [{"gagnant": "X"}])
            self.assertIn("inactif", msg)
        finally:
            if avant is not None:
                os.environ["DATABASE_URL"] = avant

    @unittest.skipUnless(PSYCOPG, "psycopg indisponible")
    def test_miroir_base_en_panne_ne_leve_jamais(self):
        """Postgres injoignable : le collecteur ne doit RIEN sentir d'autre
        qu'une ligne de journal."""
        avant = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://x:x@127.0.0.1:59999/nulle"
        try:
            msg = st.ecrire_miroir("attributions_radar", [{"gagnant": "X"}])
            self.assertIn("indisponible", msg)
            self.assertIn("run non affecte", msg)
        finally:
            if avant is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = avant


class TestPreparationLignes(unittest.TestCase):

    def test_cles_techniques_ecartees(self):
        """_etranger, _origine... sont des champs de travail des collecteurs,
        pas des donnees a persister."""
        pub, donnees = st.preparer_ligne(
            {"gagnant": "STECOL", "publication_number": "BM-1",
             "_etranger": True, "_pays_titulaire": "China"})
        self.assertEqual(pub, "BM-1")
        self.assertNotIn("_etranger", donnees)
        self.assertNotIn("_pays_titulaire", donnees)
        self.assertEqual(donnees["gagnant"], "STECOL")

    def test_dates_serialisees_en_iso(self):
        _pub, donnees = st.preparer_ligne({"date_maj": date(2026, 7, 21)})
        self.assertEqual(donnees["date_maj"], "2026-07-21")
        json.dumps(donnees)                     # ne doit pas lever

    def test_publication_absente_donne_chaine_vide(self):
        pub, _d = st.preparer_ligne({"gagnant": "X"})
        self.assertEqual(pub, "")

    def test_publication_imbriquee_des_avis(self):
        """Les six collecteurs d'avis passent des resultats imbriques : le
        publication_number vit sous r['avis']. Sans cette extraction, chaque
        run dupliquerait tous les avis en base (pub='')."""
        pub, donnees = st.preparer_ligne(
            {"avis": {"publication_number": "TED-123", "titre": "Escorte"},
             "scores": {"pertinence": 4}})
        self.assertEqual(pub, "TED-123")
        self.assertEqual(donnees["avis"]["titre"], "Escorte")

    def test_publication_racine_prime_sur_l_imbriquee(self):
        pub, _d = st.preparer_ligne(
            {"publication_number": "RACINE",
             "avis": {"publication_number": "IMBRIQUEE"}})
        self.assertEqual(pub, "RACINE")

    def test_accents_conserves(self):
        """ensure_ascii=False cote ecriture : la ligne doit rester lisible
        en francais (Bozankaya Raylı, Müş...)."""
        _p, donnees = st.preparer_ligne({"gagnant": "Prokon Müh. ve Müş."})
        self.assertIn("Müş", json.dumps(donnees, ensure_ascii=False))


# ===========================================================================
# ETAGE 2 : INTEGRATION (sautes sans RADAR_TEST_DATABASE_URL)
# ===========================================================================

@unittest.skipUnless(PSYCOPG and URL_TEST,
                     "pas de base de test (RADAR_TEST_DATABASE_URL absent)")
class TestIntegrationPostgres(unittest.TestCase):
    """Contre un VRAI Postgres. Chaque test repart d'une table vide."""

    @classmethod
    def setUpClass(cls):
        cls.conn = st.connexion(URL_TEST)
        st.initialiser(cls.conn)
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE radar_lignes")
        self.conn.commit()

    def _ligne(self, pub="ISDB-route-kg", gagnant="Yema Group Co., Ltd"):
        return {"date_maj": "2026-07-21", "gagnant": gagnant,
                "pays_execution": "KGZ", "publication_number": pub,
                "_etranger": True}

    def test_schema_idempotent(self):
        """Rejouer initialiser() sur une base deja equipee ne casse rien et
        ne detruit rien."""
        st.ajouter_lignes(self.conn, "attributions_radar", [self._ligne()])
        st.initialiser(self.conn)
        self.assertEqual(len(st.lire_onglet(self.conn, "attributions_radar")), 1)

    def test_ajout_puis_relecture_fidele(self):
        st.ajouter_lignes(self.conn, "attributions_radar", [self._ligne()])
        lignes = st.lire_onglet(self.conn, "attributions_radar")
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["gagnant"], "Yema Group Co., Ltd")
        self.assertNotIn("_etranger", lignes[0])   # cle technique non persistee

    def test_ligne_existante_jamais_reecrite(self):
        """LA garde centrale, comme au Sheet : rejouer la meme publication ne
        touche pas la ligne d'origine, meme si le contenu a change."""
        st.ajouter_lignes(self.conn, "attributions_radar",
                          [self._ligne(gagnant="ORIGINAL")])
        aj, ig = st.ajouter_lignes(self.conn, "attributions_radar",
                                   [self._ligne(gagnant="ECRASEUR")])
        self.assertEqual((aj, ig), (0, 1))
        lignes = st.lire_onglet(self.conn, "attributions_radar")
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["gagnant"], "ORIGINAL")

    def test_meme_publication_dans_deux_onglets_coexiste(self):
        """L'unicite est PAR ONGLET : 'BM-1' peut exister dans les avis et
        dans les attributions sans collision."""
        st.ajouter_lignes(self.conn, "avis", [self._ligne(pub="BM-1")])
        aj, _ = st.ajouter_lignes(self.conn, "attributions_radar",
                                  [self._ligne(pub="BM-1")])
        self.assertEqual(aj, 1)

    def test_sans_identifiant_on_insere_toujours(self):
        """Meme prudence inversee que le Sheet : un identifiant vide ne
        bloque jamais l'insertion (doublon potentiel plutot que lead perdu)."""
        aj, ig = st.ajouter_lignes(self.conn, "attributions_radar",
                                   [self._ligne(pub=""), self._ligne(pub="")])
        self.assertEqual((aj, ig), (2, 0))

    def test_publications_existantes(self):
        st.ajouter_lignes(self.conn, "attributions_radar",
                          [self._ligne(pub="A"), self._ligne(pub="B"),
                           self._ligne(pub="")])
        self.assertEqual(
            st.publications_existantes(self.conn, "attributions_radar"),
            {"A", "B"})

    def test_inventaire(self):
        st.ajouter_lignes(self.conn, "avis", [self._ligne(pub="X")])
        st.ajouter_lignes(self.conn, "attributions_radar",
                          [self._ligne(pub="Y"), self._ligne(pub="Z")])
        self.assertEqual(st.inventaire(self.conn),
                         {"attributions_radar": 2, "avis": 1})

    def test_ecrire_miroir_de_bout_en_bout(self):
        """Le point d'entree exact des collecteurs, avec DATABASE_URL."""
        avant = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = URL_TEST
        try:
            msg = st.ecrire_miroir("attributions_radar",
                                   [self._ligne(pub="MIROIR-1")])
            self.assertIn("1 ajoutee(s)", msg)
            msg2 = st.ecrire_miroir("attributions_radar",
                                    [self._ligne(pub="MIROIR-1")])
            self.assertIn("1 deja connue(s)", msg2)
        finally:
            if avant is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = avant
        # ecrire_miroir ouvre sa propre connexion : rendre visible ici.
        self.assertIn("MIROIR-1",
                      st.publications_existantes(self.conn, "attributions_radar"))


# ===========================================================================
# RATTRAPAGE HISTORIQUE (radar_rattrapage.py)
# ===========================================================================

class TestRattrapagePur(unittest.TestCase):
    """Cle de contenu et preparation : purs, toujours executes."""

    def setUp(self):
        import radar_rattrapage
        self.rt = radar_rattrapage

    def test_cle_stable(self):
        a = {"entreprise": "Egis", "signal": "recrutement", "pays": "MLI"}
        self.assertEqual(self.rt.cle_contenu(a), self.rt.cle_contenu(dict(a)))
        self.assertEqual(len(self.rt.cle_contenu(a)), 40)     # sha1 hex

    def test_statut_humain_ne_change_pas_la_cle(self):
        """Changer un statut a la main dans le Sheet ne doit pas fabriquer de
        doublon au rejeu suivant."""
        a = {"entreprise": "Egis", "statut_suivi": "nouveau"}
        b = {"entreprise": "Egis", "statut_suivi": "contacte le 20/07"}
        c = {"entreprise": "Egis", "statut_prospection": "perdu"}
        self.assertEqual(self.rt.cle_contenu(a), self.rt.cle_contenu(b))
        self.assertEqual(self.rt.cle_contenu(a), self.rt.cle_contenu(c))

    def test_contenu_different_cle_differente(self):
        self.assertNotEqual(
            self.rt.cle_contenu({"entreprise": "Egis", "pays": "MLI"}),
            self.rt.cle_contenu({"entreprise": "Egis", "pays": "NER"}))

    def test_preparation_forge_une_cle_seulement_si_besoin(self):
        avec = self.rt.preparer_rattrapage({"publication_number": "TED-1",
                                            "titre": "x"})
        self.assertEqual(avec["publication_number"], "TED-1")
        sans = self.rt.preparer_rattrapage({"entreprise": "Egis"})
        self.assertTrue(sans["publication_number"].startswith("SHA1-"))


class _FauxOnglet:
    def __init__(self, titre, lignes, erreur=None):
        self.title = titre
        self._lignes = lignes
        self._erreur = erreur

    def get_all_records(self):
        if self._erreur:
            raise self._erreur
        return list(self._lignes)


class _FauxClasseur:
    def __init__(self, onglets):
        self._onglets = onglets

    def worksheets(self):
        return list(self._onglets)


@unittest.skipUnless(PSYCOPG and URL_TEST,
                     "pas de base de test (RADAR_TEST_DATABASE_URL absent)")
class TestRattrapageIntegration(unittest.TestCase):
    """Le rattrapage complet contre un vrai Postgres, classeur factice."""

    def setUp(self):
        import radar_rattrapage
        self.rt = radar_rattrapage
        self.conn = st.connexion(URL_TEST)
        st.initialiser(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE radar_lignes")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _classeur(self, statut="nouveau"):
        return _FauxClasseur([
            _FauxOnglet("ted_radar",
                        [{"publication_number": "TED-1", "titre": "Escorte",
                          "statut_suivi": statut},
                         {"publication_number": "", "titre": ""}]),   # vide
            _FauxOnglet("prive_radar",
                        [{"entreprise": "Egis", "signal": "recrutement",
                          "statut_suivi": statut}]),
            _FauxOnglet("casse", [], erreur=RuntimeError("quota")),
        ])

    def test_rattrapage_puis_rejeu_idempotent(self):
        bilan1 = self.rt.rattraper_classeur(self._classeur(), self.conn)
        self.assertEqual(bilan1["ted_radar"], (1, 1, 0, ""))      # vide ecartee
        self.assertEqual(bilan1["prive_radar"], (1, 1, 0, ""))
        self.assertIn("illisible", bilan1["casse"][3])            # best-effort
        # Rejeu avec un statut humain MODIFIE : zero doublon nulle part.
        bilan2 = self.rt.rattraper_classeur(self._classeur("contacte"),
                                            self.conn)
        self.assertEqual(bilan2["ted_radar"], (1, 0, 1, ""))
        self.assertEqual(bilan2["prive_radar"], (1, 0, 1, ""))
        self.assertEqual(st.inventaire(self.conn),
                         {"prive_radar": 1, "ted_radar": 1})

    def test_onglet_exclu(self):
        bilan = self.rt.rattraper_classeur(self._classeur(), self.conn,
                                           exclus=("prive_radar",))
        self.assertEqual(bilan["prive_radar"], (0, 0, 0, "exclu"))
        self.assertNotIn("prive_radar", st.inventaire(self.conn))


if __name__ == "__main__":
    unittest.main(verbosity=2)
