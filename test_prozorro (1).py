# -*- coding: utf-8 -*-
"""
Tests du collecteur Prozorro (Ukraine). HORS-LIGNE : fetch injectes, aucun
reseau, aucun appel LLM. Verifie l'entonnoir (pre-filtre feed, CPV), le mapping
canonique des avis, l'extraction des attributions (awards[]), et l'arite des
lignes ecrites.
"""

import unittest
from datetime import datetime, timezone, timedelta

import prozorro_radar as pz
import bm_attributions


def _iso(jours=0):
    d = datetime.now(timezone.utc) - timedelta(days=jours)
    return d.strftime("%Y-%m-%dT%H:%M:%S+00:00")


# --- Faux feed : une page puis vide -----------------------------------------
def faux_feed(elements):
    etat = {"servi": False}

    def _fetch(params):
        if etat["servi"]:
            return {"data": [], "next_page": {"offset": None}}
        etat["servi"] = True
        return {"data": elements, "next_page": {"offset": "x"}}
    return _fetch


def faux_fiche(fiches_par_id):
    def _fetch(tid):
        return {"data": fiches_par_id.get(tid, {})}
    return _fetch


class TestPreFiltreFeed(unittest.TestCase):
    def test_rejette_bruit_statut_et_connus(self):
        elements = [
            {"id": "a", "tenderID": "UA-A", "status": "active.tendering",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(1)},
            {"id": "b", "tenderID": "UA-B", "status": "active.tendering",
             "procurementMethodType": "belowThreshold", "dateModified": _iso(1)},   # bruit
            {"id": "c", "tenderID": "UA-C", "status": "unsuccessful",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(1)},  # hors statut
            {"id": "d", "tenderID": "UA-D", "status": "active.tendering",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(1)},  # connu
        ]
        cand, stats = pz.collecter_candidats(
            fetch_feed=faux_feed(elements), connus={"UA-D"},
            borne_jours=14, statuts=pz.STATUTS_AVIS)
        ids = {c["tenderID"] for c in cand}
        self.assertEqual(ids, {"UA-A"})
        self.assertEqual(stats["bruit_methode"], 1)
        self.assertEqual(stats["hors_statut"], 1)
        self.assertEqual(stats["deja_connus"], 1)

    def test_arret_hors_fenetre(self):
        elements = [
            {"id": "a", "tenderID": "UA-A", "status": "active.tendering",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(400)},
            {"id": "b", "tenderID": "UA-B", "status": "active.tendering",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(500)},
        ]
        cand, stats = pz.collecter_candidats(
            fetch_feed=faux_feed(elements), borne_jours=14, statuts=pz.STATUTS_AVIS)
        self.assertEqual(cand, [])
        self.assertEqual(stats["hors_fenetre"], 2)


class TestCPV(unittest.TestCase):
    def _fiche(self, code):
        return {"tenderID": "UA-X", "title": "Test",
                "items": [{"classification": {"scheme": "CPV", "id": code}}]}

    def test_ingenierie_admise(self):
        ok, codes = pz.cpv_pertinent(self._fiche("71311000-1"))
        self.assertTrue(ok)
        self.assertEqual(codes, ["71311000"])          # suffixe -1 retire

    def test_alimentaire_rejete(self):
        ok, _ = pz.cpv_pertinent(self._fiche("15800000-6"))
        self.assertFalse(ok)                            # division 15 hors doctrine


class TestMappingAvis(unittest.TestCase):
    def test_fiche_vers_avis(self):
        fiche = {
            "tenderID": "UA-2026-01-01-000001",
            "title": "Реконструкція мосту", "title_en": "",
            "description": "Опис робіт", "status": "active.tendering",
            "procurementMethodType": "aboveThresholdEU",
            "procuringEntity": {"name": "Служба відновлення"},
            "value": {"amount": 5000000, "currency": "UAH"},
            "tenderPeriod": {"endDate": _iso(-10)},
            "dateCreated": "2026-01-05T09:00:00+02:00",
            "items": [{"classification": {"scheme": "CPV", "id": "45221000-2"}}],
        }
        a = pz.fiche_vers_avis(fiche)
        self.assertEqual(a["pays_execution"], "UKR")
        self.assertEqual(a["pays_acheteur"], "UKR")
        self.assertEqual(a["publication_number"], "UA-2026-01-01-000001")
        self.assertEqual(a["cpv"], "45221000")
        self.assertEqual(a["acheteur"], "Служба відновлення")
        self.assertTrue(a["lien_avis"].endswith("UA-2026-01-01-000001"))
        self.assertIn("UAH", a["valeur_estimee"])


class TestAttributions(unittest.TestCase):
    def _fiche_awards(self, supplier, statut_award="active", jours=10):
        return {
            "tenderID": "UA-2026-02-02-000009", "title": "Будівництво",
            "status": "complete",
            "procuringEntity": {"name": "Замовник"},
            "items": [{"classification": {"scheme": "CPV", "id": "45000000-7"}}],
            "awards": [{
                "id": "aw1", "status": statut_award, "date": _iso(jours),
                "value": {"amount": 9000000, "currency": "UAH"},
                "suppliers": [supplier],
            }],
        }

    def test_titulaire_local(self):
        sup = {"name": "ТОВ Будівельник",
               "identifier": {"scheme": "UA-EDR", "id": "12345678"}}
        atts = pz.fiche_vers_attributions(self._fiche_awards(sup))
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["titulaire_etranger"], "non")
        self.assertEqual(atts[0]["pays_titulaire"], "UKR")

    def test_titulaire_etranger_est_cible(self):
        sup = {"name": "Bouygues Construction",
               "identifier": {"scheme": "FR-SIREN", "id": "397480930"},
               "address": {"countryName": "France"}}
        atts = pz.fiche_vers_attributions(self._fiche_awards(sup))
        self.assertEqual(atts[0]["titulaire_etranger"], "oui")
        self.assertEqual(atts[0]["a_demarcher"], "oui")
        self.assertEqual(atts[0]["pays_titulaire"], "France")

    def test_award_hors_fenetre_ignore(self):
        sup = {"name": "ТОВ X", "identifier": {"scheme": "UA-EDR", "id": "1"}}
        atts = pz.fiche_vers_attributions(
            self._fiche_awards(sup, jours=pz.JOURS_ATTRIB + 30))
        self.assertEqual(atts, [])

    def test_award_non_actif_ignore(self):
        sup = {"name": "ТОВ X", "identifier": {"scheme": "UA-EDR", "id": "1"}}
        atts = pz.fiche_vers_attributions(
            self._fiche_awards(sup, statut_award="cancelled"))
        self.assertEqual(atts, [])

    def test_schema_attribution_complet(self):
        """Toutes les colonnes partagees sont presentes (onglet attributions)."""
        sup = {"name": "ТОВ X", "identifier": {"scheme": "UA-EDR", "id": "1"}}
        a = pz.fiche_vers_attributions(self._fiche_awards(sup))[0]
        for c in bm_attributions.COLONNES:
            self.assertIn(c, a, "colonne manquante : {}".format(c))


class TestArite(unittest.TestCase):
    def test_ligne_avis_bonne_longueur(self):
        avis = pz.fiche_vers_avis({
            "tenderID": "UA-1", "title": "T",
            "items": [{"classification": {"scheme": "CPV", "id": "71000000-8"}}],
        })
        r = {"avis": avis, "extraction": None, "score": 0.0, "surete": 0.0,
             "commercial": 0.0, "raffine": False, "divergence": ""}
        ligne = pz.ligne_depuis_resultat(r)
        self.assertEqual(len(ligne), len(pz.COLONNES))
        self.assertTrue(all(isinstance(x, str) for x in ligne))


class TestCollecteBoutABout(unittest.TestCase):
    def test_tout_produit_avis_et_attributions(self):
        elements = [
            {"id": "id-avis", "tenderID": "UA-AVIS", "status": "active.tendering",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(2)},
            {"id": "id-attr", "tenderID": "UA-ATTR", "status": "complete",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(3)},
            {"id": "id-hors", "tenderID": "UA-HORS", "status": "active.tendering",
             "procurementMethodType": "aboveThresholdEU", "dateModified": _iso(2)},
        ]
        fiches = {
            "id-avis": {"tenderID": "UA-AVIS", "title": "Ingenierie pont",
                        "status": "active.tendering",
                        "procuringEntity": {"name": "PE"},
                        "tenderPeriod": {"endDate": _iso(-15)},
                        "items": [{"classification": {"scheme": "CPV", "id": "71311000-1"}}]},
            "id-attr": {"tenderID": "UA-ATTR", "title": "Travaux", "status": "complete",
                        "procuringEntity": {"name": "PE"},
                        "items": [{"classification": {"scheme": "CPV", "id": "45000000-7"}}],
                        "awards": [{"id": "a", "status": "active", "date": _iso(20),
                                    "value": {"amount": 8000000, "currency": "UAH"},
                                    "suppliers": [{"name": "Firm SA",
                                                   "identifier": {"scheme": "DE-CR"},
                                                   "address": {"countryName": "Germany"}}]}]},
            "id-hors": {"tenderID": "UA-HORS", "title": "Nourriture",
                        "status": "active.tendering",
                        "procuringEntity": {"name": "PE"},
                        "items": [{"classification": {"scheme": "CPV", "id": "15800000-6"}}]},
        }
        avis, attributions, echantillon, stats = pz.collecter(
            "tout", connus=set(),
            fetch_feed=faux_feed(elements), fetch_fiche=faux_fiche(fiches))
        # L'avis d'ingenierie passe ; la nourriture est hors CPV.
        self.assertEqual([a["publication_number"] for a in avis], ["UA-AVIS"])
        self.assertEqual(stats["hors_cpv"], 1)
        # L'attribution etrangere (Firm SA) est extraite.
        self.assertEqual(len(attributions), 1)
        self.assertEqual(attributions[0]["titulaire_etranger"], "oui")
        # collecter() n'ecrit rien (pas d'appel Sheet/PG).
        self.assertTrue(isinstance(echantillon, list))


if __name__ == "__main__":
    unittest.main(verbosity=2)
