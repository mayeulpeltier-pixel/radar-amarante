# -*- coding: utf-8 -*-
"""Socle DETERMINISTE du pays du titulaire (18/08/2026).

Le mapping officiel eForms -> ePO (repo ted-rdf-mapping-eforms) et une sonde
live confirment que le pays du titulaire suit :
    Organization -> cccev:registeredAddress -> Address -> epo:hasCountryCode
et que le code est un IRI se terminant par l'ISO3 (ex .../country/DEU).

On l'exploite pour remplir le socle DETERMINISTE `pays_titulaire` /
`titulaire_etranger` de l'onglet brut (colonnes deja au schema, laissees vides
jusqu'ici). C'est un FAIT (adresse enregistree), distinct et complementaire de
l'inference d'origine faite par le LLM dans attributions_analyse : on ne touche
donc pas a ce dernier.

Tout est injecte (fetch), aucun appel reseau.
"""

import unittest

import sparql_titulaires as st
import ted_complet_attributions as att


PAYS = "http://publications.europa.eu/resource/authority/country/"
NUTS = "http://data.europa.eu/nuts/code/"


def _reponse(bindings):
    return {"results": {"bindings": bindings}}


def _binding(nom, pays_uri="", nuts_uri="", montant="", devise=""):
    b = {"nom": {"value": nom}}
    if pays_uri:
        b["pays"] = {"value": pays_uri}
    if nuts_uri:
        b["nuts"] = {"value": nuts_uri}
    if montant:
        b["montant"] = {"value": montant}
    if devise:
        b["devise"] = {"value": devise}
    return b


class TestExtractionPays(unittest.TestCase):

    def test_dernier_segment_iso3(self):
        self.assertEqual(st._dernier_segment(PAYS + "DEU"), "DEU")
        self.assertEqual(st._dernier_segment("DEU"), "DEU")

    def test_titulaires_par_pn_expose_pays_et_nuts(self):
        b = [_binding("Yandalux Solar GmbH", PAYS + "DEU", NUTS + "DE600")]
        r = st.titulaires_par_pn("10759-2026", fetch=lambda q: _reponse(b))
        self.assertEqual(r[0]["pays"], "DEU")
        self.assertEqual(r[0]["nuts"], "DE600")

    def test_requete_interroge_l_adresse(self):
        q = st.requete("1-2026")
        self.assertIn("cccev:registeredAddress", q)
        self.assertIn("epo:hasCountryCode", q)
        self.assertIn("?pays", q)


class TestAgregationParse(unittest.TestCase):

    def test_pays_titulaire_dedup_ordre(self):
        b = [_binding("A", PAYS + "DEU"), _binding("B", PAYS + "FRA"),
             _binding("A", PAYS + "DEU")]
        p = st.parse_depuis_sparql("1-2026", fetch=lambda q: _reponse(b))
        self.assertEqual(p["pays_titulaire"], "DEU; FRA")

    def test_pays_absent_donne_chaine_vide(self):
        b = [_binding("A")]
        p = st.parse_depuis_sparql("1-2026", fetch=lambda q: _reponse(b))
        self.assertEqual(p["pays_titulaire"], "")


class TestTitulaireEtranger(unittest.TestCase):

    def test_hors_execution_donne_oui(self):
        self.assertEqual(att._titulaire_etranger("DEU", ["MLI"]), "oui")

    def test_dans_execution_donne_non(self):
        self.assertEqual(att._titulaire_etranger("MLI", ["MLI"]), "non")

    def test_multi_pays_un_etranger_suffit(self):
        self.assertEqual(att._titulaire_etranger("MLI; DEU", ["MLI"]), "oui")

    def test_inconnu_donne_vide(self):
        self.assertEqual(att._titulaire_etranger("", ["MLI"]), "")


class TestEcritureTed(unittest.TestCase):
    """normaliser + ligne : le socle deterministe atterrit aux bonnes colonnes."""

    def _notice(self, pays_exec="MLI"):
        return {"publication-number": "1-2026", "place-of-performance": pays_exec,
                "notice-type": "can-standard"}

    def _parse(self, pays_tit):
        return {"gagnants": [{"nom": "ACME", "valeur": ""}], "total": "",
                "sous_traitance": False, "pays_titulaire": pays_tit}

    def test_normaliser_expose_socle(self):
        a = att.normaliser(self._notice("MLI"), self._parse("DEU"))
        self.assertEqual(a["pays_titulaire"], "DEU")
        self.assertEqual(a["titulaire_etranger"], "oui")

    def test_ligne_ecrit_aux_colonnes(self):
        a = att.normaliser(self._notice("MLI"), self._parse("DEU"))
        d = dict(zip(att.COLONNES, att.ligne(a)))
        self.assertEqual(d["pays_titulaire"], "DEU")
        self.assertEqual(d["titulaire_etranger"], "oui")

    def test_sparql_muet_laisse_colonnes_vides(self):
        """Sans SPARQL (pas de cle pays_titulaire), pas de regression : vide."""
        a = att.normaliser(self._notice("MLI"),
                           {"gagnants": [], "total": "", "sous_traitance": False})
        self.assertEqual(a["pays_titulaire"], "")
        self.assertEqual(a["titulaire_etranger"], "")


class TestChaineSparqlVersTed(unittest.TestCase):
    """De bout en bout : SPARQL -> obtenir_gagnants -> normaliser -> ligne."""

    def test_bout_en_bout(self):
        b = [_binding("Yandalux Solar GmbH", PAYS + "DEU", NUTS + "DE600")]
        # SPARQL actif via injection fetch_sparql ; PDF non sollicite.
        actif = getattr(st, "ACTIF", False)
        st.ACTIF = True
        try:
            parse = att.obtenir_gagnants(
                "10759-2026", fetch=lambda u: "",
                fetch_sparql=lambda q: _reponse(b),
                fetch_renouv=lambda q: {"results": {"bindings": []}})
        finally:
            st.ACTIF = actif
        a = att.normaliser({"publication-number": "10759-2026",
                            "place-of-performance": "UKR"}, parse)
        self.assertEqual(a["pays_titulaire"], "DEU")
        self.assertEqual(a["titulaire_etranger"], "oui")  # DEU hors UKR


class TestInjectionPrompt(unittest.TestCase):
    """Le pays d'adresse (socle deterministe) est injecte comme INDICE dans le
    prompt LLM d'attributions_analyse, sans remplacer l'inference d'origine."""

    def setUp(self):
        import attributions_analyse as aa
        self.aa = aa

    def test_pays_present_apparait_dans_le_prompt(self):
        p = self.aa.construire_prompt(
            {"gagnant": "Yandalux Solar GmbH", "pays_titulaire": "DEU",
             "pays_execution": "UKR"})
        self.assertIn("DEU", p)
        self.assertIn("adresse enregistree", p.lower())

    def test_pays_absent_donne_non_renseigne(self):
        p = self.aa.construire_prompt({"gagnant": "X", "pays_execution": "MLI"})
        self.assertIn("non renseigne", p)

    def test_prompt_conserve_l_inference_origine(self):
        """L'indice ne remplace pas la tache d'inference (point ORIGINE garde)."""
        p = self.aa.construire_prompt({"gagnant": "X", "pays_titulaire": "DEU"})
        self.assertIn("ORIGINE DU TITULAIRE", p)


if __name__ == "__main__":
    unittest.main()
