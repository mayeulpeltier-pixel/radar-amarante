"""Tests de la sonde de migration des modeles (sans reseau, sans cle API).

Tout appel au modele passe par une fonction `appel` injectee : on simule les
reponses de l'API (succes, refus de temperature, JSON casse, modele retire)
et on verifie que la sonde mesure juste."""

import json
import unittest

import sonde_modeles as sm
import ted_complet_v14 as ted


def _extraction(**surcharges):
    base = {
        "deploiement_terrain_reel": True,
        "type_mobilite": "terrain_isole",
        "profil_personnes_exposees": "expert_international",
        "securite_existante": "aucune",
        "indices_deploiement": ["field missions"],
        "type_activite": "assistance_technique",
        "type_client": "bailleur_donateur",
        "duree_estimee": "longue_ou_residente",
        "accessibilite_commerciale": "facile",
        "profils_acteurs_probables": ["bureau d'etudes"],
        "besoin_securite_operationnel_probable": True,
        "niveau_opportunite_amarante": "fort",
        "justification": "Experts mobiles en zone isolee.",
        "confiance": 0.9,
    }
    base.update(surcharges)
    return base


AVIS = {
    "publication_number": "123-2026", "titre": "Technical assistance Niger",
    "acheteur": "European Commission", "pays_acheteur": "BEL",
    "pays_execution": "NER", "pays_execution_incertitude": False,
    "acheteur_etranger": True, "cpv": "71000000", "valeur_estimee": "",
    "description": "Long-term experts travelling to remote sites.",
    "date_publication": "2026-11-01", "deadline": "", "lien_avis": "",
}


def _reponse(texte, code=200, tin=3000, tout=500, stop="end_turn"):
    charge = {"content": [{"type": "text", "text": texte}],
              "usage": {"input_tokens": tin, "output_tokens": tout},
              "stop_reason": stop}
    return {"code": code, "charge": charge, "corps_texte": json.dumps(charge),
            "latence_s": 1.0}


class TestCout(unittest.TestCase):
    def test_cout_connu(self):
        c, connu = sm.cout_usd("claude-sonnet-5-5", 1_000_000, 100_000)
        self.assertTrue(connu)
        self.assertAlmostEqual(c, 2.0 + 1.0)

    def test_modele_inconnu_compte_zero_et_signale(self):
        self.assertEqual(sm.cout_usd("modele-fantome", 10, 10), (0.0, False))

    def test_haiku_55_dix_fois_moins_cher_que_45(self):
        a, _ = sm.cout_usd("claude-haiku-4-5-20251001", 3000, 600)
        b, _ = sm.cout_usd("claude-haiku-5-5", 3000, 600)
        self.assertAlmostEqual(a / b, 10.0)

    def test_estimation_a_priori(self):
        self.assertGreater(sm.estimer_cout(25, ["claude-sonnet-4-6"]), 0)
        self.assertEqual(sm.estimer_cout(25, ["inconnu"]), 0)


class TestRefusTemperature(unittest.TestCase):
    def test_400_avec_mention(self):
        self.assertTrue(sm.est_refus_temperature(
            400, '{"error":{"message":"temperature is not supported"}}'))

    def test_400_sans_mention_nest_pas_un_refus(self):
        self.assertFalse(sm.est_refus_temperature(400, "prompt is too long"))

    def test_autre_code(self):
        self.assertFalse(sm.est_refus_temperature(404, "temperature"))


class TestParsing(unittest.TestCase):
    def test_direct(self):
        e, niveau = sm.parser_extraction(json.dumps(_extraction()))
        self.assertEqual(niveau, "direct")
        self.assertFalse(e["securite_existante_detectee"])  # normalise

    def test_balises_markdown(self):
        e, niveau = sm.parser_extraction("```json\n" + json.dumps(_extraction()) + "\n```")
        self.assertEqual(niveau, "direct")
        self.assertIsNotNone(e)

    def test_texte_parasite_extrait(self):
        e, niveau = sm.parser_extraction("Voici : " + json.dumps(_extraction()) + " fin")
        self.assertEqual(niveau, "extrait")

    def test_json_casse(self):
        self.assertEqual(sm.parser_extraction('{"a": 1 (commentaire)}'), (None, "echec"))
        self.assertEqual(sm.parser_extraction(""), (None, "echec"))

    def test_prestataire_tiers_marque(self):
        e, _ = sm.parser_extraction(json.dumps(_extraction(securite_existante="prestataire_tiers")))
        self.assertTrue(e["justification"].startswith(ted.MARQUEUR_DEPLACEMENT))


class TestDecisions(unittest.TestCase):
    def test_classes_suivent_les_seuils_du_coeur(self):
        self.assertEqual(sm.classe_action(ted.SEUIL_ALERTE), "contacter")
        self.assertEqual(sm.classe_action(ted.SEUIL_SURVEILLANCE), "surveiller")
        self.assertEqual(sm.classe_action(0), "ignorer")

    def test_escalade_trois_criteres(self):
        self.assertTrue(sm.merite_escalade(_extraction(), 5))
        self.assertTrue(sm.merite_escalade(_extraction(confiance=0.5), 1))
        self.assertTrue(sm.merite_escalade(
            ted.normaliser_securite(_extraction(securite_existante="interne_client")), 1))
        self.assertFalse(sm.merite_escalade(_extraction(), 1))
        self.assertFalse(sm.merite_escalade(None, 9))


class TestComparaison(unittest.TestCase):
    def _res(self, sa, sb, ea=None, eb=None):
        return {"avis": {"publication_number": "1", "titre": "t"},
                "par_role": {"a": {"extraction": ea or _extraction(), "score": sa},
                             "b": {"extraction": eb or _extraction(), "score": sb}}}

    def test_accord_parfait(self):
        r = sm.comparer_paire([self._res(7, 7)], "a", "b")
        self.assertEqual(r["accord_classe_action"], 1.0)
        self.assertEqual(r["ecarts_notables"], 0)
        self.assertEqual(r["accord_champs"]["securite_existante"], 1.0)

    def test_desaccord_action_liste(self):
        r = sm.comparer_paire([self._res(8, 2)], "a", "b")
        self.assertEqual(r["accord_classe_action"], 0.0)
        self.assertEqual(r["ecarts_notables"], 1)
        self.assertEqual(len(r["desaccords_action"]), 1)

    def test_champ_insensible_casse(self):
        r = sm.comparer_paire([self._res(5, 5, eb=_extraction(niveau_opportunite_amarante="FORT"))], "a", "b")
        self.assertEqual(r["accord_champs"]["niveau_opportunite_amarante"], 1.0)

    def test_avis_sans_extraction_exclus(self):
        x = self._res(5, 5)
        x["par_role"]["b"]["extraction"] = None
        r = sm.comparer_paire([x], "a", "b")
        self.assertEqual(r["avis_communs"], 0)
        self.assertIsNone(r["accord_classe_action"])


class TestEchantillon(unittest.TestCase):
    def test_description_dabord_et_deterministe(self):
        avis = [{"publication_number": "3", "description": ""},
                {"publication_number": "2", "description": "x"},
                {"publication_number": "1", "description": ""}]
        e = sm.echantillonner(avis, 2)
        self.assertEqual([a["publication_number"] for a in e], ["2", "1"])
        self.assertEqual(sm.echantillonner(avis, 0), [])


class FausseAPI:
    """Simule l'API : `regles[modele]` = fonction(temperature) -> reponse."""

    def __init__(self, regles):
        self.regles, self.appels = regles, []

    def __call__(self, modele, prompt, temperature, max_tokens=1000):
        self.appels.append((modele, temperature, max_tokens))
        return self.regles[modele](temperature, prompt)


def _refuse_temperature(temperature, prompt):
    if temperature is not None:
        return {"code": 400, "charge": None, "latence_s": 0.1,
                "corps_texte": '{"error":{"type":"invalid_request_error",'
                               '"message":"temperature: unsupported for this model"}}'}
    return _reponse(json.dumps(_extraction()) if "Avis" in prompt else '{"ok": true}')


def _accepte_tout(temperature, prompt):
    return _reponse(json.dumps(_extraction()) if "Avis" in prompt else '{"ok": true}')


def _retire(temperature, prompt):
    return {"code": 404, "charge": None, "latence_s": 0.1,
            "corps_texte": '{"error":{"type":"not_found_error","message":"model"}}'}


class TestCompatibilite(unittest.TestCase):
    def test_accepte_temperature(self):
        c = sm.tester_compatibilite("m", appel=FausseAPI({"m": _accepte_tout}))
        self.assertEqual((c["disponible"], c["temperature_0"]), (True, True))

    def test_refuse_temperature_mais_disponible(self):
        api = FausseAPI({"m": _refuse_temperature})
        c = sm.tester_compatibilite("m", appel=api)
        self.assertEqual((c["disponible"], c["temperature_0"]), (True, False))
        self.assertEqual([t for _, t, _ in api.appels], [0, None])

    def test_modele_retire(self):
        c = sm.tester_compatibilite("m", appel=FausseAPI({"m": _retire}))
        self.assertFalse(c["disponible"])
        self.assertIn("404", c["detail"])


class TestExecution(unittest.TestCase):
    MODELES = {"volume_actuel": "claude-haiku-4-5-20251001",
               "volume_candidat": "claude-haiku-5-5",
               "escalade_actuelle": "claude-sonnet-4-6",
               "escalade_candidate": "claude-sonnet-5-5"}

    def test_bout_en_bout_et_temperature_adaptee(self):
        api = FausseAPI({"claude-haiku-4-5-20251001": _accepte_tout,
                         "claude-haiku-5-5": _refuse_temperature,
                         "claude-sonnet-4-6": _accepte_tout,
                         "claude-sonnet-5-5": _refuse_temperature})
        bilan = sm.executer([AVIS, dict(AVIS, publication_number="124-2026")],
                            self.MODELES, plafond_usd=10, appel=api, pause=0)
        # Les candidats refusant temperature sont appeles SANS le parametre.
        analyses = [(m, t) for m, t, mx in api.appels if mx == 1000]
        self.assertTrue(all(t is None for m, t in analyses if m.endswith("5-5")))
        self.assertTrue(all(t == 0 for m, t in analyses if not m.endswith("5-5")))
        s = sm.rapport(bilan, self.MODELES)
        self.assertEqual(s["avis_analyses"], 2)
        for r in s["par_modele"]:
            self.assertEqual(r["extractions_ok"], 2)
            self.assertGreater(r["cout_total_usd"], 0)
        for p in s["par_paire"]:
            self.assertEqual(p["accord_classe_action"], 1.0)
        # Score = vrai calcul du coeur, pas une valeur inventee par la sonde.
        attendu = ted.calculer_scores(AVIS, ted.normaliser_securite(_extraction()))[2]
        self.assertEqual(bilan["resultats"][0]["par_role"]["volume_actuel"]["score"], attendu)

    def test_modele_indisponible_saute_sans_casser(self):
        api = FausseAPI({"claude-haiku-4-5-20251001": _accepte_tout,
                         "claude-haiku-5-5": _retire,
                         "claude-sonnet-4-6": _accepte_tout,
                         "claude-sonnet-5-5": _accepte_tout})
        bilan = sm.executer([AVIS], self.MODELES, plafond_usd=10, appel=api, pause=0)
        self.assertNotIn("volume_candidat", bilan["resultats"][0]["par_role"])
        self.assertEqual(sm.rapport(bilan, self.MODELES)["par_paire"][0]["avis_communs"], 0)

    def test_plafond_arrete_les_appels(self):
        api = FausseAPI({m: _accepte_tout for m in self.MODELES.values()})
        bilan = sm.executer([AVIS] * 10, self.MODELES, plafond_usd=0.001, appel=api, pause=0)
        self.assertTrue(bilan["plafond_atteint"])
        self.assertLess(len(bilan["resultats"]), 10)

    def test_erreur_http_comptee(self):
        def erreur(temperature, prompt):
            if "Avis" not in prompt:
                return _reponse('{"ok": true}')
            return {"code": 529, "charge": None, "corps_texte": "overloaded", "latence_s": 0.1}
        ligne = sm.analyser_avis(AVIS, "claude-haiku-5-5", 0, appel=FausseAPI({"claude-haiku-5-5": erreur}))
        self.assertIsNone(ligne["extraction"])
        self.assertIn("529", ligne["erreur"])

    def test_troncature_detectee(self):
        def tronque(temperature, prompt):
            return _reponse('{"deploiement_terrain_reel": tr', stop="max_tokens")
        ligne = sm.analyser_avis(AVIS, "claude-haiku-5-5", 0, appel=FausseAPI({"claude-haiku-5-5": tronque}))
        self.assertEqual(ligne["stop_reason"], "max_tokens")
        self.assertEqual(ligne["niveau_parsing"], "echec")


class TestConfiguration(unittest.TestCase):
    def test_defauts_suivent_la_production(self):
        m = sm.modeles_configures({})
        self.assertEqual(m["volume_actuel"], ted.MODELE)
        self.assertEqual(m["escalade_actuelle"], ted.MODELE_RAFFINEMENT)
        self.assertEqual(m["volume_candidat"], "claude-haiku-5-5")

    def test_surcharge(self):
        m = sm.modeles_configures({"SONDE_VOLUME_CANDIDAT": "x"})
        self.assertEqual(m["volume_candidat"], "x")


if __name__ == "__main__":
    unittest.main()
