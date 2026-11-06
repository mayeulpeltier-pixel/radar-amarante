"""Tests de la centralisation des parametres d'appel (bascule Haiku 5.5, 06/11/2026).

Constat de la sonde (sonde_modeles.py, 40 vrais avis TED) :
  - Haiku 5.5 et Sonnet 5.5 refusent `temperature` (HTTP 400) ;
  - Haiku 5.5 raisonne avant de repondre : a max_tokens=1000, 39/40 tronques.
Ces tests verrouillent que TOUS les modules qui appellent le modele passent
par `ted.corps_appel`, et que le coeur se rattrape seul si un modele inconnu
de la regle refuse `temperature`, sans compter ce refus comme une panne."""

import inspect
import unittest
from unittest import mock

import ted_complet_v14 as ted


class TestGeneration(unittest.TestCase):
    def test_identifiants(self):
        self.assertEqual(ted.generation_modele("claude-haiku-5-5"), 5)
        self.assertEqual(ted.generation_modele("claude-sonnet-4-6"), 4)
        self.assertEqual(ted.generation_modele("claude-haiku-4-5-20251001"), 4)
        self.assertEqual(ted.generation_modele("claude-sonnet-5"), 5)
        self.assertEqual(ted.generation_modele("claude-3-5-haiku-20241022"), 3)
        self.assertEqual(ted.generation_modele("claude-opus-5-5"), 5)
        self.assertIsNone(ted.generation_modele("gpt-4"))
        self.assertIsNone(ted.generation_modele(None))

    def test_modele_raisonne(self):
        self.assertTrue(ted.modele_raisonne("claude-haiku-5-5"))
        self.assertTrue(ted.modele_raisonne("claude-sonnet-5-5"))
        self.assertFalse(ted.modele_raisonne("claude-sonnet-4-6"))
        self.assertFalse(ted.modele_raisonne("claude-haiku-4-5-20251001"))


class TestCorps(unittest.TestCase):
    def test_modele_4x_corps_historique(self):
        c = ted.corps_appel("claude-sonnet-4-6", "p", 1000)
        self.assertEqual(c["temperature"], 0)
        self.assertEqual(c["max_tokens"], 1000)
        self.assertEqual(c["messages"], [{"role": "user", "content": "p"}])

    def test_modele_5x_sans_temperature_avec_marge(self):
        c = ted.corps_appel("claude-haiku-5-5", "p", 1000)
        self.assertNotIn("temperature", c)
        self.assertEqual(c["max_tokens"], 1000 + ted.MARGE_RAISONNEMENT)
        # La marge couvre le pire cas mesure (2 401 tokens) au plafond de la lentille IE.
        self.assertGreaterEqual(ted.corps_appel("claude-haiku-5-5", "p", 400)["max_tokens"], 2401)

    def test_temperature_explicite_respectee_en_4x(self):
        self.assertEqual(ted.corps_appel("claude-sonnet-4-6", "p", 10, 0.3)["temperature"], 0.3)
        self.assertNotIn("temperature", ted.corps_appel("claude-sonnet-4-6", "p", 10, None))

    def test_refus_temperature(self):
        msg = '{"error":{"type":"invalid_request_error","message":"`temperature` is deprecated for this model."}}'
        self.assertTrue(ted.est_refus_temperature(400, msg))
        self.assertFalse(ted.est_refus_temperature(400, "prompt is too long"))
        self.assertFalse(ted.est_refus_temperature(200, msg))


class TestBascule(unittest.TestCase):
    def test_defaut_volume_haiku_55(self):
        # Sauf surcharge explicite RADAR_MODELE (retour arriere).
        import os
        if os.environ.get("RADAR_MODELE"):
            self.skipTest("RADAR_MODELE surcharge")
        self.assertEqual(ted.MODELE, "claude-haiku-5-5")

    def test_escalade_inchangee(self):
        import os
        if os.environ.get("RADAR_MODELE_RAFFINEMENT"):
            self.skipTest("surcharge")
        self.assertEqual(ted.MODELE_RAFFINEMENT, "claude-sonnet-4-6")


class FausseReponse:
    def __init__(self, code, charge=None, texte=""):
        self.status_code, self._charge = code, charge
        self.text = texte or (str(charge) if charge else "")

    def json(self):
        if self._charge is None:
            raise ValueError("pas de JSON")
        return self._charge

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError("HTTP {}".format(self.status_code))


OK = {"content": [{"type": "thinking", "thinking": "..."},
                  {"type": "text", "text": '{"ok": true}'}],
      "stop_reason": "end_turn"}
REFUS = ('{"type":"error","error":{"type":"invalid_request_error",'
         '"message":"`temperature` is deprecated for this model."}}')


class TestAppelerModele(unittest.TestCase):
    def setUp(self):
        self._stats = dict(ted.STATS_LLM)
        ted.STATS_LLM.update({"appels": 0, "echecs": 0, "modele_invalide": 0,
                              "detail": "", "arret": "", "ignores": 0})
        self._appris = set(ted._SANS_TEMPERATURE_APPRIS)
        ted._SANS_TEMPERATURE_APPRIS.clear()
        self._env = mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        ted.STATS_LLM.update(self._stats)
        ted._SANS_TEMPERATURE_APPRIS.clear()
        ted._SANS_TEMPERATURE_APPRIS.update(self._appris)

    def _session(self, reponses):
        envoyes = []

        class S:
            def post(self, url, headers=None, json=None, timeout=None):
                envoyes.append((json, timeout))
                return reponses.pop(0)
        return S(), envoyes

    def test_modele_5x_appele_sans_temperature(self):
        s, envoyes = self._session([FausseReponse(200, OK)])
        with mock.patch.object(ted, "session_robuste", return_value=s):
            texte = ted.appeler_modele("p", modele="claude-haiku-5-5")
        self.assertEqual(texte, '{"ok": true}')
        self.assertNotIn("temperature", envoyes[0][0])
        self.assertEqual(envoyes[0][1], 60)

    def test_modele_inconnu_qui_refuse_temperature_rejoue_sans(self):
        """Filet : un modele hors regle refuse `temperature` -> appris, rejoue,
        et le refus n'est PAS compte comme modele invalide (run rouge a tort)."""
        s, envoyes = self._session([FausseReponse(400, None, REFUS), FausseReponse(200, OK)])
        with mock.patch.object(ted, "session_robuste", return_value=s):
            texte = ted.appeler_modele("p", modele="modele-futur-x")
        self.assertEqual(texte, '{"ok": true}')
        self.assertIn("temperature", envoyes[0][0])
        self.assertNotIn("temperature", envoyes[1][0])
        self.assertEqual(ted.STATS_LLM["echecs"], 0)
        self.assertEqual(ted.STATS_LLM["modele_invalide"], 0)
        self.assertTrue(ted.modele_raisonne("modele-futur-x"))
        ok, _ = ted.sante_llm()
        self.assertTrue(ok)

    def test_autre_400_reste_un_echec(self):
        s, envoyes = self._session([FausseReponse(400, None, "prompt is too long")])
        with mock.patch.object(ted, "session_robuste", return_value=s):
            self.assertIsNone(ted.appeler_modele("p", modele="claude-sonnet-4-6"))
        self.assertEqual(len(envoyes), 1)
        self.assertEqual(ted.STATS_LLM["echecs"], 1)

    def test_modele_4x_inchange(self):
        s, envoyes = self._session([FausseReponse(200, OK)])
        with mock.patch.object(ted, "session_robuste", return_value=s):
            ted.appeler_modele("p", modele="claude-sonnet-4-6")
        self.assertEqual(envoyes[0][0]["temperature"], 0)
        self.assertEqual(envoyes[0][0]["max_tokens"], 1000)
        self.assertEqual(envoyes[0][1], 30)


class TestCablageModules(unittest.TestCase):
    """Plus aucun module ne doit construire son corps avec temperature en dur."""

    def test_lentille_ie(self):
        import lentille_ie
        c = lentille_ie._corps("p", "claude-haiku-5-5")
        self.assertNotIn("temperature", c)
        self.assertGreater(c["max_tokens"], 400)
        self.assertEqual(lentille_ie._corps("p", "claude-sonnet-4-6")["temperature"], 0)
        self.assertNotIn('"temperature": 0', inspect.getsource(lentille_ie._appel_http))

    def test_bitd_signaux(self):
        import bitd_signaux
        src = inspect.getsource(bitd_signaux._appel_llm)
        self.assertIn("ted.corps_appel(", src)
        self.assertNotIn('"temperature": temperature', src)

    def test_shadow_run(self):
        import shadow_run_projets
        src = inspect.getsource(shadow_run_projets.appel_llm_mesure)
        self.assertIn("ted.corps_appel(", src)
        self.assertNotIn('"temperature": 0', src)

    def test_coeur(self):
        src = inspect.getsource(ted.appeler_modele)
        self.assertIn("corps_appel(", src)
        self.assertNotIn('"temperature": 0', src)


if __name__ == "__main__":
    unittest.main()
