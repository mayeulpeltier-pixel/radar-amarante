# -*- coding: utf-8 -*-
"""Sonde BOAMP IE (IE-0) : le COEUR (lexique, classement, extraction CPV,
rappel, verdict) est teste offline, sur les intitules reels des marches de
reference (AURA 24-80762, Nice 24-99592, observatoire du ministere des
Armees 26-24709). Le prober est exerce avec un fetch injecte : aucun acces
reseau."""

import json
import os
import shutil
import tempfile
import unittest

import sonde_boamp_ie as s


def rec(idweb, objet, nature="Avis de marché", donnees=None, titulaire=None,
        acheteur="Acheteur test", date="2026-05-04"):
    r = {"idweb": idweb, "objet": objet, "nature_libelle": nature,
         "nomacheteur": acheteur, "dateparution": date}
    if donnees is not None:
        r["donnees"] = donnees
    if titulaire is not None:
        r["titulaire"] = titulaire
    return r


def classe_de(objet, corps=""):
    return s.classer(s.normaliser_texte(objet),
                     s.normaliser_texte(objet + " " + corps))


class TestLexique(unittest.TestCase):

    def test_insensible_aux_accents_et_a_la_casse(self):
        n = s.normaliser_texte("Prestations d’INTELLIGENCE ÉCONOMIQUE")
        self.assertIn(("intelligence economique", "fort"), s.termes_trouves(n))

    def test_mot_entier_seulement(self):
        self.assertEqual(s.termes_trouves("calcul du cosinus"), [])
        self.assertIn(("osint", "fort"), s.termes_trouves("rapports osint"))

    def test_sapin_seul_ne_suffit_pas(self):
        self.assertEqual(s.termes_trouves("abattage d'un sapin"), [])
        self.assertIn(("sapin ii", "fort"),
                      s.termes_trouves(s.normaliser_texte("audit loi Sapin II")))


class TestClassementSurLesReferences(unittest.TestCase):

    def test_aura_cellule_intelligence_strategique(self):
        self.assertEqual(classe_de("Cellule d'Intelligence Stratégique"),
                         "fort_objet")

    def test_nice_prestations_ie(self):
        self.assertEqual(classe_de("24N0423 - Prestations d'intelligence économique"),
                         "fort_objet")

    def test_observatoire_armees_hors_perimetre(self):
        # Decision D1 : reserve a la future lentille Analyste.
        self.assertEqual(
            classe_de("Observatoire du multilatéralisme en Indopacifique",
                      "veille stratégique sur la zone"),
            "hors_analyste")

    def test_observatoire_securite_economique_reste_ie(self):
        # Pas d'exclusion par mot-cle : le noyau IE l'emporte.
        self.assertEqual(classe_de("Observatoire de la sécurité économique"),
                         "fort_objet")

    def test_sondage_touristique_est_du_bruit(self):
        self.assertEqual(
            classe_de("Enquête de satisfaction auprès des touristes",
                      "veille économique du territoire"),
            "bruit")

    def test_terme_fort_dans_le_corps_seulement(self):
        self.assertEqual(
            classe_de("Accompagnement des PME à l'export",
                      "prestations de veille stratégique et due diligence"),
            "fort_corps")

    def test_terme_ambigu_seulement(self):
        self.assertEqual(classe_de("Logiciel de veille technologique"), "a_mesurer")

    def test_aucun_terme(self):
        self.assertEqual(classe_de("Fourniture de papier"), "sans_terme")


class TestExtraction(unittest.TestCase):

    def setUp(self):
        self._div = s.DIVISIONS_CPV
        s.DIVISIONS_CPV = {"79", "73", "75"}

    def tearDown(self):
        s.DIVISIONS_CPV = self._div

    def test_cpv_texte_pdf(self):
        t = "Nomenclature principale ( cpv ): 79311000 Services d'études"
        self.assertEqual(s.extraire_cpv(t), ["79311000"])

    def test_cpv_eforms_code_avant_listname(self):
        d = json.dumps({"ItemClassificationCode": {"#text": "79996000",
                                                   "@listName": "cpv"}})
        self.assertEqual(s.extraire_cpv(d), ["79996000"])

    def test_siret_et_division_invalide_ignores(self):
        t = "SIRET 50987922700023 cpv 12345678 ; cpv 73000000"
        self.assertEqual(s.extraire_cpv(t), ["73000000"])

    def test_donnees_chaine_json_rendues_lisibles(self):
        brut = json.dumps({"objet": "Due diligence étendue"})
        self.assertIn("étendue", s.texte_donnees(brut))

    def test_normaliser_resultat_et_amarante(self):
        a = s.normaliser(rec("24-80762", "Cellule d'Intelligence Stratégique",
                             nature="Résultat de marché",
                             titulaire="AMARANTE RISK INTELLIGENCE"))
        self.assertTrue(a["resultat"])
        self.assertTrue(a["amarante"])
        self.assertEqual(a["classe"], "fort_objet")
        self.assertIn("24-80762", a["lien"])

    def test_champs_absents_sans_exception(self):
        a = s.normaliser({})
        self.assertEqual(a["idweb"], "")
        self.assertEqual(a["classe"], "sans_terme")


class TestCollecteInjectee(unittest.TestCase):

    def setUp(self):
        s.PAUSE = 0.0
        import datetime
        self.depuis = datetime.date(2024, 3, 1)

    def test_where_odsql(self):
        w = s.construire_where("due diligence", self.depuis)
        self.assertEqual(w, "\"due diligence\" AND dateparution >= date'2024-03-01'")

    def test_dedoublonnage_entre_termes_et_champs(self):
        termes = [("intelligence économique", "intelligence economique", "fort"),
                  ("veille stratégique", "veille strategique", "fort")]
        commun = rec("24-99592", "Prestations d'intelligence économique")

        def fetch(url, params):
            return {"total_count": 1, "results": [commun]}

        c = s.collecter(self.depuis, fetch=fetch, termes=termes)
        self.assertEqual(len(c["avis"]), 1)
        self.assertIn("idweb", c["champs"])
        self.assertEqual(c["par_terme"]["veille stratégique"]["total"], 1)

    def test_pagination_respecte_le_plafond(self):
        appels = []

        def fetch(url, params):
            appels.append(params["offset"])
            base = params["offset"]
            return {"total_count": 500,
                    "results": [rec("26-{}".format(base + i), "Veille stratégique")
                                for i in range(params["limit"])]}

        termes = [("veille stratégique", "veille strategique", "fort")]
        c = s.collecter(self.depuis, fetch=fetch, termes=termes, max_par_terme=250)
        self.assertEqual(len(c["avis"]), 250)
        self.assertEqual(appels, [0, 100, 200])

    def test_terme_en_erreur_n_interrompt_pas(self):
        termes = [("osint", "osint", "fort"),
                  ("due diligence", "due diligence", "fort")]

        def fetch(url, params):
            if "osint" in params["where"]:
                raise RuntimeError("HTTP 400")
            return {"total_count": 1, "results": [rec("26-1", "Due diligence")]}

        c = s.collecter(self.depuis, fetch=fetch, termes=termes)
        self.assertEqual(len(c["erreurs"]), 1)
        self.assertEqual(len(c["avis"]), 1)


class TestRappel(unittest.TestCase):

    def test_reference_captee_sans_appel_reseau(self):
        avis = [s.normaliser(rec("24-80762", "Cellule d'Intelligence Stratégique"))]

        def fetch(url, params):
            raise AssertionError("aucun appel attendu")

        r = s.evaluer_rappel(avis, fetch=fetch,
                             references={"24-80762": "AURA"})
        self.assertTrue(r["24-80762"]["captee"])

    def test_reference_manquee_diagnostiquee(self):
        def fetch(url, params):
            self.assertIn("24-99592", params["where"])
            return {"results": [rec("24-99592", "Mission d'aide à la décision")]}

        r = s.evaluer_rappel([], fetch=fetch, references={"24-99592": "Nice"})
        self.assertTrue(r["24-99592"]["existe"])
        self.assertFalse(r["24-99592"]["captee"])
        self.assertEqual(r["24-99592"]["classe"], "sans_terme")

    def test_verification_directe_en_echec(self):
        def fetch(url, params):
            raise RuntimeError("503")

        r = s.evaluer_rappel([], fetch=fetch, references={"24-1": "x"})
        self.assertIsNone(r["24-1"]["existe"])
        self.assertIn("erreur", r["24-1"])


class TestVerdict(unittest.TestCase):

    def _rap(self, nb_titre, mois=10, resultat=False):
        avis = [s.normaliser(rec("26-{}".format(i), "Prestations d'intelligence économique",
                                 nature="Résultat de marché" if resultat else "Avis de marché"))
                for i in range(nb_titre)]
        return s.analyser(avis, mois=mois)

    def _rappel(self, existe=True, captee=True):
        return {"x": {"libelle": "x", "existe": existe, "captee": captee}}

    def test_echantillon_vide_nest_pas_un_verdict(self):
        rap = s.analyser([], mois=10)
        self.assertEqual(s.verdict(rap, {}, erreurs=["503"])[0], "NON CONCLU")
        self.assertEqual(s.verdict(rap, {})[0], "NON CONCLU")

    def test_reference_existante_non_captee(self):
        self.assertEqual(s.verdict(self._rap(30), self._rappel(captee=False))[0],
                         "LEXIQUE A REVOIR")

    def test_faisable(self):
        self.assertEqual(s.verdict(self._rap(20), self._rappel())[0], "FAISABLE")

    def test_marginal(self):
        self.assertEqual(s.verdict(self._rap(5), self._rappel())[0], "MARGINAL")

    def test_infaisable_si_seulement_des_resultats(self):
        self.assertEqual(s.verdict(self._rap(3, resultat=True), self._rappel())[0],
                         "INFAISABLE")

    def test_titulaires_comptes_sur_les_resultats(self):
        avis = [s.normaliser(rec("24-80762", "Cellule d'Intelligence Stratégique",
                                 nature="Résultat de marché",
                                 titulaire="AMARANTE RISK INTELLIGENCE ; CABINET X"))]
        rap = s.analyser(avis, mois=10)
        self.assertEqual(rap["titulaires"]["AMARANTE RISK INTELLIGENCE"], 1)
        self.assertEqual(len(rap["amarante"]), 1)


class TestSorties(unittest.TestCase):

    def test_rapport_et_csv(self):
        import datetime
        avis = [s.normaliser(rec("24-99592", "Prestations d'intelligence économique"))]
        collecte = {"avis": avis, "par_terme": {}, "erreurs": [], "champs": ["idweb"]}
        rap = s.analyser(avis, mois=10)
        rappel = {"24-99592": {"libelle": "Nice", "existe": True, "captee": True,
                               "classe": "fort_objet", "objet": avis[0]["objet"]}}
        v = s.verdict(rap, rappel)
        md = s.rapport_markdown(collecte, rap, rappel, v,
                                datetime.date(2024, 3, 1), datetime.date(2026, 10, 30))
        self.assertIn("## Verdict : MARGINAL", md)
        self.assertIn("24-99592", md)
        d = tempfile.mkdtemp()
        try:
            chemin = os.path.join(d, "avis.csv")
            s.ecrire_csv(avis, chemin)
            with open(chemin, encoding="utf-8") as f:
                lignes = f.read().splitlines()
            self.assertEqual(len(lignes), 2)
            self.assertTrue(lignes[0].startswith("idweb;"))
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
