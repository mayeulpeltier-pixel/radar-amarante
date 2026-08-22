# -*- coding: utf-8 -*-
"""Sonde Project Intelligence : le COEUR (grille cycle de vie, resolution
d'alias projet, agregation, verdict) est teste offline, sur les articles
exacts cites dans le cahier des charges (Inga 3, Tanzania LNG). Le prober
est exerce avec un fetch injecte : aucun acces reseau."""

import datetime
import unittest

import sonde_projets as s


def art(titre, resume="", date="", lien=None):
    return {"titre": titre, "resume": resume, "date": date,
            "lien": lien or ("http://x/" + str(abs(hash(titre)) % 99999))}


class TestGrilleCycleDeVie(unittest.TestCase):

    def test_phases_des_articles_du_cahier_des_charges(self):
        cas = [
            ("World Bank approves $250m for Inga 3", "FUNDING"),
            ("AECOM selected for Inga studies", "CONSULTANT_SELECTION"),
            ("DRC government approves Grand Inga law", "POLITICAL_ANNOUNCEMENT"),
            ("Tanzania and Shell sign host government agreement",
             "GOVERNMENT_AGREEMENT"),
            ("Tanzania LNG final investment decision expected", "FID"),
            ("Feasibility study launched for Lindi LNG", "FEASIBILITY"),
        ]
        for titre, attendu in cas:
            self.assertEqual(s.phase_de_article(art(titre)), attendu, titre)

    def test_article_sans_expression_cycle_de_vie(self):
        self.assertEqual(s.phase_de_article(art("Inga 3 : le debat continue")), "")

    def test_expression_longue_prioritaire_sur_courte(self):
        # "EPC contract" ne doit pas etre avale par une expression plus courte.
        self.assertEqual(s.phase_de_article(art("Firm wins EPC contract")), "EPC")

    def test_insensible_aux_accents_et_a_la_casse(self):
        self.assertEqual(
            s.phase_de_article(art("ÉTUDE DE FAISABILITÉ lancée")), "FEASIBILITY")


class TestResolutionAliasProjet(unittest.TestCase):

    def setUp(self):
        self.inga = s.CAS["inga"]
        self.tz = s.CAS["tanzanie"]

    def test_variantes_du_meme_projet_rattachent(self):
        for titre in ("World Bank approves $250m for Inga 3",
                      "DRC government approves Grand Inga law",
                      "Inga III hydropower moves forward",
                      "Le barrage Inga relance le corridor"):
            self.assertTrue(s.rattache_au_projet(art(titre), self.inga), titre)

    def test_projet_different_ne_rattache_pas(self):
        self.assertFalse(
            s.rattache_au_projet(art("Tanzania LNG final investment decision"),
                                 self.inga))

    def test_tanzania_lng_rattache(self):
        self.assertTrue(
            s.rattache_au_projet(art("Lindi LNG site works begin"), self.tz))

    def test_acteurs_detectes(self):
        a = art("AECOM selected for Inga studies, World Bank backs project")
        self.assertEqual(s.acteurs_cites(a, self.inga), ["aecom", "world bank"])


class TestAgregationEtVerdict(unittest.TestCase):

    def setUp(self):
        self.inga = s.CAS["inga"]
        self.ref = datetime.datetime(2026, 8, 22,
                                     tzinfo=datetime.timezone.utc)

    def _articles(self):
        return [
            art("World Bank approves $250m for Inga 3", date="Mon, 03 Jun 2025 10:00:00 +0000"),
            art("DRC signs agreement with AFD on Inga 3", date="Tue, 14 Apr 2026 10:00:00 +0000"),
            art("AECOM selected for Inga studies", date="Wed, 10 Jun 2026 10:00:00 +0000"),
            art("DRC government approves Grand Inga law", date="Thu, 06 Aug 2026 10:00:00 +0000"),
            art("Manchester United beat Arsenal", date="Fri, 07 Aug 2026 10:00:00 +0000"),
        ]

    def test_regroupe_les_signaux_du_meme_projet(self):
        rap = s.analyser(self._articles(), self.inga, aujourd=self.ref)
        self.assertEqual(len(rap["retenus"]), 4)   # les 4 articles Inga
        self.assertEqual(rap["bruit"], 1)          # le football ecarte

    def test_profondeur_temporelle_mesuree(self):
        rap = s.analyser(self._articles(), self.inga, aujourd=self.ref)
        # Le plus ancien (juin 2025) doit depasser un an : c'est l'enjeu meme
        # du chantier (detecter des ANNEES avant l'appel d'offres).
        self.assertGreater(rap["plus_ancien"], 365)

    def test_plusieurs_phases_distinctes(self):
        rap = s.analyser(self._articles(), self.inga, aujourd=self.ref)
        self.assertIn("FUNDING", rap["phases"])
        self.assertIn("CONSULTANT_SELECTION", rap["phases"])
        self.assertIn("POLITICAL_ANNOUNCEMENT", rap["phases"])

    def test_verdict_faisable(self):
        # 4 signaux, 3+ phases -> la matiere existe.
        arts = self._articles() + [
            art("Inga 3 feasibility study launched", date="Mon, 05 Jan 2026 10:00:00 +0000")]
        rap = s.analyser(arts, self.inga, aujourd=self.ref)
        self.assertEqual(s.verdict(rap)[0], "FAISABLE")

    def test_verdict_infaisable_si_rien(self):
        rap = s.analyser([art("Sujet sans rapport")], self.inga, aujourd=self.ref)
        self.assertEqual(s.verdict(rap)[0], "INFAISABLE")

    def test_dedup_par_lien(self):
        a = art("World Bank approves $250m for Inga 3", lien="http://m/1")
        rap = s.analyser([a, dict(a)], self.inga, aujourd=self.ref)
        self.assertEqual(len(rap["retenus"]), 1)


class TestProberInjecte(unittest.TestCase):

    def test_collecte_avec_fetch_injecte(self):
        xml = (u"<?xml version='1.0'?><rss><channel>"
               u"<item><title>AECOM selected for Inga studies</title>"
               u"<link>http://x/1</link><pubDate>Wed, 10 Jun 2026 10:00:00 +0000</pubDate>"
               u"<description>Consultant appointed for Inga 3.</description>"
               u"</item></channel></rss>")
        s.PAUSE = 0.0
        articles, erreurs = s.collecter(s.CAS["inga"], fetch=lambda url: xml)
        self.assertEqual(erreurs, [])
        self.assertTrue(articles)
        rap = s.analyser(articles, s.CAS["inga"])
        self.assertEqual(len(rap["retenus"]), 1)     # dedup par lien
        self.assertIn("CONSULTANT_SELECTION", rap["phases"])

    def test_requete_en_erreur_n_interrompt_pas(self):
        s.PAUSE = 0.0

        def fetch(url):
            raise RuntimeError("503")

        articles, erreurs = s.collecter(s.CAS["tanzanie"], fetch=fetch)
        self.assertEqual(articles, [])
        self.assertTrue(erreurs)                     # collectees, pas levees


if __name__ == "__main__":
    unittest.main()


class TestGardeFouVerdict(unittest.TestCase):
    """Lecon de la sonde ADB : un echantillon vide n'est pas un verdict."""

    def test_zero_article_collecte_nest_pas_infaisable(self):
        rap = s.analyser([], s.CAS["inga"])
        self.assertEqual(s.verdict(rap, erreurs=["503"])[0], "NON CONCLU")
        self.assertEqual(s.verdict(rap)[0], "NON CONCLU")

    def test_articles_collectes_mais_hors_sujet_est_un_verdict(self):
        rap = s.analyser([art("Recette de la tarte aux pommes")], s.CAS["inga"])
        self.assertEqual(s.verdict(rap)[0], "INFAISABLE")
