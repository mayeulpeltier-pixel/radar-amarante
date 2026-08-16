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

Le point le plus important verrouille ici : ON CONFLICT DO UPDATE. Une ligne
existante est RAFRAICHIE, jamais dupliquee, et `date_detection` ne bouge pas.

  Correction du 23/07/2026. C'etait `DO NOTHING`, presente comme la
  transposition en base de la garde Sheet qui protege `statut_prospection`.
  La transposition etait fausse : cette zone humaine n'est pas dans
  `radar_lignes`, elle est dans `radar_statuts`. La garde ne protegeait donc
  rien, mais elle bloquait les scores raffines : l'application Render
  affichait 5.0 / "surveiller" quand le dashboard Cloudflare affichait
  8.5 / "contacter", pour le meme avis.
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

    def test_ligne_existante_rafraichie_sans_doublon(self):
        """LA garde centrale, version 23/07/2026 : rejouer la meme publication
        MET A JOUR la ligne, sans jamais en creer une seconde.

        Le Sheet fait exactement cela quand un avis est reanalyse (escalade
        Sonnet). Postgres le faisait pas : l'application Render restait sur le
        score Haiku pendant que le dashboard Cloudflare affichait le score
        raffine. Un miroir qui ne reflete pas n'est pas un miroir."""
        st.ajouter_lignes(self.conn, "attributions_radar",
                          [self._ligne(gagnant="AVANT RAFFINEMENT")])
        aj, maj = st.ajouter_lignes(self.conn, "attributions_radar",
                                    [self._ligne(gagnant="APRES RAFFINEMENT")])
        self.assertEqual((aj, maj), (0, 1))
        lignes = st.lire_onglet(self.conn, "attributions_radar")
        self.assertEqual(len(lignes), 1, "une mise a jour ne doit pas dupliquer")
        self.assertEqual(lignes[0]["gagnant"], "APRES RAFFINEMENT")

    def test_date_de_premiere_detection_jamais_reecrite(self):
        """`date_detection` repond a "depuis quand connait-on ce lead ?".
        Une mise a jour de score ne doit pas la faire glisser, sinon la
        question n'a plus de reponse."""
        st.ajouter_lignes(self.conn, "attributions_radar", [self._ligne()])
        with self.conn.cursor() as cur:
            cur.execute("UPDATE radar_lignes SET date_detection = %s",
                        (date(2026, 1, 15),))
        st.ajouter_lignes(self.conn, "attributions_radar",
                          [self._ligne(gagnant="AUTRE")])
        with self.conn.cursor() as cur:
            cur.execute("SELECT date_detection FROM radar_lignes")
            self.assertEqual(cur.fetchone()[0], date(2026, 1, 15))

    def test_maj_rafraichie_pour_expirer_le_cache(self):
        """`radar_app.version_donnees` s'appuie sur max(maj) pour savoir si la
        page doit etre reconstruite. Avec DO NOTHING, un run qui ne faisait
        QUE raffiner des scores ne bougeait pas `maj` : l'application servait
        une page perimee sans aucun moyen de le savoir."""
        st.ajouter_lignes(self.conn, "attributions_radar", [self._ligne()])
        with self.conn.cursor() as cur:
            cur.execute("SELECT maj FROM radar_lignes")
            avant = cur.fetchone()[0]
        # COMMIT VOLONTAIRE, et ce n'est pas un detail de plomberie : `now()`
        # renvoie l'heure de la TRANSACTION, pas de l'instruction. Deux
        # ecritures dans une meme transaction portent donc le meme `maj`. En
        # production le cas ne se pose pas -- `ecrire_miroir` ouvre sa propre
        # connexion a chaque appel, donc une transaction par run -- mais si un
        # jour quelqu'un regroupe plusieurs runs dans une seule transaction,
        # `maj` cessera de bouger et le cache de l'application se figera.
        # Le commit ci-dessous reproduit fidelement la production.
        self.conn.commit()
        st.ajouter_lignes(self.conn, "attributions_radar",
                          [self._ligne(gagnant="AUTRE")])
        with self.conn.cursor() as cur:
            cur.execute("SELECT maj FROM radar_lignes")
            self.assertGreater(cur.fetchone()[0], avant)

    def test_les_statuts_humains_survivent_a_une_mise_a_jour(self):
        """La raison pour laquelle le rafraichissement est SANS DANGER : la
        zone de saisie humaine vit dans `radar_statuts`, table separee. C'est
        cette separation qui rendait la garde `DO NOTHING` inutile."""
        st.ajouter_lignes(self.conn, "attributions_radar", [self._ligne()])
        st.definir_statut(self.conn, "attributions_radar",
                          "ISDB-route-kg", "contacte")
        st.ajouter_lignes(self.conn, "attributions_radar",
                          [self._ligne(gagnant="APRES RAFFINEMENT")])
        self.assertEqual(
            st.lire_statuts(self.conn)[("attributions_radar", "ISDB-route-kg")],
            "contacte")

    def test_motif_ecartement_persiste(self):
        """« Pas pertinent » stocke la RAISON dans radar_statuts.motif et la
        relit via lire_motifs (apprentissage). Un statut sans motif reste vide."""
        st.definir_statut(self.conn, "ted_radar", "TED-1", "non_pertinent",
                          motif="hors_zone")
        st.definir_statut(self.conn, "ted_radar", "TED-2", "contacte")
        motifs = st.lire_motifs(self.conn)
        self.assertEqual(motifs.get(("ted_radar", "TED-1")), "hors_zone")
        self.assertNotIn(("ted_radar", "TED-2"), motifs)      # motif vide -> absent

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
            self.assertIn("1 mise(s) a jour", msg2)
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
    def __init__(self, titre, lignes, erreur=None, valeurs=None):
        self.title = titre
        self._lignes = lignes
        self._erreur = erreur
        self._valeurs = valeurs

    def get_all_records(self):
        if self._erreur:
            raise self._erreur
        return list(self._lignes)

    def get_all_values(self):
        if self._erreur:
            raise self._erreur
        return [list(r) for r in (self._valeurs or [])]


class _FauxClasseur:
    def __init__(self, onglets):
        self._onglets = onglets

    def worksheets(self):
        return list(self._onglets)


class TestLecturePositionnelle(unittest.TestCase):
    """LE bug du 22/07/2026 : un en-tete desaligne d'UNE colonne avait fait
    ranger les numeros de telephone sous 'publication_number'. La lecture
    positionnelle doit y etre immunisee."""

    def setUp(self):
        import radar_rattrapage
        self.rt = radar_rattrapage

    COLONNES = ["titre", "acheteur", "contact_phone", "publication_number"]

    def test_entete_desaligne_ignore(self):
        """En-tete ampute d'une colonne (le cas bm_radar reel) : la lecture
        par position rend quand meme les bonnes valeurs."""
        valeurs = [
            ["titre", "acheteur", "publication_number"],        # en-tete FAUX
            ["Route RN6", "Banque Mondiale", "+211 920 117 553", "OP00264347"],
        ]
        lignes = self.rt.lignes_positionnelles(valeurs, self.COLONNES)
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["publication_number"], "OP00264347")
        self.assertEqual(lignes[0]["contact_phone"], "+211 920 117 553")

    def test_sans_entete(self):
        valeurs = [["Route RN6", "BM", "+33 1", "OP1"]]
        lignes = self.rt.lignes_positionnelles(valeurs, self.COLONNES)
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["publication_number"], "OP1")

    def test_lignes_vides_et_courtes(self):
        valeurs = [["Route", "BM"], ["", "", "", ""]]
        lignes = self.rt.lignes_positionnelles(valeurs, self.COLONNES)
        self.assertEqual(len(lignes), 1)                 # la vide est ecartee
        self.assertEqual(lignes[0]["publication_number"], "")

    def test_schemas_connus_couvrent_les_onglets_de_collecte(self):
        s = self.rt.schemas_connus()
        for onglet in ("ted_radar", "bm_radar", "attributions_radar"):
            self.assertIn(onglet, s, "schema manquant pour " + onglet)
            self.assertIn("publication_number", s[onglet])


@unittest.skipUnless(PSYCOPG and URL_TEST,
                     "pas de base de test (RADAR_TEST_DATABASE_URL absent)")
class TestRattrapageIntegration(unittest.TestCase):
    """Le rattrapage complet contre un vrai Postgres, classeur factice."""

    COLONNES = ["titre", "acheteur", "contact_phone", "publication_number",
                "statut_suivi"]

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

    def _classeur(self, statut="nouveau", pub="OP1"):
        return _FauxClasseur([
            _FauxOnglet("ted_radar", [], valeurs=[
                ["titre", "acheteur", "publication_number"],   # en-tete FAUX
                ["Escorte", "UE", "+33 1 23", pub, statut],
                ["", "", "", "", ""]]),                        # ligne vide
            _FauxOnglet("libre", [{"entreprise": "Egis",
                                   "statut_suivi": statut}]),
            _FauxOnglet("casse", [], erreur=RuntimeError("quota")),
        ])

    def _schemas(self):
        return {"ted_radar": self.COLONNES}

    def test_positionnel_puis_rejeu_idempotent(self):
        b1 = self.rt.rattraper_classeur(self._classeur(), self.conn,
                                        schemas=self._schemas())
        self.assertEqual(b1["ted_radar"][:3], (1, 1, 0))
        self.assertEqual(b1["ted_radar"][3], "positionnel")
        self.assertEqual(b1["libre"][3], "en-tete")
        self.assertIn("illisible", b1["casse"][3])         # best-effort
        # La bonne valeur a bien ete rangee sous le bon nom.
        ligne = st.lire_onglet(self.conn, "ted_radar")[0]
        self.assertEqual(ligne["publication_number"], "OP1")
        self.assertEqual(ligne["contact_phone"], "+33 1 23")
        # Rejeu avec un statut humain MODIFIE : zero doublon.
        b2 = self.rt.rattraper_classeur(self._classeur("contacte"), self.conn,
                                        schemas=self._schemas())
        self.assertEqual(b2["ted_radar"][:3], (1, 0, 1))
        self.assertEqual(b2["libre"][:3], (1, 0, 1))

    def test_purge_remplace_les_lignes_corrompues(self):
        """Scenario reel : la base contient des lignes fausses (telephone en
        guise d'identifiant). La purge les remplace, sans toucher aux statuts."""
        st.ajouter_lignes(self.conn, "ted_radar",
                          [{"publication_number": "+211 920 117 553",
                            "titre": "ligne corrompue"}])
        st.definir_statut(self.conn, "ted_radar", "OP1", "contacte")
        self.conn.commit()
        self.rt.rattraper_classeur(self._classeur(), self.conn, purger=True,
                                   schemas=self._schemas())
        pubs = st.publications_existantes(self.conn, "ted_radar")
        self.assertEqual(pubs, {"OP1"})                   # corrompue effacee
        # La zone humaine vit dans une AUTRE table : intacte.
        self.assertEqual(st.lire_statuts(self.conn)[("ted_radar", "OP1")],
                         "contacte")

    def test_sans_purge_les_anciennes_lignes_restent(self):
        st.ajouter_lignes(self.conn, "ted_radar",
                          [{"publication_number": "ANCIENNE"}])
        self.conn.commit()
        self.rt.rattraper_classeur(self._classeur(), self.conn,
                                   schemas=self._schemas())
        self.assertEqual(st.publications_existantes(self.conn, "ted_radar"),
                         {"ANCIENNE", "OP1"})

    def test_onglet_exclu(self):
        bilan = self.rt.rattraper_classeur(self._classeur(), self.conn,
                                           exclus=("libre",),
                                           schemas=self._schemas())
        self.assertEqual(bilan["libre"], (0, 0, 0, "exclu"))
        self.assertNotIn("libre", st.inventaire(self.conn))


class TestVuesAnalytiques(unittest.TestCase):
    """Vues SQL posees sur le JSONB (palier 3). Tests PURS : on verifie la
    definition et l'execution, sans base (l'integration contre un vrai Postgres
    est couverte par la reexecution idempotente d'initialiser)."""

    def test_les_trois_vues_sont_definies(self):
        for v in ("v_attributions", "v_incumbents", "v_renouvellements"):
            self.assertIn("CREATE VIEW " + v, st.VUES_SQL)

    def test_vues_sur_le_bon_onglet_et_champs(self):
        self.assertIn("attributions_radar", st.VUES_SQL)
        self.assertIn("gagnant", st.VUES_SQL)
        self.assertIn("statut_renouv", st.VUES_SQL)

    def test_recreation_idempotente(self):
        # DROP VIEW IF EXISTS avant chaque CREATE -> rejouable sans erreur.
        self.assertEqual(st.VUES_SQL.count("DROP VIEW IF EXISTS"), 3)

    def test_initialiser_execute_schema_puis_vues(self):
        executes = []

        class _Cur:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql):
                executes.append(sql)

        class _Conn:
            def cursor(self_):
                return _Cur()

        st.initialiser(_Conn())
        self.assertIn(st.SCHEMA_SQL, executes)
        self.assertIn(st.VUES_SQL, executes)
        # Les tables avant les vues (une vue qui precede sa table echouerait).
        self.assertLess(executes.index(st.SCHEMA_SQL), executes.index(st.VUES_SQL))


if __name__ == "__main__":
    unittest.main(verbosity=2)
