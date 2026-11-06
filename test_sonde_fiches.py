"""Tests de la sonde fiches Analyste (sans reseau, sans base, sans cle API)."""

import datetime
import json
import unittest

import sonde_fiches as sf
import ted_complet_v14 as ted

AUJ = datetime.date(2026, 11, 6)


def _lead(**k):
    base = {"src": "TED", "pub": "1-2026", "titre": "Assistance technique Niger",
            "agence": "Commission européenne", "pays": "Niger", "zone": "Sahel",
            "sect": "Eau", "final": 6.5, "surete": 7, "comm": 6, "action": "contacter",
            "statut": "nouveau", "date_det": "2026-11-01", "deadline": "2026-12-15",
            "justif": "Experts en zone isolée.", "lien": "https://ted.europa.eu/x"}
    base.update(k)
    return base


FICHE = {"en_une_phrase": "Experts UE au Niger rural.", "pourquoi_maintenant": "Clôture 15/12.",
         "acheteur": "Commission européenne", "qui_deploiera": [
             {"acteur": "bureau d'études européen en hydraulique", "source": ""}],
         "besoin_surete": ["escorte"], "concurrence": "inconnu", "angle_amarante": "Sahel",
         "prochaine_action": {"action": "Appeler", "cible": "titulaire", "avant_le": "2026-12-01"},
         "a_verifier": ["lots"], "verdict": "poursuivre", "confiance": 0.6}


class TestSelection(unittest.TestCase):
    def test_filtres_et_tri(self):
        leads = [_lead(pub="a", final=5), _lead(pub="b", final=8),
                 _lead(pub="prive", src="PRIVÉ", final=9),
                 _lead(pub="ecarte", statut="non_pertinent", final=9),
                 _lead(pub="vieux", date_det="2026-07-01", final=9),
                 _lead(pub="clos", deadline="2026-11-01", final=9),
                 _lead(pub="sansdate", date_det="", final=9),
                 _lead(pub="sansclot", deadline="", final=7)]
        s = sf.selectionner_leads(leads, 10, aujourd=AUJ)
        self.assertEqual([l["pub"] for l in s], ["b", "sansclot", "a"])
        self.assertEqual(len(sf.selectionner_leads(leads, 1, aujourd=AUJ)), 1)

    def test_departage_deterministe(self):
        leads = [_lead(pub="z", final=6), _lead(pub="a", final=6)]
        self.assertEqual([l["pub"] for l in sf.selectionner_leads(leads, 2, aujourd=AUJ)], ["a", "z"])


class TestPrompt(unittest.TestCase):
    def test_donnees_completes(self):
        d = sf.donnees_pour_prompt(
            _lead(valeur="n.c."),
            [{"entreprise": "GAUFF", "probabilite": 60, "motifs": ["actif sur ce théâtre"], "etranger": True}],
            "TEXTE " * 3000)
        self.assertIn("Commission européenne", d)
        self.assertIn("GAUFF (probabilité 60 %", d)
        self.assertNotIn("Montant estimé", d)          # n.c. ignore
        self.assertLessEqual(d.count("TEXTE"), sf.PLAFOND_TEXTE_NOTICE // 6 + 1)

    def test_consigne_web_seulement_en_variante_b(self):
        self.assertIn("recherche web", sf.construire_prompt(_lead(), web=True))
        self.assertNotIn("recherche web", sf.construire_prompt(_lead(), web=False))
        self.assertIn("N'invente AUCUN nom", sf.construire_prompt(_lead()))

    def test_corps_passe_par_le_constructeur_unique(self):
        a = sf.corps_fiche("claude-sonnet-5-5", "p")
        self.assertNotIn("temperature", a)
        self.assertNotIn("tools", a)
        self.assertEqual(a["max_tokens"], 2500 + ted.MARGE_RAISONNEMENT)
        b = sf.corps_fiche("claude-sonnet-5-5", "p", web=True)
        self.assertEqual(b["tools"][0]["name"], "web_search")
        self.assertEqual(b["tools"][0]["max_uses"], sf.MAX_RECHERCHES)


class TestCoutEtSources(unittest.TestCase):
    def test_cout_avec_recherches(self):
        usd, n = sf.cout("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000,
                                               "server_tool_use": {"web_search_requests": 3}})
        self.assertEqual(n, 3)
        self.assertAlmostEqual(usd, 2.0 + 1.0 + 3 * sf.PRIX_RECHERCHE_USD)

    def test_cout_modele_inconnu_nul(self):
        self.assertEqual(sf.cout("x", {"input_tokens": 10}), (0.0, 0))

    def test_sources_dedoublonnees(self):
        charge = {"content": [
            {"type": "web_search_tool_result", "content": [{"url": "https://a"}, {"url": "https://b"}]},
            {"type": "text", "text": "x", "citations": [{"url": "https://a"}, {"url": "https://c"}]}]}
        self.assertEqual(sf.sources_web(charge), ["https://a", "https://b", "https://c"])
        self.assertEqual(sf.sources_web(None), [])


class TestAntiInvention(unittest.TestCase):
    def test_profil_non_signale(self):
        self.assertEqual(sf.noms_non_sources(FICHE, "", []), [])

    def test_nom_inconnu_signale(self):
        f = dict(FICHE, qui_deploiera=[{"acteur": "GOPA Consultants", "source": ""}])
        self.assertEqual(sf.noms_non_sources(f, "", []), ["GOPA Consultants"])

    def test_nom_present_dans_les_donnees_ou_source_consultee(self):
        f = dict(FICHE, qui_deploiera=[{"acteur": "GOPA Consultants", "source": ""},
                                       {"acteur": "Louis Berger", "source": "https://x"}])
        self.assertEqual(sf.noms_non_sources(f, "gopa consultants", ["https://x"]), [])

    def test_url_citee_mais_non_consultee(self):
        f = dict(FICHE, qui_deploiera=[{"acteur": "Louis Berger", "source": "https://invente"}])
        self.assertEqual(sf.noms_non_sources(f, "", ["https://x"]), ["Louis Berger"])


def _reponse(texte, usage=None, code=200, blocs_avant=None):
    contenu = list(blocs_avant or []) + [{"type": "text", "text": texte}]
    return {"code": code, "charge": {"content": contenu, "usage": usage or
                                     {"input_tokens": 5000, "output_tokens": 1500},
                                     "stop_reason": "end_turn"},
            "texte": "", "latence_s": 1.0}


class TestProduction(unittest.TestCase):
    def test_fiche_ok(self):
        f = sf.produire_fiche(_lead(), "claude-sonnet-5-5",
                              appel=lambda corps: _reponse("```json\n" + json.dumps(FICHE) + "\n```"))
        self.assertEqual(f["fiche"]["verdict"], "poursuivre")
        self.assertEqual(f["variante"], "A_radar")
        self.assertGreater(f["cout_usd"], 0)

    def test_erreur_http_rapportee(self):
        f = sf.produire_fiche(_lead(), "claude-sonnet-5-5", web=True,
                              appel=lambda corps: {"code": 400, "charge": None,
                                                   "texte": "tool type invalid", "latence_s": 0})
        self.assertIsNone(f["fiche"])
        self.assertIn("400", f["erreur"])
        self.assertEqual(f["variante"], "B_web")

    def test_json_illisible(self):
        f = sf.produire_fiche(_lead(), "claude-sonnet-5-5", appel=lambda corps: _reponse("pas de json"))
        self.assertIsNone(f["fiche"])
        self.assertIn("JSON", f["erreur"])

    def test_execution_deux_variantes_et_plafond(self):
        appels = []

        def faux(corps):
            appels.append("tools" in corps)
            return _reponse(json.dumps(FICHE), {"input_tokens": 100_000, "output_tokens": 10_000,
                                                "server_tool_use": {"web_search_requests": 2}})
        b = sf.executer([_lead(pub="1"), _lead(pub="2")], [], "claude-sonnet-5-5", plafond=10,
                        appel=faux, texte_notice=lambda l: "", candidats_pour=lambda l: [], pause=0)
        self.assertEqual(appels, [False, True, False, True])
        s = sf.synthese(b)
        self.assertEqual(s["A_radar"]["ok"], 2)
        self.assertEqual(s["B_web"]["recherches"], 4)
        coupe = sf.executer([_lead()] * 5, [], "claude-sonnet-5-5", plafond=0.5, appel=faux,
                            texte_notice=lambda l: "", candidats_pour=lambda l: [], pause=0)
        self.assertTrue(coupe["plafond_atteint"])
        self.assertLess(sum(len(e["fiches"]) for e in coupe["resultats"]), 10)

    def test_contexte_indisponible_ne_casse_pas(self):
        def boum(lead):
            raise RuntimeError("reseau")
        b = sf.executer([_lead()], [], "claude-sonnet-5-5", plafond=10,
                        appel=lambda c: _reponse(json.dumps(FICHE)),
                        texte_notice=boum, candidats_pour=boum, pause=0)
        self.assertEqual(len(b["resultats"][0]["fiches"]), 2)

    def test_aucun_soumissionnaire_probable_par_defaut(self):
        """Doctrine du 06/11 : soit de vrais prospects, soit rien. Plus de
        liste hypothetique tiree de la base dans le prompt."""
        prompts = []

        def faux(corps):
            prompts.append(corps["messages"][0]["content"])
            return _reponse(json.dumps(FICHE))
        from unittest import mock
        import candidats_probables as cp
        piege = [{"entreprise": "NEGOCIANT FANTOME", "probabilite": 60, "motifs": []}]
        with mock.patch.object(cp, "soumissionnaires_probables", return_value=piege), \
                mock.patch.object(cp, "historique_titulaires", return_value={}):
            b = sf.executer([_lead()], [_lead(pub="autre")], "claude-sonnet-5-5", plafond=10,
                            appel=faux, texte_notice=lambda l: "", pause=0)
        self.assertEqual(b["resultats"][0]["candidats"], [])
        self.assertTrue(all("NEGOCIANT FANTOME" not in p for p in prompts))

    def test_prompt_exige_des_prospects_reels(self):
        p = sf.construire_prompt(_lead())
        self.assertIn("QUE des prospects réels", p)
        self.assertIn("liste vide", p)
        self.assertNotIn("nom ou profil", p)

    def test_markdown(self):
        md = sf.fiche_markdown(FICHE, "t")
        self.assertIn("**Prochaine action** : Appeler (titulaire, avant le 2026-12-01)", md)
        self.assertEqual(sf.fiche_markdown(None, "t"), "_Fiche indisponible._")


if __name__ == "__main__":
    unittest.main()
