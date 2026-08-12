# -*- coding: utf-8 -*-
"""Cockpit « À faire » (discipline de pipeline, 12/08/2026).

La logique de bucketisation est cote client (elle depend de la date du jour du
navigateur et du statut CRM). Comme pour les lentilles geo/secteur, on verrouille
ici le CABLAGE dans le gabarit (points de branchement) et le rendu de bout en
bout, la convention du depot pour les fonctionnalites JS. Aucun reseau.
"""

import unittest
import radar_dashboard as rd


class TestCockpitCablage(unittest.TestCase):
    def setUp(self):
        self.html = rd.GABARIT_HTML

    def test_lentille_todo_presente(self):
        self.assertIn('data-lens="todo"', self.html)

    def test_points_de_branchement(self):
        for nom, marqueur in (
                ("dispatch render", "if(state.lens==='todo'){ renderTodo(); return; }"),
                ("classifieur bucket", "function bucketTodo(l){"),
                ("liste todo", "function todoListe(){"),
                ("rendu", "function renderTodo(){"),
                ("SLA immédiat", "SLA_IMMEDIAT_JOURS"),
                ("fenêtre échéance", "ECHEANCE_JOURS"),
                ("seuil relance", "RELANCE_JOURS"),
                ("buckets", "BUCKETS_TODO"),
                ("KPI cockpit", "state.lens==='todo'"),
                ("titre carte", "todo:'Carte des actions à mener'")):
            self.assertIn(marqueur, self.html, "branchement cockpit absent : {}".format(nom))

    def test_date_de_contact_memorisee(self):
        """Le clic « Je contacte » enregistre la date locale (bucket relance)."""
        self.assertIn("suivi_dates", self.html)

    def test_controles_masques_en_cockpit(self):
        """majControlesLentille gère le mode todo."""
        self.assertIn("state.lens==='todo'", self.html)
        self.assertIn("!todo", self.html)

    def test_ordre_buckets(self):
        """Priorité: retard > echeance > contacter > suivre (un seul bucket)."""
        i = self.html.index("const BUCKETS_TODO=[")
        bloc = self.html[i:i + 400]
        pr = bloc.index("retard"); pe = bloc.index("echeance")
        pc = bloc.index("contacter"); ps = bloc.index("suivre")
        self.assertTrue(pr < pe < pc < ps, "ordre de priorité des buckets incorrect")


class TestCockpitRendu(unittest.TestCase):
    def test_generation_expose_la_lentille(self):
        row = {"titre": "Escorte", "acheteur": "PAM", "pays_execution": "MLI",
               "score_final": "7", "action_recommandee": "contacter",
               "fenetre_action": "immediate", "publication_number": "R1",
               "date_detection": "2020-01-01"}
        leads = rd.construire_leads([row], [], [], {}, [])
        html = rd.generer_html(leads, [], alertes=[])
        self.assertIn('data-lens="todo"', html)
        self.assertNotIn("__LEADS_JSON__", html)
        # Les champs dont depend le cockpit sont serialises.
        for champ in ('"statut"', '"deadline"', '"win"', '"date_det"', '"action"'):
            self.assertIn(champ, html, "champ requis par le cockpit absent : {}".format(champ))


if __name__ == "__main__":
    unittest.main()
