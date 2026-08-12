# -*- coding: utf-8 -*-
"""Resolution contact international : titulaires etrangers eligibles a Hunter
sur le RELIQUAT de budget (12/08/2026).

Doctrine (validee) :
  Q1-A : reliquat seulement -> la watchlist Haute passe d'abord, l'existant
         n'est jamais degrade ;
  Q2-A : titulaires ETRANGERS uniquement, tries par taille de contrat ;
  Q3-A : email GENERIQUE d'abord (posture RGPD).

Hunter est simule (fetch injecte). Aucun reseau, aucune cle reelle.
"""

import os
import unittest

import enrichir_entreprises as e


# --- Fixtures Hunter Domain Search ------------------------------------------
def _hunter_reponse(personal=True, generic=True, domain="acme.com"):
    emails = []
    if personal:
        emails.append({"value": "j.doe@acme.com", "type": "personal",
                       "confidence": 92, "first_name": "Jane", "last_name": "Doe"})
    if generic:
        emails.append({"value": "contact@acme.com", "type": "generic",
                       "confidence": 80})
    return {"data": {"domain": domain, "emails": emails}}


class TestEmailGenerique(unittest.TestCase):
    def test_defaut_prefere_nominatif(self):
        m = e.meilleur_email_domaine(_hunter_reponse()["data"]["emails"])
        self.assertEqual(m["email"], "j.doe@acme.com")   # personnel prioritaire

    def test_prefer_generic_prend_le_generique(self):
        m = e.meilleur_email_domaine(_hunter_reponse()["data"]["emails"],
                                     prefer_generic=True)
        self.assertEqual(m["email"], "contact@acme.com")

    def test_prefer_generic_retombe_sur_nominatif_si_pas_de_generique(self):
        m = e.meilleur_email_domaine(_hunter_reponse(generic=False)["data"]["emails"],
                                     prefer_generic=True)
        self.assertEqual(m["email"], "j.doe@acme.com")   # dernier recours

    def test_domaine_hunter_mode_generique(self):
        old = os.environ.get("HUNTER_API_KEY")
        os.environ["HUNTER_API_KEY"] = "test"
        try:
            c = e.trouver_contact_domaine_hunter(
                "Acme", fetch=lambda u, p: _hunter_reponse(), prefer_generic=True)
        finally:
            if old is None: os.environ.pop("HUNTER_API_KEY", None)
            else: os.environ["HUNTER_API_KEY"] = old
        self.assertEqual(c["email"], "contact@acme.com")
        self.assertEqual(c["domaine"], "acme.com")


class TestAttributairesEtranger(unittest.TestCase):
    def test_extraction_etranger_et_valeur(self):
        vals = [["gagnant", "titulaire_etranger", "valeur_attribuee"],
                ["Bouygues", "oui", "45 millions EUR"],
                ["Local SARL", "non", "10 millions"]]
        out = {d["entreprise"]: d for d in e.entreprises_attributaires(vals)}
        self.assertTrue(out["Bouygues"]["etranger"])
        self.assertFalse(out["Local SARL"]["etranger"])
        self.assertEqual(out["Bouygues"]["valeur"], 45_000_000.0)

    def test_sans_colonne_etranger_defaut_false(self):
        vals = [["gagnant"], ["Vinci"]]
        self.assertFalse(e.entreprises_attributaires(vals)[0]["etranger"])


class TestSelectionReliquat(unittest.TestCase):
    def _comptes(self):
        return [{"entreprise": "FrHaute", "priorite_socle": "Haute"},
                {"entreprise": "BigForeign", "priorite_socle": "Moyenne",
                 "origine": "attributaire", "etranger": True, "valeur": 45e6},
                {"entreprise": "SmallForeign", "priorite_socle": "Moyenne",
                 "origine": "attributaire", "etranger": True, "valeur": 2e6},
                {"entreprise": "LocalTit", "priorite_socle": "Moyenne",
                 "origine": "attributaire", "etranger": False, "valeur": 99e6}]

    def test_watchlist_dabord_puis_titulaires(self):
        infos = {"frhaute": ("Jean Dupont", "gouv")}
        cibles = e.selectionner_cibles_hunter(self._comptes(), infos, set(), budget=3)
        noms = [c[0] for c in cibles]
        self.assertEqual(noms[0], "FrHaute")             # Haute servie en premier
        self.assertIn("BigForeign", noms)
        self.assertIn("SmallForeign", noms)

    def test_local_jamais_cible(self):
        cibles = e.selectionner_cibles_hunter(self._comptes(), {}, set(), budget=9)
        self.assertNotIn("LocalTit", [c[0] for c in cibles])

    def test_tri_par_valeur_decroissante(self):
        cibles = e.selectionner_cibles_hunter(self._comptes(), {}, set(), budget=9)
        tit = [c[0] for c in cibles if c[1] == "domaine" and c[3]]
        self.assertEqual(tit, ["BigForeign", "SmallForeign"])

    def test_reliquat_seulement_budget_epuise_par_haute(self):
        """Si la watchlist Haute consomme tout le budget, aucun titulaire."""
        comptes = [{"entreprise": "H1", "priorite_socle": "Haute"},
                   {"entreprise": "H2", "priorite_socle": "Haute"},
                   {"entreprise": "BigForeign", "priorite_socle": "Moyenne",
                    "origine": "attributaire", "etranger": True, "valeur": 45e6}]
        infos = {"h1": ("D1", "gouv"), "h2": ("D2", "gouv")}
        cibles = e.selectionner_cibles_hunter(comptes, infos, set(), budget=2)
        self.assertEqual(len(cibles), 2)
        self.assertNotIn("BigForeign", [c[0] for c in cibles])

    def test_titulaire_generique_true(self):
        cibles = e.selectionner_cibles_hunter(self._comptes(), {}, set(), budget=9)
        for nom, methode, dirig, generique in cibles:
            if nom in ("BigForeign", "SmallForeign"):
                self.assertTrue(generique)               # RGPD : generique

    def test_deja_tente_saute(self):
        cibles = e.selectionner_cibles_hunter(
            self._comptes(), {}, deja={"bigforeign"}, budget=9)
        self.assertNotIn("BigForeign", [c[0] for c in cibles])


if __name__ == "__main__":
    unittest.main()
