# -*- coding: utf-8 -*-
"""Lecture des reponses de l'API Anthropic : robustesse et comptabilite des
echecs.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
`appeler_modele` se terminait par cette ligne, placee HORS du bloc try :

    texte = reponse.json()["content"][0]["text"].strip()

Elle suppose trois choses a la fois, dont aucune n'etait garantie ni rattrapee :

  1. le corps de la reponse est du JSON      -> sinon ValueError ;
  2. `content` existe et n'est pas vide      -> sinon KeyError / IndexError ;
  3. son PREMIER bloc est du texte           -> sinon KeyError.

Le troisieme cas n'a rien de theorique : l'API place un bloc `thinking` AVANT
le texte des qu'un modele a raisonnement est utilise. Le jour ou
RADAR_MODELE_RAFFINEMENT pointerait vers un tel modele, chaque appel de
raffinement leverait une exception non rattrapee. `lancer_collecteur` isole la
casse, donc le run continuerait -- mais la source entiere serait perdue, en
silence, avec un run declare vert.

Deux exigences, testees ici :

  - LIRE ce qui est lisible : concatener tous les blocs `text`, ignorer
    `thinking`, `redacted_thinking` et `tool_use`, plutot que de parier sur la
    position d'un bloc ;
  - COMPTER ce qui ne l'est pas : un echec de lecture doit passer par
    `_marquer_echec_llm`, sinon `sante_llm()` ne voit rien et le run passe pour
    un succes alors qu'il n'a rien produit.

Aucun appel reseau : `session_robuste` est remplacee par une doublure.
"""

import unittest

import ted_complet_v14 as ted


class _Reponse:
    """Doublure de requests.Response, reduite a ce que lit appeler_modele."""

    def __init__(self, charge=None, texte="", json_invalide=False):
        self._charge, self.text = charge, texte
        self._invalide = json_invalide

    def json(self):
        if self._invalide:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._charge

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, reponse):
        self.reponse = reponse

    def post(self, *a, **k):
        return self.reponse


class _Api:
    """Installe une reponse figee et remet tout en place a la sortie, y
    compris les compteurs : STATS_LLM est un dict de module, un test qui le
    laisserait sale fausserait les suivants."""

    def __init__(self, reponse):
        self.reponse = reponse

    def __enter__(self):
        import os
        self._session = ted.session_robuste
        self._stats = dict(ted.STATS_LLM)
        self._cle = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "cle-de-test"
        ted.session_robuste = lambda: _Session(self.reponse)
        ted.STATS_LLM.update({"appels": 0, "echecs": 0, "modele_invalide": 0,
                              "detail": ""})
        return ted.STATS_LLM

    def __exit__(self, *a):
        import os
        ted.session_robuste = self._session
        ted.STATS_LLM.clear()
        ted.STATS_LLM.update(self._stats)
        if self._cle is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._cle
        return False


# ===========================================================================
# EXTRACTION DU TEXTE : FONCTION PURE
# ===========================================================================

class TestTexteDesBlocs(unittest.TestCase):

    def test_cas_nominal(self):
        texte, motif = ted.texte_des_blocs(
            {"content": [{"type": "text", "text": '{"a": 1}'}],
             "stop_reason": "end_turn"})
        self.assertEqual(texte, '{"a": 1}')
        self.assertEqual(motif, "")

    def test_bloc_thinking_avant_le_texte(self):
        """LE cas de regression. Un modele a raisonnement place son bloc
        `thinking` en premier : l'ancienne lecture de `content[0]["text"]`
        levait un KeyError sur chaque appel."""
        texte, _ = ted.texte_des_blocs({"content": [
            {"type": "thinking", "thinking": "reflexion interne",
             "signature": "abc"},
            {"type": "text", "text": '{"a": 1}'}]})
        self.assertEqual(texte, '{"a": 1}')

    def test_blocs_texte_multiples_concatenes(self):
        """L'API peut decouper une meme reponse en plusieurs blocs. Ne prendre
        que le premier tronquerait le JSON au milieu."""
        texte, _ = ted.texte_des_blocs({"content": [
            {"type": "text", "text": '{"a":'},
            {"type": "text", "text": ' 1}'}]})
        self.assertEqual(texte, '{"a": 1}')

    def test_blocs_non_textuels_ignores(self):
        texte, _ = ted.texte_des_blocs({"content": [
            {"type": "redacted_thinking", "data": "xxx"},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {}},
            {"type": "text", "text": "utile"}]})
        self.assertEqual(texte, "utile")

    def test_aucun_bloc_de_texte(self):
        texte, motif = ted.texte_des_blocs(
            {"content": [{"type": "thinking", "thinking": "x"}],
             "stop_reason": "max_tokens"})
        self.assertIsNone(texte)
        self.assertIn("thinking", motif)
        self.assertIn("max_tokens", motif)

    def test_content_vide(self):
        texte, motif = ted.texte_des_blocs(
            {"content": [], "stop_reason": "refusal"})
        self.assertIsNone(texte)
        self.assertIn("refusal", motif)

    def test_charge_d_erreur_conserve_le_type(self):
        """Le TYPE doit apparaitre dans le motif : c'est lui que
        `_marquer_echec_llm` cherche pour distinguer un modele retire d'un
        incident passager."""
        texte, motif = ted.texte_des_blocs(
            {"type": "error",
             "error": {"type": "not_found_error", "message": "model: xyz"}})
        self.assertIsNone(texte)
        self.assertIn("not_found_error", motif)

    def test_content_absent(self):
        texte, motif = ted.texte_des_blocs({"id": "msg_1"})
        self.assertIsNone(texte)
        self.assertIn("content", motif)

    def test_entrees_degenerees(self):
        for charge in (None, [], "texte", 42):
            texte, motif = ted.texte_des_blocs(charge)
            self.assertIsNone(texte)
            self.assertTrue(motif)

    def test_ne_leve_jamais(self):
        """La promesse centrale : quelle que soit la charge, on renvoie un
        couple, on ne propage pas d'exception."""
        for charge in ({"content": [None, 3, {"type": "text"}]},
                       {"content": [{"pas_de_type": True}]},
                       {"content": {"type": "text"}},
                       {"error": "chaine au lieu d'un objet"}):
            texte, motif = ted.texte_des_blocs(charge)
            self.assertTrue(texte is None or isinstance(texte, str))


# ===========================================================================
# INTEGRATION DANS appeler_modele
# ===========================================================================

class TestAppelerModele(unittest.TestCase):

    def test_reponse_normale(self):
        with _Api(_Reponse({"content": [{"type": "text", "text": "resultat"}]})):
            self.assertEqual(ted.appeler_modele("prompt"), "resultat")

    def test_balises_markdown_retirees(self):
        with _Api(_Reponse({"content": [
                {"type": "text", "text": '```json\n{"a": 1}\n```'}]})):
            self.assertEqual(ted.appeler_modele("prompt"), '{"a": 1}')

    def test_bloc_thinking_ne_fait_plus_planter(self):
        """Avant : KeyError non rattrapee, remontee jusqu'a lancer_collecteur,
        source entiere perdue pour le run."""
        with _Api(_Reponse({"content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": "resultat"}]})) as stats:
            self.assertEqual(ted.appeler_modele("prompt"), "resultat")
            self.assertEqual(stats["echecs"], 0)

    def test_corps_non_json_compte_comme_echec(self):
        """Une page d'erreur HTML renvoyee par un proxy, par exemple."""
        with _Api(_Reponse(json_invalide=True,
                           texte="<html>502 Bad Gateway</html>")) as stats:
            self.assertIsNone(ted.appeler_modele("prompt"))
            self.assertEqual(stats["echecs"], 1)

    def test_absence_de_texte_compte_comme_echec(self):
        with _Api(_Reponse({"content": [], "stop_reason": "refusal"})) as stats:
            self.assertIsNone(ted.appeler_modele("prompt"))
            self.assertEqual(stats["echecs"], 1)

    def test_modele_retire_remonte_dans_la_sante(self):
        """Bout en bout : une charge d'erreur `not_found_error` doit faire
        basculer `sante_llm()` en rouge, avec un message qui dit quoi faire."""
        with _Api(_Reponse({"type": "error", "error": {
                "type": "not_found_error",
                "message": "model: claude-inexistant"}})):
            self.assertIsNone(ted.appeler_modele("prompt"))
            ok, message = ted.sante_llm()
            self.assertFalse(ok)
            self.assertIn("MODELE REFUSE", message)

    def test_les_echecs_de_lecture_ne_sont_pas_silencieux(self):
        """Sans comptabilite, un run ou TOUS les appels renvoient une charge
        illisible se terminerait en vert, avec zero avis et aucune alerte."""
        with _Api(_Reponse({"content": []})) as stats:
            for _ in range(ted.MINI_APPELS_LLM):
                ted.appeler_modele("prompt")
            self.assertEqual(stats["appels"], ted.MINI_APPELS_LLM)
            ok, message = ted.sante_llm()
            self.assertFalse(ok)
            self.assertIn("MASSIVEMENT EN ECHEC", message)

    def test_sans_cle_api_aucun_appel(self):
        import os
        with _Api(_Reponse({"content": [{"type": "text", "text": "x"}]})) as stats:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertIsNone(ted.appeler_modele("prompt"))
            self.assertEqual(stats["appels"], 0)


# ===========================================================================
# LE MEME DEFAUT DANS LE MOTEUR DE SIGNAUX PRIVES
# ===========================================================================

class TestAppelLLMSignauxPrives(unittest.TestCase):
    """`bitd_signaux._appel_llm` portait la MEME ligne fragile, avec une
    circonstance aggravante : ses appelants enveloppent tout dans
    `except Exception` et se contentent d'un "(info) Analyse LLM echouee".
    Rien n'atteignait `ted.STATS_LLM`, donc un run ou 100 % des analyses de
    signaux prives echouaient etait declare EN BONNE SANTE.

    Le module est bien VIVANT (moteur reutilise par signaux_prives,
    bm_attributions, enrichir_entreprises, radar_dashboard) : seul l'ancien
    point d'entree `bitd.main` est du code mort."""

    def setUp(self):
        try:
            import bitd_signaux
        except Exception as e:
            self.skipTest("bitd_signaux indisponible ({})".format(e))
        self.bitd = bitd_signaux

    def test_bloc_thinking_ne_fait_plus_planter(self):
        with _Api(_Reponse({"content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": "resultat"}]})):
            self.assertEqual(self.bitd._appel_llm("prompt"), "resultat")

    def test_appel_reussi_est_compte(self):
        """Compter les SUCCES autant que les echecs : sans le denominateur,
        le ratio de sante_llm serait faux dans l'autre sens."""
        with _Api(_Reponse({"content": [{"type": "text", "text": "ok"}]})) as stats:
            self.bitd._appel_llm("prompt")
            self.assertEqual((stats["appels"], stats["echecs"]), (1, 0))

    def test_reponse_inexploitable_comptee_et_levee(self):
        """Le contrat ne change pas : la fonction leve, les appelants
        rattrapent. Mais l'echec est desormais VISIBLE."""
        with _Api(_Reponse({"content": [], "stop_reason": "refusal"})) as stats:
            with self.assertRaises(RuntimeError):
                self.bitd._appel_llm("prompt")
            self.assertEqual(stats["echecs"], 1)

    def test_corps_non_json_compte_et_leve(self):
        with _Api(_Reponse(json_invalide=True, texte="<html>502</html>")) as stats:
            with self.assertRaises(RuntimeError):
                self.bitd._appel_llm("prompt")
            self.assertEqual(stats["echecs"], 1)

    def test_les_appelants_degradent_toujours_proprement(self):
        """Bout en bout : malgre la levee, `analyser_signal_llm` rend None
        sans propager, comme avant."""
        with _Api(_Reponse({"content": []})):
            self.assertIsNone(self.bitd.analyser_signal_llm(
                "Entreprise", {"titre": "t", "resume": "r"}))

    def test_echec_massif_visible_dans_la_sante(self):
        """Le scenario qui passait totalement inapercu."""
        with _Api(_Reponse({"content": []})):
            for _ in range(ted.MINI_APPELS_LLM):
                self.bitd.analyser_signal_llm("E", {"titre": "t"})
            ok, message = ted.sante_llm()
            self.assertFalse(ok)
            self.assertIn("MASSIVEMENT EN ECHEC", message)


if __name__ == "__main__":
    unittest.main()
