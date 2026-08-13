# -*- coding: utf-8 -*-
"""Bouton « Pas pertinent » : écarter une opportunité + apprendre (12/08/2026).

Le lead part dans « Écartés » (réversible), la RAISON est enregistrée (serveur +
localStorage). La logique de masquage/raison est cliente ; comme pour les autres
features JS, on verrouille le CABLAGE dans le gabarit + la lecture serveur du
motif (superposé dans motif_ecart). Le flux navigateur se valide en réel.
"""

import unittest
import radar_dashboard as rd


class TestCablageEcarter(unittest.TestCase):
    def setUp(self):
        self.html = rd.GABARIT_HTML

    def test_bouton_et_menu(self):
        self.assertIn("data-ecart=", self.html)          # bouton sur chaque lead
        self.assertIn("MOTIFS_ECART", self.html)         # raisons structurées
        self.assertIn("data-ecartmotif", self.html)      # choix de raison

    def test_ecriture_serveur_avec_motif(self):
        """L'écartement poste statut 'non_pertinent' + motif à /api/statut."""
        self.assertIn("'non_pertinent'", self.html)
        self.assertIn("/api/statut", self.html)
        self.assertIn("motif:motif", self.html)

    def test_reversible_restaurer(self):
        self.assertIn("function restaurerEcarte", self.html)
        self.assertIn("data-restaurer", self.html)

    def test_section_et_apprentissage(self):
        self.assertIn("ecartes-body", self.html)
        self.assertIn("function renderEcartes", self.html)
        self.assertIn("Raisons :", self.html)            # readout d'apprentissage

    def test_masquage_dans_les_vues(self):
        self.assertIn("function estEcarte", self.html)
        self.assertIn("estEcarte(l)", self.html)         # filtré dans match()

    def test_points_de_branchement(self):
        for m in ("function marquerNonPertinent", "function menuRaisons",
                  "ecartes_motifs", "MOTIF_LABEL"):
            self.assertIn(m, self.html, "câblage écarter absent : {}".format(m))


class TestMotifLuDansLeLead(unittest.TestCase):
    def test_lead_expose_motif_ecart(self):
        """Le motif superposé côté serveur (motif_ecart) arrive jusqu'au lead."""
        row = {"titre": "x", "pays_execution": "MLI", "score_final": "5",
               "action_recommandee": "surveiller", "publication_number": "T1",
               "statut_suivi": "non_pertinent", "motif_ecart": "hors_zone"}
        lead = rd.ligne_vers_lead(row, "TED")
        self.assertEqual(lead["motif_ecart"], "hors_zone")
        self.assertEqual(lead["statut"], "non_pertinent")

    def test_serialisation_dans_le_html(self):
        row = {"titre": "x", "pays_execution": "MLI", "score_final": "5",
               "action_recommandee": "surveiller", "publication_number": "T1",
               "statut_suivi": "non_pertinent", "motif_ecart": "hors_zone"}
        leads = rd.construire_leads([row], [], [], {}, [])
        html = rd.generer_html(leads, [], alertes=[])
        self.assertIn("motif_ecart", html)


if __name__ == "__main__":
    unittest.main()
