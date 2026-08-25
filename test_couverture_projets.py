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
        self.assertEqual(pref.nom_pour_requete(moz, "pt"), "Moçambique")
        self.assertEqual(pref.nom_pour_requete(moz, "en"), "Mozambique")

    def test_declencheurs_traduits(self):
        for langue in ("fr", "pt", "es", "ar", "ru", "uk", "sw"):
            self.assertNotEqual(dp.declencheurs(langue),
                                dp.DECLENCHEURS_NAISSANCE, langue)

    def test_langue_inconnue_retombe_sur_anglais(self):
        self.assertEqual(dp.declencheurs("zz"), dp.DECLENCHEURS_NAISSANCE)

    def test_une_url_par_langue_et_par_famille(self):
        """Depuis la correction du 24/08 : une requete par (langue x famille)
        de declencheurs, et non plus une seule requete geante par langue."""
        moz = pref.pays_par_iso3("MOZ")
        urls = dp.urls_du_pays(moz)
        langues = [l for l, _ in urls]
        self.assertEqual(set(langues), {"pt", "en"})
        self.assertGreater(len(urls), 2)
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

    def test_source_officielle_seule_ne_promeut_plus(self):
        """Resserrement du 24/08/2026 : une source officielle isolee cree bien
        un candidat, mais ne le promeut plus sans corroboration de presse."""
        c = dp.regrouper([self._signal("https://www.worldbank.org/p1")],
                         registre=[])
        self.assertEqual(len(c), 1)
        self.assertTrue(c[0]["sources_officielles"])
        self.assertEqual(c[0]["nb_sources_presse"], 0)
        self.assertFalse(dp.promouvable(c[0]))

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


class TestCorrectionFormeRequete(unittest.TestCase):
    """Corrections validees par sonde_requetes.py le 24/08/2026.

    Mesure : la forme de production (38 declencheurs en OR PUIS le pays)
    obtenait 0,0 % de titres parlant du pays, sur TZA, COD et GIN. Placer le
    pays EN TETE la faisait passer a 75-85 %, et une famille courte a 65-95 %.
    Ces tests verrouillent les trois corrections."""

    @staticmethod
    def _requete(iso3, i=0):
        from urllib.parse import unquote
        p = pref.pays_par_iso3(iso3)
        url = dp.urls_du_pays(p)[i][1]
        return unquote(url.split("q=")[1].split("&")[0]).replace("+", " ")

    def test_le_pays_est_en_tete(self):
        for iso3 in ("TZA", "COD", "GIN"):
            q = self._requete(iso3)
            self.assertTrue(q.startswith('"'), iso3)
            nom = q.split('"')[1]
            self.assertIn(nom.lower()[:4],
                          pref.pays_par_iso3(iso3)["nom"].lower()
                          + " ".join(pref.pays_par_iso3(iso3)["noms_locaux"].values()).lower())

    def test_requete_courte(self):
        # L'ancienne forme (0 % de pertinence) faisait ~780 caracteres. Les
        # familles restent bien en deca ; la Guinee monte a ~260 a cause de ses
        # six exclusions d'homonymes, ce qui reste sans commune mesure.
        for iso3 in ("TZA", "COD", "GIN"):
            for i in range(len(dp.urls_du_pays(pref.pays_par_iso3(iso3)))):
                self.assertLess(len(self._requete(iso3, i)), 300, iso3)

    def test_plusieurs_familles_par_langue(self):
        urls = dp.urls_du_pays(pref.pays_par_iso3("TZA"))
        self.assertGreaterEqual(len(urls), 4)

    def test_accents_retablis_en_francais(self):
        q = self._requete("GIN")
        self.assertIn("Guinée", q)
        fr = dp.familles("fr")
        self.assertIn("étude de faisabilité", fr["etudes"])
        self.assertIn("financement approuvé", fr["financement"])

    def test_homonymes_exclus(self):
        self.assertIn('-"Papua New Guinea"', self._requete("GIN"))
        self.assertIn('-"Republic of the Congo"', self._requete("COD"))

    def test_swahili_sans_mots_ambigus(self):
        """'bandari' (port) ramenait 10 matchs du Bandari FC : retire."""
        sw = " ".join(dp.familles("sw").values())
        self.assertNotIn("bandari", sw.lower())

    def test_toutes_les_langues_ont_des_familles(self):
        for langue in ("en", "fr", "pt", "es", "ar", "ru", "uk", "sw"):
            self.assertTrue(dp.familles(langue), langue)

    def test_langue_inconnue_retombe_sur_anglais(self):
        self.assertEqual(dp.familles("zz"), dp.FAMILLES_DECLENCHEURS["en"])


class TestEditeurDerriereAgregateur(unittest.TestCase):
    """SHADOW RUN DU 24/08/2026 : tous les liens Google News pointent sur
    news.google.com. Dix redactions distinctes comptaient donc pour UNE seule
    source, le poids de preuve etait plafonne a 0.40 et AUCUN candidat ne
    pouvait jamais etre promu. L'editeur reel est dans le suffixe du titre."""

    L = "https://news.google.com/rss/articles/CBM"

    def test_editeur_extrait_du_titre(self):
        self.assertEqual(sref.editeur_du_titre(
            "Tanzania, Uganda Sign MoU for Tanga Energy Hub - TanzaniaInvest"),
            "TanzaniaInvest")

    def test_titre_sans_suffixe(self):
        self.assertEqual(sref.editeur_du_titre("Un titre sans editeur"), "")

    def test_suffixe_trop_long_rejete(self):
        long = "Titre - " + "x" * 50
        self.assertEqual(sref.editeur_du_titre(long), "")

    def test_cle_source_distingue_les_redactions(self):
        a = {"titre": "X - TanzaniaInvest", "lien": self.L + "1"}
        b = {"titre": "Y - CNBC Africa", "lien": self.L + "2"}
        self.assertNotEqual(sref.cle_source(a), sref.cle_source(b))

    def test_meme_redaction_meme_cle(self):
        a = {"titre": "X - The EastAfrican", "lien": self.L + "1"}
        b = {"titre": "Y - The EastAfrican", "lien": self.L + "2"}
        self.assertEqual(sref.cle_source(a), sref.cle_source(b))

    def test_editeur_connu_recoit_sa_fiabilite(self):
        sig = {"titre": "X - Bloomberg", "lien": self.L + "1"}
        self.assertEqual(sref.type_du_signal(sig), "presse_economique")

    def test_editeur_inconnu_mais_nomme_vaut_mieux_qu_anonyme(self):
        nomme = {"titre": "X - Journal du Coin Perdu", "lien": self.L + "1"}
        anonyme = {"titre": "X", "lien": self.L + "2"}
        self.assertGreater(sref.fiabilite_du_signal(nomme),
                           sref.fiabilite_du_signal(anonyme))

    def test_lien_direct_prime_sur_le_titre(self):
        sig = {"titre": "X - Blog Inconnu", "lien": "https://www.worldbank.org/p"}
        self.assertEqual(sref.type_du_signal(sig), "dfi")

    def test_cas_reel_tanga_refinery_devient_promouvable(self):
        titres = ["A - TanzaniaInvest", "B - CNBC Africa", "C - The EastAfrican",
                  "D - African Energy", "E - TRT Afrika"]
        sig = [{"titre": t, "lien": self.L + str(i), "date": "2026-08-07",
                "resume": "", "extraction": {
                    "projet": "Tanga Refinery", "iso3": "TZA", "secteur": "energie",
                    "phase": "MOU", "acteurs": ["vitol"], "montant_musd": 20000,
                    "confiance": 56, "localisation": "Tanga"}}
               for i, t in enumerate(titres)]
        c = dp.regrouper(sig, registre=[])[0]
        self.assertEqual(c["nb_sources"], 5)
        self.assertGreater(c["poids_sources"], 1.0)
        self.assertTrue(dp.promouvable(c))

    def test_une_seule_redaction_repetee_ne_suffit_pas(self):
        """Controle inverse : la correction ne doit pas ouvrir les vannes."""
        sig = [{"titre": "Titre {} - TanzaniaInvest".format(i),
                "lien": self.L + str(i), "date": "2026-08-07", "resume": "",
                "extraction": {"projet": "Projet Solo", "iso3": "TZA",
                               "secteur": "energie", "phase": "MOU",
                               "acteurs": [], "montant_musd": 0,
                               "confiance": 60, "localisation": "Tanga"}}
               for i in range(4)]
        c = dp.regrouper(sig, registre=[])[0]
        self.assertEqual(c["nb_sources"], 1)
        self.assertFalse(dp.promouvable(c))


class TestPlancherConfianceLLM(unittest.TestCase):
    """SHADOW RUN DU 24/08/2026, faux positif PROMU. Le candidat "Gasabo" --
    une raffinerie rwandaise sanctionnee pour contrebande d'or, donc pas un
    projet -- est passe promouvable parce que Bloomberg, la BBC et The Africa
    Report l'avaient couvert (poids 1.65), alors que le modele ne lui donnait
    que 25 % de confiance. Le poids des sources rachetait le rejet du modele."""

    def _cand(self, **kw):
        base = {"nom": "Test", "iso3": "RWA", "secteur": "industrie",
                "nb_signaux": 3, "nb_sources": 3, "poids_sources": 1.65,
                "meilleure_fiabilite": 0.65, "confiance_llm": 70,
                "phase": "OPERATIONS", "acteurs_top": [], "sources_officielles": [],
                "nb_sources_presse": 3, "montant_musd": 2000,
                "signaux": [{"titre": "LNG refinery megaproject"}]}
        base.update(kw)
        base["confiance"] = dp.score_confiance(base)
        return base

    def test_cas_gasabo_bloque(self):
        self.assertFalse(dp.promouvable(self._cand(confiance_llm=25)))

    def test_cas_tanga_passe(self):
        self.assertTrue(dp.promouvable(self._cand(
            confiance_llm=66, nb_signaux=9, nb_sources=9, poids_sources=4.8)))

    def test_le_poids_ne_rachete_pas_un_doute_du_modele(self):
        """Meme avec dix sources de reference, un doute du modele bloque."""
        self.assertFalse(dp.promouvable(self._cand(
            confiance_llm=20, nb_sources=10, poids_sources=6.5)))

    def test_seuil_configurable(self):
        self.assertGreater(dp.SEUIL_CONFIANCE_LLM, 0)


class TestCorrectifsIssusDuShadowRun(unittest.TestCase):
    """Trois autres defauts reveles par le run reel du 24/08/2026."""

    def test_exclusion_papouasie_en_francais(self):
        """L'article rate etait francophone : 'Un projet minier fait craindre
        un desastre environnemental en Papouasie-Nouvelle-Guinee'."""
        self.assertIn('-"Papouasie"', dp.EXCLUSIONS_PAYS["GIN"])
        from urllib.parse import unquote
        p = pref.pays_par_iso3("GIN")
        q = unquote(dp.urls_du_pays(p)[0][1].split("q=")[1].split("&")[0])
        self.assertIn("Papouasie", q)

    def test_eacop_reconnait_son_doublon(self):
        """Le run a cree "Uganda-Tanzania Oil Pipeline" comme NOUVEAU projet
        alors qu'EACOP est deja au registre : alias manquant."""
        self.assertEqual(dp.deja_connu("Uganda-Tanzania Oil Pipeline"), "EACOP_UGA")
        self.assertEqual(dp.deja_connu("Uganda Tanzania Oil Pipeline"), "EACOP_UGA")

    def test_prompt_rejette_sanctions_et_cooperation(self):
        p = dp.construire_prompt([{"titre": "x"}])
        self.assertIn("SANCTION", p)
        self.assertIn("COOPÉRATION SANITAIRE", p)


class TestCorrectifsDuRunDeProduction(unittest.TestCase):
    """RUN DU 24/08/2026 : 38 promotions en un seul run, dont l'essentiel etait
    le portefeuille de la Banque Mondiale (assainissement a Kinshasa, mobilite
    urbaine a Karachi, resilience aux inondations). Trois correctifs."""

    def _cand(self, **kw):
        base = {"nom": "Projet Test", "iso3": "MLI", "secteur": "energie",
                "nb_signaux": 2, "nb_sources": 2, "poids_sources": 1.6,
                "meilleure_fiabilite": 0.95, "confiance_llm": 70,
                "phase": "FUNDING_APPROVED", "acteurs_top": [],
                "sources_officielles": ["BM"], "nb_sources_presse": 1,
                "montant_musd": 2000,
                "signaux": [{"titre": "LNG refinery project"}]}
        base.update(kw)
        base["confiance"] = dp.score_confiance(base)
        return base

    # --- 1. Corroboration de presse exigee -------------------------------
    def test_dfi_seul_ne_promeut_plus(self):
        self.assertFalse(dp.promouvable(self._cand(
            nb_sources_presse=0, nb_signaux=1, nb_sources=1, poids_sources=0.95)))

    def test_dfi_corrobore_par_la_presse_promeut(self):
        self.assertTrue(dp.promouvable(self._cand()))

    def test_le_comptage_des_sources_presse_exclut_les_officielles(self):
        L = "https://news.google.com/rss/articles/CBM"
        ext = {"projet": "Projet Alpha", "iso3": "TZA", "secteur": "energie",
               "phase": "FEASIBILITY", "acteurs": [], "montant_musd": 0,
               "confiance": 80, "localisation": ""}
        sig = [{"titre": "A - Reuters", "lien": L + "1", "date": "2026-08-01",
                "resume": "", "extraction": ext},
               {"titre": "B", "lien": "https://www.worldbank.org/x",
                "date": "2026-08-02", "resume": "", "extraction": ext}]
        c = dp.regrouper(sig, registre=[])[0]
        self.assertEqual(c["nb_sources"], 2)
        self.assertEqual(c["nb_sources_presse"], 1)   # la BM ne compte pas

    # --- 2. Filtre de pertinence Amarante --------------------------------
    def test_assainissement_urbain_ecarte(self):
        eau = self._cand(nom="DRC Urban Flood Resilience Project",
                         secteur="infrastructure", montant_musd=0,
                         signaux=[{"titre": "urban flood water supply sanitation"}])
        self.assertFalse(dp.promouvable(eau))
        self.assertFalse(dp.pertinent_pour_amarante(eau)[0])

    def test_lng_en_pays_a_risque_retenu(self):
        tanga = self._cand(nom="Tanga Refinery", iso3="TZA", nb_sources_presse=8,
                           nb_sources=8, poids_sources=4.3, confiance_llm=72,
                           montant_musd=20000,
                           signaux=[{"titre": "Tanga refinery energy hub MoU"}])
        self.assertTrue(dp.promouvable(tanga))

    def test_motif_de_pertinence_explicable(self):
        _, motif = dp.pertinent_pour_amarante(self._cand())
        self.assertIn("secteur", motif)
        self.assertIn("risque pays", motif)

    def test_secteurs_hierarchises(self):
        d = dp.SECTEURS_DEPLOIEMENT
        self.assertGreater(d["energie"], d["transport"])
        self.assertGreater(d["transport"], d["infrastructure"])

    # --- 3. Priorisation avant le plafond --------------------------------
    def test_les_grands_projets_passent_devant(self):
        signaux = ([{"titre": "Institutional capacity building program",
                     "resume": "", "lien": "http://x/{}".format(i)}
                    for i in range(20)]
                   + [{"titre": "New LNG terminal, $12 billion refinery",
                       "resume": "", "lien": "http://y/1"}])
        retenus, ecartes = dp.prioriser(signaux, plafond=5)
        self.assertIn("LNG", retenus[0]["titre"])
        self.assertEqual(len(ecartes), 16)

    def test_priorisation_ne_perd_aucun_signal(self):
        signaux = [{"titre": "T{}".format(i), "resume": "",
                    "lien": "http://x/{}".format(i)} for i in range(50)]
        retenus, ecartes = dp.prioriser(signaux, plafond=10)
        self.assertEqual(len(retenus) + len(ecartes), 50)

    def test_source_officielle_favorisee_a_note_egale(self):
        officiel = {"titre": "Projet X", "resume": "",
                    "lien": "https://www.worldbank.org/1"}
        blog = {"titre": "Projet Y", "resume": "", "lien": "https://blog.example/1"}
        retenus, _ = dp.prioriser([blog, officiel], plafond=1)
        self.assertEqual(retenus[0]["lien"], officiel["lien"])


class TestMemoireNeSacrifiePasLesEcartes(unittest.TestCase):
    """RUN DU 24/08/2026, 2e occurrence. Le run marquait "vus" les 1264 signaux
    prepares alors qu'il n'en avait analyse que 300 : les 964 ecartes par le
    plafond etaient perdus DEFINITIVEMENT. Le run suivant a trouve 0 article
    nouveau et 0 signal DFI -- tout etait deja "vu", sans avoir ete lu."""

    def test_seuls_les_signaux_analyses_sont_memorises(self):
        import inspect
        src = inspect.getsource(dp.main)
        i = src.find("signaux, ecartes = prioriser")
        j = src.find("radar_etat.sauver")
        self.assertGreater(i, 0, "la priorisation doit exister")
        self.assertLess(i, j, "prioriser DOIT preceder la memorisation")
        self.assertIn('[s["id"] for s in signaux]', src)

    def test_prioriser_ne_renvoie_que_les_retenus(self):
        signaux = [{"titre": "T{}".format(i), "resume": "",
                    "lien": "http://x/{}".format(i)} for i in range(30)]
        retenus, ecartes = dp.prioriser(signaux, plafond=10)
        self.assertEqual(len(retenus), 10)
        self.assertEqual(len(ecartes), 20)
        # Aucun ecarte ne doit se retrouver parmi les retenus : sinon il serait
        # memorise sans avoir ete analyse.
        liens_retenus = {s["lien"] for s in retenus}
        for e in ecartes:
            self.assertNotIn(e["lien"], liens_retenus)


class TestMotsEntiers(unittest.TestCase):
    """RUN DU 24/08/2026. La recherche par SOUS-CHAINE faisait matcher "mine"
    dans "determine", "dam" dans "fundamental", "port" dans "support". Un
    programme de protection sociale heritait du "vocabulaire de grand
    chantier" et gagnait 25 points de pertinence indus."""

    def test_faux_positifs_de_sous_chaine_elimines(self):
        for t in ("Determine the scope of the reform",
                  "Fundamental restructuring", "Important support programme",
                  "Rural export promotion", "Examine the framework"):
            self.assertFalse(dp._contient_mot(dp._norm(t), dp.MOTS_GRAND_PROJET), t)

    def test_vrais_positifs_conserves(self):
        for t in ("New LNG terminal", "Copper mine development",
                  "Hydropower dam project", "Transport corridors programme",
                  "Deepwater port expansion"):
            self.assertTrue(dp._contient_mot(dp._norm(t), dp.MOTS_GRAND_PROJET), t)

    def test_pluriel_tolere(self):
        self.assertTrue(dp._contient_mot(dp._norm("rail corridors"), ("corridor",)))

    def test_le_filtre_de_pertinence_utilise_les_mots_entiers(self):
        social = {"nom": "Resilient Social Protection Program", "iso3": "MLI",
                  "secteur": "infrastructure", "montant_musd": 0,
                  "signaux": [{"titre": "Determine the fundamental scope"}]}
        pertinent, motif = dp.pertinent_pour_amarante(social)
        self.assertFalse(pertinent)
        self.assertNotIn("grand chantier", motif)


class TestVerificationPresseCiblee(unittest.TestCase):
    """Corrige l'asymetrie structurelle mesuree le 24/08/2026 : les leads DFI
    couvrent 40 pays, les requetes presse 3 par run. Un projet DFI au Malawi ne
    pouvait donc pas etre corrobore. Au lieu d'assouplir la regle, on VA
    CHERCHER la corroboration sur le nom exact du projet."""

    A = staticmethod(lambda t: {
        "titre": t, "resume": "", "date": "Mon, 10 Aug 2026 10:00:00 +0000",
        "lien": "https://news.google.com/rss/" + str(abs(hash(t)) % 9999)})

    def _cand(self, nom="Mpatamanga Hydropower Storage Project", iso3="MWI", **kw):
        base = {"nom": nom, "iso3": iso3, "secteur": "energie", "nb_signaux": 2,
                "nb_sources": 1, "nb_sources_presse": 0, "poids_sources": 0.95,
                "meilleure_fiabilite": 0.95, "confiance_llm": 75,
                "phase": "FUNDING_APPROVED", "acteurs_top": [],
                "sources_officielles": ["BM"], "montant_musd": 1500,
                "sources": ["BMP"],
                "signaux": [{"titre": nom, "lien": "", "date": "2026-07-01"}]}
        base.update(kw)
        base["confiance"] = dp.score_confiance(base)
        return base

    # --- Selection --------------------------------------------------------
    def test_candidat_dfi_isole_et_pertinent_est_verifie(self):
        self.assertEqual(len(dp.candidats_a_verifier([self._cand()])), 1)

    def test_candidat_deja_corrobore_non_verifie(self):
        self.assertEqual(dp.candidats_a_verifier(
            [self._cand(nb_sources_presse=2)]), [])

    def test_candidat_non_pertinent_non_verifie(self):
        """Inutile de depenser une requete sur un programme d'assainissement."""
        eau = self._cand(nom="Urban Water Supply Project", secteur="infrastructure",
                         montant_musd=0,
                         signaux=[{"titre": "urban water supply sanitation"}])
        self.assertEqual(dp.candidats_a_verifier([eau]), [])

    def test_candidat_sans_nom_non_verifie(self):
        self.assertEqual(dp.candidats_a_verifier(
            [self._cand(sans_nom=True)]), [])

    def test_plafond_de_verifications(self):
        cands = [self._cand(nom="Projet Alpha{} Hydropower".format(i))
                 for i in range(20)]
        self.assertEqual(len(dp.candidats_a_verifier(cands, plafond=5)), 5)

    def test_requete_porte_le_nom_exact(self):
        r = dp.requete_verification(self._cand())
        self.assertIn('"Mpatamanga Hydropower Storage Project"', r)

    # --- Appariement ------------------------------------------------------
    def test_ancre_forte_tolere_un_nom_incomplet(self):
        """"Mpatamanga hydropower project" doit matcher, sans le mot Storage."""
        trouves = dp.articles_confirmants([
            self.A("Mpatamanga hydropower project reaches financial close - Reuters"),
            self.A("Mpatamanga scheme contractors shortlisted - African Energy"),
            self.A("Unrelated football match - Sofascore")], self._cand())
        self.assertEqual(len(trouves), 2)

    def test_ancre_faible_exige_le_pays(self):
        b = self._cand(nom="BRIDGE", iso3="NGA")
        self.assertEqual(len(dp.articles_confirmants(
            [self.A("New bridge opens in Lagos - Blog")], b)), 0)
        self.assertEqual(len(dp.articles_confirmants(
            [self.A("Nigeria BRIDGE programme launched - Blog")], b)), 1)

    def test_source_officielle_ne_corrobore_pas(self):
        """Une seconde source DFI n'est pas une corroboration independante."""
        trouves = dp.articles_confirmants([{
            "titre": "Mpatamanga hydropower approved",
            "lien": "https://www.worldbank.org/p", "resume": ""}], self._cand())
        self.assertEqual(trouves, [])

    # --- Integration ------------------------------------------------------
    def test_confirmation_debloque_la_promotion(self):
        c = self._cand()
        self.assertFalse(dp.promouvable(c))
        c2 = dp.integrer_confirmations(c, [
            self.A("Mpatamanga hydropower project financial close - Reuters"),
            self.A("Mpatamanga contractors shortlisted - African Energy")])
        self.assertEqual(c2["nb_sources_presse"], 2)
        self.assertGreater(c2["poids_sources"], c["poids_sources"])
        self.assertTrue(dp.promouvable(c2))

    def test_absence_de_confirmation_laisse_en_attente(self):
        c = self._cand()
        self.assertEqual(dp.integrer_confirmations(c, []), c)
        self.assertFalse(dp.promouvable(c))

    def test_entree_non_mutee(self):
        c = self._cand()
        dp.integrer_confirmations(c, [self.A("Mpatamanga hydropower - Reuters")])
        self.assertEqual(c["nb_sources_presse"], 0)

    def test_requete_en_erreur_laisse_le_candidat_intact(self):
        dp.PAUSE = 0.0

        def fetch(url):
            raise RuntimeError("503")

        out, n = dp.verifier_par_la_presse([self._cand()], fetch=fetch)
        self.assertFalse(dp.promouvable(out[0]))
