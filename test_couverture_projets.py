# -*- coding: utf-8 -*-
"""Referentiels de couverture (P2, P3, P5, P7, P8) : geographie a trois
niveaux, langues par pays, cadence, registre de sources et fiabilite ponderee.
Tout est offline et pur."""

import unittest

import decouverte_projets as dp
import pays_projets_reference as pref
import sources_reference as sref


class TestNiveauxGeographiques(unittest.TestCase):
    """P7 : trois niveaux, plus limites aux PAYS_COUVERTS_AMARANTE."""

    def test_les_trois_niveaux_sont_peuples(self):
        for niveau in ("suivi", "strategique", "global_watch"):
            self.assertTrue(pref.pays_du_niveau(niveau), niveau)

    def test_pays_du_global_watch_demandes_presents(self):
        iso = {p["iso3"] for p in pref.charger_pays()}
        for attendu in ("GHA", "CMR", "ETH", "EGY", "MOZ", "AGO", "PAN",
                        "BRA", "COL", "GUY", "SUR", "SAU", "ARE", "QAT",
                        "OMN", "PAK", "IDN"):
            self.assertIn(attendu, iso, attendu)

    def test_pays_hors_perimetre_amarante_couverts(self):
        # Le point de P7 : la decouverte ne doit PAS etre bornee au perimetre
        # commercial actuel.
        import ted_complet_v14 as ted
        hors = [p for p in pref.charger_pays()
                if not ted.dans_le_perimetre(p["iso3"])]
        self.assertGreater(len(hors), 10)

    def test_iso3_uniques(self):
        iso = [p["iso3"] for p in pref.charger_pays()]
        self.assertEqual(len(iso), len(set(iso)))

    def test_ajout_d_un_pays_sans_toucher_au_moteur(self):
        nouveau = [pref._p("KEN", "Kenya", "global_watch", ["en", "sw"])]
        self.assertEqual(pref.charger_pays(nouveau)[0]["iso3"], "KEN")


class TestLangues(unittest.TestCase):
    """P3 : langues pertinentes par pays."""

    def test_lusophones_corriges(self):
        # Defaut identifie a l'audit : Mozambique et Angola etaient interroges
        # en anglais alors qu'ils sont lusophones.
        for iso in ("MOZ", "AGO"):
            self.assertEqual(pref.pays_par_iso3(iso)["langues"][0], "pt", iso)

    def test_arabophones(self):
        for iso in ("IRQ", "LBY", "EGY", "SAU"):
            self.assertIn("ar", pref.pays_par_iso3(iso)["langues"], iso)

    def test_ukraine_et_asie_centrale(self):
        self.assertIn("uk", pref.pays_par_iso3("UKR")["langues"])
        self.assertIn("ru", pref.pays_par_iso3("KAZ")["langues"])

    def test_hispanophones(self):
        for iso in ("PAN", "COL"):
            self.assertIn("es", pref.pays_par_iso3(iso)["langues"], iso)

    def test_swahili(self):
        self.assertIn("sw", pref.pays_par_iso3("TZA")["langues"])

    def test_les_sept_langues_demandees_sont_utilisees(self):
        utilisees = {l for p in pref.charger_pays() for l in p["langues"]}
        for langue in ("fr", "en", "pt", "ar", "es", "sw", "ru", "uk"):
            self.assertIn(langue, utilisees, langue)

    def test_nom_du_pays_traduit_dans_la_requete(self):
        moz = pref.pays_par_iso3("MOZ")
        self.assertEqual(pref.nom_pour_requete(moz, "pt"), "Mocambique")
        self.assertEqual(pref.nom_pour_requete(moz, "en"), "Mozambique")

    def test_declencheurs_traduits(self):
        for langue in ("fr", "pt", "es", "ar", "ru", "uk", "sw"):
            self.assertNotEqual(dp.declencheurs(langue),
                                dp.DECLENCHEURS_NAISSANCE, langue)

    def test_langue_inconnue_retombe_sur_anglais(self):
        self.assertEqual(dp.declencheurs("zz"), dp.DECLENCHEURS_NAISSANCE)

    def test_une_url_par_langue_pertinente(self):
        moz = pref.pays_par_iso3("MOZ")
        urls = dp.urls_du_pays(moz)
        self.assertEqual([l for l, _ in urls], ["pt", "en"])
        self.assertTrue(all(u.startswith("https://news.google.com") for _, u in urls))


class TestCadence(unittest.TestCase):
    """P8 : frequence par niveau."""

    def test_cadences_conformes(self):
        self.assertEqual(pref.CADENCE["suivi"], 1)          # quotidien
        self.assertEqual(pref.CADENCE["strategique"], 3)    # 2-3 fois/semaine
        self.assertEqual(pref.CADENCE["global_watch"], 7)   # hebdomadaire

    def test_pays_jamais_interroge_est_du(self):
        self.assertTrue(pref.a_interroger(pref.pays_par_iso3("GHA"), None))

    def test_global_watch_pas_du_apres_deux_jours(self):
        self.assertFalse(pref.a_interroger(pref.pays_par_iso3("GHA"), 2))

    def test_suivi_du_des_le_lendemain(self):
        self.assertTrue(pref.a_interroger(pref.pays_par_iso3("MLI"), 1))

    def test_selection_priorise_le_niveau(self):
        derniers = {p["iso3"]: 30 for p in pref.charger_pays()}
        sel = pref.selection_du_run(derniers, plafond=5)
        self.assertTrue(all(p["niveau"] == "suivi" for p in sel), 
                        [(p["iso3"], p["niveau"]) for p in sel])

    def test_plafond_respecte(self):
        self.assertEqual(len(pref.selection_du_run({}, plafond=4)), 4)

    def test_pays_a_jour_exclu(self):
        derniers = {p["iso3"]: 0 for p in pref.charger_pays()}
        self.assertEqual(pref.selection_du_run(derniers), [])


class TestRegistreSources(unittest.TestCase):
    """P2 : sources extensibles, P1 : collecteurs internes."""

    def test_familles_demandees_presentes(self):
        types = {s["type"] for s in sref.SOURCES}
        for attendu in ("dfi", "gouvernement", "agence_publique", "entreprise",
                        "presse_economique", "presse_sectorielle",
                        "presse_generaliste"):
            self.assertIn(attendu, types, attendu)

    def test_dfi_demandees_presentes(self):
        domaines = {s["domaine"] for s in sref.SOURCES}
        for attendu in ("worldbank.org", "ifc.org", "miga.org", "afdb.org",
                        "ebrd.com", "afd.fr"):
            self.assertIn(attendu, domaines, attendu)

    def test_sources_par_pays(self):
        self.assertTrue(sref.sources_du_pays("TZA"))
        self.assertTrue(sref.sources_du_pays("COD"))

    def test_ajout_d_une_source_sans_toucher_au_moteur(self):
        # Une source inconnue est simplement traitee au niveau le plus prudent.
        inconnue = {"lien": "https://blog-inconnu.example/x"}
        self.assertEqual(sref.type_du_signal(inconnue), "presse_locale")

    def test_sous_domaine_reconnu(self):
        s = sref.source_du_lien("https://data.worldbank.org/projects/x")
        self.assertIsNotNone(s)
        self.assertEqual(s["type"], "dfi")


class TestFiabilite(unittest.TestCase):
    """P5 : la fiabilite est une echelle, pas un comptage."""

    def test_hierarchie_des_types(self):
        f = sref.fiabilite_du_type
        self.assertGreater(f("dfi"), f("gouvernement"))
        self.assertGreater(f("gouvernement"), f("presse_economique"))
        self.assertGreater(f("presse_economique"), f("presse_locale"))

    def test_collecteur_interne_reconnu_sans_url(self):
        # Un signal issu du collecteur BM n'a pas besoin d'URL pour etre
        # reconnu comme officiel : son origine est certaine.
        sig = {"source": "BM", "lien": ""}
        self.assertEqual(sref.type_du_signal(sig), "dfi")
        self.assertTrue(sref.est_officielle(sig))

    def test_presse_non_officielle(self):
        self.assertFalse(sref.est_officielle({"lien": "https://www.reuters.com/x"}))

    def test_officielles_reconnues(self):
        for lien in ("https://www.worldbank.org/a", "https://presidence.cd/b",
                     "https://tpdc.co.tz/c"):
            self.assertTrue(sref.est_officielle({"lien": lien}), lien)

    def test_surcharge_par_environnement(self):
        import os
        os.environ["RADAR_FIABILITE_DFI"] = "0.70"
        try:
            self.assertAlmostEqual(sref.fiabilite_du_type("dfi"), 0.70)
        finally:
            del os.environ["RADAR_FIABILITE_DFI"]

    def test_description_lisible(self):
        d = sref.decrire_source({"lien": "https://www.afdb.org/x"})
        self.assertIn("developpement", d.lower())


class TestPoidsDePreuveDansLesCandidats(unittest.TestCase):
    """Integration : le poids de preuve remonte jusqu'au candidat."""

    def _signal(self, lien, projet="Projet Alpha", iso3="TZA", phase="FEASIBILITY"):
        return {"titre": projet, "date": "2026-05-01", "lien": lien, "resume": "",
                "extraction": {"projet": projet, "iso3": iso3, "secteur": "energie",
                               "phase": phase, "acteurs": [], "montant_musd": 0,
                               "confiance": 80}}

    def test_source_officielle_unique_cree_un_candidat_promouvable(self):
        c = dp.regrouper([self._signal("https://www.worldbank.org/p1")],
                         registre=[])
        self.assertEqual(len(c), 1)
        self.assertTrue(c[0]["sources_officielles"])
        self.assertTrue(dp.promouvable(c[0]),
                        (c[0]["confiance"], c[0]["poids_sources"]))

    def test_deux_sources_faibles_ne_suffisent_pas(self):
        c = dp.regrouper([self._signal("https://blog-a.example/1"),
                          self._signal("https://blog-b.example/2"),
                          self._signal("https://blog-c.example/3")],
                         registre=[])
        self.assertFalse(dp.promouvable(c[0]),
                         (c[0]["confiance"], c[0]["poids_sources"]))

    def test_une_seule_source_prolixe_ne_gonfle_pas_le_poids(self):
        # Trois articles du meme domaine = une seule source de preuve.
        c = dp.regrouper([self._signal("https://www.reuters.com/1"),
                          self._signal("https://www.reuters.com/2"),
                          self._signal("https://www.reuters.com/3")],
                         registre=[])
        self.assertEqual(c[0]["nb_signaux"], 3)
        self.assertAlmostEqual(c[0]["poids_sources"],
                               sref.fiabilite_du_type("presse_economique"), 2)
        self.assertFalse(dp.promouvable(c[0]))

    def test_motifs_citent_la_source_officielle(self):
        c = dp.regrouper([self._signal("https://www.afdb.org/p1")], registre=[])
        self.assertTrue(any("officielle" in m for m in dp.motifs_confiance(c[0])))


if __name__ == "__main__":
    unittest.main()
