"""Tests du collecteur de titulaires SPARQL et de son cablage (SPARQL
prioritaire, PDF en secours). Tout hors-ligne : le JSON SPARQL et le texte PDF
sont injectes (fetch), aucun appel reseau.
"""

import unittest

import sparql_titulaires as st
import ted_complet_attributions as att


def _reponse(bindings):
    return {"results": {"bindings": bindings}}


def _binding(nom, montant="", devise_uri=""):
    b = {"nom": {"value": nom}}
    if montant:
        b["montant"] = {"value": montant}
    if devise_uri:
        b["devise"] = {"value": devise_uri}
    return b


CUR = "http://publications.europa.eu/resource/authority/currency/"


class TestHelpers(unittest.TestCase):

    def test_variantes_pn_padde(self):
        vs = st._variantes_pn("302871-2026")
        self.assertIn("302871-2026", vs)
        self.assertIn("00302871-2026", vs)

    def test_variantes_pn_depadde(self):
        vs = st._variantes_pn("00196376-2026")
        self.assertIn("196376-2026", vs)
        self.assertIn("00196376-2026", vs)

    def test_code_devise(self):
        self.assertEqual(st._code_devise(CUR + "RON"), "RON")
        self.assertEqual(st._code_devise("EUR"), "EUR")


class TestParsing(unittest.TestCase):

    def setUp(self):
        st._ETAT["echecs"] = 0
        st._ETAT["coupe"] = False

    def test_titulaires_par_pn(self):
        b = [_binding("INTELLISOFT SYSTEMS", "960000", CUR + "RON")]
        r = st.titulaires_par_pn("302871-2026", fetch=lambda q: _reponse(b))
        self.assertEqual(r, [{"nom": "INTELLISOFT SYSTEMS",
                              "montant": "960000", "devise": "RON"}])

    def test_parse_format_valeur_et_dedup(self):
        b = [_binding("ACME", "100", CUR + "EUR"),
             _binding("ACME", "200", CUR + "EUR")]     # meme titulaire, 2 lots
        p = st.parse_depuis_sparql("1-2026", fetch=lambda q: _reponse(b))
        self.assertEqual(len(p["gagnants"]), 1)         # dedup par nom
        self.assertEqual(p["gagnants"][0]["valeur"], "100 EUR")
        self.assertEqual(p["total"], "")

    def test_vide_renvoie_none(self):
        p = st.parse_depuis_sparql("1-2026", fetch=lambda q: _reponse([]))
        self.assertIsNone(p)

    def test_titulaire_sans_montant(self):
        # Cas reel (ex. 10759-2026) : offre financiere non publiee -> le
        # titulaire doit quand meme sortir, valeur vide.
        b = [_binding("Yandalux Solar GmbH")]
        p = st.parse_depuis_sparql("10759-2026", fetch=lambda q: _reponse(b))
        self.assertEqual(len(p["gagnants"]), 1)
        self.assertEqual(p["gagnants"][0]["nom"], "Yandalux Solar GmbH")
        self.assertEqual(p["gagnants"][0]["valeur"], "")

    def test_disjoncteur_coupe(self):
        st._ETAT["coupe"] = True
        r = st.titulaires_par_pn("1-2026", fetch=lambda q: _reponse([_binding("X")]))
        self.assertEqual(r, [])


class TestCablage(unittest.TestCase):

    def setUp(self):
        st._ETAT["echecs"] = 0
        st._ETAT["coupe"] = False
        self._actif = att.sparql_titulaires.ACTIF

    def tearDown(self):
        att.sparql_titulaires.ACTIF = self._actif

    def test_sparql_prioritaire_ignore_le_pdf(self):
        att.sparql_titulaires.ACTIF = True
        sp = lambda q: _reponse([_binding("INTELLISOFT SYSTEMS", "960000", CUR + "RON")])
        pdf = lambda pub: "Information about winners Official name: PDF_NON_UTILISE 8. Notice information"
        parse = att.obtenir_gagnants("302871-2026", fetch=pdf, fetch_sparql=sp)
        self.assertEqual(parse["gagnants"][0]["nom"], "INTELLISOFT SYSTEMS")
        self.assertEqual(parse["gagnants"][0]["valeur"], "960000 RON")

    def test_fallback_pdf_si_sparql_vide(self):
        att.sparql_titulaires.ACTIF = True
        sp = lambda q: _reponse([])           # ODS n'a pas (encore) l'avis
        pdf = lambda pub: "Information about winners Official name: Badenelektra GmbH 8. Notice information"
        parse = att.obtenir_gagnants("302871-2026", fetch=pdf, fetch_sparql=sp)
        self.assertTrue(any("Baden" in g["nom"] for g in parse["gagnants"]))

    def test_pdf_seul_si_sparql_inactif(self):
        att.sparql_titulaires.ACTIF = False   # flag OFF : comportement d'avant
        sp = lambda q: _reponse([_binding("NE_DOIT_PAS_APPARAITRE")])
        pdf = lambda pub: "Information about winners Official name: Badenelektra GmbH 8. Notice information"
        parse = att.obtenir_gagnants("302871-2026", fetch=pdf, fetch_sparql=sp)
        self.assertTrue(any("Baden" in g["nom"] for g in parse["gagnants"]))
        self.assertFalse(any("APPARAITRE" in g["nom"] for g in parse["gagnants"]))


import datetime


def _reponse_renouv(conclusion, val, unit):
    u = "http://www.w3.org/2006/time#unit" + unit
    return {"results": {"bindings": [
        {"conclusion": {"value": conclusion},
         "dureeVal": {"value": val},
         "dureeUnit": {"value": u}}]}}


class TestRenouvellement(unittest.TestCase):

    def setUp(self):
        st._ETAT["echecs"] = 0
        st._ETAT["coupe"] = False
        self.ref = datetime.date(2026, 8, 16)   # date fixe pour deterministe

    def test_date_fin_mois(self):
        self.assertEqual(st._date_fin("2024-06-01", "36", "unitMonth").year, 2027)

    def test_date_fin_annee(self):
        f = st._date_fin("2023-01-01", "3", "unitYear")
        self.assertEqual(f.year, 2025)

    def test_date_fin_invalide(self):
        self.assertIsNone(st._date_fin("", "12", "unitMonth"))
        self.assertIsNone(st._date_fin("2024-01-01", "x", "unitMonth"))
        self.assertIsNone(st._date_fin("2024-01-01", "12", "unitInconnue"))

    def test_statut_imminent_a_venir_expire(self):
        self.assertEqual(st.statut_renouvellement(3), "imminent")
        self.assertEqual(st.statut_renouvellement(9), "a_venir")
        self.assertEqual(st.statut_renouvellement(-2), "")     # expire
        self.assertEqual(st.statut_renouvellement(24), "")     # trop loin

    def test_par_pn_retient_fin_la_plus_lointaine(self):
        # 3 lots : 12, 36 et 24 mois depuis la meme conclusion -> on garde 36.
        data = {"results": {"bindings": [
            {"conclusion": {"value": "2024-06-01"}, "dureeVal": {"value": "12"},
             "dureeUnit": {"value": "http://www.w3.org/2006/time#unitMonth"}},
            {"conclusion": {"value": "2024-06-01"}, "dureeVal": {"value": "36"},
             "dureeUnit": {"value": "http://www.w3.org/2006/time#unitMonth"}},
            {"conclusion": {"value": "2024-06-01"}, "dureeVal": {"value": "24"},
             "dureeUnit": {"value": "http://www.w3.org/2006/time#unitMonth"}},
        ]}}
        r = st.renouvellement_par_pn("x", fetch=lambda q: data, aujourdhui=self.ref)
        self.assertEqual(r["fin"], "2027-06-01")     # la plus lointaine
        self.assertEqual(r["statut"], "a_venir")

    def test_par_pn_vide(self):
        r = st.renouvellement_par_pn(
            "x", fetch=lambda q: {"results": {"bindings": []}}, aujourdhui=self.ref)
        self.assertEqual(r, {})

    def test_cablage_colonnes_dans_le_lead(self):
        att.sparql_titulaires.ACTIF = True
        st._ETAT["coupe"] = False
        notice = {"publication-number": "x-2026", "notice-title": "Escorte",
                  "buyer-name": "MinDef", "place-of-performance": "MLI",
                  "classification-cpv": "79710000", "publication-date": "2026-08-01"}
        sp = lambda q: _reponse([_binding("ACME Security")])
        ren = lambda q: _reponse_renouv("2024-06-01", "36", "Month")
        try:
            parse = att.obtenir_gagnants("x-2026", fetch_sparql=sp, fetch_renouv=ren)
            lead = att.normaliser(notice, parse)
        finally:
            att.sparql_titulaires.ACTIF = False
        self.assertEqual(lead["fin_contrat"], "2027-06-01")
        self.assertIn(lead["statut_renouv"], ("imminent", "a_venir"))


if __name__ == "__main__":
    unittest.main()
