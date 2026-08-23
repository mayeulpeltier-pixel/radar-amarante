# -*- coding: utf-8 -*-
"""
PROJECT DISCOVERY -- tests, dont la SIMULATION HISTORIQUE OBLIGATOIRE.
===============================================================================

Le test central (TestSimulationHistorique) place Inga 3 et Tanzania LNG dans la
situation qui compte : ils sont ABSENTS du registre. Le systeme doit les
DECOUVRIR a partir de signaux seuls, les promouvoir, puis les faire entrer dans
le socle existant (lifecycle / maturite / opportunite / prospects) sans qu'une
seule ligne du socle ait ete modifiee.

Tout est offline : l'extraction LLM est injectee.
"""

import datetime
import json
import unittest

import decouverte_projets as dp
import projets as pj
import projets_reference as ref
import ted_complet_v14 as ted


AUJ = datetime.date(2026, 8, 22)

# Registre VOLONTAIREMENT VIDE : les deux projets sont inconnus du radar.
REGISTRE_VIDE = []


def s(titre, date, lien, resume="", extraction=None):
    d = {"titre": titre, "date": date, "lien": lien, "resume": resume}
    if extraction is not None:
        d["extraction"] = extraction
    return d


def ext(projet, iso3, secteur, phase="", acteurs=(), montant=0, conf=85):
    return {"projet": projet, "iso3": iso3, "secteur": secteur, "phase": phase,
            "acteurs": list(acteurs), "montant_musd": montant, "confiance": conf}


# --- Chronologie ANTERIEURE a l'entree au registre -------------------------
SIGNAUX_HISTORIQUES = [
    # --- Inga 3 (RDC), vu par 3 sources differentes ---
    s("DRC government approves Grand Inga law", "2025-02-10",
      "https://www.reuters.com/a1",
      extraction=ext("Grand Inga", "COD", "energie", "POLITICAL_ANNOUNCEMENT",
                     ["drc government"], 14000, 80)),
    s("World Bank approves $250m for Inga 3", "2025-06-03",
      "https://www.jeuneafrique.com/a2",
      extraction=ext("Inga 3", "COD", "energie", "FUNDING_APPROVED",
                     ["world bank"], 250, 90)),
    s("DRC signs agreement with AFD on Inga 3", "2026-01-20",
      "https://www.afd.fr/a3",
      extraction=ext("Inga 3", "COD", "energie", "MOU", ["afd"], 0, 85)),
    s("AECOM selected for Inga studies", "2026-04-15",
      "https://www.reuters.com/a4",
      extraction=ext("Inga III", "COD", "energie", "CONSULTANT_SELECTION",
                     ["aecom"], 0, 88)),
    # --- Tanzania LNG (TZA), pays HORS perimetre de collecte actuel ---
    s("Tanzania and Shell sign host government agreement", "2023-05-22",
      "https://www.thecitizen.co.tz/b1",
      extraction=ext("Tanzania LNG", "TZA", "energie", "GOVERNMENT_AGREEMENT",
                     ["shell", "equinor", "tpdc"], 42000, 92)),
    s("Lindi LNG site preparation discussed", "2024-02-11",
      "https://www.theeastafrican.co.ke/b2",
      extraction=ext("Lindi LNG", "TZA", "energie", "FEASIBILITY",
                     ["equinor"], 0, 78)),
    s("Tanzania LNG project talks drag on", "2024-03-11",
      "https://www.bloomberg.com/b3",
      extraction=ext("Tanzania LNG", "TZA", "energie", "", ["shell"], 0, 70)),
    s("Tanzania LNG: government sets new negotiation deadline", "2024-06-04",
      "https://www.thecitizen.co.tz/b4",
      extraction=ext("Tanzania LNG", "TZA", "energie", "GOVERNMENT_AGREEMENT",
                     ["tpdc"], 42000, 80)),
    # --- Bruit : ne doit produire AUCUN candidat ---
    s("Opinion: what Africa needs from its energy debate", "2026-05-01",
      "https://www.example.com/c1", extraction=ext("", "", "", "", [], 0, 0)),
    s("Manchester United beat Arsenal", "2026-08-01",
      "https://www.bbc.com/c2", extraction=ext("", "", "", "", [], 0, 0)),
]


class TestSimulationHistorique(unittest.TestCase):
    """LE test du cahier des charges : retrouver Inga 3 et Tanzania LNG a
    partir de signaux anterieurs a leur presence au registre."""

    def setUp(self):
        self.candidats = dp.regrouper(SIGNAUX_HISTORIQUES, registre=REGISTRE_VIDE)

    def test_les_deux_projets_sont_decouverts(self):
        noms = " | ".join(c["nom"].lower() for c in self.candidats)
        self.assertIn("inga", noms)
        self.assertIn("lng", noms)

    def test_le_bruit_ne_produit_aucun_candidat(self):
        for c in self.candidats:
            self.assertNotIn("manchester", c["nom"].lower())
            self.assertTrue(c["nom"].strip())

    def test_variantes_inga_fusionnees_en_un_seul_candidat(self):
        # "Grand Inga", "Inga 3" et "Inga III" designent le MEME projet.
        inga = [c for c in self.candidats if "inga" in c["nom"].lower()]
        self.assertEqual(len(inga), 1, [c["nom"] for c in inga])
        self.assertEqual(inga[0]["nb_signaux"], 4)

    def test_variantes_tanzanie_fusionnees(self):
        tz = [c for c in self.candidats if c["nom"] == "Tanzania LNG"]
        self.assertEqual(len(tz), 1)
        self.assertEqual(tz[0]["nb_signaux"], 3)

    def test_limite_assumee_lindi_lng_reste_un_candidat_distinct(self):
        """LIMITE ASSUMEE, verrouillee pour ne pas etre oubliee.

        "Lindi LNG" et "Tanzania LNG" designent le meme projet, mais ne
        partagent AUCUN jeton distinctif ("lng" est trop commun pour prouver
        quoi que ce soit). Ils restent donc deux candidats. Les fusionner
        exigerait une regle sur les acteurs partages, qui melangerait deux
        projets d'un meme operateur dans un meme pays : pire que la lacune.
        Le candidat faible reste EN ATTENTE et la curation humaine tranche."""
        lindi = [c for c in self.candidats if c["nom"] == "Lindi LNG"]
        self.assertEqual(len(lindi), 1)
        # Un seul signal, une seule source : il n'est PAS promu tout seul.
        self.assertFalse(dp.promouvable(lindi[0]))

    def test_sources_distinctes_comptees(self):
        inga = [c for c in self.candidats if "inga" in c["nom"].lower()][0]
        self.assertGreaterEqual(inga["nb_sources"], 3)   # reuters, JA, afd

    def test_premiere_detection_est_la_plus_ancienne(self):
        inga = [c for c in self.candidats if "inga" in c["nom"].lower()][0]
        self.assertEqual(inga["premiere_detection"], "2025-02-10")

    def test_phase_la_plus_recente_retenue(self):
        inga = [c for c in self.candidats if "inga" in c["nom"].lower()][0]
        self.assertEqual(inga["phase"], "CONSULTANT_SELECTION")

    def test_acteurs_et_montant_agreges(self):
        inga = [c for c in self.candidats if "inga" in c["nom"].lower()][0]
        for a in ("world bank", "afd", "aecom"):
            self.assertIn(a, inga["acteurs_top"], a)
        self.assertEqual(inga["montant_musd"], 14000)

    def test_les_deux_sont_promouvables(self):
        promus, attente = dp.promouvoir(self.candidats, registre=REGISTRE_VIDE)
        ids = {e["project_id"] for e in promus}
        self.assertTrue(any("INGA" in i for i in ids), ids)
        self.assertTrue(any("LNG" in i for i in ids), ids)

    def test_project_id_genere_lisible_et_stable(self):
        self.assertEqual(dp.generer_project_id("Inga 3", "COD"), "INGA3_COD")
        self.assertEqual(dp.generer_project_id("Inga III", "COD"), "INGA3_COD")
        self.assertEqual(dp.generer_project_id("Tanzania LNG", "TZA"),
                         "TANZANIALNG_TZA")


class TestEntreeDansLeSocle(unittest.TestCase):
    """Le projet decouvert doit traverser le socle EXISTANT sans modification."""

    def setUp(self):
        candidats = dp.regrouper(SIGNAUX_HISTORIQUES, registre=REGISTRE_VIDE)
        self.promus, _ = dp.promouvoir(candidats, registre=REGISTRE_VIDE)
        self.registre = dp.registre_enrichi(self.promus, registre=REGISTRE_VIDE)

    def test_entree_au_format_registre(self):
        for e in self.promus:
            for champ in ("project_id", "libelle", "iso3", "secteur",
                          "alias", "alias_faibles", "acteurs", "valeur_musd"):
                self.assertIn(champ, e, champ)
        # charger_registre doit l'accepter sans broncher.
        self.assertEqual(len(ref.charger_registre(self.registre)),
                         len(self.promus))

    def test_alias_faible_uniquement_sur_jeton_distinctif(self):
        """Un alias faible auto est utile ("Inga") mais dangereux s'il est
        commun. Regle : jeton >= 4 caracteres et non generique. Le socle exige
        en plus un mot de contexte pour l'accepter."""
        par_id = {e["project_id"]: e for e in self.promus}
        inga = [e for i, e in par_id.items() if "INGA" in i][0]
        self.assertEqual(inga["alias_faibles"], ["inga"])
        # "Tanzania LNG" n'a que des jetons trop communs : aucun alias faible.
        tz = [e for i, e in par_id.items() if "LNG" in i]
        if tz:
            self.assertEqual(tz[0]["alias_faibles"], [])

    def test_pas_d_alias_faible_sur_mots_communs(self):
        for nom in ("Tanzania LNG", "Solar Park", "Port Development",
                    "Data Center"):
            self.assertEqual(dp.alias_faible_auto(nom), [], nom)

    def test_lifecycle_complet_sur_projet_decouvert(self):
        # On rejoue les MEMES signaux dans le socle, avec le registre enrichi.
        signaux = [{"titre": x["titre"], "date": x["date"], "lien": x["lien"],
                    "resume": x.get("resume", ""),
                    "phase": (x.get("extraction") or {}).get("phase", "")}
                   for x in SIGNAUX_HISTORIQUES]
        ps = pj.construire_projets(signaux, registre=self.registre, aujourd=AUJ)
        self.assertTrue(ps)
        inga = [p for p in ps if "INGA" in p["project_id"]]
        self.assertTrue(inga, [p["project_id"] for p in ps])
        p = inga[0]
        self.assertEqual(p["phase_courante"], "CONSULTANT_SELECTION")
        self.assertGreater(p["maturite"], 0)
        self.assertGreater(p["opportunite"]["score"], 0)
        self.assertTrue(p["historique"])
        self.assertTrue(pj.timeline(p))

    def test_prospects_produits_depuis_un_projet_decouvert(self):
        signaux = [{"titre": x["titre"], "date": x["date"], "lien": x["lien"],
                    "resume": x.get("resume", ""),
                    "phase": (x.get("extraction") or {}).get("phase", "")}
                   for x in SIGNAUX_HISTORIQUES]
        ps = pj.construire_projets(signaux, registre=self.registre, aujourd=AUJ)
        tous = [x["entreprise"] for p in ps for x in pj.prospects(p)]
        self.assertTrue(tous)   # au moins un contractor international


class TestDedupContreRegistreExistant(unittest.TestCase):
    """Un signal d'un projet DEJA suivi n'est pas une decouverte."""

    def test_projet_connu_ecarte(self):
        # Registre reel : Inga 3 y figure -> aucun candidat Inga.
        cands = dp.regrouper(SIGNAUX_HISTORIQUES)
        self.assertFalse([c for c in cands if "inga" in c["nom"].lower()],
                         [c["nom"] for c in cands])

    def test_deja_connu_repond_sur_le_registre_reel(self):
        self.assertEqual(dp.deja_connu("Inga 3"), "INGA3_COD")
        self.assertEqual(dp.deja_connu("Tanzania LNG"), "TANZLNG_TZA")
        self.assertEqual(dp.deja_connu("Projet totalement inedit du Tchad"), "")

    def test_promotion_refuse_un_doublon_du_registre(self):
        faux = [{"nom": "Inga 3", "iso3": "COD", "secteur": "energie",
                 "confiance": 99, "nb_signaux": 9, "nb_sources": 9}]
        promus, attente = dp.promouvoir(faux)
        self.assertEqual(promus, [])
        self.assertEqual(len(attente), 1)


class TestClesEtFusion(unittest.TestCase):

    def test_chiffres_romains_normalises(self):
        self.assertEqual(dp.jetons_projet("Inga III"), dp.jetons_projet("Inga 3"))

    def test_mots_generiques_retires(self):
        self.assertEqual(dp.jetons_projet("the Inga 3 project"),
                         dp.jetons_projet("Inga 3"))

    def test_cle_inclut_le_pays(self):
        self.assertNotEqual(dp.cle_projet("Solar Park", "MLI"),
                            dp.cle_projet("Solar Park", "NER"))

    def test_pas_de_fusion_sur_un_jeton_non_distinctif(self):
        # Deux projets tanzaniens differents partagent "tanzania" et "port" :
        # ce n'est PAS une preuve, ils doivent rester distincts.
        a = {"nom": "Tanzania Port Alpha", "iso3": "TZA", "secteur": "transport",
             "signaux": [], "sources": [], "acteurs_top": []}
        b = {"nom": "Tanzania Port Beta", "iso3": "TZA", "secteur": "transport",
             "signaux": [], "sources": [], "acteurs_top": []}
        self.assertEqual(len(dp.fusionner([a, b])), 2)

    def test_pas_de_fusion_entre_pays_differents(self):
        a = {"nom": "Inga 3", "iso3": "COD", "secteur": "energie",
             "signaux": [], "sources": [], "acteurs_top": []}
        b = {"nom": "Inga Sud", "iso3": "AGO", "secteur": "energie",
             "signaux": [], "sources": [], "acteurs_top": []}
        self.assertEqual(len(dp.fusionner([a, b])), 2)


class TestConfianceEtPromotion(unittest.TestCase):

    def _cand(self, **kw):
        base = {"nom": "Projet Test", "iso3": "MLI", "secteur": "mines",
                "nb_signaux": 4, "nb_sources": 3, "confiance_llm": 85,
                "phase": "FEASIBILITY", "acteurs_top": ["x"], "montant_musd": 900,
                "poids_sources": 1.6, "meilleure_fiabilite": 0.65,
                "sources_officielles": []}
        base.update(kw)
        base["confiance"] = dp.score_confiance(base)
        return base

    def test_candidat_solide_est_promouvable(self):
        self.assertTrue(dp.promouvable(self._cand()))

    def test_voie_officielle_une_seule_source_suffit(self):
        """P5 : une annonce de la Banque Mondiale n'a pas besoin d'etre reprise
        par deux blogs pour etre vraie."""
        officiel = self._cand(nb_signaux=1, nb_sources=1, poids_sources=0.95,
                              sources_officielles=["Banque Mondiale"])
        self.assertTrue(dp.promouvable(officiel))

    def test_deux_agregateurs_ne_suffisent_pas(self):
        """L'inverse : deux sources faibles ne font pas une preuve."""
        faible = self._cand(nb_signaux=3, nb_sources=2, poids_sources=0.80,
                            meilleure_fiabilite=0.40, sources_officielles=[])
        self.assertFalse(dp.promouvable(faible))

    def test_qualite_prime_sur_quantite_dans_la_decision(self):
        """Le point de P5 n'est pas le score brut mais la DECISION : un signal
        officiel unique est promu, tandis qu'un candidat plus bavard mais
        adosse a des sources faibles reste en attente."""
        officiel = self._cand(nb_signaux=1, nb_sources=1, poids_sources=0.95,
                              sources_officielles=["AfDB"])
        bavard = self._cand(nb_signaux=6, nb_sources=2, poids_sources=0.80,
                            meilleure_fiabilite=0.40)
        self.assertTrue(dp.promouvable(officiel))
        self.assertFalse(dp.promouvable(bavard))

    def test_signal_unique_refuse(self):
        self.assertFalse(dp.promouvable(self._cand(nb_signaux=1, nb_sources=1)))

    def test_source_unique_refusee(self):
        # Une seule source = une seule reprise de depeche : pas une preuve.
        self.assertFalse(dp.promouvable(self._cand(nb_sources=1)))

    def test_sans_pays_jamais_promu(self):
        self.assertFalse(dp.promouvable(self._cand(iso3="")))

    def test_confiance_llm_faible_fait_chuter_le_score(self):
        fort = self._cand(confiance_llm=95)["confiance"]
        faible = self._cand(confiance_llm=10)["confiance"]
        self.assertGreater(fort, faible)

    def test_motifs_explicables(self):
        m = dp.motifs_confiance(self._cand())
        self.assertTrue(any("signal" in x for x in m))
        self.assertTrue(any("source" in x for x in m))


class TestExtractionLLM(unittest.TestCase):

    def setUp(self):
        dp.PAUSE = 0.0
        ted.STATS_LLM["arret"] = ""

    def tearDown(self):
        ted.STATS_LLM["arret"] = ""

    def test_prompt_interdit_l_invention(self):
        p = dp.construire_prompt([{"titre": "x"}])
        self.assertIn("N'INVENTE JAMAIS", p)
        self.assertIn("FUNDING_APPROVED", p)

    def test_parsing_nominal(self):
        rep = json.dumps([{"n": 1, "projet": "Simandou", "iso3": "GIN",
                           "secteur": "mines", "phase": "EPC_AWARDED",
                           "acteurs": ["Rio Tinto"], "montant_musd": 20000,
                           "confiance": 90}])
        out = dp.parser_reponse(rep, 1)[0]
        self.assertEqual(out["projet"], "Simandou")
        self.assertEqual(out["secteur"], "mines")
        self.assertEqual(out["acteurs"], ["rio tinto"])

    def test_secteur_et_phase_invalides_neutralises(self):
        rep = json.dumps([{"n": 1, "projet": "X", "secteur": "licorne",
                           "phase": "IMAGINAIRE", "confiance": 50}])
        out = dp.parser_reponse(rep, 1)[0]
        self.assertEqual(out["secteur"], "infrastructure")
        self.assertEqual(out["phase"], "")

    def test_json_casse_ne_perd_pas_le_lot(self):
        out = dp.parser_reponse("pas du json", 3)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(o["projet"] == "" for o in out))

    def test_confiance_bornee(self):
        rep = json.dumps([{"n": 1, "projet": "X", "confiance": 5000}])
        self.assertEqual(dp.parser_reponse(rep, 1)[0]["confiance"], 100)

    def test_disjoncteur_interrompt(self):
        ted.STATS_LLM["arret"] = "credit balance too low"
        _, lots = dp.extraire_par_lots([{"titre": "x"}] * 20, appel=lambda p: "[]")
        self.assertEqual(lots, 0)

    def test_plafond_de_lots(self):
        dp.TAILLE_LOT = 10
        _, lots = dp.extraire_par_lots([{"titre": "x"}] * 100,
                                       appel=lambda p: "[]", max_lots=2)
        self.assertEqual(lots, 2)


class TestPreFiltresEtCollecte(unittest.TestCase):

    def test_dedup_contre_memoire(self):
        a = {"titre": "Projet X", "lien": "http://m/1",
             "date": "Mon, 18 Aug 2026 10:00:00 +0000"}
        deja = [__import__("bitd_signaux").id_article("http://m/1")]
        self.assertEqual(dp.preparer([a], vus=deja), [])

    def test_article_ancien_ecarte(self):
        vieux = {"titre": "Projet X", "lien": "http://m/2",
                 "date": "Mon, 01 Jan 2020 10:00:00 +0000"}
        aujourd = datetime.datetime(2026, 8, 22, tzinfo=datetime.timezone.utc)
        self.assertEqual(dp.preparer([vieux], aujourd=aujourd), [])

    def test_rotation_pays_circulaire(self):
        f = dp.pays_du_run(0, 3)
        self.assertEqual(len(f), 3)
        self.assertEqual(dp.pays_du_run(len(dp.PAYS_DECOUVERTE), 2), dp.pays_du_run(0, 2))

    def test_tanzanie_est_balayee_malgre_le_perimetre(self):
        # Point clef : TZA est hors PAYS_COUVERTS_AMARANTE, mais la decouverte
        # doit quand meme voir naitre un projet tanzanien.
        self.assertIn("TZA", [i for _, i, _ in dp.PAYS_DECOUVERTE])
        self.assertFalse(ted.dans_le_perimetre("TZA"))

    def test_collecte_tolere_une_requete_en_erreur(self):
        dp.PAUSE = 0.0

        def fetch(url):
            raise RuntimeError("403")

        self.assertEqual(dp.collecter([("Mali", "MLI", "fr")], fetch=fetch), [])


class TestSortie(unittest.TestCase):

    def test_ligne_respecte_les_colonnes(self):
        c = dp.regrouper(SIGNAUX_HISTORIQUES, registre=REGISTRE_VIDE)[0]
        ligne = dp.ligne_candidat(c)
        self.assertEqual(len(ligne), len(dp.COLONNES))
        self.assertEqual(ligne[dp.COLONNES.index("statut")], "candidat")

    def test_statut_promu(self):
        c = dp.regrouper(SIGNAUX_HISTORIQUES, registre=REGISTRE_VIDE)[0]
        self.assertEqual(dp.ligne_candidat(c, promu=True)[dp.COLONNES.index("statut")],
                         "promu")

    def test_signaux_serialises_en_json(self):
        c = dp.regrouper(SIGNAUX_HISTORIQUES, registre=REGISTRE_VIDE)[0]
        ligne = dp.ligne_candidat(c)
        self.assertIsInstance(
            json.loads(ligne[dp.COLONNES.index("signaux_json")]), list)


class TestGardeFouActivation(unittest.TestCase):

    def test_desactive_par_defaut(self):
        import os
        self.assertFalse(dp.ACTIVER
                         or os.environ.get("RADAR_DECOUVERTE_PROJETS") == "1")


if __name__ == "__main__":
    unittest.main()
