# -*- coding: utf-8 -*-
"""Tests ADB : bascule sur l'API JSON SearchStax (01/11/2026).

L'ancien collecteur grattait la page HTML (rendue en JS -> vide -> source
desactivee). Il consomme desormais l'API Solr que la page appelle elle-meme.
On verifie : le mapping document Solr -> notice (champs multivalues, lien
relatif rendu absolu, reference stable), la resolution pays / filtrage hors-zone
(inchanges), la requete serveur (filtre `status:Active` + jeton), la
compatibilite avec le coeur TED, et le cablage du miroir Postgres. Aucun reseau,
aucun LLM.

Pourquoi plus de fenetre de fraicheur : le filtre serveur `status:Active` ne
garde que les marches OUVERTS (plus juste qu'une fenetre sur la date de
publication), et la memoire gere le dedup (regle 9)."""

import unittest

import adb_radar as adb
import radar_stockage
import ted_complet_v14 as ted


def _doc(country, title, ntype, approval, closing, posted, file_url=None, pid=None):
    """Fabrique un document Solr ADB (champs texte MULTIVALUES, comme l'API)."""
    d = {"tm_X3b_en_country": [country], "tm_X3b_en_title": [title],
         "tm_X3b_en_type": [ntype], "tm_X3b_en_sector": ["Transport"],
         "tm_X3b_en_status": ["Active"], "id": "doc-{}".format(approval or "x"),
         "ds_date_closing": closing, "ds_date_posted": posted}
    if approval:
        d["tm_X3b_en_approval_number"] = [approval]
    if pid:
        d["tm_X3b_en_project_number"] = [pid]
    if file_url:
        d["ss_file_url"] = file_url
    return d


# Zone suivie : Sri Lanka, PNG, Afghanistan. Hors zone : Viet Nam. Sans pays :
# Regional. Afghanistan est une Prequalification (phase amont).
FAKE_DOCS = [
    _doc("Sri Lanka", "Mahaweli Water Security Investment Program", "Invitation for Bids",
         "4741", "2026-12-15T12:00:00Z", "2026-10-20T12:00:00Z",
         file_url="/sites/default/files/tenders/sri4741-ifb.pdf"),
    _doc("Papua New Guinea", "Urban Water Supply and Sanitation", "Invitation for Bids",
         "4802", "2026-12-10T12:00:00Z", "2026-10-18T12:00:00Z"),
    _doc("Afghanistan", "Kabul Resilient Infrastructure - Prequalification", "Prequalification",
         "1234", "2026-12-01T12:00:00Z", "2026-10-15T12:00:00Z"),
    _doc("Viet Nam", "Ho Chi Minh Urban Rail", "Individual - Consulting",
         "5678", "2026-12-05T12:00:00Z", "2026-10-12T12:00:00Z"),
    _doc("Regional", "Regional Capacity Building", "Firm - Consulting",
         "9999", "2026-12-20T12:00:00Z", "2026-10-22T12:00:00Z"),
]


class TestMappingDoc(unittest.TestCase):
    def setUp(self):
        self.sri = adb.notice_depuis_doc(FAKE_DOCS[0])

    def test_champs_multivalues_premiere_valeur(self):
        self.assertEqual(self.sri["pays_clair"], "Sri Lanka")
        self.assertEqual(self.sri["titre"], "Mahaweli Water Security Investment Program")
        self.assertEqual(self.sri["type_notice_txt"], "Invitation for Bids")

    def test_reference_stable_numero_agrement(self):
        self.assertEqual(self.sri["reference"], "4741")

    def test_lien_relatif_rendu_absolu(self):
        self.assertTrue(self.sri["lien"].startswith("https://www.adb.org/sites/"))

    def test_reference_repli_sur_id(self):
        # Sans n° d'agrement ni n° de projet, la reference tombe sur l'id Solr.
        doc = _doc("Nepal", "X", "Invitation for Bids", None,
                   "2026-12-01T12:00:00Z", "2026-10-01T12:00:00Z")
        self.assertEqual(adb.notice_depuis_doc(doc)["reference"], "doc-x")


class TestResolutionPays(unittest.TestCase):
    def test_pays_suivi(self):
        self.assertEqual(adb.resoudre_iso3("Sri Lanka")[0], "LKA")
        self.assertEqual(adb.resoudre_iso3("Papua New Guinea")[0], "PNG")
        self.assertEqual(adb.resoudre_iso3("Afghanistan")[0], "AFG")

    def test_hors_zone_signale(self):
        iso, hz = adb.resoudre_iso3("Viet Nam")
        self.assertEqual(iso, "")
        self.assertEqual(hz, "Viet Nam")

    def test_regional_ignore(self):
        self.assertEqual(adb.resoudre_iso3("Regional"), ("", ""))


class TestTypeNotice(unittest.TestCase):
    def test_amont(self):
        self.assertEqual(adb.type_notice("Prequalification")[1], "amont")
        self.assertEqual(adb.type_notice("Advance notice")[1], "amont")

    def test_tender(self):
        self.assertEqual(adb.type_notice("Invitation for Bids")[1], "tender")
        self.assertEqual(adb.type_notice("Individual - Consulting")[1], "tender")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.avis, self.stats = adb.collecter_et_normaliser(fetch_json=lambda: FAKE_DOCS)

    def test_garde_fou_non_declenche(self):
        self.assertFalse(self.stats["api_vide"])

    def test_retient_zones_suivies(self):
        pays = sorted(a["pays_execution"] for a in self.avis)
        # Sri Lanka, PNG, Afghanistan gardes ; Viet Nam (hors zone) et Regional
        # (sans pays) exclus.
        self.assertEqual(pays, ["AFG", "LKA", "PNG"])

    def test_vietnam_signale(self):
        self.assertIn("Viet Nam", self.stats["pays_hors_zone"])

    def test_afghanistan_est_amont(self):
        afg = [a for a in self.avis if a["pays_execution"] == "AFG"][0]
        self.assertEqual(afg["phase"], "amont")   # Prequalification

    def test_deadline_convertie_iso(self):
        sri = [a for a in self.avis if a["pays_execution"] == "LKA"][0]
        self.assertEqual(sri["deadline"], "2026-12-15")

    def test_date_publication_iso(self):
        sri = [a for a in self.avis if a["pays_execution"] == "LKA"][0]
        self.assertEqual(sri["date_publication"], "2026-10-20")

    def test_reference_comme_publication_number(self):
        sri = [a for a in self.avis if a["pays_execution"] == "LKA"][0]
        self.assertEqual(sri["publication_number"], "4741")

    def test_avis_compatible_coeur_ted(self):
        avis = self.avis[0]
        extraction = {
            "deploiement_terrain_reel": True, "type_mobilite": "terrain_isole",
            "profil_personnes_exposees": "expert_international",
            "securite_existante_detectee": False, "type_client": "bailleur_donateur",
            "accessibilite_commerciale": "moyenne", "duree_estimee": "moyenne",
            "niveau_opportunite_amarante": "moyen", "confiance": 0.8,
        }
        s, c, f = ted.calculer_scores(avis, extraction)
        self.assertGreaterEqual(f, 0.0)
        self.assertLessEqual(f, 10.0)


class TestApiVide(unittest.TestCase):
    def test_api_vide_declenche_garde_fou(self):
        avis, stats = adb.collecter_et_normaliser(fetch_json=lambda: [])
        self.assertTrue(stats["api_vide"])
        self.assertEqual(avis, [])


class FauxRep:
    def __init__(self, docs):
        self._docs = docs

    def raise_for_status(self):
        pass

    def json(self):
        return {"response": {"docs": self._docs}}


class FauxSession:
    def __init__(self, docs):
        self.docs = docs
        self.appel = None

    def get(self, url, params=None, timeout=None, headers=None):
        self.appel = {"url": url, "params": params, "headers": headers}
        return FauxRep(self.docs)


class TestInterrogationApi(unittest.TestCase):
    """La requete serveur porte bien le filtre `status:Active` et le jeton :
    c'est ce filtre qui remplace l'ancienne fenetre de fraicheur."""

    def test_requete_filtre_actifs_et_jeton(self):
        s = FauxSession(FAKE_DOCS)
        docs = adb.interroger_api(session=s)
        self.assertEqual(docs, FAKE_DOCS)
        self.assertEqual(s.appel["params"]["fq"], "tm_X3b_en_status:Active")
        self.assertEqual(s.appel["params"]["q"], "*:*")
        self.assertTrue(s.appel["headers"]["Authorization"].startswith("Token "))


class CaptureMiroir:
    """Remplace radar_stockage.ecrire_miroir et capture (onglet, lignes)."""
    def __enter__(self):
        self.appels = []
        self._orig = radar_stockage.ecrire_miroir
        radar_stockage.ecrire_miroir = self._capturer
        return self

    def __exit__(self, *a):
        radar_stockage.ecrire_miroir = self._orig

    def _capturer(self, onglet, lignes):
        self.appels.append((onglet, lignes))
        return "capture: {} ligne(s)".format(len(lignes))


class TestMiroir(unittest.TestCase):
    """ADB reactive doit ecrire dans le miroir Postgres (source de verite depuis
    le 22/07), sur le BON onglet, aligne positionnellement sur COLONNES_ADB."""

    def _resultat(self):
        avis = {"titre": "Projet X", "acheteur": "Asian Development Bank",
                "pays_execution": "LKA", "pays_acheteur": "",
                "publication_number": "4741", "type_notice": "Appel d'offres",
                "phase": "tender", "lien_avis": "", "deadline": "", "date_publication": ""}
        extraction = {"type_client": "bailleur_donateur", "confiance": 0.8}
        return {"avis": avis, "extraction": extraction, "surete": 5.0,
                "commercial": 5.0, "score": 5.0, "final_haiku": 5.0,
                "raffine": False, "divergence": False}

    def test_cable_le_miroir_bon_onglet_et_alignement(self):
        with CaptureMiroir() as cap:
            msg = adb.ecrire_miroir([self._resultat()])
        self.assertEqual(len(cap.appels), 1)
        self.assertEqual(cap.appels[0][0], adb.NOM_ONGLET)          # "adb_radar"
        d = cap.appels[0][1][0]
        self.assertEqual(set(d.keys()), set(adb.COLONNES_ADB))     # alignement schema
        self.assertEqual(d["publication_number"], "4741")
        self.assertIn("ligne", msg)

    def test_miroir_best_effort_ne_leve_jamais(self):
        # Meme si la couche base casse, ecrire_miroir avale l'exception (regle 11).
        orig = radar_stockage.ecrire_miroir
        radar_stockage.ecrire_miroir = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            msg = adb.ecrire_miroir([self._resultat()])
        finally:
            radar_stockage.ecrire_miroir = orig
        self.assertIn("indisponible", msg)


if __name__ == "__main__":
    unittest.main()
