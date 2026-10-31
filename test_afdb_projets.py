# -*- coding: utf-8 -*-
"""Collecteur amont AfDB (IATI / d-portal) : resolution pays ISO2->ISO3, dates
en jours depuis 1970, filtres statut/pays/fenetre, scoring reutilise de
bm_projets, pagination, schema jumeau de bm_projets. Offline : le flux d-portal
est injecte (aucun reseau)."""

import datetime
import unittest

import afdb_projets as A
import bm_projets

AUJ = datetime.date(2026, 10, 31)


def _js(brut):
    """date -> day_start IATI (chaine d'entier, jours depuis 1970-01-01)."""
    return str((brut - A.EPOCH).days)


def projet(aid="46002-P-ML-K00-001", pays="ML", statut="1",
           debut=datetime.date(2026, 9, 1), commitment="120000000",
           titre="Mali - Programme routier", fin=datetime.date(2030, 1, 1)):
    return {"aid": aid, "country_code": pays, "status_code": statut,
            "day_start": _js(debut), "day_end": _js(fin) if fin else "",
            "commitment": commitment, "title": titre, "country_percent": "100"}


def rep(rows):
    return {"rows": rows, "count": len(rows)}


class TestResolutionPays(unittest.TestCase):

    def test_iso2_suivi(self):
        self.assertEqual(A.resoudre_iso3("ML"), "MLI")
        self.assertEqual(A.resoudre_iso3("cd"), "COD")      # casse ignoree

    def test_iso2_inconnu_ou_hors_univers(self):
        self.assertIsNone(A.resoudre_iso3("FR"))            # hors table AfDB
        self.assertIsNone(A.resoudre_iso3(""))
        self.assertIsNone(A.resoudre_iso3(None))

    def test_toute_la_table_est_suivie(self):
        import ted_complet_v14 as ted
        suivis = set(ted.CODES_PAYS_SUIVIS)
        self.assertTrue(all(v in suivis for v in A.ISO2_VERS_ISO3.values()))

    def test_garde_fou_pays_hors_univers(self):
        # Si la table venait a mapper vers un ISO3 non suivi, le garde-fou
        # doit l'ecarter (il n'y a pas de tier de risque pour lui).
        A.ISO2_VERS_ISO3["XX"] = "USA"
        try:
            self.assertIsNone(A.resoudre_iso3("XX"))
        finally:
            del A.ISO2_VERS_ISO3["XX"]


class TestDatesEtMontant(unittest.TestCase):

    def test_jour_depuis_entier(self):
        self.assertEqual(A._jour(_js(datetime.date(2026, 8, 24))),
                         datetime.date(2026, 8, 24))

    def test_jour_illisible(self):
        self.assertIsNone(A._jour("abc"))
        self.assertIsNone(A._jour(None))

    def test_montant(self):
        self.assertEqual(A._montant({"commitment": "396350340"}), 396350340.0)
        self.assertEqual(A._montant({"commitment": "0"}), 0.0)
        self.assertEqual(A._montant({"commitment": None}), 0.0)
        self.assertEqual(A._montant({"commitment": "-100"}), 0.0)   # aberrant -> 0


class TestNormalisation(unittest.TestCase):

    def test_projet_pipeline_a_risque(self):
        a = A.normaliser(projet(), aujourd=AUJ)
        self.assertEqual(a["pays_execution"], "MLI")
        self.assertEqual(a["publication_number"], "AFDBP-46002-P-ML-K00-001")
        self.assertEqual(a["type_notice"], "Projet AfDB (Pipeline)")
        self.assertEqual(a["date_publication"], "2026-09-01")
        self.assertTrue(a["lien_avis"].startswith("https://d-portal.org/"))
        # Scoring reutilise bm_projets : Mali (rouge, tier 1.0) -> surete 10.
        self.assertEqual(a["score_surete"], 10.0)

    def test_statut_finalisation_exclu(self):
        self.assertIsNone(A.normaliser(projet(statut="3"), aujourd=AUJ))
        self.assertIsNone(A.normaliser(projet(statut="4"), aujourd=AUJ))

    def test_pays_hors_univers_exclu(self):
        self.assertIsNone(A.normaliser(projet(pays="FR"), aujourd=AUJ))

    def test_hors_fenetre_exclu(self):
        marge = A.FENETRE_MOIS * 31
        # Juste au-dela du bord ancien : exclu ; juste en deca : garde.
        self.assertIsNone(A.normaliser(
            projet(debut=AUJ - datetime.timedelta(days=marge + 2)), aujourd=AUJ))
        self.assertIsNotNone(A.normaliser(
            projet(debut=AUJ - datetime.timedelta(days=marge - 2)), aujourd=AUJ))
        # Juste au-dela du bord futur : exclu (projet approuve trop loin).
        self.assertIsNone(A.normaliser(
            projet(debut=AUJ + datetime.timedelta(days=marge + 2)), aujourd=AUJ))
        self.assertIsNotNone(A.normaliser(
            projet(debut=AUJ + datetime.timedelta(days=marge - 2)), aujourd=AUJ))

    def test_fenetre_action_court_terme_si_en_cours_et_recent(self):
        a = A.normaliser(projet(statut="2", debut=AUJ - datetime.timedelta(days=30)),
                         aujourd=AUJ)
        self.assertEqual(a["fenetre_action"], "court_terme")
        # Un pipeline, meme recent, reste indetermine (pas encore en cours).
        b = A.normaliser(projet(statut="1", debut=AUJ - datetime.timedelta(days=30)),
                         aujourd=AUJ)
        self.assertEqual(b["fenetre_action"], "indetermine")

    def test_sans_aid_pas_de_publication(self):
        self.assertEqual(A.normaliser(projet(aid=""), aujourd=AUJ)["publication_number"], "")


class TestCollecte(unittest.TestCase):

    def test_dedoublonnage_et_filtrage(self):
        rows = [projet(aid="X1", pays="ML"),
                projet(aid="X1", pays="ML"),                 # doublon
                projet(aid="X2", pays="FR"),                 # hors univers
                projet(aid="X3", pays="SO", statut="2")]     # Somalie en cours

        def fetch(url, params):
            return rep(rows) if params["offset"] == 0 else rep([])

        avis = A.collecter_et_normaliser(fetch=fetch, aujourd=AUJ)
        self.assertEqual(sorted(a["publication_number"] for a in avis),
                         ["AFDBP-X1", "AFDBP-X3"])

    def test_pagination_s_arrete_sous_le_plafond(self):
        offsets = []

        def fetch(url, params):
            offsets.append(params["offset"])
            # Une page pleine puis une page courte -> arret.
            if params["offset"] == 0:
                return rep([projet(aid="P%d" % i) for i in range(A.LIGNES_PAR_PAGE)])
            return rep([projet(aid="Q1")])

        A.collecter_et_normaliser(fetch=fetch, aujourd=AUJ)
        self.assertEqual(offsets, [0, A.LIGNES_PAR_PAGE])

    def test_filtre_date_serveur_envoye(self):
        vus = {}

        def fetch(url, params):
            vus.update(params)
            return rep([])
        A.collecter_flux(fetch=fetch, aujourd=AUJ)
        attendu = (AUJ - datetime.timedelta(days=A.FENETRE_MOIS * 31) - A.EPOCH).days
        self.assertEqual(vus["day_start_gt"], str(attendu))
        self.assertEqual(vus["reporting_ref"], A.REPORTING_REF)

    def test_flux_illisible_ne_leve_pas(self):
        self.assertEqual(A.collecter_et_normaliser(fetch=lambda u, p: None, aujourd=AUJ), [])


class TestSchema(unittest.TestCase):

    def test_colonnes_identiques_a_bm_projets(self):
        # Le dashboard lit l'onglet avec le meme helper generique : le schema
        # doit rester jumeau de bm_projets_radar.
        self.assertEqual(A.COLONNES, bm_projets.COLONNES_BMP)
        self.assertEqual(A.NOM_ONGLET, "afdb_projets_radar")

    def test_ligne_respecte_le_schema(self):
        a = A.normaliser(projet(), aujourd=AUJ)
        ligne = A.ligne_depuis_avis(a)
        self.assertEqual(len(ligne), len(A.COLONNES))
        self.assertEqual(ligne[A.COLONNES.index("publication_number")],
                         "AFDBP-46002-P-ML-K00-001")


if __name__ == "__main__":
    unittest.main()
