# -*- coding: utf-8 -*-
"""Generateur du cockpit (nouvelle interface) : enrichissement + injection.

Le cockpit reutilise le moteur de radar_dashboard et n'ajoute qu'un champ
`valeur_meur`. On verifie ici que l'injection est propre (placeholders remplaces,
JSON valide) et que la conversion de montant est correcte, sans reseau.
"""

import json
import re
import unittest

import radar_cockpit as rc


def lead(**kw):
    base = {"src": "BM", "zone": "Sahel", "pays": "Mali", "titre": "Marché T",
            "agence": "Banque Mondiale", "final": 8.0, "action": "contacter",
            "sect": "Génie civil / BTP", "statut": "nouveau", "valeur": ""}
    base.update(kw)
    return base


class TestEnrichir(unittest.TestCase):

    def test_valeur_meur_depuis_attribution(self):
        out = rc.enrichir([lead(valeur="9000000 EUR")])
        self.assertEqual(out[0]["valeur_meur"], 9.0)

    def test_avis_sans_montant_donne_zero(self):
        self.assertEqual(rc.enrichir([lead(valeur="")])[0]["valeur_meur"], 0.0)

    def test_montant_illisible_ne_plante_pas(self):
        self.assertEqual(rc.enrichir([lead(valeur="n.c.")])[0]["valeur_meur"], 0.0)

    def test_entree_non_mutee(self):
        src = lead()
        rc.enrichir([src])
        self.assertNotIn("valeur_meur", src)


class TestGenerer(unittest.TestCase):

    def test_placeholders_tous_remplaces(self):
        h = rc.generer_cockpit([lead()])
        for p in ("__LEADS_JSON__", "__COORDS_JSON__", "__RISQUE_JSON__"):
            self.assertNotIn(p, h)

    def test_lead_injecte_visible(self):
        self.assertIn("Route RN17", rc.generer_cockpit([lead(titre="Route RN17")]))

    def test_json_leads_valide(self):
        h = rc.generer_cockpit([lead(valeur="9000000 EUR")])
        m = re.search(r"const RAW=(\[.*?\]), COORDS=", h)
        self.assertIsNotNone(m)
        data = json.loads(m.group(1))
        self.assertEqual(data[0]["valeur_meur"], 9.0)

    def test_coords_injectees(self):
        self.assertIn('"Mali"', rc.generer_cockpit([lead()]))

    def test_liste_vide_ne_plante_pas(self):
        h = rc.generer_cockpit([])
        self.assertIn("const RAW=[]", h)


class TestLot2(unittest.TestCase):
    """Geo (alertes), config suivi (bouton statut), entreprises 360."""

    def test_geo_injecte(self):
        geo = [{"pays": "Mali", "zone": "Sahel", "sens": "aggravation",
                "motif": "Dégradation", "severite": 8, "date": "2026-08-17"}]
        h = rc.generer_cockpit([lead()], geo=geo)
        self.assertNotIn("__GEO_JSON__", h)
        self.assertIn("Dégradation", h)

    def test_geo_defaut_vide(self):
        h = rc.generer_cockpit([lead()])
        self.assertIn("GEO=[]", h)

    def test_suivi_injecte(self):
        h = rc.generer_cockpit([lead()], suivi={"url": "https://x/exec",
                                                "token": "TK", "api": False})
        self.assertNotIn("__SUIVI_URL__", h)
        self.assertIn("https://x/exec", h)
        self.assertIn("SUIVI_TOKEN=\"TK\"", h)
        self.assertIn("API_STATUT=false", h)

    def test_suivi_absent_desactive(self):
        h = rc.generer_cockpit([lead()])
        self.assertIn('SUIVI_URL=""', h)
        self.assertIn("API_STATUT=false", h)

    def test_api_statut_vrai(self):
        h = rc.generer_cockpit([lead()], suivi={"api": True})
        self.assertIn("API_STATUT=true", h)

    def test_titulaire_present_pour_360(self):
        h = rc.generer_cockpit([lead(src="ATTRIB", entreprise="Onur Group")])
        self.assertIn("Onur Group", h)

    def test_bouton_surveiller_present(self):
        h = rc.generer_cockpit([lead()])
        self.assertIn("Surveiller", h)
        self.assertIn("toggleSurv", h)
        self.assertIn("estSurveille", h)

    def test_gagnant_detecte_mappe(self):
        """Une attribution parue expose le gagnant detecte (motif_ecart)."""
        h = rc.generer_cockpit([lead(statut="attribution_publiee",
                                     motif_ecart="Constructora Meco")])
        self.assertIn("Constructora Meco", h)

    def test_watchlist_injectee(self):
        wl = [{"entreprise": "Bouygues", "secteur": "BTP", "wl": "prives"}]
        h = rc.generer_cockpit([lead()], watchlist=wl)
        self.assertNotIn("__WATCHLIST_JSON__", h)
        self.assertIn("Bouygues", h)

    def test_watchlist_defaut_vide(self):
        h = rc.generer_cockpit([lead()])
        self.assertIn("WATCHLIST=[]", h)

    def test_type_depuis_grp_et_resume_depuis_justif(self):
        """Signal privé : le type vient de grp, le résumé de justif (schéma réel).
        comm est un score, jamais un résumé."""
        h = rc.generer_cockpit([lead(src="PRIVÉ", entreprise="Vinci",
                                     grp="delegation_mission",
                                     justif="mission éco MEDEF Mali", comm=7.5)])
        self.assertIn("delegation_mission", h)
        self.assertIn("mission éco MEDEF Mali", h)

    def test_charger_watchlist_mode_render(self):
        """Sans sheet_id/fichier (chemin Render/Postgres), aucune lecture Sheet :
        la watchlist se limite au BITD passe en argument."""
        wl = rc.charger_watchlist(None, None,
                                  [{"entreprise": "Thales", "secteur": "Défense"},
                                   {"nom": "Nexter", "secteur": "Défense"}])
        self.assertEqual(sorted(e["entreprise"] for e in wl), ["Nexter", "Thales"])
        self.assertTrue(all(e["wl"] == "bitd" for e in wl))


if __name__ == "__main__":
    unittest.main()
