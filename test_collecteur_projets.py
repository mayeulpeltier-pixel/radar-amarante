# -*- coding: utf-8 -*-
"""Collecteur Project Intelligence : tout est teste OFFLINE (fetch et appel LLM
injectes). On verrouille surtout ce qui protege le budget et la fiabilite :
pre-filtres deterministes, dedup contre la memoire, parsing LLM tolerant,
disjoncteur, plafond de lots."""

import datetime
import json
import unittest

import collecteur_projets as cp
import projets as pj
import projets_reference as ref
import ted_complet_v14 as ted


AUJ = datetime.datetime(2026, 8, 22, tzinfo=datetime.timezone.utc)
INGA = ref.projet_par_id("INGA3_COD")


def art(titre, date="Mon, 18 Aug 2026 10:00:00 +0000", resume="", lien=None):
    return {"titre": titre, "date": date, "resume": resume,
            "lien": lien or "http://x/" + str(abs(hash(titre)) % 99999)}


class TestRequetes(unittest.TestCase):

    def test_requetes_sur_alias_forts_seulement(self):
        r = cp.requetes_du_projet(INGA)
        self.assertTrue(r)
        joint = " ".join(r)
        self.assertIn("inga 3", joint)
        # "inga" seul (alias faible) ne doit JAMAIS devenir une requete :
        # il ramenerait des homonymes.
        self.assertNotIn('"inga"', joint)

    def test_rotation_bornee_et_circulaire(self):
        reg = [{"project_id": str(i)} for i in range(5)]
        self.assertEqual([p["project_id"] for p in cp.projets_du_run(reg, 0, 2)],
                         ["0", "1"])
        self.assertEqual([p["project_id"] for p in cp.projets_du_run(reg, 4, 3)],
                         ["4", "0", "1"])

    def test_registre_vide_ne_plante_pas(self):
        self.assertEqual(cp.projets_du_run([], 0, 3), [])


class TestPreFiltres(unittest.TestCase):
    """Ce qui protege le budget LLM."""

    def test_article_ancien_ecarte_hors_backfill(self):
        vieux = art("Inga 3 avance", date="Mon, 01 Jan 2024 10:00:00 +0000")
        self.assertFalse(cp.signal_retenu(vieux, aujourd=AUJ, backfill=False))

    def test_article_ancien_retenu_en_backfill(self):
        vieux = art("Inga 3 avance", date="Mon, 01 Jan 2024 10:00:00 +0000")
        self.assertTrue(cp.signal_retenu(vieux, aujourd=AUJ, backfill=True))

    def test_dedup_contre_la_memoire(self):
        a = art("World Bank approves $250m for Inga 3", lien="http://m/1")
        import bitd_signaux as bitd
        deja = [bitd.id_article("http://m/1")]
        self.assertEqual(cp.preparer_signaux([a], INGA, vus=deja, aujourd=AUJ), [])

    def test_dedup_dans_le_meme_run(self):
        a = art("Inga 3 : financement", lien="http://m/2")
        out = cp.preparer_signaux([a, dict(a)], INGA, aujourd=AUJ)
        self.assertEqual(len(out), 1)

    def test_project_id_pose_par_la_requete_ciblee(self):
        out = cp.preparer_signaux([art("Titre sans alias du projet")], INGA,
                                  aujourd=AUJ)
        self.assertEqual(out[0]["project_id"], "INGA3_COD")

    def test_article_sans_lien_ignore(self):
        self.assertEqual(
            cp.preparer_signaux([{"titre": "x", "lien": ""}], INGA, aujourd=AUJ), [])

    def test_date_convertie_en_iso(self):
        out = cp.preparer_signaux([art("Inga 3")], INGA, aujourd=AUJ)
        self.assertEqual(out[0]["date"], "2026-08-18")

    def test_date_illisible_donne_vide(self):
        out = cp.preparer_signaux([art("Inga 3", date="pas une date")], INGA,
                                  aujourd=AUJ)
        self.assertEqual(out[0]["date"], "")


class TestPromptEtParsing(unittest.TestCase):

    def test_prompt_contient_les_phases_et_les_items(self):
        p = cp.construire_prompt_lot([{"titre": "AECOM selected for Inga studies"}])
        self.assertIn("CONSULTANT_SELECTION", p)
        self.assertIn("AECOM selected", p)

    def test_parsing_nominal(self):
        rep = '[{"n":1,"phase":"FID","acteurs":["Shell"]},' \
              ' {"n":2,"phase":"","acteurs":[]}]'
        out = cp.parser_reponse_lot(rep, 2)
        self.assertEqual(out[0]["phase"], "FID")
        self.assertEqual(out[0]["acteurs"], ["shell"])
        self.assertEqual(out[1]["phase"], "")

    def test_parsing_tolere_le_bavardage_autour_du_json(self):
        rep = 'Voici le resultat :\n[{"n":1,"phase":"FEASIBILITY"}]\nVoila.'
        self.assertEqual(cp.parser_reponse_lot(rep, 1)[0]["phase"], "FEASIBILITY")

    def test_phase_inconnue_devient_vide(self):
        rep = '[{"n":1,"phase":"PHASE_IMAGINAIRE"}]'
        self.assertEqual(cp.parser_reponse_lot(rep, 1)[0]["phase"], "")

    def test_json_casse_ne_perd_pas_le_lot(self):
        out = cp.parser_reponse_lot("{{ casse", 3)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(o["phase"] == "" for o in out))

    def test_reponse_partielle_complete_le_reste(self):
        out = cp.parser_reponse_lot('[{"n":2,"phase":"FID"}]', 3)
        self.assertEqual([o["phase"] for o in out], ["", "FID", ""])

    def test_indice_hors_bornes_ignore(self):
        out = cp.parser_reponse_lot('[{"n":99,"phase":"FID"}]', 2)
        self.assertTrue(all(o["phase"] == "" for o in out))


class TestClassementParLots(unittest.TestCase):

    def setUp(self):
        cp.PAUSE = 0.0
        ted.STATS_LLM["arret"] = ""

    def tearDown(self):
        ted.STATS_LLM["arret"] = ""

    def _signaux(self, n):
        return [{"titre": "Signal {}".format(i), "resume": "", "date": "2026-08-01",
                 "lien": "http://x/{}".format(i), "project_id": "INGA3_COD",
                 "phase": ""} for i in range(n)]

    def test_lots_de_dix(self):
        appels = []

        def appel(prompt):
            appels.append(prompt)
            return json.dumps([{"n": i + 1, "phase": "FID"} for i in range(10)])

        cp.TAILLE_LOT = 10
        out, lots = cp.classer_lots(self._signaux(25), appel=appel)
        self.assertEqual(lots, 3)            # 10 + 10 + 5
        self.assertEqual(len(appels), 3)
        self.assertEqual(out[0]["phase"], "FID")

    def test_plafond_de_lots_respecte(self):
        appel = lambda p: '[{"n":1,"phase":"FID"}]'
        _, lots = cp.classer_lots(self._signaux(50), appel=appel, max_lots=2)
        self.assertEqual(lots, 2)

    def test_disjoncteur_interrompt(self):
        ted.STATS_LLM["arret"] = "credit balance too low"
        _, lots = cp.classer_lots(self._signaux(30), appel=lambda p: "[]")
        self.assertEqual(lots, 0)

    def test_lot_en_erreur_nannule_pas_les_autres(self):
        etat = {"n": 0}

        def appel(prompt):
            etat["n"] += 1
            if etat["n"] == 1:
                raise RuntimeError("503")
            return json.dumps([{"n": i + 1, "phase": "FID"} for i in range(10)])

        cp.TAILLE_LOT = 10
        out, lots = cp.classer_lots(self._signaux(20), appel=appel)
        self.assertEqual(lots, 2)
        self.assertEqual(out[0]["phase"], "")      # 1er lot perdu
        self.assertEqual(out[10]["phase"], "FID")  # 2e lot classe

    def test_entree_non_modifiee(self):
        src = self._signaux(2)
        cp.classer_lots(src, appel=lambda p: '[{"n":1,"phase":"FID"}]')
        self.assertEqual(src[0]["phase"], "")      # copie, pas mutation


class TestCollecteInjectee(unittest.TestCase):

    def test_collecte_avec_fetch_injecte(self):
        cp.PAUSE = 0.0
        xml = (u"<?xml version='1.0'?><rss><channel><item>"
               u"<title>AECOM selected for Inga studies</title>"
               u"<link>http://x/1</link>"
               u"<pubDate>Wed, 12 Aug 2026 10:00:00 +0000</pubDate>"
               u"<description>Consultant appointed.</description>"
               u"</item></channel></rss>")
        articles = cp.collecter_projet(INGA, fetch=lambda url: xml)
        self.assertTrue(articles)
        signaux = cp.preparer_signaux(articles, INGA, aujourd=AUJ)
        self.assertEqual(len(signaux), 1)          # dedup par lien

    def test_requete_en_erreur_ne_bloque_pas(self):
        cp.PAUSE = 0.0

        def fetch(url):
            raise RuntimeError("403")

        self.assertEqual(cp.collecter_projet(INGA, fetch=fetch), [])


class TestSortie(unittest.TestCase):

    def _projet(self):
        signaux = [
            {"titre": "World Bank approves $250m for Inga 3", "date": "2025-06-03",
             "phase": "FUNDING_APPROVED", "lien": "http://a"},
            {"titre": "AECOM selected for Inga studies", "date": "2026-04-15",
             "phase": "CONSULTANT_SELECTION", "lien": "http://b"},
        ]
        return pj.construire_projets(signaux, aujourd=datetime.date(2026, 8, 22))[0]

    def test_ligne_respecte_l_ordre_des_colonnes(self):
        ligne = cp.ligne_depuis_projet(self._projet())
        self.assertEqual(len(ligne), len(cp.COLONNES))
        self.assertEqual(ligne[cp.COLONNES.index("project_id")], "INGA3_COD")

    def test_ligne_porte_les_deux_scores_distincts(self):
        p = self._projet()
        ligne = cp.ligne_depuis_projet(p)
        self.assertEqual(ligne[cp.COLONNES.index("maturite")], str(p["maturite"]))
        self.assertEqual(ligne[cp.COLONNES.index("opportunite")],
                         str(p["opportunite"]["score"]))

    def test_timeline_serialisee_en_json_valide(self):
        ligne = cp.ligne_depuis_projet(self._projet())
        data = json.loads(ligne[cp.COLONNES.index("timeline_json")])
        self.assertEqual([b["annee"] for b in data], ["2025", "2026"])

    def test_prospects_dans_la_ligne(self):
        ligne = cp.ligne_depuis_projet(self._projet())
        self.assertIn("aecom", ligne[cp.COLONNES.index("prospects")])


class TestGardeFouActivation(unittest.TestCase):

    def test_desactive_par_defaut(self):
        # Chantier neuf : OFF tant qu'il n'est pas valide en production.
        self.assertFalse(cp.ACTIVER or os.environ.get("RADAR_PROJETS") == "1")


import os  # noqa: E402  (utilise par le dernier test)

if __name__ == "__main__":
    unittest.main()
