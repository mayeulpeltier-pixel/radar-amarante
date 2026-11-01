# -*- coding: utf-8 -*-
"""Enrichissement des prospects IE : fonctions pures (normalisation, garde
anti-homonyme, extraction des dirigeants, parsing), execution (budget, cache,
isolation des erreurs) et lignes de sortie. Offline : API injectee."""

import datetime
import unittest

import enrichir_ie as E

E.PAUSE = 0.0
AUJ = datetime.date(2026, 11, 1)


def reponse(nom="NOVAXIA INVESTISSEMENT", siren="802346551", ville="PARIS",
            naf="Gestion de portefeuille", dirigeants=None):
    return {"results": [{
        "siren": siren, "nom_complet": nom, "nom_raison_sociale": nom,
        "libelle_activite_principale": naf,
        "siege": {"libelle_commune": ville, "activite_principale": "66.30Z"},
        "tranche_effectif_salarie": "12",
        "dirigeants": dirigeants if dirigeants is not None else
        [{"type_dirigeant": "personne physique", "prenoms": "Lucile Marie",
          "nom": "Bertoni", "qualite": "Président"}]}]}


class TestNormalisation(unittest.TestCase):

    def test_cle_societe(self):
        self.assertEqual(E.cle_societe("Uzès Gestion"), "uzes gestion")
        self.assertEqual(E.cle_societe("M Capital Partners (Midi Capital)"),
                         "m capital partners midi capital")

    def test_noms_compatibles(self):
        self.assertTrue(E.noms_compatibles("Caceis Bank", "CACEIS BANK"))
        self.assertTrue(E.noms_compatibles("M Capital Partners",
                                           "M CAPITAL PARTNERS (MIDI CAPITAL)"))
        self.assertTrue(E.noms_compatibles("Novaxia Investissement", "NOVAXIA INVESTISSEMENT"))

    def test_noms_incompatibles(self):
        self.assertFalse(E.noms_compatibles("Alpha Conseil", "Betacorp Industries"))

    def test_seuil_moitie_inclus(self):
        # 1 token commun sur 2 = 0.5 : inclus (seuil >=).
        self.assertTrue(E.noms_compatibles("Alpha Beta", "Alpha Gamma"))

    def test_tokens_signifiants_ecarte_les_courts(self):
        # Les tokens <= 2 caracteres sont ecartes (bruit : initiales, articles).
        self.assertEqual(E._tokens_signifiants("M Capital Partners"),
                         ["capital", "partners"])

    def test_formes_seules_egalite_stricte(self):
        # Noms faits uniquement de formes juridiques (ecartees) -> repli egalite.
        self.assertTrue(E.noms_compatibles("SAS", "sas"))
        self.assertFalse(E.noms_compatibles("SAS", "SARL"))

    def test_mots_activite_discriminants(self):
        # 'finance'/'gestion' restent discriminants (pas des formes juridiques).
        self.assertFalse(E.noms_compatibles("Groupe Finance", "Capital Gestion"))


class TestDirigeants(unittest.TestCase):

    def test_priorite_qualite(self):
        dirs = [{"type_dirigeant": "personne physique", "prenoms": "Jean",
                 "nom": "Martin", "qualite": "Membre"},
                {"type_dirigeant": "personne physique", "prenoms": "Marie",
                 "nom": "Durand", "qualite": "Directeur général"}]
        principal, autres = E.extraire_dirigeants(dirs)
        self.assertEqual(principal, "Marie Durand (Directeur général)")
        self.assertEqual(len(autres), 1)

    def test_personne_morale_en_dernier(self):
        dirs = [{"type_dirigeant": "personne morale", "denomination": "HOLDING X",
                 "qualite": "Associé"},
                {"type_dirigeant": "personne physique", "prenoms": "Ana", "nom": "Lopez",
                 "qualite": "Président"}]
        principal, autres = E.extraire_dirigeants(dirs)
        self.assertEqual(principal, "Ana Lopez (Président)")
        # La personne morale est conservee (en dernier), pas traitee en physique.
        self.assertIn("HOLDING X (Associé)", autres)

    def test_sans_dirigeant(self):
        self.assertEqual(E.extraire_dirigeants([]), ("", []))

    def test_fraiche_borne(self):
        limite = datetime.date(2026, 7, 1)
        self.assertTrue(E._fraiche(datetime.date(2026, 7, 1), limite))   # pile = frais
        self.assertFalse(E._fraiche(datetime.date(2026, 6, 30), limite))
        self.assertFalse(E._fraiche(None, limite))


class TestParse(unittest.TestCase):

    def test_parse_ok(self):
        info = E.parse_resultat("Novaxia Investissement", reponse())
        self.assertEqual(info["siren"], "802346551")
        self.assertEqual(info["cle_societe"], "novaxia investissement")
        self.assertEqual(info["dirigeant_principal"], "Lucile Bertoni (Président)")
        self.assertEqual(info["ville"], "PARIS")

    def test_homonyme_ecarte(self):
        self.assertIsNone(E.parse_resultat("Uzès Gestion", reponse(nom="BOUYGUES TELECOM")))

    def test_aucun_resultat(self):
        self.assertIsNone(E.parse_resultat("X", {"results": []}))


class TestExecuter(unittest.TestCase):

    def test_dedup_et_resolution(self):
        source = [{"societe": "Novaxia Investissement"}, {"societe": "novaxia investissement"},
                  {"societe": ""}, {"societe": "Caceis Bank"}]
        appels = []
        def fetch(nom):
            appels.append(nom)
            return reponse(nom=nom.upper())
        enr, info = E.executer(source, fetch=fetch, aujourd=AUJ)
        self.assertEqual(info["candidats"], 2)        # doublon et vide ecartes
        self.assertEqual(len(appels), 2)
        self.assertEqual(info["resolues"], 2)
        self.assertEqual(enr[0]["enrichi_le"], "2026-11-01")

    def test_cache_saute_les_fraiches(self):
        source = [{"societe": "Novaxia Investissement"}]
        enr, info = E.executer(source, deja_fraiches={"novaxia investissement"},
                               fetch=lambda n: reponse(), aujourd=AUJ)
        self.assertEqual(info["a_faire"], 0)
        self.assertEqual(enr, [])

    def test_budget_borne(self):
        source = [{"societe": "A corp"}, {"societe": "B corp"}, {"societe": "C corp"}]
        enr, info = E.executer(source, fetch=lambda n: reponse(nom=n.upper()),
                               aujourd=AUJ, budget=2)
        self.assertEqual(info["resolues"], 2)
        self.assertEqual(info["hors_budget"], 1)

    def test_erreur_fetch_isolee(self):
        def fetch(nom):
            raise RuntimeError("reset")
        enr, info = E.executer([{"societe": "A corp"}], fetch=fetch, aujourd=AUJ)
        self.assertEqual(enr, [])
        self.assertEqual(len(info["erreurs"]), 1)
        self.assertEqual(info["non_resolues"], 1)


class TestSorties(unittest.TestCase):

    def _e(self):
        return E.parse_resultat("Novaxia Investissement", reponse()) | {"enrichi_le": "2026-11-01"}

    def test_ligne_sheet_schema(self):
        ligne = E.ligne_sheet(self._e())
        self.assertEqual(len(ligne), len(E.COLONNES_SHEET))
        self.assertEqual(ligne[E.COL_CLE], "novaxia investissement")

    def test_valeurs_pg_schema(self):
        self.assertEqual(len(E.valeurs_pg(self._e())), len(E.COLONNES_PG))

    def test_ecritures_sans_configuration(self):
        self.assertIn("absent", E.ecrire_pg(None, [{}]))
        self.assertIn("rien", E.ecrire_pg("postg:///x", []))
        self.assertIn("non configure", E.ecrire_sheet(None, []))
        self.assertIsNone(E.memoire_pg(None))
        self.assertEqual(E.lire_source_pg(None), [])


class TestSocietesAEnrichir(unittest.TestCase):

    def test_distinct_non_vide(self):
        src = [{"societe": "Alpha"}, {"societe": "alpha"}, {"societe": ""},
               {"societe": None}, {"societe": "Beta"}]
        self.assertEqual(E.societes_a_enrichir(src), ["Alpha", "Beta"])


if __name__ == "__main__":
    unittest.main()
