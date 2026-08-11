# -*- coding: utf-8 -*-
"""
Resolution d'entite : cle canonique UNIQUE (02/08/2026).
========================================================

CE QUE CE FICHIER VERROUILLE
----------------------------
La meme societe apparait sous des variantes de nom selon la source (TED
soumissionnaire, titulaire d'attribution, signal prive, enrichissement). La
fiche "Entreprises 360" doit la montrer UNE SEULE FOIS.

Avant : deux normalisations separees (Python `_norm_ent` pour l'enrichissement,
JS `normEntreprise` pour les fiches) qu'il fallait garder synchronisees a la
main -> risque de derive, et fusion incoherente.

Maintenant : `_norm_ent` est la SOURCE DE VERITE UNIQUE. La cle `ent_cle` est
precalculee cote Python sur chaque lead ET chaque entree de watchlist ; le JS
regroupe sur cette cle (il ne re-normalise plus, sauf repli pour pages en cache).

Proprietes testees :
  - variantes juridiques / casse / accents / connecteurs / separateurs fusionnent ;
  - des entites reellement distinctes ne fusionnent PAS ;
  - chaque lead porte ent_cle, coherent entre sources pour une meme societe ;
  - la watchlist serialisee porte ent_cle ;
  - les agregateurs JS lisent bien ent_cle.
"""

import unittest
from datetime import date

import radar_dashboard as dash


class TestCleCanonique(unittest.TestCase):
    def _k(self, s):
        return dash._norm_ent(s)

    def test_casse_et_forme_juridique_abregee(self):
        self.assertEqual(self._k("ACME Security Ltd"), self._k("acme security"))
        self.assertEqual(self._k("ACME Security SA"), self._k("Acme Security"))

    def test_forme_longue_equivaut_a_l_abregee(self):
        # Ltd == Limited, Corp == Corporation, Inc == Incorporated
        self.assertEqual(self._k("Acme Ltd"), self._k("Acme Limited"))
        self.assertEqual(self._k("Beta Corp"), self._k("Beta Corporation"))
        self.assertEqual(self._k("Gamma Inc"), self._k("Gamma Incorporated"))

    def test_formes_juridiques_pointees(self):
        # S.A. et S.A.S. (formes francaises pointees) fusionnent avec la forme nue.
        self.assertEqual(self._k("Acme Security S.A."), self._k("Acme Security"))
        self.assertEqual(self._k("Acme Security S.A.S."), self._k("Acme Security"))

    def test_accents(self):
        self.assertEqual(self._k("Sécurité Sûreté SA"), self._k("Securite Surete"))

    def test_connecteurs_et_esperluette(self):
        self.assertEqual(self._k("Acme & Co"), self._k("Acme and Co"))
        self.assertEqual(self._k("Black and Decker"), self._k("Black Decker"))

    def test_separateurs_et_prefixe_the(self):
        self.assertEqual(self._k("Foo-Bar Group"), self._k("Foo Bar"))
        self.assertEqual(self._k("The Acme Corporation"), self._k("Acme"))

    def test_entites_distinctes_ne_fusionnent_pas(self):
        self.assertNotEqual(self._k("Acme Security"), self._k("Beta Security"))
        self.assertNotEqual(self._k("Alpha Group"), self._k("Alpha Beta Group"))

    def test_vide_et_bruit(self):
        self.assertEqual(self._k(""), "")
        self.assertEqual(self._k(None), "")
        self.assertEqual(self._k("   .,()  "), "")


class TestNomEntreprise(unittest.TestCase):
    def test_entreprise_prioritaire(self):
        self.assertEqual(dash._nom_entreprise({"entreprise": "Acme", "agence": "Ministere"}), "Acme")

    def test_repli_sur_agence(self):
        self.assertEqual(dash._nom_entreprise({"entreprise": "", "agence": "Ministere"}), "Ministere")
        self.assertEqual(dash._nom_entreprise({"entreprise": "n.c.", "agence": "Ministere"}), "Ministere")


def _row_prive(nom, pub):
    return {"score_final": "6.0", "score_surete": "6.0", "score_commercial": "6.0",
            "action_recommandee": "surveiller", "fenetre_action": "court_terme",
            "titre": "Signal", "acheteur": nom, "pays_execution": "MLI",
            "justification": "j", "confiance": "0.8", "modele": "m",
            "publication_number": pub, "lien_avis": "http://x/" + pub,
            "date_detection": date.today().isoformat()}


def _row_attrib(gagnant, pub):
    return {"gagnant": gagnant, "secteur": "Securite", "pays_execution": "MLI",
            "valeur_attribuee": "2 M", "acheteur": "Agence", "publication_number": pub,
            "lien_avis": "http://a/" + pub, "date_publication": date.today().isoformat()}


class TestEntCleSurLeads(unittest.TestCase):
    def test_chaque_lead_porte_ent_cle(self):
        leads = dash.construire_leads([], [], lignes_prive=[_row_prive("Acme Security Ltd", "P:1")])
        self.assertTrue(leads)
        self.assertIn("ent_cle", leads[0])
        self.assertTrue(leads[0]["ent_cle"])

    def test_meme_societe_deux_sources_meme_cle(self):
        # Un signal PRIVE et un titulaire ATTRIB pour la meme societe sous deux
        # variantes de nom doivent partager EXACTEMENT ent_cle -> fusionnables.
        leads = dash.construire_leads(
            [], [],
            lignes_prive=[_row_prive("ACME Security Limited", "P:1")],
            lignes_attrib=[_row_attrib("Acme Security Ltd", "A:1")])
        cles = {l["src"]: l["ent_cle"] for l in leads}
        self.assertIn("PRIVÉ", cles)
        self.assertIn("ATTRIB", cles)
        self.assertEqual(cles["PRIVÉ"], cles["ATTRIB"],
                         "Meme societe -> meme ent_cle, quelle que soit la source.")


class TestCablageJs(unittest.TestCase):
    def test_watchlist_serialisee_porte_ent_cle(self):
        leads = dash.construire_leads([], [], lignes_prive=[_row_prive("Acme", "P:1")])
        html = dash.generer_html(leads, watchlist=[{"entreprise": "Acme Security Ltd", "secteur": "BTP"}])
        self.assertIn('"ent_cle"', html, "La watchlist doit exposer ent_cle au JS.")

    def test_agregateurs_lisent_ent_cle(self):
        html = dash.generer_html(dash.construire_leads([], []))
        self.assertIn("l.ent_cle||normEntreprise", html, "Les leads : cle precalculee d'abord.")
        self.assertIn("w.ent_cle||normEntreprise", html, "La watchlist : cle precalculee d'abord.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
