# -*- coding: utf-8 -*-
"""
LOT 3 -- CANDIDATS SANS NOM (P4), ACTOR DISCOVERY (P6), BATTERIE (P10).
===============================================================================

La batterie (TestBatterieSixProjets) est le test de recette demande : six
projets passes de bout en bout, avec pour chacun date de premiere detection,
date de promotion, nombre de sources, nombre de signaux, entreprises
detectees, score projet et score Amarante.

L'extraction LLM est FOURNIE (comme dans tous les tests du parc) : on teste le
raisonnement, pas le modele. Tout est offline.
"""

import datetime
import unittest

import acteurs_reference as aref
import decouverte_projets as dp
import projets as pj


AUJ = datetime.date(2026, 8, 22)


def sig(titre, date, lien, projet, iso3, secteur, phase="", acteurs=(),
        montant=0, conf=85, loc=""):
    return {"titre": titre, "date": date, "lien": lien, "resume": "",
            "extraction": {"projet": projet, "iso3": iso3, "secteur": secteur,
                           "phase": phase, "acteurs": list(acteurs),
                           "montant_musd": montant, "confiance": conf,
                           "localisation": loc}}


# ===========================================================================
# P4 -- PROJECT CANDIDATE SANS NOM
# ===========================================================================
class TestCandidatSansNom(unittest.TestCase):

    def _anonymes(self):
        return [
            sig("Government approves gas processing hub in Lindi region",
                "2026-02-10", "https://www.reuters.com/a1", "", "TZA", "energie",
                "POLITICAL_ANNOUNCEMENT", ["shell"], 8000, 80, loc="Lindi"),
            sig("Feasibility work starts on Lindi gas facility", "2026-04-02",
                "https://www.thecitizen.co.tz/a2", "", "TZA", "energie",
                "FEASIBILITY", ["shell"], 0, 78, loc="Lindi"),
        ]

    def test_candidat_cree_sans_nom_officiel(self):
        c = dp.regrouper(self._anonymes(), registre=[])
        self.assertEqual(len(c), 1)
        self.assertTrue(c[0]["sans_nom"])
        self.assertEqual(c[0]["nb_signaux"], 2)

    def test_identifiant_temporaire_stable(self):
        a = dp.regrouper(self._anonymes(), registre=[])[0]
        b = dp.regrouper(self._anonymes(), registre=[])[0]
        self.assertTrue(a["id_temporaire"].startswith("TMP-TZA-"))
        self.assertEqual(a["id_temporaire"], b["id_temporaire"])

    def test_libelle_lisible(self):
        c = dp.regrouper(self._anonymes(), registre=[])[0]
        self.assertIn("[sans nom]", c["nom"])
        self.assertIn("Lindi", c["nom"])

    def test_recoit_de_nouveaux_signaux(self):
        """Le candidat doit rester vivant d'un run a l'autre."""
        signaux = self._anonymes() + [
            sig("Contractors shortlisted for Lindi gas facility", "2026-06-15",
                "https://www.upstreamonline.com/a3", "", "TZA", "energie",
                "EPC_PROCUREMENT", ["saipem"], 0, 82, loc="Lindi")]
        c = dp.regrouper(signaux, registre=[])[0]
        self.assertEqual(c["nb_signaux"], 3)
        self.assertEqual(c["phase"], "EPC_PROCUREMENT")   # phase la plus recente

    def test_jamais_promu_sans_nom(self):
        """Un PROJECT_ID stable suppose un nom stable."""
        c = dp.regrouper(self._anonymes(), registre=[])[0]
        self.assertFalse(dp.promouvable(c))
        promus, attente = dp.promouvoir([c], registre=[])
        self.assertEqual(promus, [])
        self.assertEqual(len(attente), 1)

    def test_faisceau_trop_maigre_rejete(self):
        """Sans localisation, ni acteur, ni montant : aucun candidat fantome."""
        maigre = [sig("Energy sector reforms discussed", "2026-05-01",
                      "https://www.example.com/x", "", "TZA", "energie")]
        self.assertEqual(dp.regrouper(maigre, registre=[]), [])

    def test_pays_ou_secteur_manquant_rejete(self):
        self.assertEqual(dp.empreinte_sans_nom(
            {"iso3": "", "secteur": "energie", "localisation": "Lindi"}), "")
        self.assertEqual(dp.empreinte_sans_nom(
            {"iso3": "TZA", "secteur": "", "localisation": "Lindi"}), "")

    def test_deux_localisations_differentes_ne_se_melangent_pas(self):
        signaux = self._anonymes() + [
            sig("Gas hub studied in Mtwara", "2026-05-01",
                "https://www.reuters.com/b1", "", "TZA", "energie",
                "FEASIBILITY", [], 0, 75, loc="Mtwara")]
        c = dp.regrouper(signaux, registre=[])
        self.assertEqual(len(c), 2)

    def test_ordre_de_grandeur(self):
        self.assertEqual(dp.ordre_de_grandeur(50), "<100M")
        self.assertEqual(dp.ordre_de_grandeur(500), "100M-1Md")
        self.assertEqual(dp.ordre_de_grandeur(42000), ">10Md")
        self.assertEqual(dp.ordre_de_grandeur(0), "")


class TestFusionQuandLeNomApparait(unittest.TestCase):
    """P4 : le candidat temporaire disparait dans le projet nomme."""

    def _mixte(self):
        anonymes = [
            sig("Government approves gas hub in Lindi region", "2026-02-10",
                "https://www.reuters.com/a1", "", "TZA", "energie",
                "POLITICAL_ANNOUNCEMENT", ["shell"], 8000, 80, loc="Lindi"),
            sig("Feasibility work starts on Lindi gas facility", "2026-04-02",
                "https://www.thecitizen.co.tz/a2", "", "TZA", "energie",
                "FEASIBILITY", ["shell"], 0, 78, loc="Lindi"),
        ]
        nomme = [
            sig("Project officially named Lindi Gas Hub", "2026-07-01",
                "https://www.upstreamonline.com/a4", "Lindi Gas Hub", "TZA",
                "energie", "FEED", ["shell"], 8000, 90, loc="Lindi"),
        ]
        return anonymes + nomme

    def test_absorption_dans_le_projet_nomme(self):
        c = dp.regrouper(self._mixte(), registre=[])
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["nom"], "Lindi Gas Hub")
        self.assertFalse(c[0].get("sans_nom"))

    def test_historique_conserve_a_la_fusion(self):
        c = dp.regrouper(self._mixte(), registre=[])[0]
        self.assertEqual(c["nb_signaux"], 3)
        # La premiere detection remonte au signal ANONYME, pas au nommage :
        # c'est tout l'interet du candidat temporaire.
        self.assertEqual(c["premiere_detection"], "2026-02-10")
        self.assertEqual(c["absorbe_temporaires"], 1)

    def test_devient_promouvable_apres_nommage(self):
        c = dp.regrouper(self._mixte(), registre=[])[0]
        self.assertTrue(dp.promouvable(c), (c["confiance"], c["poids_sources"]))

    def test_pas_d_absorption_sans_trait_commun(self):
        anon = sig("Mining hub planned in Kolwezi", "2026-03-01",
                   "https://www.reuters.com/c1", "", "COD", "mines",
                   "FEASIBILITY", ["glencore"], 900, 80, loc="Kolwezi")
        autre = sig("Kipushi project advances", "2026-05-01",
                    "https://www.mining.com/c2", "Kipushi", "COD", "mines",
                    "CONSTRUCTION", ["ivanhoe mines"], 0, 85, loc="Kipushi")
        c = dp.regrouper([anon, autre], registre=[])
        self.assertEqual(len(c), 2)


# ===========================================================================
# P6 -- ACTOR DISCOVERY
# ===========================================================================
class TestActorDiscovery(unittest.TestCase):

    def test_miniers_demandes_reconnus(self):
        """Le cas qui a motive P6 : ces trois societes etaient identifiees
        mais ne produisaient AUCUN prospect."""
        for nom in ("Ivanhoe Mines", "Zijin Mining", "Barrick"):
            a = aref.resoudre(nom)
            self.assertTrue(a["connu"], nom)
            self.assertEqual(a["role"], "minier", nom)
            self.assertTrue(a["deploie"], nom)

    def test_origine_et_secteur_identifies(self):
        a = aref.resoudre("Ivanhoe Mines")
        self.assertEqual(a["origine"], "Canada")
        self.assertEqual(a["secteur"], "mines")

    def test_alias_resolus_vers_le_nom_canonique(self):
        for alias, attendu in (("zijin", "Zijin Mining"),
                               ("barrick", "Barrick Gold"),
                               ("total", "TotalEnergies"),
                               ("salini", "Webuild")):
            self.assertEqual(aref.resoudre(alias)["nom"], attendu, alias)

    def test_forme_juridique_ignoree(self):
        self.assertEqual(aref.resoudre("Ivanhoe Mines Ltd")["nom"], "Ivanhoe Mines")

    def test_bailleurs_ne_sont_pas_des_prospects(self):
        for nom in ("World Bank", "AFD", "IFC", "MIGA", "EBRD"):
            self.assertFalse(aref.est_deployeur(nom), nom)

    def test_etats_ne_sont_pas_des_prospects(self):
        for nom in ("TPDC", "Sonangol", "SNEL"):
            self.assertFalse(aref.est_deployeur(nom), nom)

    def test_base_ouverte_raisonne_sur_un_inconnu(self):
        """Le coeur de P6 : la base n'est PAS une liste fermee."""
        a = aref.resoudre("Sahara Mining Corporation")
        self.assertFalse(a["connu"])
        self.assertTrue(a["infere"])
        self.assertEqual(a["role"], "minier")
        self.assertTrue(a["deploie"])

    def test_inference_constructeur(self):
        a = aref.resoudre("Bamako Engineering & Construction")
        self.assertEqual(a["role"], "epc")
        self.assertTrue(a["deploie"])

    def test_inference_banque_non_deployeur(self):
        a = aref.resoudre("Development Bank of Someland")
        self.assertEqual(a["role"], "bailleur")
        self.assertFalse(a["deploie"])

    def test_inconnu_non_qualifiable_reste_prudent(self):
        a = aref.resoudre("Groupe Azerty")
        self.assertEqual(a["role"], "inconnu")
        self.assertFalse(a["deploie"])

    def test_prospects_priorisent_les_acteurs_confirmes(self):
        p = aref.prospects_du_projet(
            ["Sahara Mining Corporation", "Ivanhoe Mines", "World Bank"])
        self.assertEqual([x["nom"] for x in p][0], "Ivanhoe Mines")
        self.assertEqual(len(p), 2)          # la Banque Mondiale est exclue

    def test_dedup_par_entite(self):
        p = aref.prospects_du_projet(["Ivanhoe", "Ivanhoe Mines", "ivanhoe mines ltd"])
        self.assertEqual(len(p), 1)


class TestProspectsDuSocle(unittest.TestCase):
    """Integration : le socle consomme la base d'acteurs."""

    def test_kamoa_produit_enfin_ses_prospects(self):
        signaux = [
            {"titre": "Ivanhoe Mines announces Kamoa-Kakula phase 3",
             "date": "2026-03-04", "lien": "http://a", "phase": "EPC_PROCUREMENT"},
            {"titre": "Zijin Mining backs Kamoa-Kakula smelter",
             "date": "2026-05-04", "lien": "http://b", "phase": "CONSTRUCTION"},
        ]
        registre = [{"project_id": "KAMOA_COD", "libelle": "Kamoa-Kakula",
                     "iso3": "COD", "pays": "RDC", "secteur": "mines",
                     "valeur_musd": 3000, "alias": ["kamoa-kakula", "kamoa kakula"],
                     "alias_faibles": ["kamoa"],
                     "acteurs": ["ivanhoe mines", "zijin mining"]}]
        p = pj.construire_projets(signaux, registre=registre, aujourd=AUJ)[0]
        noms = {x["entreprise"] for x in pj.prospects(p)}
        self.assertIn("Ivanhoe Mines", noms)
        self.assertIn("Zijin Mining", noms)

    def test_prospect_porte_role_et_origine(self):
        signaux = [{"titre": "Ivanhoe Mines advances Kamoa-Kakula",
                    "date": "2026-05-04", "lien": "http://a", "phase": "CONSTRUCTION"}]
        registre = [{"project_id": "KAMOA_COD", "libelle": "Kamoa-Kakula",
                     "iso3": "COD", "pays": "RDC", "secteur": "mines",
                     "alias": ["kamoa-kakula"], "acteurs": ["ivanhoe mines"]}]
        p = pj.construire_projets(signaux, registre=registre, aujourd=AUJ)[0]
        x = pj.prospects(p)[0]
        self.assertEqual(x["role"], "Exploitant minier")
        self.assertEqual(x["origine"], "Canada")
        self.assertEqual(x["qualification"], "confirmé")


# ===========================================================================
# P10 -- BATTERIE DE RECETTE : SIX PROJETS
# ===========================================================================
def _scenario(nom_projet, iso3, secteur, signaux_bruts):
    """(signaux, candidat, promotion, projet calcule) pour un scenario."""
    signaux = [sig(t, d, l, nom_projet, iso3, secteur, ph, ac, mt, cf)
               for (t, d, l, ph, ac, mt, cf) in signaux_bruts]
    cands = dp.regrouper(signaux, registre=[])
    promus, _ = dp.promouvoir(cands, registre=[])
    registre = dp.registre_enrichi(promus, registre=[])
    socle = [{"titre": s["titre"], "date": s["date"], "lien": s["lien"],
              "phase": s["extraction"]["phase"], "resume": ""} for s in signaux]
    projets = pj.construire_projets(socle, registre=registre, aujourd=AUJ)
    return cands[0], promus, (projets[0] if projets else None)


BATTERIE = {
    "Inga 3 (RDC, énergie)": ("Inga 3", "COD", "energie", [
        ("DRC government approves Grand Inga law", "2025-02-10",
         "https://www.reuters.com/i1", "POLITICAL_ANNOUNCEMENT", ["drc government"], 14000, 80),
        ("World Bank approves $250m for Inga 3", "2025-06-03",
         "https://www.worldbank.org/i2", "FUNDING_APPROVED", ["world bank"], 250, 90),
        ("AECOM selected for Inga 3 studies", "2026-04-15",
         "https://www.jeuneafrique.com/i3", "CONSULTANT_SELECTION", ["aecom"], 0, 88),
    ]),
    "Tanzania LNG (Tanzanie, énergie)": ("Tanzania LNG", "TZA", "energie", [
        ("Tanzania LNG host government agreement signed", "2023-05-22",
         "https://www.thecitizen.co.tz/t1", "GOVERNMENT_AGREEMENT",
         ["shell", "equinor", "tpdc"], 42000, 92),
        ("Tanzania LNG talks drag on", "2024-03-11",
         "https://www.bloomberg.com/t2", "", ["shell"], 0, 70),
        ("Tanzania LNG negotiation deadline set", "2024-06-04",
         "https://www.reuters.com/t3", "GOVERNMENT_AGREEMENT", ["tpdc"], 42000, 80),
    ]),
    "Kamoa-Kakula (RDC, mines)": ("Kamoa-Kakula", "COD", "mines", [
        ("Ivanhoe announces Kamoa-Kakula phase 3", "2025-03-04",
         "https://www.reuters.com/k1", "EPC_PROCUREMENT",
         ["ivanhoe mines", "zijin mining"], 3000, 88),
        ("Kamoa-Kakula smelter reaches financial close", "2025-09-18",
         "https://www.mining.com/k2", "FUNDING_APPROVED", ["ivanhoe mines"], 1200, 90),
        ("Kamoa-Kakula ramps up production", "2026-02-10",
         "https://www.afdb.org/k3", "CONSTRUCTION", ["zijin mining"], 0, 85),
    ]),
    "Loulo-Gounkoto (Mali, mines)": ("Loulo-Gounkoto", "MLI", "mines", [
        ("Barrick outlines Loulo-Gounkoto expansion", "2025-11-06",
         "https://www.miningweekly.com/m1", "FEASIBILITY", ["barrick gold"], 800, 84),
        ("Mali and Barrick sign agreement on Loulo-Gounkoto", "2026-02-18",
         "https://www.reuters.com/m2", "GOVERNMENT_AGREEMENT", ["barrick gold"], 0, 88),
        ("Loulo-Gounkoto underground works awarded", "2026-06-20",
         "https://www.mining.com/m3", "EPC_AWARDED", ["barrick gold"], 1000, 86),
    ]),
    "Stabroek (Guyana, énergie)": ("Stabroek", "GUY", "energie", [
        ("ExxonMobil approves new Stabroek development", "2025-08-12",
         "https://www.reuters.com/g1", "FID", ["exxonmobil"], 12000, 90),
        ("Stabroek block EPC contract awarded", "2026-01-15",
         "https://www.upstreamonline.com/g2", "EPC_AWARDED",
         ["exxonmobil", "saipem"], 0, 88),
        ("Guyana approves Stabroek expansion", "2026-05-09",
         "https://www.worldbank.org/g3", "CONSTRUCTION", ["exxonmobil"], 12000, 85),
    ]),
    "Route du développement (Irak, transport)": ("Development Road", "IRQ", "transport", [
        ("Iraq cabinet approves Development Road masterplan", "2025-04-10",
         "https://www.reuters.com/r1", "POLITICAL_ANNOUNCEMENT", [], 17000, 82),
        ("Turkey and Iraq sign Development Road agreement", "2025-10-02",
         "https://www.meed.com/r2", "MOU", ["daewoo"], 0, 84),
        ("Development Road first works tendered", "2026-03-22",
         "https://www.zawya.com/r3", "EPC_PROCUREMENT", ["daewoo"], 17000, 86),
    ]),
}


class TestBatterieSixProjets(unittest.TestCase):
    """P10 : les six projets doivent traverser toute la chaine."""

    def test_les_six_sont_decouverts_et_promus(self):
        for libelle, (nom, iso3, secteur, bruts) in BATTERIE.items():
            cand, promus, projet = _scenario(nom, iso3, secteur, bruts)
            self.assertTrue(promus, "{} : non promu".format(libelle))
            self.assertIsNotNone(projet, "{} : absent du socle".format(libelle))

    def test_metriques_completes_pour_chacun(self):
        for libelle, (nom, iso3, secteur, bruts) in BATTERIE.items():
            cand, promus, projet = _scenario(nom, iso3, secteur, bruts)
            self.assertGreaterEqual(cand["nb_signaux"], 3, libelle)
            self.assertGreaterEqual(cand["nb_sources"], 2, libelle)
            self.assertTrue(cand["premiere_detection"], libelle)
            self.assertGreater(projet["maturite"], 0, libelle)
            self.assertGreater(projet["opportunite"]["score"], 0, libelle)
            self.assertTrue(projet["historique"], libelle)

    def test_projets_miniers_produisent_des_prospects(self):
        for libelle in ("Kamoa-Kakula (RDC, mines)", "Loulo-Gounkoto (Mali, mines)"):
            nom, iso3, secteur, bruts = BATTERIE[libelle]
            _, _, projet = _scenario(nom, iso3, secteur, bruts)
            self.assertTrue(pj.prospects(projet),
                            "{} : aucun prospect".format(libelle))

    def test_couverture_geographique_de_la_batterie(self):
        """Afrique, Amerique latine et Moyen-Orient sont representes."""
        iso = {v[1] for v in BATTERIE.values()}
        self.assertTrue({"COD", "TZA", "MLI"} & iso)   # Afrique
        self.assertIn("GUY", iso)                      # Amerique latine
        self.assertIn("IRQ", iso)                      # Moyen-Orient


if __name__ == "__main__":
    unittest.main()
