# -*- coding: utf-8 -*-
"""
Tests du collecteur Proparco. HORS-LIGNE : fetch injecte, aucun reseau, aucun
LLM. Verifie le mapping pays (nom FR -> ISO3, accents), le crible FI, la
fenetre, le mapping d'un enregistrement, l'entonnoir et l'arite des lignes.
"""

import unittest
from datetime import date, timedelta

import proparco_radar as pz


def _iso(jours=0):
    return (date.today() - timedelta(days=jours)).isoformat()


def _rec(**kw):
    base = {
        "id_concours": "PPE1000", "nom_du_client": "Sahel Energie SA",
        "nature_du_client": "Entreprise industrielle",
        "pays_de_realisation": "Mali", "secteur_s_concerne_s_par_le_projet": "Energie",
        "montant_du_financement_en_euro": 25000000.0,
        "date_de_signature": _iso(30), "etat_en_cours_ou_cloture": "En cours",
        "resume_du_projet": "Construction d'une centrale solaire en zone rurale.",
        "lien_vers_la_fiche_projet": "https://www.proparco.fr/fr/carte-des-projets/x",
        "ces": "A", "titre_court_du_projet": "Sahel Solaire",
    }
    base.update(kw)
    return base


def faux_fetch(records):
    """Une page pleine puis vide (total = len)."""
    etat = {"servi": False}

    def _f(params):
        if etat["servi"]:
            return {"results": [], "total_count": len(records)}
        etat["servi"] = True
        return {"results": records, "total_count": len(records)}
    return _f


class TestMappingPays(unittest.TestCase):
    def test_accents_et_alias(self):
        self.assertEqual(pz.iso3_depuis_nom("Pérou"), "PER")     # accent
        self.assertEqual(pz.iso3_depuis_nom("Mali"), "MLI")
        self.assertEqual(pz.iso3_depuis_nom("Côte d'Ivoire"), "CIV")
        self.assertEqual(pz.iso3_depuis_nom("République démocratique du Congo"), "COD")

    def test_inconnu(self):
        self.assertEqual(pz.iso3_depuis_nom("Pays Imaginaire"), "")


class TestCribleFI(unittest.TestCase):
    def test_institution_financiere_ecartee(self):
        self.assertTrue(pz.est_client_financier("Institution financière"))
        self.assertTrue(pz.est_client_financier("Fonds d'investissement"))
        self.assertTrue(pz.est_client_financier("Banque commerciale"))

    def test_industriel_garde(self):
        self.assertFalse(pz.est_client_financier("Entreprise industrielle"))
        self.assertFalse(pz.est_client_financier("Opérateur énergétique"))


class TestMappingAvis(unittest.TestCase):
    def test_rec_vers_avis(self):
        a = pz.rec_vers_avis(_rec())
        self.assertEqual(a["publication_number"], "PPE1000")
        self.assertEqual(a["acheteur"], "Sahel Energie SA")
        self.assertEqual(a["pays_execution"], "Mali")
        self.assertEqual(a["pays_iso3"], "MLI")
        self.assertIn("EUR", a["valeur_estimee"])
        self.assertIn("solaire", a["description"].lower())


class TestCollecteEntonnoir(unittest.TestCase):
    def test_filtres(self):
        records = [
            _rec(id_concours="A", pays_de_realisation="Mali"),                  # retenu
            _rec(id_concours="B", pays_de_realisation="France"),                # hors perimetre
            _rec(id_concours="C", nature_du_client="Institution financière"),   # FI
            _rec(id_concours="D", date_de_signature=_iso(2000)),               # hors fenetre
            _rec(id_concours="E", pays_de_realisation="Pays Imaginaire"),       # non mappe
        ]
        avis, c = pz.collecte(deja_vus=set(), fetch=faux_fetch(records))
        ids = {a["publication_number"] for a in avis}
        self.assertEqual(ids, {"A"})
        self.assertEqual(c["hors_perimetre"], 2)   # France + Pays Imaginaire
        self.assertEqual(c["rejet_fi"], 1)
        self.assertEqual(c["hors_fenetre"], 1)
        self.assertIn("Pays Imaginaire", c["pays_non_mappes"])

    def test_memoire(self):
        records = [_rec(id_concours="A"), _rec(id_concours="B")]
        avis, c = pz.collecte(deja_vus={"A"}, fetch=faux_fetch(records))
        self.assertEqual([a["publication_number"] for a in avis], ["B"])
        self.assertEqual(c["deja_connus"], 1)

    def test_tri_par_montant(self):
        records = [
            _rec(id_concours="petit", montant_du_financement_en_euro=1000000),
            _rec(id_concours="gros", montant_du_financement_en_euro=90000000),
        ]
        avis, _c = pz.collecte(deja_vus=set(), fetch=faux_fetch(records))
        self.assertEqual(avis[0]["publication_number"], "gros")


class TestArite(unittest.TestCase):
    def test_ligne_bonne_longueur(self):
        a = pz.rec_vers_avis(_rec())
        r = {"avis": a, "extraction": None, "score": 0.0, "surete": 0.0,
             "commercial": 0.0, "raffine": False, "divergence": False}
        ligne = pz.ligne_depuis_resultat(r)
        self.assertEqual(len(ligne), len(pz.COLONNES))
        self.assertTrue(all(isinstance(x, str) for x in ligne))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestMultiPaysEtAlias(unittest.TestCase):
    def test_palestine_alias_exact(self):
        self.assertEqual(pz.iso3_depuis_nom("Territoires autonomes palestiniens"), "PSE")

    def test_multipays_resolu_via_detail(self):
        rec = _rec(id_concours="MP", pays_de_realisation="Multi-Pays Afrique",
                   detail_multi_pays="Mali, Sénégal, France")
        nom, iso3 = pz.resoudre_pays(rec)
        self.assertEqual(iso3, "MLI")                 # 1er pays du perimetre trouve

    def test_multipays_sans_pays_perimetre_reste_exclu(self):
        rec = _rec(id_concours="MP2", pays_de_realisation="Multi-Pays Etranger",
                   detail_multi_pays="France, Allemagne")
        _nom, iso3 = pz.resoudre_pays(rec)
        self.assertEqual(iso3, "")

    def test_hors_perimetre_mappe_reste_exclu(self):
        # Inde est mappe (IND) mais hors perimetre -> exclu, pas "non mappe".
        records = [_rec(id_concours="X", pays_de_realisation="Inde")]
        avis, c = pz.collecte(deja_vus=set(), fetch=faux_fetch(records))
        self.assertEqual(avis, [])
        self.assertEqual(c["hors_perimetre"], 1)
        self.assertNotIn("Inde", c.get("pays_non_mappes", {}))
