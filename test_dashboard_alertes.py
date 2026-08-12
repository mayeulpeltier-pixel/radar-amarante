# -*- coding: utf-8 -*-
"""Volet geopolitique du dashboard (ex-"bandeau alertes").

HISTORIQUE (12/08/2026)
-----------------------
Le bandeau de contexte en tete de page a ete SUPPRIME : il apparaissait dans
toutes les lentilles et prenait trop de place pour trop peu de pertinence, avec
des evenements souvent trop vieux. Les signaux geopolitiques vivent desormais
UNIQUEMENT dans l'onglet dedie « Geopolitique », et ne montrent que la SEMAINE
EN COURS (7 jours). Ces tests verrouillent les deux garanties :

  1. le bandeau n'existe plus (aucun residu dans le gabarit) ;
  2. `preparer_geo` ne garde que le tres recent (7 j), trie par severite, et
     reste robuste (severite illisible, liste vide).

Aucun appel reseau : dicts en memoire, dates calculees jamais figees.
"""

import unittest
from datetime import date, timedelta

import radar_dashboard as dash


CETTE_SEMAINE = (date.today() - timedelta(days=2)).isoformat()
SEMAINE_DERNIERE = (date.today() - timedelta(days=9)).isoformat()   # > 7 j
TRES_VIEILLE = (date.today() - timedelta(days=400)).isoformat()


def _alerte(pays, iso3, sens, sev, maj=None, **extra):
    base = {
        "date_maj": maj or CETTE_SEMAINE, "pays_execution": iso3, "pays_nom": pays,
        "zone": "Sahel", "niveau_avant": "Voyage essentiel uniquement",
        "niveau_apres": "Tout voyage deconseille", "sens": sens,
        "severite": str(sev), "motif": "Motif test",
        "publication_number": "FCDO-{}".format(iso3), "lien": "http://x/" + iso3,
    }
    base.update(extra)
    return base


# ===========================================================================
# 1. FENETRE SEMAINE EN COURS (preparer_geo)
# ===========================================================================
class TestFenetreSemaine(unittest.TestCase):

    def test_semaine_derniere_ecartee(self):
        """Un signal de plus de 7 jours n'est plus pertinent : ecarte de
        l'onglet. Dates relatives -> le test reste vrai quel que soit le jour."""
        recent = _alerte("Mali", "MLI", "aggravation", 5)              # 2 j
        vieux = _alerte("Tchad", "TCD", "aggravation", 5, maj=SEMAINE_DERNIERE)
        prep = dash.preparer_geo([recent, vieux])
        pays = [a["pays"] for a in prep]
        self.assertIn("Mali", pays)
        self.assertNotIn("Tchad", pays)

    def test_tres_vieille_ecartee(self):
        prep = dash.preparer_geo([_alerte("Tchad", "TCD", "aggravation", 5,
                                          maj=TRES_VIEILLE)])
        self.assertEqual(prep, [])

    def test_severite_en_tete(self):
        """Tri par severite decroissante : la plus grave d'abord."""
        prep = dash.preparer_geo([
            _alerte("Niger", "NER", "aggravation", 2),
            _alerte("Mali", "MLI", "aggravation", 5),
        ])
        self.assertEqual(prep[0]["pays"], "Mali")

    def test_severite_illisible_ne_casse_pas(self):
        prep = dash.preparer_geo([_alerte("Mali", "MLI", "aggravation", "?")])
        self.assertEqual(prep[0]["severite"], 0)

    def test_liste_vide(self):
        self.assertEqual(dash.preparer_geo([]), [])
        self.assertEqual(dash.preparer_geo(None), [])

    def test_champs_exposes(self):
        prep = dash.preparer_geo([_alerte("Mali", "MLI", "aggravation", 5)])
        a = prep[0]
        for champ in ("pays", "iso3", "zone", "sens", "avant", "apres",
                      "motif", "severite", "date", "lien"):
            self.assertIn(champ, a)

    def test_fenetre_surchargeable(self):
        """Le parametre `jours` permet d'elargir ponctuellement la fenetre."""
        vieux = _alerte("Tchad", "TCD", "aggravation", 5, maj=SEMAINE_DERNIERE)
        self.assertEqual(dash.preparer_geo([vieux]), [])          # 7 j : ecarte
        self.assertEqual(len(dash.preparer_geo([vieux], jours=30)), 1)  # 30 j : garde


# ===========================================================================
# 2. LE BANDEAU N'EXISTE PLUS
# ===========================================================================
class TestBandeauSupprime(unittest.TestCase):

    def test_aucun_residu_de_bandeau_dans_le_gabarit(self):
        html = dash.GABARIT_HTML
        for residu in ("alertesPays", "function renderAlertes", "const ALERTES",
                       "__ALERTES_JSON__", "Alertes pays", ".al-item", ".al-agg{"):
            self.assertNotIn(residu, html,
                             "residu de bandeau a supprimer : {}".format(residu))

    def test_page_se_genere_sans_alerte(self):
        html = dash.generer_html([], alertes=None)
        self.assertIsInstance(html, str)
        self.assertNotIn("alertesPays", html)

    def test_signaux_geo_injectes_seulement_dans_l_onglet(self):
        """Les signaux vont dans GEO (onglet), pas dans un bandeau ni les leads."""
        html = dash.generer_html(
            [], alertes=[_alerte("Mali", "MLI", "aggravation", 5)])
        self.assertIn("const GEO =", html)
        self.assertIn('data-lens="geo"', html)
        self.assertIn("Mali", html)          # present via GEO, pas via bandeau


if __name__ == "__main__":
    unittest.main()
