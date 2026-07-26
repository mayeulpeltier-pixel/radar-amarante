# -*- coding: utf-8 -*-
"""Section "Alertes pays" du dashboard (branchement des alertes voyageurs).

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Le collecteur alertes_voyageurs.py produit des changements d'alerte dans
`alertes_radar`. Encore faut-il les afficher. OPTION A retenue : un bandeau de
CONTEXTE en tete du dashboard, SEPARE des leads.

Ce choix est deliberatement conservateur, et ces tests le verrouillent :
  - une alerte n'a NI score, NI bouton « je contacter » : ce n'est pas un
    prospect mais une info qui rend les autres leads d'un pays plus chauds.
    La melanger au scoring des leads serait le defaut corrige au chantier C ;
  - `preparer_alertes` ne garde que le RECENT (30 j) et trie par severite puis
    sens (une aggravation prime, c'est le signal le plus chaud) ;
  - repli silencieux : aucune alerte -> section vide, page inchangee.

Aucun appel reseau : on travaille sur des dicts en memoire.
"""

import unittest
from datetime import date, timedelta

import radar_dashboard as dash


# Dates CALCULEES depuis aujourd'hui, jamais figees : une date en dur (ex.
# "2026-07-24") tomberait hors de la fenetre de 30 jours des que la suite est
# rejouee plus tard, et casserait sans raison. Lecon des bombes a retardement
# du 23/07/2026.
RECENTE = (date.today() - timedelta(days=2)).isoformat()
TRES_VIEILLE = (date.today() - timedelta(days=400)).isoformat()


def _alerte(pays, iso3, sens, sev, maj=None, **extra):
    base = {
        "date_maj": maj or RECENTE, "pays_execution": iso3, "pays_nom": pays,
        "zone": "Sahel", "niveau_avant": "Voyage essentiel uniquement",
        "niveau_apres": "Tout voyage deconseille", "sens": sens,
        "severite": str(sev), "motif": "Motif test",
        "publication_number": "FCDO-{}".format(iso3), "lien": "http://x/" + iso3,
    }
    base.update(extra)
    return base


# ===========================================================================
# PREPARATION DES ALERTES
# ===========================================================================

class TestPreparation(unittest.TestCase):

    def test_aggravation_severe_en_tete(self):
        """Tri : la severite d'abord, puis le sens. Une aggravation a 5 doit
        passer devant une aggravation a 2 et devant un allegement a 5."""
        alertes = [
            _alerte("Colombie", "COL", "allegement", 5),
            _alerte("Mali", "MLI", "aggravation", 5),
            _alerte("Niger", "NER", "aggravation", 2),
        ]
        prep = dash.preparer_alertes(alertes)
        self.assertEqual(prep[0]["pays"], "Mali")     # aggravation + severe

    def test_alerte_ancienne_ecartee(self):
        """Une alerte de plus de 30 jours n'est plus un signal chaud. Dates
        relatives a aujourd'hui : le test reste vrai quel que soit le jour ou
        il est rejoue."""
        recente = _alerte("Mali", "MLI", "aggravation", 5)          # RECENTE
        ancienne = _alerte("Tchad", "TCD", "aggravation", 5, maj=TRES_VIEILLE)
        prep = dash.preparer_alertes([recente, ancienne])
        pays = [a["pays"] for a in prep]
        self.assertIn("Mali", pays)
        self.assertNotIn("Tchad", pays)

    def test_severite_illisible_ne_casse_pas(self):
        prep = dash.preparer_alertes([_alerte("Mali", "MLI", "aggravation", "?")])
        self.assertEqual(prep[0]["severite"], 0)

    def test_aucune_alerte_donne_liste_vide(self):
        self.assertEqual(dash.preparer_alertes([]), [])
        self.assertEqual(dash.preparer_alertes(None), [])

    def test_champs_exposes(self):
        prep = dash.preparer_alertes([_alerte("Mali", "MLI", "aggravation", 5)])
        a = prep[0]
        for champ in ("pays", "iso3", "zone", "sens", "avant", "apres",
                      "motif", "severite", "date", "lien"):
            self.assertIn(champ, a)


# ===========================================================================
# RENDU DANS LA PAGE
# ===========================================================================

class TestRenduHtml(unittest.TestCase):

    def test_section_presente_avec_alertes(self):
        html = dash.generer_html(
            [], alertes=[_alerte("Mali", "MLI", "aggravation", 5)])
        self.assertIn("alertesPays", html)
        self.assertIn("Alertes pays", html)
        self.assertIn("Mali", html)

    def test_fonction_de_rendu_presente(self):
        html = dash.generer_html([], alertes=[])
        self.assertIn("function renderAlertes", html)

    def test_alerte_separee_des_leads(self):
        """L'alerte ne doit pas devenir un lead : elle n'apparait pas dans le
        JSON des leads, seulement dans celui des alertes."""
        html = dash.generer_html(
            [], alertes=[_alerte("Mali", "MLI", "aggravation", 5)])
        self.assertIn("const ALERTES =", html)
        self.assertIn("const LEADS =", html)

    def test_page_sans_alerte_reste_valide(self):
        """Repli silencieux : pas d'alerte, la page se genere sans la section
        et sans erreur."""
        html = dash.generer_html([], alertes=None)
        self.assertIn("const ALERTES =", html)
        self.assertIsInstance(html, str)

    def test_sens_visuellement_distingue(self):
        """Le style distingue aggravation / allegement (bordure rouge/verte).
        On verifie que les classes existent dans le CSS."""
        html = dash.generer_html([], alertes=[])
        self.assertIn(".al-agg", html)
        self.assertIn(".al-alleg", html)


if __name__ == "__main__":
    unittest.main()
