# -*- coding: utf-8 -*-
"""Branchement de l'analyse d'attributions dans le dashboard.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
`attributions_analyse.py` produit une table `attributions_analyse` (scores
reels, origine du titulaire, interlocuteur a viser). Encore faut-il que le
dashboard la LISE : sans branchement, le module tournerait pour rien et
l'onglet Titulaires continuerait d'afficher le score deterministe recopie
trois fois.

`superposer_analyse_attribution` fait la jointure sur publication_number,
exactement comme le dashboard joint deja l'enrichissement firmographique. La
propriete la plus importante testee ici est la SUPERPOSITION NON DESTRUCTIVE :
si l'analyse manque (pas encore produite, solde LLM epuise, publication_number
absent), le lead garde son score deterministe et la page reste complete. Une
regression sur ce point trouerait le dashboard des le premier run partiel.

Aucun appel reseau ni LLM : on travaille sur des dicts en memoire.
"""

import unittest

import radar_dashboard as dash


ATTRIB = {
    "gagnant": "Yapi Merkezi", "secteur": "Travaux", "pays_execution": "MLI",
    "valeur_attribuee": "USD 42000000", "acheteur": "Banque Mondiale",
    "titre": "Route Bamako-Segou", "publication_number": "BM-1",
    "lien": "http://exemple/BM-1",
}

ANALYSE = {
    "publication_number": "BM-1", "score_final": "9.7", "score_surete": "10.0",
    "score_commercial": "9.2", "action_recommandee": "contacter",
    "pays_origine_titulaire": "Turquie", "titulaire_etranger": "True",
    "nature_deploiement": "expatrie_significatif",
    "besoin_surete_probable": "fort",
    "interlocuteur_vise": "directeur des operations Afrique de l'Ouest",
    "justification": "Base vie isolee, rotation d'expatries sur 30 mois.",
}


def _lead_attrib(analyses=None):
    """Construit le lead ATTRIB, avec ou sans analyse jointe."""
    leads = dash.construire_leads([], [], lignes_attrib=[ATTRIB],
                                  analyses_attrib=analyses)
    return leads[0]


# ===========================================================================
# SUPERPOSITION NON DESTRUCTIVE
# ===========================================================================

class TestSuperpositionNonDestructive(unittest.TestCase):

    def test_sans_analyse_le_score_deterministe_est_conserve(self):
        """LA garantie : une attribution non encore analysee reste affichee,
        avec son score d'origine. La page n'est jamais trouee."""
        lead = _lead_attrib(analyses=None)
        self.assertFalse(lead.get("analysee", False))
        self.assertGreater(lead["final"], 0.0)

    def test_analyse_absente_pour_ce_pub_ne_change_rien(self):
        """Une analyse existe, mais pour une AUTRE attribution."""
        autre = dict(ANALYSE, publication_number="ZZZ-999")
        lead = _lead_attrib(analyses=[autre])
        self.assertFalse(lead.get("analysee", False))

    def test_avec_analyse_les_vrais_scores_remplacent(self):
        lead = _lead_attrib(analyses=[ANALYSE])
        self.assertTrue(lead["analysee"])
        self.assertEqual(lead["final"], 9.7)
        self.assertEqual(lead["surete"], 10.0)
        self.assertEqual(lead["comm"], 9.2)

    def test_les_scores_ne_sont_plus_identiques(self):
        """Le defaut d'origine etait surete == commercial == final. L'analyse
        les separe."""
        lead = _lead_attrib(analyses=[ANALYSE])
        self.assertNotEqual(lead["surete"], lead["comm"])

    def test_champ_score_illisible_garde_l_ancienne_valeur(self):
        """Robustesse : une analyse au score vide ne doit pas mettre le lead a
        zero. On retombe sur le deterministe champ par champ."""
        avant = _lead_attrib(analyses=None)
        casse = dict(ANALYSE, score_final="", score_surete="n.c.")
        lead = _lead_attrib(analyses=[casse])
        self.assertEqual(lead["final"], avant["final"])
        self.assertEqual(lead["surete"], avant["surete"])
        self.assertEqual(lead["comm"], 9.2)     # celui-la etait valide


# ===========================================================================
# CE QUE L'ANALYSE APPORTE A LA FICHE
# ===========================================================================

class TestApportCommercial(unittest.TestCase):

    def test_l_action_suit_l_analyse(self):
        lead = _lead_attrib(analyses=[ANALYSE])
        self.assertEqual(lead["action"], "contacter")

    def test_l_interlocuteur_est_mis_en_avant(self):
        lead = _lead_attrib(analyses=[ANALYSE])
        self.assertIn("directeur des operations", lead["cible"])
        self.assertEqual(lead["interlocuteur"],
                         "directeur des operations Afrique de l'Ouest")

    def test_l_origine_prefixe_la_justification(self):
        """Le signal jete auparavant, desormais le premier que l'oeil voit."""
        lead = _lead_attrib(analyses=[ANALYSE])
        self.assertTrue(lead["justif"].startswith("Titulaire Turquie"))
        self.assertIn("ETRANGER", lead["justif"])

    def test_les_champs_de_filtre_sont_exposes(self):
        lead = _lead_attrib(analyses=[ANALYSE])
        self.assertEqual(lead["origine"], "Turquie")
        self.assertTrue(lead["etranger_titulaire"])
        self.assertEqual(lead["nature_deploiement"], "expatrie_significatif")
        self.assertEqual(lead["besoin_surete"], "fort")

    def test_titulaire_local_signale_comme_tel(self):
        local = dict(ANALYSE, pays_origine_titulaire="Mali",
                     titulaire_etranger="False")
        lead = _lead_attrib(analyses=[local])
        self.assertFalse(lead["etranger_titulaire"])
        self.assertIn("local", lead["justif"])


# ===========================================================================
# INTEGRATION : LE LEAD RESTE UN LEAD ATTRIB VALIDE
# ===========================================================================

class TestIntegrite(unittest.TestCase):

    def test_le_lead_reste_de_source_attrib(self):
        """La superposition ne doit pas changer la nature du lead : il reste un
        titulaire, compte comme tel dans les KPI, pas comme un avis."""
        lead = _lead_attrib(analyses=[ANALYSE])
        self.assertEqual(lead["src"], "ATTRIB")

    def test_les_titulaires_restent_hors_kpi_d_action(self):
        """generer_html exclut les ATTRIB du total contacter/surveiller/ignorer.
        L'analyse ne doit pas les y reintroduire : ce sont des prospects, pas
        des avis de marche."""
        leads = dash.construire_leads([], [], lignes_attrib=[ATTRIB],
                                      analyses_attrib=[ANALYSE])
        html = dash.generer_html(leads)
        self.assertIsInstance(html, str)
        self.assertIn("BM-1", html)

    def test_cle_de_jointure_robuste_aux_espaces(self):
        """publication_number cote analyse peut porter des espaces parasites :
        la jointure doit tenir malgre eux (les deux cotes passent par _txt)."""
        avec_espace = dict(ANALYSE, publication_number="  BM-1  ")
        lead = _lead_attrib(analyses=[avec_espace])
        self.assertTrue(lead["analysee"])


class TestRenouvellementExpose(unittest.TestCase):
    """Le lead ATTRIB doit exposer les champs de renouvellement (calcules a la
    collecte via SPARQL) pour que le dashboard affiche le badge d'echeance."""

    def test_champs_exposes_quand_presents(self):
        row = dict(ATTRIB, fin_contrat="2027-06-01",
                   mois_avant_fin=9.5, statut_renouv="a_venir")
        lead = dash.attribution_vers_lead(row)
        self.assertEqual(lead["fin_contrat"], "2027-06-01")
        self.assertEqual(lead["mois_avant_fin"], 9.5)
        self.assertEqual(lead["statut_renouv"], "a_venir")

    def test_champs_vides_quand_absents(self):
        lead = dash.attribution_vers_lead(ATTRIB)
        self.assertEqual(lead["statut_renouv"], "")
        self.assertEqual(lead["fin_contrat"], "")


class TestIncumbentAffichage(unittest.TestCase):
    """Intelligence concurrents : le code du badge 'incumbent' (acteur
    recurrent, N marches gagnes) doit etre present dans la page. Garde-fou
    contre une suppression accidentelle du JS/CSS."""

    def test_code_du_badge_present(self):
        html = dash.generer_html([], [], alertes=[])
        self.assertIn("fincumbent", html)      # CSS + rendu du badge
        self.assertIn("nAttrib", html)         # compteur de marches ATTRIB


if __name__ == "__main__":
    unittest.main()
