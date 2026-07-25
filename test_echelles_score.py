# -*- coding: utf-8 -*-
"""Etiquetage des echelles de score dans le dashboard (chantier C).

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Trois familles de leads coexistent, avec des scores NON comparables :
  - AVIS (TED/BM/bailleurs)   : bareme additif ;
  - SIGNAL PRIVE              : bareme multiplicatif ;
  - TITULAIRE (attributions)  : soit un INDICE deterministe (zone + secteur +
    valeur), soit une SURETE reellement analysee par le modele (chantier A).

Toutes s'affichaient avec le meme chiffre nu, et la lentille Titulaires triait
tout par ce chiffre. Un titulaire jamais analyse, dont l'indice etait gonfle
par la zone et le montant, pouvait ainsi passer devant un prospect qualifie
par le modele. C'est la source concrete du ressenti « c'est mal trie, les
attributions ne veulent rien dire ».

Le chantier C ne touche PAS aux scores : il rend l'echelle LISIBLE.
  1. `echelleLabel` distingue « echelle avis / signal », « indice titulaire »
     (deterministe) et « surete analysee » (LLM) ;
  2. la lentille Titulaires fait remonter les fiches analysees devant les
     autres ;
  3. chaque ligne de la timeline porte son etiquette d'echelle.

L'etiquetage lui-meme est du JavaScript (fonction `echelleLabel` dans la page).
Ces tests verrouillent ce qui est verifiable cote Python : la DONNEE qui pilote
l'affichage (`analysee` sur le lead) et la PRESENCE des etiquettes dans le HTML
genere selon l'etat d'analyse.

Aucun appel reseau ni LLM.
"""

import unittest

import radar_dashboard as dash


ATTRIB = {
    "gagnant": "Yapi Merkezi", "secteur": "Travaux", "pays_execution": "MLI",
    "valeur_attribuee": "USD 42000000", "acheteur": "BM", "titre": "Route",
    "publication_number": "BM-1", "lien": "http://x",
    "pays_titulaire": "Turquie", "titulaire_etranger": "oui",
}

ANALYSE = {
    "publication_number": "BM-1", "score_final": "9.7", "score_surete": "10.0",
    "score_commercial": "9.2", "action_recommandee": "contacter",
    "pays_origine_titulaire": "Turquie", "titulaire_etranger": "True",
    "nature_deploiement": "expatrie_significatif",
    "besoin_surete_probable": "fort", "interlocuteur_vise": "dir ops",
    "justification": "Base vie isolee.",
}


class TestFlagAnalysee(unittest.TestCase):
    """`analysee` est la donnee qui pilote toute la distinction d'echelle."""

    def test_attribution_analysee_porte_le_flag(self):
        lead = dash.construire_leads([], [], lignes_attrib=[ATTRIB],
                                     analyses_attrib=[ANALYSE])[0]
        self.assertTrue(lead["analysee"])

    def test_attribution_non_analysee_ne_le_porte_pas(self):
        lead = dash.construire_leads([], [], lignes_attrib=[ATTRIB])[0]
        self.assertFalse(lead["analysee"])

    def test_avis_ordinaire_n_est_pas_marque_analysee(self):
        """Le flag ne concerne que les attributions : un avis n'a pas de
        notion d'analyse d'attribution."""
        avis = {"score_final": "7.0", "titre": "Escorte", "pays_execution": "MLI",
                "publication_number": "TED-1", "lien_avis": "http://a"}
        leads = dash.construire_leads([avis], [])
        ted = [l for l in leads if l["src"] == "TED"]
        self.assertTrue(ted)
        self.assertFalse(ted[0].get("analysee", False))


class TestEtiquettesDansLeHtml(unittest.TestCase):
    """Verifie que les libelles d'echelle sont bien injectes dans la page selon
    l'etat d'analyse. Le rendu final est du JS, mais la fonction et ses libelles
    doivent etre presents et corrects dans le source servi."""

    def _html(self, avec_analyse):
        leads = dash.construire_leads(
            [], [], lignes_attrib=[ATTRIB],
            analyses_attrib=[ANALYSE] if avec_analyse else None)
        return dash.generer_html(leads)

    def test_les_trois_libelles_existent(self):
        html = self._html(avec_analyse=True)
        for libelle in ("échelle avis", "échelle signal",
                        "indice titulaire", "sûreté analysée"):
            self.assertIn(libelle, html)

    def test_echelle_label_recoit_le_lead_entier(self):
        """Regression : l'ancien appel `echelleLabel(l.src)` ne pouvait pas
        distinguer analyse et indicatif, faute d'acces au flag. Le nouvel
        appel passe le lead entier."""
        html = self._html(avec_analyse=True)
        self.assertIn("echelleLabel(l)", html)
        self.assertIn("echelleLabel(s)", html)
        self.assertNotIn("echelleLabel(l.src)", html)

    def test_le_tri_titulaires_privilegie_les_analysees(self):
        """La fonction de tri de la lentille Titulaires doit comparer d'abord
        le flag `analysee`, puis le score."""
        html = self._html(avec_analyse=True)
        self.assertIn("analysee?1:0", html.replace(" ", ""))

    def test_la_timeline_porte_une_etiquette_par_ligne(self):
        html = self._html(avec_analyse=True)
        self.assertIn("tlech", html)

    def test_le_style_des_etiquettes_est_defini(self):
        html = self._html(avec_analyse=True)
        self.assertIn(".sub-echelle", html)
        self.assertIn(".tlrow .tlech", html)


class TestFicheAnalyseeFlag(unittest.TestCase):
    """La fiche titulaire (agregation par entreprise) doit exposer `analysee`
    des qu'au moins un de ses signaux est une attribution analysee, pour piloter
    le tri."""

    def test_flag_propage_a_la_fiche(self):
        """On genere la page avec une attribution analysee et on verifie que la
        logique d'agregation expose bien le flag (present dans le JS)."""
        html = dash.generer_html(dash.construire_leads(
            [], [], lignes_attrib=[ATTRIB], analyses_attrib=[ANALYSE]))
        # La fiche calcule analysee = un signal au moins est analyse.
        self.assertIn("f.signaux.some(s=>s.analysee)", html.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
