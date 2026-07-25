# -*- coding: utf-8 -*-
"""Disjoncteur d'appels au modele et sortie en echec des collecteurs autonomes.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Run reel sur UNGM : le solde de credits Anthropic etait epuise. Le journal
montre SOIXANTE fois la meme erreur :

    Erreur d'appel API : 400 Client Error: Bad Request
    Detail : "Your credit balance is too low to access the Anthropic API."

Aucun de ces soixante appels n'avait la moindre chance d'aboutir : un solde
vide ne se repare pas en reessayant. Deux defauts distincts.

1. AUCUN DISJONCTEUR
   Le pipeline a continue a appeler l'API pour chacun des 60 avis. Cout :
   le temps du run, et surtout un journal ou la cause reelle est enterree
   sous soixante blocs identiques. Le jour ou ce sera une cle REVOQUEE
   plutot qu'un solde, ce seront soixante appels authentifies inutiles.

2. UN ECHEC TOTAL, MAIS UNE ETAPE VERTE
   `ungm_radar.py` s'est termine en code 0 : « 0 avis analyse(s) »,
   « 0 nouvelle(s) ligne(s) ». L'etape GitHub etait donc VERTE. Le seul
   moyen d'apprendre que le run n'avait rien produit etait de lire le
   journal ligne a ligne. `radar_run.py` verifiait bien `sante_llm()`, mais
   les collecteurs lances DIRECTEMENT par le workflow, non.

CE QUI EST DELICAT ICI
----------------------
Un disjoncteur trop sensible est pire que pas de disjoncteur : il suffirait
d'un seul avis mal forme pour couper toute une source. `invalid_request_error`
n'est donc PAS un motif d'arret en soi -- il couvre aussi le prompt trop long,
qui ne concerne qu'un avis. Seuls les motifs DEFINITIFS coupent : solde
epuise, cle revoquee, droits insuffisants. La classe de tests
`TestCeQuiNeDoitPasCouper` verrouille cette frontiere, et elle compte autant
que le reste.

Aucun appel reseau : la session HTTP est remplacee par une doublure.
"""

import importlib
import unittest

import ted_complet_v14 as ted


# Message exact renvoye par Anthropic le 23/07/2026.
SOLDE_EPUISE = (
    '{"type":"error","error":{"type":"invalid_request_error","message":'
    '"Your credit balance is too low to access the Anthropic API. '
    'Please go to Plans & Billing to upgrade or purchase credits."}}')


class _Reponse:
    def __init__(self, texte):
        self.text = texte

    def raise_for_status(self):
        import requests
        raise requests.exceptions.HTTPError("400 Client Error: Bad Request")

    def json(self):
        return {}


class _Api:
    """Installe une reponse d'erreur figee et remet tout en place ensuite,
    compteurs compris : STATS_LLM est un dict de module."""

    def __init__(self, detail):
        self.detail = detail

    def __enter__(self):
        import os
        self._session = ted.session_robuste
        self._stats = dict(ted.STATS_LLM)
        self._annonce = ted._ARRET_ANNONCE
        self._cle = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "cle-de-test"
        ted.session_robuste = lambda: type(
            "S", (), {"post": lambda _s, *a, **k: _Reponse(self.detail)})()
        ted.STATS_LLM.update({"appels": 0, "echecs": 0, "modele_invalide": 0,
                              "detail": "", "arret": "", "ignores": 0})
        ted._ARRET_ANNONCE = False
        return ted.STATS_LLM

    def __exit__(self, *a):
        import os
        ted.session_robuste = self._session
        ted._ARRET_ANNONCE = self._annonce
        ted.STATS_LLM.clear()
        ted.STATS_LLM.update(self._stats)
        if self._cle is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._cle
        return False


# ===========================================================================
# CE QUI DOIT COUPER
# ===========================================================================

class TestCeQuiDoitCouper(unittest.TestCase):

    DEFINITIFS = [
        ("solde epuise", SOLDE_EPUISE),
        ("cle revoquee",
         '{"error":{"type":"authentication_error","message":"invalid x-api-key"}}'),
        ("droits insuffisants",
         '{"error":{"type":"permission_error","message":"not allowed"}}'),
    ]

    def test_le_disjoncteur_s_ouvre(self):
        for nom, detail in self.DEFINITIFS:
            with self.subTest(motif=nom):
                importlib.reload(ted)
                ted._marquer_echec_llm(detail)
                self.assertTrue(ted.STATS_LLM["arret"])

    def test_un_seul_appel_http_au_lieu_de_soixante(self):
        """LE chiffre du 23/07 : 60 avis, 60 appels, 60 echecs identiques."""
        with _Api(SOLDE_EPUISE) as stats:
            for i in range(60):
                self.assertIsNone(ted.appeler_modele("prompt {}".format(i)))
            self.assertEqual(stats["appels"], 1)
            self.assertEqual(stats["ignores"], 59)

    def test_le_motif_n_est_annonce_qu_une_fois(self):
        """Soixante lignes identiques enterrent la cause. Une seule la met en
        evidence."""
        with _Api(SOLDE_EPUISE):
            import io
            import contextlib
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                for i in range(20):
                    ted.appeler_modele("prompt")
            self.assertEqual(sortie.getvalue().count("INTERROMPUS"), 1)

    def test_la_sante_bascule_meme_sur_peu_d_appels(self):
        """Piege evite : un solde epuise au 2e avis ne produit que 2 echecs
        sur 2 appels, donc trop peu pour franchir MINI_APPELS_LLM. Sans
        traitement dedie, le run passerait pour sain alors qu'il n'a rien
        analyse. Un motif definitif ne se dilue pas dans un ratio."""
        with _Api(SOLDE_EPUISE) as stats:
            for _ in range(2):
                ted.appeler_modele("prompt")
            self.assertLess(stats["appels"], ted.MINI_APPELS_LLM)
            ok, message = ted.sante_llm()
            self.assertFalse(ok)
            self.assertIn("INTERROMPUS", message)

    def test_le_message_dit_quoi_faire(self):
        """Un message d'echec qui n'indique pas l'action a mener oblige a
        rouvrir le code. Celui-ci nomme la cause et le remede."""
        with _Api(SOLDE_EPUISE):
            for _ in range(3):
                ted.appeler_modele("prompt")
            _ok, message = ted.sante_llm()
            self.assertIn("solde de credits", message)
            self.assertIn("non analyse", message)


# ===========================================================================
# CE QUI NE DOIT PAS COUPER
# ===========================================================================

class TestCeQuiNeDoitPasCouper(unittest.TestCase):
    """La frontiere compte autant que le disjoncteur lui-meme : couper sur un
    incident passager, ou sur un seul avis mal forme, priverait le radar d'une
    source entiere pour rien."""

    PASSAGERS = [
        ("prompt trop long (un seul avis)",
         '{"error":{"type":"invalid_request_error","message":'
         '"prompt is too long: 250000 tokens"}}'),
        ("surcharge passagere",
         '{"error":{"type":"overloaded_error","message":"Overloaded"}}'),
        ("timeout", "timeout"),
        ("erreur serveur", "500 Internal Server Error"),
        ("modele retire",
         '{"error":{"type":"not_found_error","message":"model: claude-vieux"}}'),
    ]

    def test_le_disjoncteur_reste_ferme(self):
        for nom, detail in self.PASSAGERS:
            with self.subTest(motif=nom):
                importlib.reload(ted)
                ted._marquer_echec_llm(detail)
                self.assertFalse(ted.STATS_LLM["arret"],
                                 "coupure abusive sur : {}".format(nom))

    def test_invalid_request_error_seul_ne_coupe_pas(self):
        """Le piege principal : `invalid_request_error` est la classe du solde
        epuise ET du prompt trop long. Couper sur le TYPE aurait suffi a
        perdre une source entiere pour un seul avis mal forme."""
        importlib.reload(ted)
        ted._marquer_echec_llm(
            '{"error":{"type":"invalid_request_error","message":"messages: too many"}}')
        self.assertFalse(ted.STATS_LLM["arret"])

    def test_un_modele_retire_garde_son_traitement_propre(self):
        """Il a deja son diagnostic dedie (`modele_invalide`), plus precis et
        plus actionnable. Le disjoncteur ne doit pas le masquer."""
        importlib.reload(ted)
        for _ in range(ted.MINI_APPELS_LLM):
            ted._marquer_echec_llm(
                '{"error":{"type":"not_found_error","message":"model: x"}}')
        ted.STATS_LLM["appels"] = ted.MINI_APPELS_LLM
        ok, message = ted.sante_llm()
        self.assertFalse(ok)
        self.assertIn("MODELE REFUSE", message)

    def test_les_echecs_passagers_restent_toleres(self):
        importlib.reload(ted)
        ted.STATS_LLM["appels"] = 20
        for _ in range(2):
            ted._marquer_echec_llm("timeout")
        ok, _message = ted.sante_llm()
        self.assertTrue(ok, "deux timeouts ne sont pas un incident")


# ===========================================================================
# SORTIE EN ECHEC DES COLLECTEURS AUTONOMES
# ===========================================================================

class TestSortieDesCollecteursAutonomes(unittest.TestCase):
    """`radar_run.py` verifiait deja `sante_llm()`. Les collecteurs lances
    directement par le workflow (`python ungm_radar.py`,
    `python idb_radar.py`), non : le 23/07, UNGM a termine en code 0 apres
    n'avoir analyse aucun avis, et l'etape GitHub etait verte."""

    def test_run_sain_ne_sort_pas_en_echec(self):
        importlib.reload(ted)
        ted.STATS_LLM.update({"appels": 40, "echecs": 1})
        try:
            ted.sortie_selon_sante_llm("test")
        except SystemExit:
            self.fail("un run sain ne doit pas sortir en echec")

    def test_run_sans_analyse_sort_en_code_1(self):
        importlib.reload(ted)
        ted.STATS_LLM.update({"appels": 1, "ignores": 59, "arret": SOLDE_EPUISE})
        with self.assertRaises(SystemExit) as ctx:
            ted.sortie_selon_sante_llm("ungm")
        self.assertEqual(ctx.exception.code, 1)

    def test_collecteur_sans_appel_au_modele_reste_vert(self):
        """Tous les avis deja connus, donc zero appel : c'est un run normal,
        pas un incident. Il ne doit surtout pas alerter."""
        importlib.reload(ted)
        ted.STATS_LLM.update({"appels": 0, "echecs": 0})
        try:
            ted.sortie_selon_sante_llm("ungm")
        except SystemExit:
            self.fail("zero appel n'est pas un echec")

    def test_les_deux_collecteurs_concernes_sont_cables(self):
        """Seuls UNGM et IDB appellent le modele parmi les etapes autonomes
        du workflow. Les trois autres (attributions UNGM, IsDB, attributions
        BM) n'y touchent pas : les cabler n'aurait rien verifie."""
        import inspect
        for module in ("ungm_radar", "idb_radar"):
            with self.subTest(collecteur=module):
                try:
                    mod = importlib.import_module(module)
                except Exception as e:
                    self.skipTest("{} indisponible ({})".format(module, e))
                self.assertIn("ted.sortie_selon_sante_llm",
                              inspect.getsource(mod))

    def test_les_collecteurs_sans_llm_ne_sont_pas_cables(self):
        """Garde-fou inverse : y ajouter le controle ferait croire a une
        protection inexistante."""
        import inspect
        for module in ("ungm_attributions", "isdb_radar", "bm_attributions"):
            with self.subTest(collecteur=module):
                try:
                    mod = importlib.import_module(module)
                except Exception as e:
                    self.skipTest("{} indisponible ({})".format(module, e))
                source = inspect.getsource(mod)
                self.assertNotIn("ted.appeler_modele", source)
                self.assertNotIn("ted.appeler_llm", source)


if __name__ == "__main__":
    unittest.main()
