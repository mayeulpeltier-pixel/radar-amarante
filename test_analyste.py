"""Tests de la couche Analyste (sans reseau, sans base, sans cle API)."""

import datetime
import json
import os
import unittest
from unittest import mock

import analyste as an
import ted_complet_v14 as ted

AUJ = datetime.date(2026, 11, 6)


def _lead(**k):
    base = {"src": "AFDB", "pub": "p1", "titre": "AGPM - RDC - Projet de Réponse à la Crise PRECAPE",
            "agence": "African Development Bank", "pays": "RDC", "final": 7.5,
            "statut": "nouveau", "date_det": "2026-11-01", "deadline": "",
            "justif": "Zone de conflit.", "lien": "https://afdb.org/x"}
    base.update(k)
    return base


FICHE = {"en_une_phrase": "PRECAPE (cite index=\"22-4\">49 M USD</cite> à Uvira.",
         "pourquoi_maintenant": "Lancé le 29/10.", "acheteur": "FSRDC",
         "qui_deploiera": [{"acteur": "Fonds social de la RDC", "source": "https://afdb.org/precape"},
                           {"acteur": "GOPA Consultants", "source": "https://invente"},
                           {"acteur": "Bureau d'études européen", "source": ""},
                           {"acteur": "SOGEA SATOM", "source": "avis"}],
         "besoin_surete": ["escorte"], "concurrence": "inconnu", "angle_amarante": "sous-traitance",
         "prochaine_action": {"action": "Lire le PPM", "cible": "FSRDC", "avant_le": ""},
         "a_verifier": ["lots"], "verdict": "surveiller", "confiance": 0.6}


def _reponse(fiche=FICHE, code=200, urls=("https://afdb.org/precape",)):
    contenu = [{"type": "server_tool_use", "name": "web_search"},
               {"type": "web_search_tool_result", "content": [{"url": u} for u in urls]},
               {"type": "text", "text": json.dumps(fiche, ensure_ascii=False)}]
    return {"code": code, "charge": {"content": contenu, "stop_reason": "end_turn",
                                     "usage": {"input_tokens": 50_000, "output_tokens": 3_000,
                                               "server_tool_use": {"web_search_requests": 3}}},
            "texte": "", "latence_s": 1.0}


class TestOpportunite(unittest.TestCase):
    def test_documents_d_un_meme_projet_regroupes(self):
        a = _lead(titre="AGPM - RDC - Projet de Réponse à la Crise en Appui aux Populations (PRECAPE)")
        b = _lead(titre="PPM - RDC - Projet de Réponse à la Crise en Appui aux Populations (simplifié)", pub="p2")
        self.assertEqual(an.cle_fiche(a), an.cle_fiche(b))

    def test_projets_differents_distincts(self):
        self.assertNotEqual(an.cle_fiche(_lead(titre="PPM - RDC - Projet cobalt")),
                            an.cle_fiche(_lead(titre="PPM - RDC - Projet eau")))
        self.assertNotEqual(an.cle_fiche(_lead(pays="RDC")), an.cle_fiche(_lead(pays="Niger")))

    def test_projet_identifie_prioritaire(self):
        self.assertEqual(an.cle_fiche(_lead(projet_id="P123")), "PROJ:P123")


class TestSelection(unittest.TestCase):
    def test_une_fiche_par_opportunite_et_memoire(self):
        leads = [_lead(pub="a", final=7.5),
                 _lead(pub="b", final=7.0, titre="PPM - RDC - Projet de Réponse à la Crise PRECAPE"),
                 _lead(pub="c", final=6.0, titre="AON - Niger - Route", pays="Niger"),
                 _lead(pub="d", final=9.0, titre="Avis clos", deadline="2026-10-01"),
                 _lead(pub="e", final=9.0, titre="Ecarte", statut="non_pertinent"),
                 _lead(pub="f", final=9.0, titre="Prive", src="PRIVÉ"),
                 _lead(pub="g", final=9.0, titre="Vieux", date_det="2026-06-01")]
        s = an.selectionner(leads, 10, set(), aujourd=AUJ)
        self.assertEqual([l["pub"] for _, l in s], ["a", "c"])
        deja = {an.cle_fiche(leads[0])}
        self.assertEqual([l["pub"] for _, l in an.selectionner(leads, 10, deja, aujourd=AUJ)], ["c"])
        self.assertEqual(len(an.selectionner(leads, 1, set(), aujourd=AUJ)), 1)


class TestDoctrine(unittest.TestCase):
    def test_vrais_prospects_ou_rien(self):
        f = an.prospects_reels(FICHE, ["https://afdb.org/precape"], "Le titulaire SOGEA SATOM réalisera")
        self.assertEqual([a["acteur"] for a in f["qui_deploiera"]],
                         ["Fonds social de la RDC", "SOGEA SATOM"])

    def test_avis_invoque_sans_le_nom_dans_l_avis(self):
        f = an.prospects_reels(FICHE, [], "texte sans nom")
        self.assertEqual(f["qui_deploiera"], [])

    def test_prompt_exige_des_prospects_reels(self):
        self.assertIn("QUE des prospects réels", an.PROMPT)
        self.assertIn("liste vide", an.PROMPT)

    def test_aucune_lecture_des_soumissionnaires_probables(self):
        import inspect
        src = inspect.getsource(an)
        self.assertNotIn("import candidats_probables", src)
        self.assertNotIn("soumissionnaires_probables(", src)

    def test_nettoyage_des_citations(self):
        self.assertEqual(an.nettoyer(FICHE)["en_une_phrase"], "PRECAPE 49 M USD à Uvira.")
        self.assertEqual(an.nettoyer({"a": ["<cite index='1-2'>x</cite>"]}), {"a": ["x"]})


class TestAppel(unittest.TestCase):
    def test_corps_constructeur_unique_et_web(self):
        c = an.corps("claude-sonnet-5-5", "p")
        self.assertNotIn("temperature", c)
        self.assertEqual(c["tools"][0]["name"], "web_search")
        self.assertEqual(c["max_tokens"], 2500 + ted.MARGE_RAISONNEMENT)

    def test_production_complete(self):
        ligne, usd, msg = an.produire("TITRE:rdc:x", _lead(), "claude-sonnet-5-5",
                                      appel=lambda c: _reponse(), aujourd=AUJ)
        self.assertEqual(msg, "ok")
        self.assertEqual(ligne["publication_number"], "TITRE:rdc:x")
        self.assertEqual(ligne["date"], "2026-11-06")
        self.assertEqual(ligne["sources"], ["https://afdb.org/precape"])
        self.assertEqual([a["acteur"] for a in ligne["fiche"]["qui_deploiera"]], ["Fonds social de la RDC"])
        self.assertNotIn("cite", ligne["fiche"]["en_une_phrase"])
        self.assertAlmostEqual(usd, 0.1 + 0.03 + 0.03)

    def test_erreurs_ne_levent_pas(self):
        self.assertIsNone(an.produire("k", _lead(), "m", appel=lambda c: _reponse(code=400))[0])
        self.assertIsNone(an.produire("k", _lead(), "m",
                                      appel=lambda c: (_ for _ in ()).throw(RuntimeError("x")))[0])
        r = _reponse()
        r["charge"]["content"][-1]["text"] = "pas de json"
        self.assertIsNone(an.produire("k", _lead(), "m", appel=lambda c: r)[0])

    def test_plafond(self):
        sel = [("k{}".format(i), _lead(pub=str(i))) for i in range(10)]
        lignes, dep = an.executer(sel, "claude-sonnet-5-5", plafond=0.3,
                                  appel=lambda c: _reponse(), texte_notice=lambda l: "", pause=0)
        self.assertLess(len(lignes), 10)
        self.assertGreaterEqual(dep, 0.3)


class TestDrapeau(unittest.TestCase):
    def test_desactive_par_defaut(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RADAR_ANALYSTE", None)
            with mock.patch.object(an, "charger_leads", side_effect=AssertionError("ne doit pas lire")):
                self.assertEqual(an.main(), 0)

    def test_sans_cle_ne_fait_rien(self):
        with mock.patch.dict(os.environ, {"RADAR_ANALYSTE": "1", "ANTHROPIC_API_KEY": "", "DATABASE_URL": ""}):
            with mock.patch.object(an, "charger_leads", side_effect=AssertionError("ne doit pas lire")):
                self.assertEqual(an.main(), 0)


if __name__ == "__main__":
    unittest.main()
