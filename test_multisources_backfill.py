# -*- coding: utf-8 -*-
"""Multi-sources DFI (P1) et backfill (P9).

Deux exigences verrouillees ici :
  - les collecteurs DFI alimentent LE MEME pipeline que la presse, avec la
    fiabilite qui va avec (une source officielle unique peut suffire) ;
  - le backfill sait explorer 12 a 24 mois d'archive, decoupes en fenetres
    datees, sans quoi Google News ne rend que le present.
Tout est offline.
"""

import datetime
import unittest

import adaptateurs_dfi as adfi
import decouverte_projets as dp
import pays_projets_reference as pref
import sources_reference as sref


def lead(titre, src="BM", **kw):
    d = {"titre": titre, "src": src, "pays": "Tanzanie", "zone": "Afrique de l'Est",
         "agence": "World Bank", "lien": "https://projects.worldbank.org/" + titre[:6],
         "date_det": "2026-05-03", "mois": "2026-05", "pub": "P" + str(abs(hash(titre)) % 9999),
         "valeur": "", "enveloppe": "", "justif": ""}
    d.update(kw)
    return d


class TestFiltrageAmont(unittest.TestCase):
    """Protege le budget LLM : tout avis DFI n'est pas un signal de projet."""

    def test_avis_de_grand_projet_retenu(self):
        for t in ("Construction of the Lindi LNG terminal",
                  "Rehabilitation of the Kinshasa-Matadi road corridor",
                  "Feasibility study for the Souapiti hydropower plant",
                  "EPC contract for the Kamoa smelter"):
            self.assertTrue(adfi.est_signal_de_projet(lead(t)), t)

    def test_achat_courant_ecarte(self):
        for t in ("Supply of office supplies for the country office",
                  "Audit services for the annual report",
                  "Training workshop on procurement",
                  "Vehicle rental for the project unit"):
            self.assertFalse(adfi.est_signal_de_projet(lead(t)), t)

    def test_montant_significatif_sauve_un_intitule_laconique(self):
        maigre = lead("Lot 3", valeur="")
        self.assertFalse(adfi.est_signal_de_projet(maigre))
        riche = lead("Lot 3", valeur="450000000 USD")
        self.assertTrue(adfi.est_signal_de_projet(riche))

    def test_lead_vide_ecarte(self):
        self.assertFalse(adfi.est_signal_de_projet({}))


class TestConversionEnSignal(unittest.TestCase):

    def test_source_portee_par_le_collecteur(self):
        s = adfi.signal_depuis_lead(lead("LNG plant construction", src="AFDB"))
        self.assertEqual(s["source"], "AFDB")

    def test_fiabilite_dfi_sans_dependre_de_l_url(self):
        # Point clef : un avis sans lien exploitable reste officiel.
        s = adfi.signal_depuis_lead(lead("Hydropower project", src="MIGA", lien=""))
        self.assertEqual(sref.type_du_signal(s), "dfi")
        self.assertTrue(sref.est_officielle(s))

    def test_identifiant_stable_sans_lien(self):
        a = adfi.signal_depuis_lead(lead("Projet A", src="IFC", lien="", pub="P1"))
        b = adfi.signal_depuis_lead(lead("Projet B", src="IFC", lien="", pub="P2"))
        self.assertNotEqual(a["id"], b["id"])

    def test_date_reprise_du_lead(self):
        s = adfi.signal_depuis_lead(lead("Rail corridor project"))
        self.assertEqual(s["date"], "2026-05-03")

    def test_projet_id_amont_conserve(self):
        s = adfi.signal_depuis_lead(lead("Dam project", projet_id="P123456"))
        self.assertEqual(s["projet_id_amont"], "P123456")


class TestSelectionDesSources(unittest.TestCase):

    def test_toutes_les_dfi_demandees_sont_branchees(self):
        for src in ("BM", "IFC", "MIGA", "AFDB", "EBRD", "PROPARCO"):
            self.assertIn(src, adfi.SOURCES_DFI, src)

    def test_ted_exclu_par_defaut(self):
        out = adfi.signaux_depuis_leads([lead("Port development project", src="TED")])
        self.assertEqual(out, [])

    def test_ted_activable(self):
        out = adfi.signaux_depuis_leads([lead("Port development project", src="TED")],
                                        activer_ted=True)
        self.assertEqual(len(out), 1)

    def test_dedup_contre_la_memoire(self):
        l = lead("Hydropower project")
        s = adfi.signal_depuis_lead(l)
        self.assertEqual(adfi.signaux_depuis_leads([l], vus=[s["id"]]), [])

    def test_repartition_journalisee(self):
        out = adfi.signaux_depuis_leads([lead("LNG project", src="BM"),
                                         lead("Mine development", src="AFDB")])
        rep = adfi.repartition(out)
        self.assertEqual(rep["total"], 2)
        self.assertIn("BM", rep["par_source"])
        self.assertGreater(rep["poids_cumule"], 1.5)   # 2 x fiabilite dfi


class TestPipelineUnifie(unittest.TestCase):
    """P1 : meme pipeline, pas de branche parallele."""

    def test_un_signal_dfi_traverse_le_pipeline_de_decouverte(self):
        l = lead("Construction of the Lindi LNG terminal", src="BM")
        signaux = adfi.signaux_depuis_leads([l])
        self.assertEqual(len(signaux), 1)
        # On lui attache une extraction (ce que fera le LLM) et on le passe
        # exactement a la meme fonction que les articles de presse.
        signaux[0]["extraction"] = {
            "projet": "Lindi LNG", "iso3": "TZA", "secteur": "energie",
            "phase": "EPC_PROCUREMENT", "acteurs": ["shell"],
            "montant_musd": 42000, "confiance": 88}
        cands = dp.regrouper(signaux, registre=[])
        self.assertEqual(len(cands), 1)
        self.assertTrue(cands[0]["sources_officielles"])

    def test_source_officielle_unique_suffit_a_promouvoir(self):
        """Le gain concret de P1 + P5 : un seul avis Banque Mondiale cree un
        candidat solide, la ou il fallait trois articles de presse."""
        l = lead("Feasibility study for the Souapiti hydropower plant", src="BM")
        signaux = adfi.signaux_depuis_leads([l])
        signaux[0]["extraction"] = {
            "projet": "Souapiti", "iso3": "GIN", "secteur": "energie",
            "phase": "FEASIBILITY", "acteurs": ["cwe"], "montant_musd": 1400,
            "confiance": 85}
        cands = dp.regrouper(signaux, registre=[])
        self.assertTrue(dp.promouvable(cands[0]),
                        (cands[0]["confiance"], cands[0]["poids_sources"]))

    def test_dfi_et_presse_fusionnent_dans_un_meme_candidat(self):
        dfi = adfi.signaux_depuis_leads([lead("Kamoa smelter EPC contract",
                                              src="AFDB")])[0]
        dfi["extraction"] = {"projet": "Kamoa-Kakula", "iso3": "COD",
                             "secteur": "mines", "phase": "EPC_AWARDED",
                             "acteurs": ["ivanhoe mines"], "montant_musd": 3000,
                             "confiance": 90}
        presse = {"titre": "Kamoa Kakula expansion", "date": "2026-06-01",
                  "lien": "https://www.mining.com/k9", "resume": "",
                  "extraction": {"projet": "Kamoa Kakula", "iso3": "COD",
                                 "secteur": "mines", "phase": "CONSTRUCTION",
                                 "acteurs": ["zijin"], "montant_musd": 0,
                                 "confiance": 85}}
        cands = dp.regrouper([dfi, presse], registre=[])
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["nb_signaux"], 2)
        self.assertEqual(cands[0]["nb_sources"], 2)
        self.assertTrue(cands[0]["sources_officielles"])


class TestBackfill(unittest.TestCase):
    """P9 : explorer 12 a 24 mois d'archive."""

    def test_decoupage_trimestriel(self):
        f = dp.fenetres_backfill(mois=12, aujourd=datetime.date(2026, 8, 22))
        self.assertEqual(len(f), 4)                 # 12 mois / 3

    def test_couvre_la_periode_demandee(self):
        f = dp.fenetres_backfill(mois=24, aujourd=datetime.date(2026, 8, 22))
        plus_ancien = min(d for d, _ in f)
        self.assertLess(plus_ancien, "2024-09-01")

    def test_fenetres_contigues_et_decroissantes(self):
        f = dp.fenetres_backfill(mois=12, aujourd=datetime.date(2026, 8, 22))
        for i in range(len(f) - 1):
            self.assertEqual(f[i][0], f[i + 1][1])   # debut = fin de la suivante

    def test_url_porte_les_bornes_de_date(self):
        pays = pref.pays_par_iso3("TZA")
        _, url = dp.urls_du_pays(pays, fenetre=("2025-01-01", "2025-04-01"))[0]
        self.assertIn("after:2025-01-01", url.replace("%3A", ":"))
        self.assertIn("before:2025-04-01", url.replace("%3A", ":"))

    def test_sans_fenetre_pas_de_bornes(self):
        pays = pref.pays_par_iso3("TZA")
        _, url = dp.urls_du_pays(pays)[0]
        self.assertNotIn("after", url)

    def test_fraicheur_ignoree_en_backfill(self):
        vieux = {"titre": "Projet ancien", "lien": "http://x/1",
                 "date": "Mon, 01 Feb 2025 10:00:00 +0000"}
        auj = datetime.datetime(2026, 8, 22, tzinfo=datetime.timezone.utc)
        self.assertEqual(dp.preparer([vieux], aujourd=auj, backfill=False), [])
        self.assertEqual(len(dp.preparer([vieux], aujourd=auj, backfill=True)), 1)

    def test_collecte_multiplie_les_requetes_par_fenetre(self):
        dp.PAUSE = 0.0
        appels = []

        def fetch(url):
            appels.append(url)
            return "<rss><channel></channel></rss>"

        pays = [pref.pays_par_iso3("MOZ")]          # 2 langues
        dp.collecter_referentiel(pays, fetch=fetch,
                                 fenetres=[("2025-01-01", "2025-04-01"),
                                           ("2024-10-01", "2025-01-01")])
        self.assertEqual(len(appels), 4)            # 2 langues x 2 fenetres


if __name__ == "__main__":
    unittest.main()
