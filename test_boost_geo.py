# -*- coding: utf-8 -*-
"""Couplage GEO -> SCORE (« board vivant », 12/08/2026).

Un pays en AGGRAVATION recente (FCDO/presse) rehausse ses AVIS. Applique en
display-time dans le dashboard, non destructif. Ces tests verrouillent la
doctrine choisie :
  - seules les aggravations rehaussent (jamais un allegement) ;
  - boost borne, degressif sur la fenetre, module par la severite ;
  - AVIS uniquement (PRIVÉ/ATTRIB intacts) ;
  - non destructif (score de base conserve) ;
  - le rang de tri suit (le lead booste remonte en Importance).

Dates calculees, jamais figees. Aucun reseau.
"""

import unittest
from datetime import date, timedelta

import radar_dashboard as rd


AUJ = date.today()


def _avis(pays_iso, score="6.0", pub="A1", jours=2):
    return {"titre": "Avis " + pays_iso, "acheteur": "Min",
            "pays_execution": pays_iso, "score_final": score,
            "score_surete": score, "score_commercial": score,
            "action_recommandee": "surveiller", "fenetre_action": "court_terme",
            "justification": "x", "lien_avis": "http://x/" + pub,
            "publication_number": pub,
            "date_detection": (AUJ - timedelta(days=jours)).isoformat()}


def _alerte(iso, nom, sens, sev, jours=1, motif="motif"):
    return {"date_maj": (AUJ - timedelta(days=jours)).isoformat(),
            "pays_execution": iso, "pays_nom": nom, "zone": "Sahel",
            "sens": sens, "severite": str(sev), "motif": motif,
            "niveau_avant": "Orange", "niveau_apres": "Rouge"}


class TestBoostGeo(unittest.TestCase):

    def _leads(self, *avis):
        return rd.construire_leads(list(avis), [], [], {}, [])

    def test_aggravation_rehausse(self):
        leads = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "aggravation", 4)])
        l = leads[0]
        self.assertGreater(l["final"], l["final_base"])
        self.assertEqual(l["final_base"], 6.0)
        self.assertIsNotNone(l.get("geo_boost"))

    def test_allegement_ne_baisse_pas(self):
        """Un allegement ne doit JAMAIS baisser un lead (pas de masquage)."""
        leads = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "allegement", 4)])
        self.assertNotIn("geo_boost", leads[0])
        self.assertEqual(leads[0]["final"], 6.0)

    def test_lateral_ignore(self):
        leads = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "lateral", 4)])
        self.assertNotIn("geo_boost", leads[0])

    def test_hors_pays_intact(self):
        """Seuls les leads du pays en aggravation bougent."""
        leads = self._leads(_avis("MLI", pub="A1"), _avis("SEN", pub="A2"))
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "aggravation", 4)])
        par_pays = {l["pays"]: l for l in leads}
        self.assertIn("geo_boost", par_pays["Mali"])
        self.assertNotIn("geo_boost", par_pays["Sénégal"])

    def test_boost_borne(self):
        """Meme severite max et tres frais, le boost ne depasse pas le plafond."""
        leads = self._leads(_avis("MLI", score="9.5"))
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "aggravation", 4, jours=0)])
        self.assertLessEqual(leads[0]["geo_boost"], rd.BOOST_GEO_MAX + 1e-9)
        self.assertLessEqual(leads[0]["final"], 10.0)   # jamais > 10

    def test_severite_module(self):
        """Une sévérité 4 booste plus qu'une sévérité 1 (même âge)."""
        l4 = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(l4, [_alerte("MLI", "Mali", "aggravation", 4, jours=1)])
        l1 = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(l1, [_alerte("MLI", "Mali", "aggravation", 1, jours=1)])
        self.assertGreater(l4[0]["geo_boost"], l1[0]["geo_boost"])

    def test_decroissance_age(self):
        """Une aggravation d'il y a 1 j booste plus qu'une d'il y a 13 j."""
        recent = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(recent, [_alerte("MLI", "Mali", "aggravation", 4, jours=1)])
        vieux = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(vieux, [_alerte("MLI", "Mali", "aggravation", 4, jours=13)])
        self.assertGreater(recent[0]["geo_boost"], vieux[0]["geo_boost"])

    def test_hors_fenetre_aucun_boost(self):
        leads = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "aggravation", 4, jours=40)])
        self.assertNotIn("geo_boost", leads[0])

    def test_prive_attrib_intacts(self):
        """PRIVÉ/ATTRIB ne sont jamais boostés (baremes non comparables)."""
        prive = [{"titre": "Filiale Mali", "acheteur": "Nexter", "entreprise": "Nexter",
                  "pays_execution": "ML", "zone": "Sahel", "score_final": "6",
                  "action_recommandee": "contacter", "fenetre_action": "immediate",
                  "type_activite": "implantation", "publication_number": "P1"}]
        attrib = [{"gagnant": "Bouygues", "secteur": "BTP", "pays_execution": "MLI",
                   "valeur_attribuee": "10M", "titre": "Route", "publication_number": "AT1"}]
        leads = rd.construire_leads([], [], prive, {}, attrib)
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "aggravation", 4)])
        for l in leads:
            self.assertNotIn("geo_boost", l, "PRIVÉ/ATTRIB ne doit pas être boosté")

    def test_par_pays_plus_forte_aggravation(self):
        """Deux aggravations sur le même pays -> on garde la plus forte."""
        leads = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(leads, [
            _alerte("MLI", "Mali", "aggravation", 1, jours=1),
            _alerte("MLI", "Mali", "aggravation", 4, jours=1),
        ])
        seul = self._leads(_avis("MLI"))
        rd.appliquer_boost_geo(seul, [_alerte("MLI", "Mali", "aggravation", 4, jours=1)])
        self.assertAlmostEqual(leads[0]["geo_boost"], seul[0]["geo_boost"])

    def test_rang_remonte(self):
        """Le lead boosté doit remonter dans le tri Importance (rang)."""
        # Deux avis même score ; celui du pays en aggravation doit passer devant.
        leads = self._leads(_avis("SEN", pub="A1"), _avis("MLI", pub="A2"))
        rd.appliquer_boost_geo(leads, [_alerte("MLI", "Mali", "aggravation", 4)])
        self.assertEqual(leads[0]["pays"], "Mali")   # re-trié en tête

    def test_aucune_alerte_ne_change_rien(self):
        leads = self._leads(_avis("MLI"))
        avant = leads[0]["final"]
        rd.appliquer_boost_geo(leads, [])
        self.assertEqual(leads[0]["final"], avant)
        self.assertNotIn("geo_boost", leads[0])


class TestBoostDansLeRendu(unittest.TestCase):
    def test_boost_visible_dans_le_html(self):
        leads = rd.construire_leads([_avis("MLI")], [], [], {}, [])
        html = rd.generer_html(leads, [], alertes=[_alerte("MLI", "Mali", "aggravation", 4)])
        self.assertIn("geoboost", html)          # classe CSS du badge
        self.assertIn("geo_boost", html)         # champ présent dans le JSON

    def test_generation_sans_alerte_ok(self):
        leads = rd.construire_leads([_avis("MLI")], [], [], {}, [])
        html = rd.generer_html(leads, [], alertes=None)
        self.assertIsInstance(html, str)


if __name__ == "__main__":
    unittest.main()
