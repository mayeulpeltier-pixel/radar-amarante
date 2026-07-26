# -*- coding: utf-8 -*-
"""Tests Prozorro : cribles structurels, entonnoir de collecte, classification
avis/attribution, normalisation, stabilite du schema Sheet.

Aucun reseau (fetch_liste / fetch_detail injectes), aucun LLM reel
(ted.appeler_modele remplace par une reponse figee). Rappel doctrine : le flux
Prozorro ne portant que {id, dateModified}, les cribles s'appliquent APRES le
telechargement du detail -- c'est teste ici sur des details fictifs.
"""

import json
import unittest
from datetime import datetime, timezone, timedelta

import ted_complet_v14 as ted
import prozorro_radar as pz


def _iso(delta_jours):
    return (datetime.now(timezone.utc) - timedelta(days=delta_jours)).isoformat()


# --- Details fictifs couvrant chaque branche des cribles -------------------
DETAILS = {
    # Crible 1 : methode bruit -> rejet_methode.
    "t1": {"id": "t1", "tenderID": "UA-1", "status": "complete",
           "procurementMethodType": "reporting",
           "value": {"amount": 9000000, "currency": "UAH"},
           "items": [{"classification": {"scheme": "ДК021", "id": "45000000-7"}}]},
    # Crible 2 : valeur sous le seuil -> rejet_valeur.
    "t2": {"id": "t2", "tenderID": "UA-2", "status": "active.tendering",
           "procurementMethodType": "aboveThreshold",
           "value": {"amount": 100000, "currency": "UAH"},
           "items": [{"classification": {"scheme": "ДК021", "id": "45250000-1"}}]},
    # Crible 3 : CPV hors cible (division 80) -> rejet_cpv.
    "t3": {"id": "t3", "tenderID": "UA-3", "status": "active.tendering",
           "procurementMethodType": "aboveThreshold",
           "value": {"amount": 5000000, "currency": "UAH"},
           "items": [{"classification": {"scheme": "ДК021", "id": "80000000-4"}}]},
    # Survivant AVIS ouvert (division 45 admise).
    "t4": {"id": "t4", "tenderID": "UA-4", "status": "active.tendering",
           "procurementMethodType": "aboveThreshold",
           "title": "Реконструкція мосту",
           "value": {"amount": 5000000, "currency": "UAH"},
           "procuringEntity": {"name": "Служба відновлення",
                               "address": {"region": "Харківська область"}},
           "tenderPeriod": {"endDate": "2026-08-15T00:00:00+03:00"},
           "date": "2026-07-26T10:00:00+03:00",
           "items": [{"classification": {"scheme": "ДК021", "id": "45250000-1"},
                      "description": "будівельні роботи"}]},
    # Survivant ATTRIBUTION (award actif + fournisseur ; division 09 admise).
    "t5": {"id": "t5", "tenderID": "UA-5", "status": "complete",
           "procurementMethodType": "aboveThreshold",
           "title": "Постачання пального",
           "value": {"amount": 10000000, "currency": "UAH"},
           "items": [{"classification": {"scheme": "ДК021", "id": "09210000-4"}}],
           "awards": [{"status": "active", "suppliers": [{"name": "ТОВ Приклад"}]}]},
}


def _feed(url):
    """Page 1 : t1..t5 recents. Page 2 : une entree trop vieille -> arret fenetre."""
    if "offset=PAGE2" in url:
        return {"data": [{"id": "vieux", "dateModified": _iso(30)}]}
    return {
        "data": [{"id": k, "dateModified": _iso(1)} for k in ("t1", "t2", "t3", "t4", "t5")],
        "next_page": {"uri": pz.ENDPOINT_LISTE + "?descending=1&limit=100&offset=PAGE2"},
    }


def _detail(tid):
    return DETAILS.get(tid, {})


class TestCribles(unittest.TestCase):
    def test_cpv_division_admise(self):
        self.assertTrue(pz._cpv_admissible(["45250000"]))   # 45 largement admise

    def test_cpv_division_hors_cible(self):
        self.assertFalse(pz._cpv_admissible(["80000000"]))  # 80 non admise

    def test_cpv_code_precis_admis_malgre_division_conditionnelle(self):
        # Un code precis toujours admis passe meme si sa division (75) n'est que
        # conditionnelle (gating mots-cles, ecarte sur Prozorro). On lit le set
        # reel du coeur plutot que de coder un code en dur.
        code_precis = next(iter(ted.CODES_PRECIS_TOUJOURS_ADMIS))
        self.assertEqual(code_precis[:2], "75")   # division conditionnelle
        self.assertTrue(pz._cpv_admissible([code_precis]))

    def test_cpv_absent_rejete(self):
        self.assertFalse(pz._cpv_admissible([]))

    def test_valeur_conversion_et_seuil(self):
        uah, txt = pz._valeur_uah({"value": {"amount": 5000000, "currency": "UAH"}})
        self.assertEqual(uah, 5000000.0)
        self.assertIn("UAH", txt)

    def test_valeur_devise_inconnue_ne_rejette_pas(self):
        # Devise hors table : montant_uah None -> le crible valeur ne rejette pas.
        uah, _ = pz._valeur_uah({"value": {"amount": 10, "currency": "XYZ"}})
        self.assertIsNone(uah)

    def test_codes_cpv_normalises_8_chiffres(self):
        codes = pz._codes_cpv(DETAILS["t4"])
        self.assertEqual(codes, ["45250000"])   # cle '-1' retiree

    def test_est_attribution(self):
        self.assertTrue(pz._est_attribution(DETAILS["t5"]))
        self.assertFalse(pz._est_attribution(DETAILS["t4"]))


class TestEntonnoir(unittest.TestCase):
    def test_collecte_complet(self):
        avis, attributions, c = pz.collecte(
            session=object(), fetch_liste=_feed, fetch_detail=_detail)
        # Un seul avis ouvert survivant (t4), une seule attribution (t5).
        self.assertEqual(c["survivants_avis"], 1)
        self.assertEqual(c["survivants_attribution"], 1)
        self.assertEqual(c["rejet_methode"], 1)   # t1
        self.assertEqual(c["rejet_valeur"], 1)     # t2
        self.assertEqual(c["rejet_cpv"], 1)        # t3
        self.assertEqual(len(avis), 1)
        self.assertEqual(len(attributions), 1)
        self.assertEqual(c["arret"], "fin_fenetre")   # stoppe sur l'entree vieille

    def test_deja_vus_saute_le_detail(self):
        # t4 deja connu : compte comme deja_connu, pas de survivant avis.
        avis, _attr, c = pz.collecte(
            session=object(), fetch_liste=_feed, fetch_detail=_detail,
            deja_vus={"PZt4"})
        self.assertEqual(c["deja_connus"], 1)
        self.assertEqual(c["survivants_avis"], 0)
        self.assertEqual(len(avis), 0)


class TestNormalisation(unittest.TestCase):
    def test_champs_cles(self):
        a = pz.normaliser(DETAILS["t4"])
        self.assertEqual(a["publication_number"], "PZt4")
        self.assertEqual(a["pays_iso3"], "UKR")
        self.assertEqual(a["pays_execution"], "Ukraine")
        self.assertEqual(a["cpv"], "45250000")
        self.assertEqual(a["region"], "Харківська область")
        self.assertTrue(a["lien_avis"].endswith("/UA-4"))   # tenderID lisible
        self.assertIn("UAH", a["valeur_estimee"])

    def test_avis_pour_scoring_force_iso3_et_garde_cpv(self):
        a = pz.normaliser(DETAILS["t4"])
        s = pz.avis_pour_scoring(a)
        self.assertEqual(s["pays_execution"], "UKR")   # multiplicateur de zone
        self.assertEqual(s["cpv"], "45250000")          # bonus infra conserve


class TestSchemaSheet(unittest.TestCase):
    def test_publication_number_present_et_ligne_alignee(self):
        # Schema positionnel : publication_number doit exister, et une ligne
        # generee doit avoir exactement len(COLONNES) cellules (regle 4).
        self.assertIn("publication_number", pz.COLONNES)

        ted.appeler_modele = lambda prompt, modele=None: json.dumps({
            "deploiement_terrain_reel": True, "type_mobilite": "chantier",
            "profil_personnes_exposees": "technicien", "securite_existante": "aucune",
            "type_activite": "supervision_chantier", "type_client": "etat_administration_locale",
            "duree_estimee": "longue_ou_residente", "accessibilite_commerciale": "moyenne",
            "profils_acteurs_probables": ["entreprise BTP"],
            "besoin_securite_operationnel_probable": True,
            "niveau_opportunite_amarante": "fort",
            "justification": "Chantier de reconstruction, escorte probable.",
            "confiance": 0.8,
        })
        avis = pz.normaliser(DETAILS["t4"])
        extraction = pz.analyser(avis)
        self.assertIsNotNone(extraction)
        self.assertIn("securite_existante_detectee", extraction)   # enum normalisee
        s, c, f = ted.calculer_scores(pz.avis_pour_scoring(avis), extraction)
        r = {"avis": avis, "extraction": extraction, "surete": s,
             "commercial": c, "score": f, "raffine": False, "divergence": False}
        ligne = pz.ligne_depuis_resultat(r)
        self.assertEqual(len(ligne), len(pz.COLONNES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
