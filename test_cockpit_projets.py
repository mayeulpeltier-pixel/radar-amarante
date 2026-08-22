# -*- coding: utf-8 -*-
"""Vue PROJETS du cockpit (points 17-19 du cahier des charges).

On verrouille le CONTRAT DE DONNEES et la RETRO-COMPATIBILITE : le Project
Intelligence est additif, un cockpit sans projets doit s'afficher exactement
comme avant, et une donnee Sheet inattendue ne doit jamais casser la page.
"""

import datetime
import json
import re
import unittest

import collecteur_projets as cp
import projets as pj
import radar_cockpit as rc


LEADS = [{"src": "BM", "zone": "Sahel", "pays": "Mali", "titre": "Avis route",
          "final": 6.0, "action": "surveiller", "sect": "Génie civil / BTP"}]


def _projets_via_sheet():
    """Projets calcules PUIS passes par le format Sheet (tout en texte), pour
    tester le vrai chemin de production."""
    signaux = [
        {"titre": "World Bank approves $250m for Inga 3", "date": "2025-06-03",
         "phase": "FUNDING_APPROVED", "lien": "http://a"},
        {"titre": "AECOM selected for Inga studies", "date": "2026-04-15",
         "phase": "CONSULTANT_SELECTION", "lien": "http://b"},
    ]
    calc = pj.construire_projets(signaux, aujourd=datetime.date(2026, 8, 22))
    out = []
    for p in calc:
        d = dict(zip(cp.COLONNES, cp.ligne_depuis_projet(p)))
        d["timeline"] = json.loads(d.pop("timeline_json"))
        for champ in ("maturite", "opportunite", "nb_signaux", "valeur_musd"):
            d[champ] = float(d[champ])
        out.append(d)
    return out


def _projets_json(html):
    return json.loads(re.search(r"PROJETS_RAW=(\[.*?\]);", html, re.S).group(1))


class TestInjection(unittest.TestCase):

    def test_projets_injectes(self):
        h = rc.generer_cockpit(LEADS, projets=_projets_via_sheet())
        self.assertEqual([p["project_id"] for p in _projets_json(h)], ["INGA3_COD"])

    def test_aucun_placeholder_restant(self):
        h = rc.generer_cockpit(LEADS, projets=_projets_via_sheet())
        self.assertNotIn("__PROJETS_JSON__", h)

    def test_vue_et_navigation_presentes(self):
        h = rc.generer_cockpit(LEADS, projets=_projets_via_sheet())
        for attendu in ('id="v-proj"', 'data-view="proj"', "renderProj",
                        "openProjet", "Top 20 opportunités"):
            self.assertIn(attendu, h, attendu)

    def test_les_deux_scores_sont_exposes_separement(self):
        h = rc.generer_cockpit(LEADS, projets=_projets_via_sheet())
        p = _projets_json(h)[0]
        self.assertIn("maturite", p)
        self.assertIn("opportunite", p)
        self.assertNotEqual(p["maturite"], p["opportunite"])

    def test_timeline_transmise(self):
        h = rc.generer_cockpit(LEADS, projets=_projets_via_sheet())
        self.assertEqual([b["annee"] for b in _projets_json(h)[0]["timeline"]],
                         ["2025", "2026"])


class TestRetroCompatibilite(unittest.TestCase):
    """Le chantier est ADDITIF : sans projets, rien ne change."""

    def test_cockpit_sans_projets_reste_valide(self):
        h = rc.generer_cockpit(LEADS)
        self.assertNotIn("__PROJETS_JSON__", h)
        self.assertEqual(_projets_json(h), [])

    def test_vues_existantes_intactes(self):
        h = rc.generer_cockpit(LEADS)
        for attendu in ('id="v-opps"', 'id="v-firmo"', 'id="v-doss"',
                        'id="v-attrib"', 'id="v-geo"'):
            self.assertIn(attendu, h, attendu)


class TestChargementTolerant(unittest.TestCase):
    """charger_projets est best-effort : il ne doit JAMAIS lever."""

    def test_sans_identifiants_renvoie_vide(self):
        self.assertEqual(rc.charger_projets(None, None), [])
        self.assertEqual(rc.charger_projets("id", None), [])

    def test_sheet_illisible_renvoie_vide(self):
        # signaux_prives._ouvrir_classeur va echouer (pas de credentials) :
        # la fonction doit avaler l'erreur et rendre une liste vide.
        self.assertEqual(rc.charger_projets("faux_id", "/inexistant.json"), [])


class TestRobustesseDonnees(unittest.TestCase):
    """Piege connu du projet : une valeur Sheet inattendue ne doit pas casser
    le rendu (numeriques en texte, JSON malforme, colonnes manquantes)."""

    def test_valeurs_numeriques_en_texte(self):
        p = {"project_id": "X", "libelle": "Projet X", "maturite": "65",
             "opportunite": "80", "nb_signaux": "4", "valeur_musd": "14000"}
        h = rc.generer_cockpit(LEADS, projets=[p])
        self.assertIn("Projet X", h)

    def test_projet_sans_aucune_colonne_optionnelle(self):
        h = rc.generer_cockpit(LEADS, projets=[{"project_id": "Y"}])
        self.assertEqual(_projets_json(h)[0]["project_id"], "Y")

    def test_timeline_absente_ne_casse_pas(self):
        h = rc.generer_cockpit(LEADS, projets=[{"project_id": "Z",
                                                "timeline": None}])
        self.assertIn("PROJETS_RAW", h)


if __name__ == "__main__":
    unittest.main()
