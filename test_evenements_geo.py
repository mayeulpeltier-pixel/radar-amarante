# -*- coding: utf-8 -*-
"""Collecteur d'evenements geopolitiques (veille pays via Google News).

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Deuxieme brique de la veille "signaux faibles" (le "niveau 10" du document
strategique). Quand un coup d'Etat, une evacuation ou un attentat majeur
survient dans un pays du perimetre, les entreprises deja presentes deviennent
prospects. ACLED aurait ete la source academique, mais ses conditions
restreignent l'usage LLM et la revente, avec un risque juridique reel pour un
projet commercial qui vend de la due diligence. On obtient donc le meme besoin
par Google News, deja legal et deja teste.

CE QUE CES TESTS VERROUILLENT
-----------------------------
  - LE STRICT. Un bandeau d'evenements dilue est inutile : `est_pertinent`
    doit ecarter l'actu ordinaire, l'homonymie (article ne concernant pas le
    pays), la faible gravite et la faible confiance. C'est le coeur du filtre.
  - LE SCHEMA PARTAGE avec les alertes voyageurs : un evenement s'affiche dans
    le MEME bandeau "contexte pays". Si les schemas divergeaient, l'affichage
    casserait.
  - LA NORMALISATION ferme le vocabulaire : un type inconnu retombe sur
    "autre", jamais une valeur libre infiltrable dans le dashboard.

Aucun appel reseau ni LLM : les extractions sont injectees.
"""

import unittest

import evenements_geo as geo


def _ext(**surcharge):
    base = {"concerne_le_pays": True, "type_evenement": "coup_etat",
            "gravite": 5, "resume_court": "Putsch a Bamako", "confiance": 0.9}
    base.update(surcharge)
    return base


# ===========================================================================
# NORMALISATION
# ===========================================================================

class TestNormalisation(unittest.TestCase):

    def test_type_inconnu_retombe_sur_autre(self):
        e = geo.normaliser_evenement(_ext(type_evenement="revolution_totale"))
        self.assertEqual(e["type_evenement"], "autre")

    def test_gravite_bornee(self):
        self.assertEqual(geo.normaliser_evenement(_ext(gravite=9))["gravite"], 5)
        self.assertEqual(geo.normaliser_evenement(_ext(gravite=-3))["gravite"], 0)

    def test_gravite_illisible(self):
        self.assertEqual(geo.normaliser_evenement(_ext(gravite="beaucoup"))["gravite"], 0)

    def test_confiance_bornee(self):
        self.assertEqual(geo.normaliser_evenement(_ext(confiance=5))["confiance"], 1.0)
        self.assertEqual(geo.normaliser_evenement(_ext(confiance="x"))["confiance"], 0.5)

    def test_entree_degeneree(self):
        self.assertIsNone(geo.normaliser_evenement(None))
        self.assertIsNone(geo.normaliser_evenement("texte"))


# ===========================================================================
# PERTINENCE : LE STRICT
# ===========================================================================

class TestPertinence(unittest.TestCase):

    def test_evenement_net_retenu(self):
        self.assertTrue(geo.est_pertinent(geo.normaliser_evenement(_ext())))

    def test_actu_ordinaire_ecartee(self):
        """type 'aucun' = pas une rupture securitaire."""
        self.assertFalse(geo.est_pertinent(geo.normaliser_evenement(
            _ext(type_evenement="aucun", gravite=0))))

    def test_homonymie_ecartee(self):
        """L'article ne concerne pas reellement le pays surveille."""
        self.assertFalse(geo.est_pertinent(geo.normaliser_evenement(
            _ext(concerne_le_pays=False))))

    def test_gravite_trop_faible_ecartee(self):
        self.assertFalse(geo.est_pertinent(geo.normaliser_evenement(
            _ext(type_evenement="tension_diplomatique", gravite=1))))

    def test_confiance_trop_faible_ecartee(self):
        """Un evenement grave mais peu sur ne doit pas polluer le bandeau."""
        self.assertFalse(geo.est_pertinent(geo.normaliser_evenement(
            _ext(confiance=0.3))))

    def test_evenement_vide(self):
        self.assertFalse(geo.est_pertinent(None))


# ===========================================================================
# LEAD ET SCHEMA PARTAGE
# ===========================================================================

class TestLead(unittest.TestCase):

    ART = {"titre": "Coup d'Etat au Mali", "lien": "http://news/mali-coup",
           "resume": "L'armee prend le pouvoir."}

    def test_schema_identique_aux_alertes(self):
        """Le bandeau 'contexte pays' est partage : les deux collecteurs
        DOIVENT ecrire le meme schema, sinon l'affichage casse."""
        import alertes_voyageurs as av
        self.assertEqual(geo.COLONNES, av.COLONNES)

    def test_lead_complet(self):
        e = geo.normaliser_evenement(_ext())
        lead = geo.lead_evenement("MLI", "Mali", "Sahel", self.ART, e)
        for colonne in geo.COLONNES:
            self.assertIn(colonne, lead)
        self.assertEqual(lead["pays_execution"], "MLI")
        self.assertEqual(lead["sens"], "aggravation")
        self.assertEqual(lead["severite"], 5)

    def test_publication_number_stable(self):
        """Meme article, meme cle : deux runs ne creent pas deux lignes.
        C'est ce qui permet a `deja_vus` de dedupliquer."""
        e = geo.normaliser_evenement(_ext())
        l1 = geo.lead_evenement("MLI", "Mali", "Sahel", self.ART, e)
        l2 = geo.lead_evenement("MLI", "Mali", "Sahel", self.ART, e)
        self.assertEqual(l1["publication_number"], l2["publication_number"])
        self.assertTrue(l1["publication_number"].startswith("GEO-MLI-"))

    def test_le_motif_retombe_sur_le_titre_si_resume_vide(self):
        e = geo.normaliser_evenement(_ext(resume_court=""))
        lead = geo.lead_evenement("MLI", "Mali", "Sahel", self.ART, e)
        self.assertEqual(lead["motif"], self.ART["titre"])


# ===========================================================================
# REQUETE
# ===========================================================================

class TestRequete(unittest.TestCase):

    def test_le_pays_est_ancre_entre_guillemets(self):
        r = geo.construire_requete("Burkina Faso")
        self.assertIn('"Burkina Faso"', r)

    def test_les_declencheurs_sont_bilingues(self):
        r = geo.construire_requete("Mali")
        self.assertIn("coup d'etat", r)
        self.assertIn("state of emergency", r)


# ===========================================================================
# ANALYSE (LLM injecte)
# ===========================================================================

class TestAnalyse(unittest.TestCase):

    ART = {"titre": "Putsch au Mali", "resume": "L'armee prend le pouvoir.",
           "lien": "http://x"}

    def test_analyse_json_direct(self):
        import json
        reponse = json.dumps(_ext())
        e = geo.analyser_article("Mali", self.ART, appel=lambda p: reponse)
        self.assertEqual(e["type_evenement"], "coup_etat")
        self.assertTrue(geo.est_pertinent(e))

    def test_llm_muet_renvoie_none(self):
        e = geo.analyser_article("Mali", self.ART, appel=lambda p: None)
        self.assertIsNone(e)

    def test_le_prompt_nomme_le_pays_et_est_strict(self):
        capturé = {}
        def espion(prompt):
            capturé["p"] = prompt
            import json
            return json.dumps(_ext(type_evenement="aucun", gravite=0))
        geo.analyser_article("Niger", self.ART, appel=espion)
        self.assertIn("Niger", capturé["p"])
        self.assertIn("STRICT", capturé["p"])


if __name__ == "__main__":
    unittest.main()
