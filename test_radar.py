# -*- coding: utf-8 -*-
"""
Radar Amarante -- Tests unitaires cibles
=========================================

Filet de securite pour l'automatisation. Ces tests verifient les fonctions
PURES (sans reseau ni cle API) : celles qu'on regle a la main a chaque
modification de filtre. Les figer ici permet a GitHub Actions de BLOQUER un
deploiement si une modif casse une regle metier.

Choix volontaire : bibliotheque standard `unittest`, AUCUNE dependance a
installer (pas de pytest). Lancement :
    python -m unittest test_radar -v
    (ou simplement : python test_radar.py)

Pre-requis : ted_complet_v14.py et ted_complet_bm.py dans le meme dossier.
"""

import unittest
from datetime import date

import ted_complet_v14 as ted
import ted_complet_bm as bm


# ===========================================================================
# 1. PARSING DE DATE (robustesse au format Banque Mondiale)
# ===========================================================================
class TestParsingDate(unittest.TestCase):

    def test_format_bm_standard(self):
        self.assertEqual(bm._date_notice({"noticedate": "28-Oct-2025"}),
                         date(2025, 10, 28))

    def test_format_iso(self):
        self.assertEqual(bm._date_notice({"noticedate": "2025-10-28"}),
                         date(2025, 10, 28))

    def test_format_illisible_renvoie_none(self):
        # Ne doit pas planter, juste renvoyer None (avis ignore proprement).
        self.assertIsNone(bm._date_notice({"noticedate": "pas-une-date"}))

    def test_date_absente_renvoie_none(self):
        self.assertIsNone(bm._date_notice({"noticedate": ""}))
        self.assertIsNone(bm._date_notice({}))


# ===========================================================================
# 2. MAPPING PAYS (le piege des sous-chaines, deja corrige)
# ===========================================================================
class TestMappingPays(unittest.TestCase):

    def test_romania_n_est_pas_oman(self):
        # "oman" est contenu dans "romania" : un repli par sous-chaine
        # classerait Romania en Oman (orange). Doit rester non mappe.
        self.assertEqual(bm.code_iso3_pays("romania"), "")

    def test_nom_avec_virgule(self):
        # "Egypt, Arab Republic of" -> partie avant la virgule -> EGY.
        self.assertEqual(bm.code_iso3_pays("Egypt, Arab Republic of"), "EGY")

    def test_pays_simple(self):
        self.assertEqual(bm.code_iso3_pays("mali"), "MLI")

    def test_pays_inconnu(self):
        self.assertEqual(bm.code_iso3_pays("Pays Imaginaire"), "")


# ===========================================================================
# 3. PRE-FILTRE cible_amarante (risque pays + exclusions + override surete)
# ===========================================================================
def _rec(titre, pays="Mali", groupe="CS"):
    return {"bid_description": titre, "project_name": "",
            "project_ctry_name": pays, "procurement_group": groupe,
            "notice_type": "Request for Expression of Interest",
            "notice_status": "Published"}


class TestPreFiltre(unittest.TestCase):

    def test_lead_zone_risque_garde(self):
        self.assertTrue(bm.cible_amarante(
            _rec("Controle et surveillance des travaux multi-sites", "Niger")))

    def test_pays_sous_seuil_exclu(self):
        # Tunisie = tier 0.3 < TIER_RISQUE_MINIMAL (0.6) -> exclu d'office.
        self.assertFalse(bm.cible_amarante(
            _rec("Controle et surveillance des travaux", "Tunisia")))

    def test_exclusion_ecoles_pluriel(self):
        # "ecole" (racine) doit attraper "ecoles".
        self.assertFalse(bm.cible_amarante(
            _rec("Construction de cinq ecoles primaires", "Mali")))

    def test_override_surete_garde_malgre_audit(self):
        # "audit" est exclu, mais "physical security" force la conservation.
        self.assertTrue(bm.cible_amarante(
            _rec("Physical security audit for remote sites", "Mali")))

    def test_safeguarding_exclu(self):
        # racine "safeguard" attrape "safeguarding".
        self.assertFalse(bm.cible_amarante(
            _rec("Social safeguarding officer", "Mali")))

    def test_preschool_pas_confondu_avec_school(self):
        # Borne de DEBUT : "school" ne matche pas dans "preschool".
        self.assertTrue(bm.cible_amarante(
            _rec("Construction of preschool sport facility", "Mali")))

    def test_override_guarding_pas_safeguarding(self):
        # "guarding" (override) ne doit PAS se declencher sur "safeguarding".
        # Ici safeguarding seul -> exclu (pas d'override parasite).
        self.assertFalse(bm.cible_amarante(
            _rec("Environmental safeguarding specialist", "Niger")))


# ===========================================================================
# 4. PONT DE SCORING avis_pour_scoring (Fix 4 : humain avant beton)
# ===========================================================================
class TestPontScoring(unittest.TestCase):

    def _avis_cw(self):
        return {"procurement_group": "CW", "pays_iso3": "NER",
                "pays_execution": "Niger", "cpv": ""}

    def test_bonus_infra_si_expert_international(self):
        copie = bm.avis_pour_scoring(
            self._avis_cw(), {"profil_personnes_exposees": "expert_international"})
        self.assertEqual(copie["cpv"], "45000000")

    def test_pas_de_bonus_si_ouvrier_local(self):
        copie = bm.avis_pour_scoring(
            self._avis_cw(), {"profil_personnes_exposees": "ouvrier_local"})
        self.assertEqual(copie.get("cpv", ""), "")

    def test_pas_de_bonus_sans_extraction(self):
        copie = bm.avis_pour_scoring(self._avis_cw(), None)
        self.assertEqual(copie.get("cpv", ""), "")

    def test_pays_execution_devient_iso3(self):
        # Pour le scoring, pays_execution doit etre l'ISO3 (multiplicateur).
        copie = bm.avis_pour_scoring(
            self._avis_cw(), {"profil_personnes_exposees": "expert_international"})
        self.assertEqual(copie["pays_execution"], "NER")


# ===========================================================================
# 5. CIBLE COMMERCIALE (qui demarcher reellement)
# ===========================================================================
class TestCibleCommerciale(unittest.TestCase):

    def test_travaux_pointe_titulaire_btp(self):
        texte = bm.cible_commerciale({"procurement_group": "CW"}, {}).lower()
        self.assertIn("travaux", texte)
        self.assertIn("pas l'agence", texte)

    def test_conseil_pointe_consortium(self):
        texte = bm.cible_commerciale({"procurement_group": "CS"}, {}).lower()
        self.assertIn("conseil", texte)


# ===========================================================================
# 6. FILTRE DE PERTINENCE avis_correspond_bm
# ===========================================================================
class TestCorrespondBM(unittest.TestCase):

    def test_publie_cs_valide(self):
        self.assertTrue(bm.avis_correspond_bm(_rec("x", "Mali", "CS")))

    def test_non_publie_rejete(self):
        rec = _rec("x", "Mali", "CS")
        rec["notice_status"] = "Cancelled"
        self.assertFalse(bm.avis_correspond_bm(rec))

    def test_groupe_goods_rejete(self):
        # GO (fournitures) hors perimetre : seuls CS et CW.
        self.assertFalse(bm.avis_correspond_bm(_rec("x", "Mali", "GO")))


# ===========================================================================
# 7. COEUR TED : action recommandee (coupe-circuits doctrine)
# ===========================================================================
class TestActionRecommandee(unittest.TestCase):

    def test_securite_existante_ignore(self):
        action = ted.calculer_action_recommandee(
            9.0, {"securite_existante_detectee": True,
                  "accessibilite_commerciale": "facile"}, surete=9.0)
        self.assertEqual(action, "ignorer")

    def test_score_fort_accessible_contacter(self):
        action = ted.calculer_action_recommandee(
            7.0, {"securite_existante_detectee": False,
                  "accessibilite_commerciale": "facile"}, surete=7.0)
        self.assertEqual(action, "contacter")

    def test_marche_difficile_surveiller(self):
        # Score fort mais marche difficile -> on ne dit pas "contacter".
        action = ted.calculer_action_recommandee(
            7.0, {"securite_existante_detectee": False,
                  "accessibilite_commerciale": "difficile"}, surete=7.0)
        self.assertEqual(action, "surveiller")

    def test_extraction_absente_ignore(self):
        self.assertEqual(
            ted.calculer_action_recommandee(8.0, None), "ignorer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
