# -*- coding: utf-8 -*-
"""
Tri "Importance" pondere par la FRAICHEUR (02/08/2026).
=======================================================

CE QUE CE FICHIER VERROUILLE
----------------------------
Le tri par defaut ("Importance") classait par score PUR : un lead ancien un peu
mieux note enterrait un lead frais a peine moins bon. Correctif : un RANG = score
attenue par l'age, applique UNIQUEMENT au tri (le score AFFICHE reste intact).

DOCTRINE testee (cf. CLAUDE.md §7) : le SCORE domine, la fraicheur DEPARTAGE.
  - un lead frais mais FAIBLE ne double jamais un lead fort ancien ;
  - un lead frais et COMPARABLE repasse devant un lead un peu mieux note mais vieux ;
  - le score montre a l'ecran n'est jamais modifie ;
  - desactivable (RADAR_TRI_FRAICHEUR=0) -> retour au tri par score pur.
"""

import unittest
from datetime import date, timedelta

import radar_dashboard as dash


def _iso(d):
    return d.isoformat()


class TestAgeJours(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(dash._age_jours("2026-08-01", date(2026, 8, 11)), 10)

    def test_jj_mm_aaaa(self):
        self.assertEqual(dash._age_jours("01/08/2026", date(2026, 8, 11)), 10)

    def test_date_future_compte_age_zero(self):
        futur = _iso(date(2026, 8, 21))
        self.assertEqual(dash._age_jours(futur, date(2026, 8, 11)), 0)

    def test_illisible_renvoie_none(self):
        self.assertIsNone(dash._age_jours("pas-une-date", date(2026, 8, 11)))
        self.assertIsNone(dash._age_jours("", date(2026, 8, 11)))


class TestRangTri(unittest.TestCase):
    def setUp(self):
        self.auj = date(2026, 8, 11)
        # On fige les parametres pour des assertions numeriques stables, quel que
        # soit l'environnement d'execution.
        self._sav = (dash.RADAR_TRI_FRAICHEUR, dash.RADAR_TRI_DEMIVIE_JOURS,
                     dash.RADAR_TRI_PLANCHER)
        dash.RADAR_TRI_FRAICHEUR = True
        dash.RADAR_TRI_DEMIVIE_JOURS = 45.0
        dash.RADAR_TRI_PLANCHER = 0.85

    def tearDown(self):
        (dash.RADAR_TRI_FRAICHEUR, dash.RADAR_TRI_DEMIVIE_JOURS,
         dash.RADAR_TRI_PLANCHER) = self._sav

    def test_frais_garde_le_score_plein(self):
        r = dash.rang_tri(7.0, _iso(self.auj), self.auj)
        self.assertAlmostEqual(r, 7.0, places=3)

    def test_ancien_attenue_mais_borne_par_le_plancher(self):
        r = dash.rang_tri(7.0, _iso(self.auj - timedelta(days=60)), self.auj)
        self.assertLess(r, 7.0)                       # attenue
        self.assertGreaterEqual(r, 0.85 * 7.0 - 1e-6) # jamais sous PLANCHER x score

    def test_tres_ancien_tend_vers_le_plancher_sans_le_franchir(self):
        r = dash.rang_tri(7.0, _iso(self.auj - timedelta(days=3650)), self.auj)
        self.assertGreaterEqual(r, 0.85 * 7.0 - 1e-6)

    def test_doctrine_un_lead_fort_ancien_bat_un_lead_frais_faible(self):
        fort_ancien = dash.rang_tri(9.0, _iso(self.auj - timedelta(days=60)), self.auj)
        frais_faible = dash.rang_tri(5.8, _iso(self.auj), self.auj)
        self.assertGreater(fort_ancien, frais_faible)

    def test_un_lead_frais_comparable_repasse_devant_un_ancien_un_peu_mieux_note(self):
        # LE cas signale : 6.8 frais doit passer devant 7.0 vieux de 40 jours.
        frais = dash.rang_tri(6.8, _iso(self.auj), self.auj)
        ancien = dash.rang_tri(7.0, _iso(self.auj - timedelta(days=40)), self.auj)
        self.assertGreater(frais, ancien)

    def test_desactive_rend_le_score_brut(self):
        dash.RADAR_TRI_FRAICHEUR = False
        r = dash.rang_tri(7.0, _iso(self.auj - timedelta(days=60)), self.auj)
        self.assertEqual(r, 7.0)

    def test_date_illisible_rend_le_score_brut(self):
        self.assertEqual(dash.rang_tri(7.0, "pas-une-date", self.auj), 7.0)

    def test_score_non_numerique_renvoie_zero(self):
        self.assertEqual(dash.rang_tri("n.c.", _iso(self.auj), self.auj), 0.0)


def _row_ted(final, date_detection, pub, titre="Mission terrain"):
    """Ligne TED plate minimale valide (titre non vide -> lead non filtre)."""
    return {
        "score_final": str(final), "score_surete": str(final),
        "score_commercial": str(final), "action_recommandee": "contacter",
        "fenetre_action": "court_terme", "titre": titre, "acheteur": "Agence",
        "pays_execution": "MLI", "justification": "j", "confiance": "0.8",
        "modele": "m", "publication_number": pub, "lien_avis": "http://x/" + pub,
        "date_detection": date_detection,
    }


class TestConstruireLeadsTrieParFraicheur(unittest.TestCase):
    """Integration : le tri final de construire_leads respecte le rang."""

    def setUp(self):
        self._sav = (dash.RADAR_TRI_FRAICHEUR, dash.RADAR_TRI_DEMIVIE_JOURS,
                     dash.RADAR_TRI_PLANCHER)
        dash.RADAR_TRI_FRAICHEUR = True
        dash.RADAR_TRI_DEMIVIE_JOURS = 45.0
        dash.RADAR_TRI_PLANCHER = 0.85
        self.auj = date.today()

    def tearDown(self):
        (dash.RADAR_TRI_FRAICHEUR, dash.RADAR_TRI_DEMIVIE_JOURS,
         dash.RADAR_TRI_PLANCHER) = self._sav

    def test_chaque_lead_porte_un_rang(self):
        leads = dash.construire_leads(
            [_row_ted(7.0, _iso(self.auj), "TED:1")], [])
        self.assertTrue(leads)
        self.assertIn("rang", leads[0])

    def test_le_frais_comparable_passe_devant_l_ancien_mieux_note(self):
        frais = _row_ted(6.8, _iso(self.auj), "TED:FRAIS")
        ancien = _row_ted(7.0, _iso(self.auj - timedelta(days=40)), "TED:ANCIEN")
        leads = dash.construire_leads([ancien, frais], [])
        self.assertEqual(leads[0]["pub"], "TED:FRAIS",
                         "Le lead frais comparable doit etre en tete.")

    def test_le_faible_frais_ne_double_pas_le_fort_ancien(self):
        fort_ancien = _row_ted(9.0, _iso(self.auj - timedelta(days=60)), "TED:FORT")
        faible_frais = _row_ted(5.8, _iso(self.auj), "TED:FAIBLE")
        leads = dash.construire_leads([faible_frais, fort_ancien], [])
        self.assertEqual(leads[0]["pub"], "TED:FORT",
                         "Le lead fort ancien reste en tete (score dominant).")

    def test_desactive_revient_au_tri_par_score_pur(self):
        dash.RADAR_TRI_FRAICHEUR = False
        frais = _row_ted(6.8, _iso(self.auj), "TED:FRAIS")
        ancien = _row_ted(7.0, _iso(self.auj - timedelta(days=40)), "TED:ANCIEN")
        leads = dash.construire_leads([ancien, frais], [])
        self.assertEqual(leads[0]["pub"], "TED:ANCIEN",
                         "Fraicheur off : c'est le meilleur SCORE qui prime.")

    def test_le_score_affiche_reste_intact(self):
        ancien = _row_ted(7.0, _iso(self.auj - timedelta(days=60)), "TED:ANCIEN")
        leads = dash.construire_leads([ancien], [])
        self.assertEqual(leads[0]["final"], 7.0, "Le score AFFICHE n'est pas attenue.")
        self.assertLess(leads[0]["rang"], 7.0, "Seul le RANG (tri) est attenue.")


class TestCablageJsRang(unittest.TestCase):
    """Le front-end doit trier l'onglet Importance par `rang` et recevoir le
    champ dans le JSON serialise."""

    def test_le_rang_est_serialise_et_utilise_par_le_tri_par_defaut(self):
        leads = dash.construire_leads(
            [_row_ted(7.0, date.today().isoformat(), "TED:1")], [])
        html = dash.generer_html(leads)
        self.assertIn('"rang"', html, "Le champ rang doit etre serialise vers le JS.")
        self.assertIn("b.rang", html,
                      "Le tri par defaut (Importance) doit utiliser rang cote JS.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
