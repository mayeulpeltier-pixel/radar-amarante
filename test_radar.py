# -*- coding: utf-8 -*-
"""
Radar Amarante -- Tests unitaires cibles
=========================================

Filet de securite pour l'automatisation. Ces tests verifient les fonctions
PURES (sans reseau ni cle API) : celles qu'on regle a la main a chaque
modification de filtre. Les figer ici permet a GitHub Actions de BLOQUER un
deploiement si une modif casse une regle metier.

Choix volontaire : bibliotheque standard `unittest`, AUCUNE dependance a
installer (pas de pytest). Lancement :
    python -m unittest test_radar -v
    (ou simplement : python test_radar.py)

Pre-requis : ted_complet_v14.py et ted_complet_bm.py dans le meme dossier.
"""

import os
import unittest
from datetime import date

import ted_complet_v14 as ted
import ted_complet_bm as bm

# Modules testes plus bas, importes de facon RESILIENTE : si l'un manque au
# moment des tests, ses tests sont ignores (skip), jamais en echec. Cela evite
# de casser les tests TED/BM existants si un fichier n'est pas encore deploye.
try:
    import ted_complet_attributions as attributions
except Exception:
    attributions = None
try:
    import bitd_signaux as bitd
    import signaux_prives
except Exception:
    bitd = signaux_prives = None
try:
    import ted_complet_v14 as ted
except Exception:
    ted = None
try:
    import radar_etat
except Exception:
    radar_etat = None
try:
    import radar_retroaction
except Exception:
    radar_retroaction = None
try:
    import enrichir_entreprises
except Exception:
    enrichir_entreprises = None
try:
    import radar_risque
except Exception:
    radar_risque = None
try:
    import radar_dashboard
except Exception:
    radar_dashboard = None
try:
    import radar_digest
except Exception:
    radar_digest = None
try:
    import bm_attributions
except Exception:
    bm_attributions = None


# ===========================================================================
# 1. PARSING DE DATE (robustesse au format Banque Mondiale)
# ===========================================================================
class TestParsingDate(unittest.TestCase):

    def test_format_bm_standard(self):
        self.assertEqual(bm._date_notice({"noticedate": "28-Oct-2025"}),
                         date(2025, 10, 28))

    def test_format_iso(self):
        self.assertEqual(bm._date_notice({"noticedate": "2025-10-28"}),
                         date(2025, 10, 28))

    def test_format_illisible_renvoie_none(self):
        # Ne doit pas planter, juste renvoyer None (avis ignore proprement).
        self.assertIsNone(bm._date_notice({"noticedate": "pas-une-date"}))

    def test_date_absente_renvoie_none(self):
        self.assertIsNone(bm._date_notice({"noticedate": ""}))
        self.assertIsNone(bm._date_notice({}))


# ===========================================================================
# 2. MAPPING PAYS (le piege des sous-chaines, deja corrige)
# ===========================================================================
class TestMappingPays(unittest.TestCase):

    def test_romania_n_est_pas_oman(self):
        # "oman" est contenu dans "romania" : un repli par sous-chaine
        # classerait Romania en Oman (orange). Doit rester non mappe.
        self.assertEqual(bm.code_iso3_pays("romania"), "")

    def test_nom_avec_virgule(self):
        # "Egypt, Arab Republic of" -> partie avant la virgule -> EGY.
        self.assertEqual(bm.code_iso3_pays("Egypt, Arab Republic of"), "EGY")

    def test_pays_simple(self):
        self.assertEqual(bm.code_iso3_pays("mali"), "MLI")

    def test_pays_inconnu(self):
        self.assertEqual(bm.code_iso3_pays("Pays Imaginaire"), "")


# ===========================================================================
# 3. PRE-FILTRE cible_amarante (risque pays + exclusions + override surete)
# ===========================================================================
def _rec(titre, pays="Mali", groupe="CS"):
    return {"bid_description": titre, "project_name": "",
            "project_ctry_name": pays, "procurement_group": groupe,
            "notice_type": "Request for Expression of Interest",
            "notice_status": "Published"}


class TestPreFiltre(unittest.TestCase):

    def test_lead_zone_risque_garde(self):
        self.assertTrue(bm.cible_amarante(
            _rec("Controle et surveillance des travaux multi-sites", "Niger")))

    def test_pays_sous_seuil_exclu(self):
        # Tunisie = tier 0.3 < TIER_RISQUE_MINIMAL (0.6) -> exclu d'office.
        self.assertFalse(bm.cible_amarante(
            _rec("Controle et surveillance des travaux", "Tunisia")))

    def test_exclusion_ecoles_pluriel(self):
        # "ecole" (racine) doit attraper "ecoles".
        self.assertFalse(bm.cible_amarante(
            _rec("Construction de cinq ecoles primaires", "Mali")))

    def test_override_surete_garde_malgre_audit(self):
        # "audit" est exclu, mais "physical security" force la conservation.
        self.assertTrue(bm.cible_amarante(
            _rec("Physical security audit for remote sites", "Mali")))

    def test_safeguarding_exclu(self):
        # racine "safeguard" attrape "safeguarding".
        self.assertFalse(bm.cible_amarante(
            _rec("Social safeguarding officer", "Mali")))

    def test_preschool_pas_confondu_avec_school(self):
        # Borne de DEBUT : "school" ne matche pas dans "preschool".
        self.assertTrue(bm.cible_amarante(
            _rec("Construction of preschool sport facility", "Mali")))

    def test_override_guarding_pas_safeguarding(self):
        # "guarding" (override) ne doit PAS se declencher sur "safeguarding".
        # Ici safeguarding seul -> exclu (pas d'override parasite).
        self.assertFalse(bm.cible_amarante(
            _rec("Environmental safeguarding specialist", "Niger")))


# ===========================================================================
# 4. PONT DE SCORING avis_pour_scoring (Fix 4 : humain avant beton)
# ===========================================================================
class TestPontScoring(unittest.TestCase):

    def _avis_cw(self):
        return {"procurement_group": "CW", "pays_iso3": "NER",
                "pays_execution": "Niger", "cpv": ""}

    def test_bonus_infra_si_expert_international(self):
        copie = bm.avis_pour_scoring(
            self._avis_cw(), {"profil_personnes_exposees": "expert_international"})
        self.assertEqual(copie["cpv"], "45000000")

    def test_pas_de_bonus_si_ouvrier_local(self):
        copie = bm.avis_pour_scoring(
            self._avis_cw(), {"profil_personnes_exposees": "ouvrier_local"})
        self.assertEqual(copie.get("cpv", ""), "")

    def test_pas_de_bonus_sans_extraction(self):
        copie = bm.avis_pour_scoring(self._avis_cw(), None)
        self.assertEqual(copie.get("cpv", ""), "")

    def test_pays_execution_devient_iso3(self):
        # Pour le scoring, pays_execution doit etre l'ISO3 (multiplicateur).
        copie = bm.avis_pour_scoring(
            self._avis_cw(), {"profil_personnes_exposees": "expert_international"})
        self.assertEqual(copie["pays_execution"], "NER")


# ===========================================================================
# 5. CIBLE COMMERCIALE (qui demarcher reellement)
# ===========================================================================
class TestCibleCommerciale(unittest.TestCase):

    def test_travaux_pointe_titulaire_btp(self):
        texte = bm.cible_commerciale({"procurement_group": "CW"}, {}).lower()
        self.assertIn("travaux", texte)
        self.assertIn("pas l'agence", texte)

    def test_conseil_pointe_consortium(self):
        texte = bm.cible_commerciale({"procurement_group": "CS"}, {}).lower()
        self.assertIn("conseil", texte)


# ===========================================================================
# 6. FILTRE DE PERTINENCE avis_correspond_bm
# ===========================================================================
class TestCorrespondBM(unittest.TestCase):

    def test_publie_cs_valide(self):
        self.assertTrue(bm.avis_correspond_bm(_rec("x", "Mali", "CS")))

    def test_non_publie_rejete(self):
        rec = _rec("x", "Mali", "CS")
        rec["notice_status"] = "Cancelled"
        self.assertFalse(bm.avis_correspond_bm(rec))

    def test_groupe_goods_rejete(self):
        # GO (fournitures) hors perimetre : seuls CS et CW.
        self.assertFalse(bm.avis_correspond_bm(_rec("x", "Mali", "GO")))


# ===========================================================================
# 7. COEUR TED : action recommandee (coupe-circuits doctrine)
# ===========================================================================
class TestActionRecommandee(unittest.TestCase):

    def test_securite_existante_ignore(self):
        action = ted.calculer_action_recommandee(
            9.0, {"securite_existante_detectee": True,
                  "accessibilite_commerciale": "facile"}, surete=9.0)
        self.assertEqual(action, "ignorer")

    def test_score_fort_accessible_contacter(self):
        action = ted.calculer_action_recommandee(
            7.0, {"securite_existante_detectee": False,
                  "accessibilite_commerciale": "facile"}, surete=7.0)
        self.assertEqual(action, "contacter")

    def test_marche_difficile_surveiller(self):
        # Score fort mais marche difficile -> on ne dit pas "contacter".
        action = ted.calculer_action_recommandee(
            7.0, {"securite_existante_detectee": False,
                  "accessibilite_commerciale": "difficile"}, surete=7.0)
        self.assertEqual(action, "surveiller")

    def test_extraction_absente_ignore(self):
        self.assertEqual(
            ted.calculer_action_recommandee(8.0, None), "ignorer")


# ===========================================================================
# 8. MEMOIRE INTER-RUNS : ne pas reanalyser un avis deja vu
# ===========================================================================
class TestMemoireInterRuns(unittest.TestCase):

    def test_extraction_positionnelle_publications(self):
        # Grille brute facon get_all_values : en-tete + 2 lignes de donnees.
        idx = bm.COLONNES_BM.index("publication_number")
        entete = list(bm.COLONNES_BM)
        l1 = [""] * len(bm.COLONNES_BM); l1[idx] = "OP00448833"
        l2 = [""] * len(bm.COLONNES_BM); l2[idx] = "OP00453048"
        nums = ted._publications_depuis_valeurs([entete, l1, l2], bm.COLONNES_BM)
        self.assertEqual(nums, {"OP00448833", "OP00453048"})

    def test_grille_vide(self):
        self.assertEqual(ted._publications_depuis_valeurs([], bm.COLONNES_BM), set())

    def test_sans_entete(self):
        # Pas de ligne d'en-tete : toutes les lignes sont des donnees.
        idx = bm.COLONNES_BM.index("publication_number")
        l1 = [""] * len(bm.COLONNES_BM); l1[idx] = "OP1"
        nums = ted._publications_depuis_valeurs([l1], bm.COLONNES_BM)
        self.assertEqual(nums, {"OP1"})

    def test_filtre_ne_garde_que_les_nouveaux(self):
        # Simule le filtre applique dans main() : avis dont le numero est deja
        # connu sont retires.
        deja_vus = {"OP1", "OP2"}
        avis = [{"publication_number": "OP1"}, {"publication_number": "OP3"},
                {"publication_number": "OP2"}, {"publication_number": "OP4"}]
        nouveaux = [a for a in avis
                    if str(a.get("publication_number", "")).strip() not in deja_vus]
        self.assertEqual([a["publication_number"] for a in nouveaux], ["OP3", "OP4"])


@unittest.skipIf(attributions is None, "ted_complet_attributions indisponible")
class TestAttributionsPDF(unittest.TestCase):
    """Parsing des gagnants depuis le texte d'un PDF/HTML d'attribution TED.
    C'est le point le plus fragile (formats variables) : on couvre les DEUX
    schemas TED valides sur notices reelles."""

    def test_eforms_extrait_nom_et_valeur(self):
        texte = ("Results Information about winners Official name : Badenelektra GmbH "
                 "Postal address : Hauptstrasse 4 Value of the tender : 1 200 000 EUR "
                 "Notice information")
        r = attributions.parser_gagnants(texte)
        self.assertEqual(len(r["gagnants"]), 1)
        self.assertEqual(r["gagnants"][0]["nom"], "Badenelektra GmbH")
        self.assertEqual(r["gagnants"][0]["valeur"], "1 200 000 EUR")

    def test_ancien_format_f03(self):
        texte = ("Section V Name and address of the contractor Official name : PROATEC SRL "
                 "Postal address : Via Roma Town : Milano")
        r = attributions.parser_gagnants(texte)
        self.assertEqual([g["nom"] for g in r["gagnants"]], ["PROATEC SRL"])

    def test_nom_coupe_au_libelle_suivant(self):
        # Le nom doit s'arreter au prochain "Libelle :", pas engloutir l'adresse.
        bloc = "Official name : ACME Consulting Ltd Postal address : 10 Downing Street"
        self.assertEqual(attributions._nom_apres_official(bloc), "ACME Consulting Ltd")

    def test_nom_group_non_coupe(self):
        # "Group"/"Value" sans deux-points ne doivent PAS couper le nom.
        bloc = "Official name : NIRAS GROUP (UK) LTD Country : UK"
        self.assertEqual(attributions._nom_apres_official(bloc), "NIRAS GROUP (UK) LTD")

    def test_dedup_gagnants(self):
        texte = ("Information about winners Official name : ACME LTD Value of the tender : 5 EUR 8. "
                 "Information about winners Official name : ACME LTD Value of the tender : 5 EUR 8.")
        r = attributions.parser_gagnants(texte)
        self.assertEqual(len(r["gagnants"]), 1)

    def test_total_et_sous_traitance(self):
        texte = ("Information about winners Official name : X Ltd Value of the tender : 10 EUR 8. "
                 "Value of all contracts awarded in this notice : 2 500 000 EUR "
                 "Subcontracting : yes")
        r = attributions.parser_gagnants(texte)
        self.assertEqual(r["total"], "2 500 000 EUR")
        self.assertTrue(r["sous_traitance"])

    def test_aucun_gagnant_si_absent(self):
        r = attributions.parser_gagnants("Notice sans section gagnant.")
        self.assertEqual(r["gagnants"], [])
        self.assertFalse(r["sous_traitance"])

    def test_est_attribution(self):
        self.assertTrue(attributions._est_attribution({"notice-type": "can-standard"}))
        self.assertFalse(attributions._est_attribution({"notice-type": "cn-standard"}))

    def test_codes_cpv(self):
        self.assertEqual(attributions._codes_cpv("45000000 texte 71000000"),
                         ["45000000", "71000000"])

    def test_nettoyer_montant_espaces_insecables(self):
        # Les espaces insecables (PDF) sont normalises en espaces simples.
        self.assertEqual(attributions._nettoyer_montant("1\u00a0200\u202f000", "EUR"),
                         "1 200 000 EUR")


@unittest.skipIf(bitd is None, "bitd_signaux/signaux_prives indisponibles")
class TestPriveParsingJSON(unittest.TestCase):
    """Extraction du JSON renvoye par le LLM (tolerante au texte autour)."""

    def test_json_valide(self):
        self.assertEqual(bitd._parser_json('{"signal": true, "iso3": "MLI"}'),
                         {"signal": True, "iso3": "MLI"})

    def test_json_avec_texte_autour(self):
        self.assertEqual(bitd._parser_json('Voici : {"a": 1} fin'), {"a": 1})

    def test_json_invalide_renvoie_none(self):
        self.assertIsNone(bitd._parser_json("aucun json ici"))

    def test_json_casse_renvoie_none(self):
        self.assertIsNone(bitd._parser_json('{"a": }'))

    def test_vide_renvoie_none(self):
        self.assertIsNone(bitd._parser_json(""))


@unittest.skipIf(signaux_prives is None, "signaux_prives indisponible")
class TestPriveScoring(unittest.TestCase):
    """Scoring des signaux prives : seuils, garde-fou de confiance, pays suivi."""

    def _extraction(self, **kw):
        base = {"type_activite": "implantation", "imminence": "immediate", "confiance": 0.9}
        base.update(kw)
        return base

    def test_pays_non_suivi_renvoie_none(self):
        signaux_prives._RETRO = None
        self.assertIsNone(signaux_prives.scorer_signal(self._extraction(), "haute", iso3="FRA"))

    def test_garde_fou_confiance(self):
        # Un signal fort mais peu sur ne doit pas monter en "contacter".
        signaux_prives._RETRO = None
        iso = next(iter(ted.CODES_PAYS_SUIVIS))
        fort_sur = signaux_prives.scorer_signal(self._extraction(confiance=0.9), "haute", iso3=iso)
        fort_incertain = signaux_prives.scorer_signal(self._extraction(confiance=0.1), "haute", iso3=iso)
        if fort_sur and fort_sur["action"] == "contacter":
            self.assertNotEqual(fort_incertain["action"], "contacter")

    def test_retroaction_neutre_par_defaut(self):
        # Sans _RETRO, le score n'est pas modifie par la retroaction.
        signaux_prives._RETRO = None
        iso = next(iter(ted.CODES_PAYS_SUIVIS))
        r = signaux_prives.scorer_signal(self._extraction(), "haute", iso3=iso)
        self.assertIsNotNone(r)
        self.assertIn(r["action"], ("contacter", "surveiller", "ignorer"))


@unittest.skipIf(radar_etat is None, "radar_etat indisponible")
class TestRadarEtat(unittest.TestCase):
    """Etat inter-runs (item 8) : round-trip, plafond, fichier absent."""

    def setUp(self):
        self.chemin = "/tmp/_test_etat_radar.json"
        if os.path.exists(self.chemin):
            os.remove(self.chemin)

    def tearDown(self):
        if os.path.exists(self.chemin):
            os.remove(self.chemin)

    def test_fichier_absent_signale_migration(self):
        self.assertEqual(radar_etat.charger(self.chemin), (None, None))

    def test_round_trip_et_ordre(self):
        radar_etat.sauver(5, ["a", "b"], ["c"], chemin=self.chemin)
        self.assertEqual(radar_etat.charger(self.chemin), (5, ["a", "b", "c"]))

    def test_plafond_garde_les_recents(self):
        old = radar_etat.MAX_VUS_MEMOIRE
        radar_etat.MAX_VUS_MEMOIRE = 3
        try:
            n = radar_etat.sauver(0, ["1", "2"], ["3", "4", "5"], chemin=self.chemin)
            _, vus = radar_etat.charger(self.chemin)
            self.assertEqual((n, vus), (3, ["3", "4", "5"]))
        finally:
            radar_etat.MAX_VUS_MEMOIRE = old


@unittest.skipIf(radar_retroaction is None, "radar_retroaction indisponible")
class TestRadarRetroaction(unittest.TestCase):
    """Retroaction (item 7) : neutre sous le seuil, bornee au-dessus."""

    def test_neutre_sous_le_seuil(self):
        outcomes = [{"secteur": "BTP", "zone": "Sahel", "statut": "gagne"}] * 3
        m = radar_retroaction.multiplicateurs(outcomes)
        self.assertEqual(m["secteur"].get("BTP"), 1.0)

    def test_borne_au_dessus_du_seuil(self):
        outcomes = ([{"secteur": "BTP", "zone": "Sahel", "statut": "gagne"}] * 10
                    + [{"secteur": "X", "zone": "Y", "statut": "perdu"}] * 10)
        m = radar_retroaction.multiplicateurs(outcomes)
        self.assertLessEqual(m["secteur"]["BTP"], radar_retroaction.MULT_MAX)
        self.assertGreaterEqual(m["secteur"]["BTP"], 1.0)

    def test_mult_pour_absent_est_neutre(self):
        self.assertEqual(radar_retroaction.mult_pour(None, "BTP", "Sahel"), 1.0)


@unittest.skipIf(radar_risque is None, "radar_risque indisponible")
class TestRadarRisque(unittest.TestCase):
    """Risque dynamique (item 11) : surcharge, repli socle, kill switch."""

    def _ouvrir(self, matrice):
        class Faux:
            def worksheet(self, nom): return self
            def get_all_values(self): return matrice
        return lambda s, f: Faux()

    def test_surcharge_puis_repli(self):
        mat = [["iso3", "niveau"], ["MLI", "rouge"], ["TCD", "orange"]]
        radar_risque.charger("sid", "cs", ouvrir=self._ouvrir(mat))
        self.assertEqual(radar_risque.mult_zone("MLI", 0.3), 1.0)   # surcharge
        self.assertEqual(radar_risque.mult_zone("NER", 0.3), 0.3)   # absent -> socle

    def test_niveau_inconnu_repli(self):
        mat = [["iso3", "niveau"], ["LBY", "ecarlate"]]
        radar_risque.charger("sid", "cs", ouvrir=self._ouvrir(mat))
        self.assertEqual(radar_risque.mult_zone("LBY", 0.9), 0.9)   # ignore -> socle

    def test_kill_switch(self):
        mat = [["iso3", "niveau"], ["MLI", "rouge"]]
        os.environ["RADAR_RISQUE"] = "0"
        try:
            radar_risque.charger("sid", "cs", ouvrir=self._ouvrir(mat))
            self.assertEqual(radar_risque.mult_zone("MLI", 0.3), 0.3)
        finally:
            del os.environ["RADAR_RISQUE"]


# ===========================================================================
# COLLECTE PRIVEE (P1) : locale bilingue Google News + Adzuna prioritaire.
# Couvre le correctif de rendement du moteur signaux prives (item 12 : etendre
# les tests aux collecteurs prives). Tout est hors-ligne (session/fetch simules).
# ===========================================================================
class _FauxRep:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP {}".format(self.status_code))


def _rss(items):
    """Construit un flux RSS minimal parsable par bitd.parser_rss."""
    corps = "".join(
        "<item><title>{}</title><link>{}</link>"
        "<pubDate>{}</pubDate><description>x</description></item>".format(t, l, d)
        for (t, l, d) in items)
    return "<?xml version='1.0'?><rss><channel>{}</channel></rss>".format(corps)


class _FauxSessionLocale:
    """Renvoie un RSS different selon la locale (hl=) presente dans l'URL,
    et enregistre les URL appelees pour verifier qu'on interroge bien FR ET EN."""
    def __init__(self, par_locale):
        self.par_locale = par_locale
        self.appels = []

    def get(self, url, timeout=None, **kw):
        self.appels.append(url)
        hl = "fr" if "hl=fr" in url else ("en" if "hl=en" in url else "?")
        return _FauxRep(self.par_locale.get(hl, "<rss></rss>"))


class TestCollecteBilingue(unittest.TestCase):
    def setUp(self):
        self._sleep = signaux_prives.time.sleep
        signaux_prives.time.sleep = lambda *a, **k: None   # tests instantanes
        self._loc = signaux_prives.GNEWS_LOCALES
        signaux_prives.GNEWS_LOCALES = [("fr", "FR", "FR:fr"), ("en", "US", "US:en")]

    def tearDown(self):
        signaux_prives.time.sleep = self._sleep
        signaux_prives.GNEWS_LOCALES = self._loc

    def test_url_locale_par_defaut_fr(self):
        url = bitd.url_google_news("Acme")
        self.assertIn("hl=fr", url)
        self.assertIn("gl=FR", url)

    def test_url_locale_en_surchargee(self):
        url = bitd.url_google_news("Acme", hl="en", gl="US", ceid="US:en")
        self.assertIn("hl=en", url)
        self.assertIn("gl=US", url)

    def test_les_deux_locales_sont_interrogees_et_fusionnees(self):
        # FR renvoie A ; EN renvoie A (meme lien) + B. Attendu : 2 uniques, 2 appels.
        sess = _FauxSessionLocale({
            "fr": _rss([("Titre FR", "https://ex/a", "")]),
            "en": _rss([("Title EN", "https://ex/a", ""),
                        ("Deploy Mali", "https://ex/b", "")]),
        })
        arts = signaux_prives.collecter_news("Acme", session=sess)
        liens = sorted(a["lien"] for a in arts)
        self.assertEqual(liens, ["https://ex/a", "https://ex/b"])
        self.assertEqual(len(sess.appels), 2)               # FR et EN
        self.assertTrue(any("hl=en" in u for u in sess.appels))

    def test_locale_en_echec_n_empeche_pas_fr(self):
        sess = _FauxSessionLocale({
            "fr": _rss([("Titre FR", "https://ex/a", "")]),
            "en": None,          # provoquera une exception dans parser -> locale ignoree
        })
        arts = signaux_prives.collecter_news("Acme", session=sess)
        self.assertEqual([a["lien"] for a in arts], ["https://ex/a"])

    def test_plafond_articles_prive(self):
        many = [("T{}".format(i), "https://ex/{}".format(i), "") for i in range(50)]
        sess = _FauxSessionLocale({"fr": _rss(many), "en": "<rss></rss>"})
        arts = signaux_prives.collecter_news("Acme", session=sess)
        self.assertLessEqual(len(arts), signaux_prives.MAX_ARTICLES_PRIVE)

    def test_adzuna_prioritaire_en_tete(self):
        # Adzuna renvoie une offre "Mali" ; la presse renvoie un autre article.
        # collecter_signaux doit placer l'offre Adzuna EN PREMIER.
        def faux_adzuna(pays, params):
            return {"results": [{
                "title": "Country Manager", "description": "Deploy in Mali",
                "location": {"display_name": "Bamako, Mali", "area": ["Mali"]},
                "company": {"display_name": "Acme"},
                "redirect_url": "https://job/1", "created": "2026-07-01T00:00:00Z",
            }]}
        vrai_news = signaux_prives.collecter_news
        signaux_prives.collecter_news = lambda *a, **k: [
            {"titre": "Presse", "lien": "https://news/1", "date": "", "resume": "x"}]
        try:
            arts = signaux_prives.collecter_signaux(
                "Acme", "", session=object(), fetch_adzuna=faux_adzuna)
        finally:
            signaux_prives.collecter_news = vrai_news
        self.assertTrue(arts[0]["titre"].startswith("[Offre d'emploi]"))
        self.assertEqual(arts[0]["lien"], "https://job/1")
        self.assertIn("https://news/1", [a["lien"] for a in arts])


class TestFenetrePrives(unittest.TestCase):
    def test_defaut_35(self):
        self.assertEqual(signaux_prives.taille_fenetre_pour(None), 35)
        self.assertEqual(signaux_prives.taille_fenetre_pour(""), 35)

    def test_surcharge_valide(self):
        self.assertEqual(signaux_prives.taille_fenetre_pour("50"), 50)

    def test_valeur_illisible_repli_defaut(self):
        self.assertEqual(signaux_prives.taille_fenetre_pour("abc"), 35)

    def test_borne_minimale(self):
        self.assertEqual(signaux_prives.taille_fenetre_pour("0"), 1)


# ===========================================================================
# ENRICHISSEMENT ELARGI (P2) : defense + watchlist + attributaires publies.
# Fonctions pures + fuseur (classeur simule, aucun reseau).
# ===========================================================================
class _FauxWS:
    def __init__(self, m):
        self._m = m

    def get_all_values(self):
        return self._m


class _FauxClasseurEnrich:
    def __init__(self, tabs):
        self._t = tabs

    def worksheet(self, name):
        if name not in self._t:
            raise KeyError(name)
        return _FauxWS(self._t[name])


@unittest.skipIf(enrichir_entreprises is None, "enrichir_entreprises indisponible")
class TestEnrichissementElargi(unittest.TestCase):
    def test_watchlist_actif_non_ignore(self):
        vals = [["entreprise", "secteur", "actif", "requete_optionnelle"],
                ["Bouygues", "BTP", "oui", ""],
                ["PetitLocal", "BTP", "non", ""],
                ["", "x", "oui", ""]]
        out = enrichir_entreprises.entreprises_watchlist(vals)
        self.assertEqual([w["entreprise"] for w in out], ["Bouygues"])
        self.assertEqual(out[0]["priorite_socle"], "Haute")
        self.assertEqual(out[0]["origine"], "watchlist")

    def test_watchlist_vide(self):
        self.assertEqual(enrichir_entreprises.entreprises_watchlist([]), [])
        self.assertEqual(enrichir_entreprises.entreprises_watchlist([["entreprise"]]), [])

    def test_attributaires_publies_dedup_et_multi(self):
        vals = [["date_maj", "gagnant", "secteur", "pays_execution"],
                ["d", "Vinci", "BTP", "MLI"],
                ["d", "(gagnant non publie)", "x", "NER"],
                ["d", "Vinci", "BTP", "TCD"],               # doublon -> ignore
                ["d", "Orano ; Eiffage", "Mines", "NER"]]   # deux gagnants
        out = enrichir_entreprises.entreprises_attributaires(vals)
        self.assertEqual(sorted(w["entreprise"] for w in out),
                         ["Eiffage", "Orano", "Vinci"])
        self.assertTrue(all(w["priorite_socle"] == "Moyenne" for w in out))

    def test_attributaires_plafond(self):
        vals = [["gagnant"]] + [["Soc{}".format(i)] for i in range(10)]
        out = enrichir_entreprises.entreprises_attributaires(vals, max_comptes=3)
        self.assertEqual(len(out), 3)

    def test_attributaires_sans_colonne_gagnant(self):
        self.assertEqual(enrichir_entreprises.entreprises_attributaires(
            [["date_maj", "titre"], ["d", "x"]]), [])

    def test_construire_dedup_defense_prioritaire(self):
        # 'Acme' en defense (Basse) ET en watchlist (Haute) : la 1re occurrence
        # (defense) gagne. 'Newco' en watchlist ET en attributaire : watchlist
        # gagne (ordre defense > watchlist > attributaire).
        faux_defense = [{"entreprise": "Acme", "priorite_socle": "Basse"}]
        wl = [["entreprise", "actif"], ["Acme", "oui"], ["Newco", "oui"]]
        at = [["gagnant"], ["Winco"], ["Newco"]]
        classeur = _FauxClasseurEnrich({"watchlist_prives": wl,
                                        "attributions_radar": at})
        vieux = enrichir_entreprises._lire_whitelist
        enrichir_entreprises._lire_whitelist = lambda sid, cs: list(faux_defense)
        try:
            out = enrichir_entreprises.construire_liste_enrichissement(
                "sid", "cs", ouvrir=lambda s, c: classeur)
        finally:
            enrichir_entreprises._lire_whitelist = vieux
        par_nom = {w["entreprise"]: w for w in out}
        self.assertEqual(sorted(par_nom), ["Acme", "Newco", "Winco"])
        self.assertEqual(par_nom["Acme"]["priorite_socle"], "Basse")     # defense garde sa priorite
        self.assertEqual(par_nom["Newco"]["origine"], "watchlist")       # watchlist avant attributaire
        self.assertEqual(par_nom["Winco"]["priorite_socle"], "Moyenne")

    def test_hunter_cible_watchlist_pas_attributaire(self):
        # La watchlist (Haute) est eligible a Hunter ; l'attributaire (Moyenne) non.
        liste = [{"entreprise": "FrHaute", "priorite_socle": "Haute"},
                 {"entreprise": "AttribMoy", "priorite_socle": "Moyenne"}]
        infos = {"frhaute": ("Jean Dupont (Président)", "gouv")}
        cibles = enrichir_entreprises.selectionner_cibles_hunter(
            liste, infos, set(), budget=10)
        noms = [c[0] for c in cibles]
        self.assertIn("FrHaute", noms)
        self.assertNotIn("AttribMoy", noms)


@unittest.skipIf(enrichir_entreprises is None, "enrichir_entreprises indisponible")
class TestEnrichissementP2(unittest.TestCase):
    """Perimetre elargi : defense + watchlist_prives + attributaires publies,
    dedup avec priorite, et protection du quota Hunter (attributaires exclus)."""

    def test_watchlist_ignore_inactif_et_vide(self):
        v = [["entreprise", "secteur", "actif", "requete_optionnelle"],
             ["TotalEnergies", "Oil & Gas", "oui", ""],
             ["PetitLocal", "BTP", "non", ""],
             ["", "x", "oui", ""]]
        out = enrichir_entreprises.entreprises_watchlist(v)
        self.assertEqual([d["entreprise"] for d in out], ["TotalEnergies"])
        self.assertEqual(out[0]["priorite_socle"], "Haute")     # eligible Hunter
        self.assertEqual(out[0]["origine"], "watchlist")

    def test_attributaires_publies_dedup_et_split(self):
        v = [["date_maj", "gagnant", "secteur"],
             ["2026", "Bouygues Construction; Vinci", ""],
             ["2026", "(gagnant non publie)", ""],
             ["2026", "Bouygues Construction", ""],   # doublon
             ["2026", "AB", ""]]                      # trop court -> ignore
        out = enrichir_entreprises.entreprises_attributaires(v)
        self.assertEqual([d["entreprise"] for d in out], ["Bouygues Construction", "Vinci"])
        self.assertTrue(all(d["priorite_socle"] == "Moyenne" for d in out))  # pas Hunter

    def test_attributaires_respecte_plafond(self):
        v = [["gagnant"], ["A Corp"], ["B Corp"], ["C Corp"]]
        self.assertEqual(len(enrichir_entreprises.entreprises_attributaires(v, max_comptes=2)), 2)

    def test_construire_liste_dedup_defense_prioritaire(self):
        wl = [["entreprise", "actif"], ["TotalEnergies", "oui"], ["Eiffage", "oui"]]
        at = [["gagnant"], ["Eiffage"], ["Vinci"]]   # Eiffage aussi attributaire

        class _WS:
            def __init__(s, val): s.val = val
            def get_all_values(s): return s.val

        class _Classeur:
            def __init__(s, mp): s.mp = mp
            def worksheet(s, n):
                if n in s.mp: return _WS(s.mp[n])
                raise RuntimeError("absent")

        def _ouvrir(sid, cs):
            return _Classeur({"watchlist_prives": wl, "attributions_radar": at})

        vrai = enrichir_entreprises._lire_whitelist
        enrichir_entreprises._lire_whitelist = lambda sid, cs: [
            {"entreprise": "TotalEnergies", "priorite_socle": "Haute"}]
        try:
            liste = enrichir_entreprises.construire_liste_enrichissement(
                "sid", "cs", ouvrir=_ouvrir)
        finally:
            enrichir_entreprises._lire_whitelist = vrai
        noms = [(d["entreprise"], d.get("origine")) for d in liste]
        # TotalEnergies : une seule fois, marquee defense (1re occurrence gagne).
        self.assertEqual(sum(1 for n in noms if n[0] == "TotalEnergies"), 1)
        self.assertIn(("TotalEnergies", "defense"), noms)
        # Eiffage : d'abord vue via watchlist -> reste 'watchlist', pas doublee.
        self.assertEqual(sum(1 for n in noms if n[0] == "Eiffage"), 1)
        self.assertIn(("Eiffage", "watchlist"), noms)
        self.assertIn(("Vinci", "attributaire"), noms)

    def test_hunter_exclut_les_attributaires(self):
        # Quota paye protege : seule la priorite 'Haute' est ciblee par Hunter.
        liste = [
            {"entreprise": "Eiffage", "priorite_socle": "Haute"},        # watchlist
            {"entreprise": "Vinci", "priorite_socle": "Moyenne"},        # attributaire
        ]
        infos = {"eiffage": ("", "gleif"), "vinci": ("", "gleif")}
        cibles = enrichir_entreprises.selectionner_cibles_hunter(
            liste, infos, deja=set(), budget=10)
        noms = [c[0] for c in cibles]
        self.assertIn("Eiffage", noms)
        self.assertNotIn("Vinci", noms)        # attributaire jamais appele en payant


@unittest.skipIf(ted is None, "ted_complet_v14 indisponible")
class TestSecuriteDeplacementP3(unittest.TestCase):
    """Levier 'deplacement concurrent' : l'enum securite_existante remplace le
    booleen. interne_client seul supprime ; prestataire_tiers remonte a plein
    score et est marque. Le booleen historique reste derive pour tout l'aval."""

    def test_enum_derive_le_booleen(self):
        cas = {"aucune": False, "interne_client": True,
               "prestataire_tiers": False, "inconnu": False}
        for enum, attendu in cas.items():
            out = ted.normaliser_securite({"securite_existante": enum, "justification": "x"})
            self.assertEqual(out["securite_existante_detectee"], attendu, enum)
            self.assertEqual(out["securite_existante"], enum)

    def test_valeur_inconnue_repli_sur_inconnu(self):
        out = ted.normaliser_securite({"securite_existante": "n_importe_quoi"})
        self.assertEqual(out["securite_existante"], "inconnu")
        self.assertFalse(out["securite_existante_detectee"])

    def test_prestataire_marque_la_justification(self):
        out = ted.normaliser_securite(
            {"securite_existante": "prestataire_tiers", "justification": "besoin escorte"})
        self.assertTrue(out["justification"].startswith(ted.MARQUEUR_DEPLACEMENT))
        self.assertIn("besoin escorte", out["justification"])

    def test_marqueur_idempotent(self):
        d = {"securite_existante": "prestataire_tiers",
             "justification": ted.MARQUEUR_DEPLACEMENT + " deja la"}
        out = ted.normaliser_securite(d)
        self.assertEqual(out["justification"].count("DÉPLACEMENT"), 1)

    def test_repli_ancien_booleen(self):
        # Modele qui n'emet que l'ancien champ : on preserve la suppression.
        vrai = ted.normaliser_securite({"securite_existante_detectee": True})
        self.assertTrue(vrai["securite_existante_detectee"])
        self.assertEqual(vrai["securite_existante"], "interne_client")
        faux = ted.normaliser_securite({"securite_existante_detectee": False})
        self.assertFalse(faux["securite_existante_detectee"])

    def test_none_et_non_dict_tolerants(self):
        self.assertIsNone(ted.normaliser_securite(None))
        self.assertEqual(ted.normaliser_securite("x"), "x")

    def test_action_interne_supprime_prestataire_surface(self):
        base = {"accessibilite_commerciale": "facile"}
        interne = ted.normaliser_securite({**base, "securite_existante": "interne_client"})
        presta = ted.normaliser_securite({**base, "securite_existante": "prestataire_tiers"})
        self.assertEqual(ted.calculer_action_recommandee(8.0, interne, surete=6.0), "ignorer")
        self.assertNotEqual(ted.calculer_action_recommandee(8.0, presta, surete=6.0), "ignorer")

    def test_prestataire_sans_penalite_de_score(self):
        avis = {"pays_execution": "ML", "cpv": ""}
        socle = {"deploiement_terrain_reel": True, "type_mobilite": "terrain_isole",
                 "profil_personnes_exposees": "expert_international",
                 "type_client": "entreprise_privee", "accessibilite_commerciale": "facile",
                 "duree_estimee": "longue_ou_residente"}
        interne = ted.normaliser_securite({**socle, "securite_existante": "interne_client"})
        presta = ted.normaliser_securite({**socle, "securite_existante": "prestataire_tiers"})
        s_i, c_i, _ = ted.calculer_scores(avis, interne)
        s_p, c_p, _ = ted.calculer_scores(avis, presta)
        # Le prestataire (conquete) ne subit pas la penalite -3/-2 de l'interne.
        self.assertGreater(s_p, s_i)
        self.assertGreater(c_p, c_i)


class TestCouverturePaysDashboard(unittest.TestCase):
    """Garde anti-regression (audit juillet 2026). Verrouille l'ecart trouve a
    l'audit : des pays suivis par le coeur TED etaient absents de la carte du
    dashboard (zone -> 'Non classe', pas de point carte). Ces trois tests
    echouent si l'ecart revient, ce qui BLOQUE le deploiement en CI."""

    @unittest.skipIf(radar_dashboard is None or ted is None,
                     "radar_dashboard ou ted_complet_v14 indisponible")
    def test_iso3_couvre_tout_l_univers_de_risque(self):
        """Tout pays suivi par le coeur (CODES_PAYS_SUIVIS) doit avoir une zone
        dans ZONE_PAR_ISO3, sinon il s'affiche en 'Non classe'."""
        suivis = set(ted.CODES_PAYS_SUIVIS)
        mappes = set(radar_dashboard.ZONE_PAR_ISO3)
        manquants = sorted(suivis - mappes)
        self.assertEqual(manquants, [],
                         "Pays suivis absents de ZONE_PAR_ISO3 : {}".format(manquants))

    @unittest.skipIf(radar_dashboard is None, "radar_dashboard indisponible")
    def test_toute_zone_utilisee_a_un_bareme_de_risque(self):
        """Chaque zone employee dans les cartes pays doit exister dans
        RISQUE_ZONE (sinon le score d'attribution retombe sur le defaut 1.5)."""
        d = radar_dashboard
        zones = {v[1] for v in d.ZONE_PAR_ISO3.values()}
        zones |= {v[1] for v in d.ZONE_PAR_NOM.values()}
        manquantes = sorted(zones - set(d.RISQUE_ZONE))
        self.assertEqual(manquantes, [],
                         "Zones sans bareme RISQUE_ZONE : {}".format(manquantes))

    @unittest.skipIf(radar_dashboard is None, "radar_dashboard indisponible")
    def test_tout_pays_mappe_a_des_coordonnees_carte(self):
        """Tout nom de pays present dans les cartes doit avoir des coordonnees
        dans COORDS (JS du gabarit), sinon aucun point ne s'affiche sur la
        carte pour ce pays."""
        import re
        d = radar_dashboard
        m = re.search(r"const COORDS=\{(.+?)\n\};", d.GABARIT_HTML, re.S)
        self.assertIsNotNone(m, "Bloc COORDS introuvable dans le gabarit HTML.")
        coords = set(re.findall(r'"([^"]+)":\[', m.group(1)))
        noms = {v[0] for v in d.ZONE_PAR_ISO3.values()}
        noms |= {v[0] for v in d.ZONE_PAR_NOM.values()}
        manquants = sorted(noms - coords)
        self.assertEqual(manquants, [],
                         "Pays mappes sans coordonnees carte : {}".format(manquants))


class TestDigestHebdo(unittest.TestCase):
    """Digest push (option B). Teste la logique PURE de selection des leads :
    seuls les 'a contacter' non encore pris en charge partent, tries par score,
    plafonnes. Aucun reseau, aucune cle."""

    def _lead(self, **kw):
        base = {"src": "TED", "pays": "Mali", "zone": "Sahel", "titre": "AT",
                "agence": "AFD", "final": 7.0, "surete": 6.0, "comm": 8.0,
                "win": "immediate", "lien": "", "nom": "n.c.", "email": "n.c.",
                "date_det": "2026-07-10", "action": "contacter", "statut": "nouveau",
                "pub": ""}
        base.update(kw)
        return base

    @unittest.skipIf(radar_digest is None, "radar_digest indisponible")
    def test_ne_garde_que_les_a_contacter(self):
        leads = [self._lead(pub="A", action="contacter"),
                 self._lead(pub="B", action="surveiller"),
                 self._lead(pub="C", action="ignorer")]
        ids = [x["id"] for x in radar_digest.construire_payload(leads)["leads"]]
        self.assertEqual(ids, ["A"])

    @unittest.skipIf(radar_digest is None, "radar_digest indisponible")
    def test_exclut_les_statuts_deja_pris_en_charge(self):
        leads = [self._lead(pub="A", statut="nouveau"),
                 self._lead(pub="B", statut="contacté"),
                 self._lead(pub="C", statut="gagne"),
                 self._lead(pub="D", statut="")]
        ids = {x["id"] for x in radar_digest.construire_payload(leads)["leads"]}
        self.assertEqual(ids, {"A", "D"})

    @unittest.skipIf(radar_digest is None, "radar_digest indisponible")
    def test_tri_par_score_decroissant_et_plafond(self):
        leads = [self._lead(pub="bas", final=4.5),
                 self._lead(pub="haut", final=9.1),
                 self._lead(pub="moyen", final=6.0)]
        ids = [x["id"] for x in radar_digest.construire_payload(leads)["leads"]]
        self.assertEqual(ids, ["haut", "moyen", "bas"])
        court = radar_digest.construire_payload(leads, max_leads=2)["leads"]
        self.assertEqual([x["id"] for x in court], ["haut", "moyen"])

    @unittest.skipIf(radar_digest is None, "radar_digest indisponible")
    def test_lead_id_miroir_du_dashboard(self):
        # publication_number prioritaire, puis lien, puis composite.
        self.assertEqual(radar_digest.lead_id({"pub": "302871-2026"}), "302871-2026")
        self.assertEqual(radar_digest.lead_id({"lien": "http://x/y"}), "http://x/y")
        self.assertEqual(
            radar_digest.lead_id({"src": "BM", "pays": "Niger", "agence": "BM", "titre": "T"}),
            "BM|Niger|BM|T")

    @unittest.skipIf(radar_digest is None, "radar_digest indisponible")
    def test_contact_nc_normalise_en_vide(self):
        item = radar_digest.construire_payload([self._lead(pub="A", nom="n.c.", email="n.c.")])["leads"][0]
        self.assertEqual(item["contact"], "")
        self.assertEqual(item["email"], "")
        item2 = radar_digest.construire_payload(
            [self._lead(pub="B", nom="Jean Dupont", email="j@ex.com")])["leads"][0]
        self.assertEqual(item2["contact"], "Jean Dupont")
        self.assertEqual(item2["email"], "j@ex.com")

    @unittest.skipIf(radar_digest is None, "radar_digest indisponible")
    def test_envoyer_best_effort_ne_leve_pas(self):
        # Un POST vers une URL invalide ne doit jamais lever (best-effort).
        # Session simple (sans reessai) pour un test rapide et deterministe.
        import requests
        ok = radar_digest.envoyer("http://127.0.0.1:1/exec", "tok",
                                  {"type": "digest", "leads": []},
                                  session=requests.Session())
        self.assertFalse(ok)


@unittest.skipIf(bm_attributions is None, "bm_attributions indisponible")
class TestAttributionsBM(unittest.TestCase):
    """Collecteur d'attributions Banque Mondiale. Les structures HTML de ces
    tests sont celles OBSERVEES sur donnees reelles le 18/07/2026 (mode
    verification), pas des suppositions."""

    ENTETE = (
        "<div class='row col-sm-12'><h4>Contract Award</h4><p>"
        "<b>Project:</b>P178566-Food Systems Resilience Program<br/>"
        "<b>Bid/Contract Reference No:</b>ML-FSRP-517016-CW-RFQ<br/>"
        "<b>Procurement Method:</b>RFQ-Request for Quotations<br/>"
        "<b>Notice Version No:</b>0</p><br/></div>"
        "<div class='row'><div class='col-sm-4'>"
        "<b>Date Notification of Award Issued</b><br/>(YYYY/MM/DD)<br/>{d}<br/></div>"
        "<div class='col-sm-4'><b>Duration of Contract</b><br/><br/>60 Day(s)<br/></div></div>"
    )
    # Bloc titulaire REEL : nom + identifiant, adresse, pays, puis montant.
    BLOC_ETRANGER = (
        "<div><u><b>Awarded Bidder(s):</b></u></div>"
        "<div>STECOL CORPORATION (333385)</div><div>Tianjin, China</div>"
        "<div>Country: China</div>"
        "<div>Bid Price at Opening</div><div>USD</div><div>45,300,000.00</div>"
    )
    BLOC_LOCAL = (
        "<div><u><b>Awarded Bidder(s):</b></u></div>"
        "<div>DOONYODHER C.C (1107014)</div><div>Jijiga</div>"
        "<div>Country: Ethiopia</div><div>Bid Price at Opening</div><div>2,450,000.00</div>"
    )

    def _texte(self, bloc, d="2026/07/01"):
        return self.ENTETE.format(d=d) + bloc

    def _recent(self):
        from datetime import date as _d, timedelta as _td
        return (_d.today() - _td(days=10)).strftime("%Y/%m/%d")

    # -- Extraction du titulaire -----------------------------------------
    def test_nom_propre_sans_adresse_ni_entete(self):
        """Le bug corrige : l'ancien parseur produisait
        'DOONYODHER C.C (1107014) ; Jijiga ; Country: Ethiopia ; Bid Price at
        Opening'. Seule la raison sociale doit sortir."""
        noms = bm_attributions.extraire_gagnants(self._texte(self.BLOC_LOCAL))
        self.assertEqual(noms, ["DOONYODHER C.C"])

    def test_groupement_deux_titulaires(self):
        bloc = self.BLOC_ETRANGER + "<div>AK ZHOL KURYLYS (1047296)</div><div>Astana</div>"
        self.assertEqual(bm_attributions.extraire_gagnants(self._texte(bloc)),
                         ["STECOL CORPORATION", "AK ZHOL KURYLYS"])

    def test_sans_identifiant_aucun_gagnant(self):
        """Prudence assumee : sans identifiant fournisseur, on n'invente pas."""
        bloc = ("<div><u><b>Awarded Bidder(s):</b></u></div>"
                "<div>Bid Price at Opening</div><div>1,250,000.00</div>")
        self.assertEqual(bm_attributions.extraire_gagnants(self._texte(bloc)), [])

    def test_aucun_gagnant_si_section_absente(self):
        self.assertEqual(bm_attributions.extraire_gagnants("<p>Contract Award</p>"), [])

    # -- Filtre commercial etranger / local ------------------------------
    def test_titulaire_etranger_detecte(self):
        self.assertTrue(bm_attributions.titulaire_etranger("China", "Kazakhstan"))

    def test_titulaire_local_detecte(self):
        self.assertFalse(bm_attributions.titulaire_etranger("Ethiopia", "Ethiopia"))

    def test_origine_inconnue_ne_jette_pas(self):
        self.assertTrue(bm_attributions.titulaire_etranger("", "Mali"))

    def test_pays_titulaire_extrait(self):
        self.assertEqual(
            bm_attributions.pays_titulaire(self._texte(self.BLOC_ETRANGER)), "China")

    def test_entrepreneur_local_ecarte_par_defaut(self):
        """Un macon local n'achete pas de protection internationale."""
        rec = {"notice_type": "Contract Award", "procurement_group": "CW",
               "project_ctry_name": "Ethiopia", "id": "OP1",
               "bid_description": "Travaux",
               "notice_text": self._texte(self.BLOC_LOCAL, d=self._recent())}
        self.assertIsNone(bm_attributions.normaliser(rec))

    def test_titulaire_etranger_conserve(self):
        rec = {"notice_type": "Contract Award", "procurement_group": "CW",
               "project_ctry_name": "Kazakhstan", "id": "OP2",
               "bid_description": "Route",
               "notice_text": self._texte(self.BLOC_ETRANGER, d=self._recent())}
        ligne = bm_attributions.normaliser(rec)
        self.assertIsNotNone(ligne)
        self.assertEqual(ligne["gagnant"], "STECOL CORPORATION")
        self.assertIn("titulaire China", ligne["titre"])

    # -- Integration dashboard (bug ISO3) --------------------------------
    def test_pays_ecrit_en_iso3_pour_le_dashboard(self):
        """BUG CORRIGE : le dashboard resout les attributions en mode ISO3.
        Ecrire 'Mali' donnait 'Non classe' ; il faut 'MLI'."""
        rec = {"notice_type": "Contract Award", "procurement_group": "CW",
               "project_ctry_name": "Mali", "id": "OP3", "bid_description": "Forages",
               "notice_text": self._texte(self.BLOC_ETRANGER, d=self._recent())}
        ligne = bm_attributions.normaliser(rec)
        self.assertEqual(ligne["pays_execution"], "MLI")

    def test_le_dashboard_classe_bien_la_ligne_produite(self):
        """Bout en bout : la ligne ecrite doit tomber dans une vraie zone."""
        try:
            import radar_dashboard as dash
        except Exception:
            self.skipTest("radar_dashboard indisponible")
        rec = {"notice_type": "Contract Award", "procurement_group": "CW",
               "project_ctry_name": "Mali", "id": "OP4", "bid_description": "Forages",
               "notice_text": self._texte(self.BLOC_ETRANGER, d=self._recent())}
        ligne = bm_attributions.normaliser(rec)
        lead = dash.attribution_vers_lead(ligne)
        self.assertEqual(lead["pays"], "Mali")
        self.assertNotEqual(lead["zone"], "Non classé")
        self.assertEqual(lead["entreprise"], "STECOL CORPORATION")

    # -- Champs annexes ---------------------------------------------------
    def test_montant_avec_devise(self):
        # USD 45 300 000 -> exprime en millions pour que le dashboard le lise.
        self.assertEqual(
            bm_attributions.montant_attribue(self._texte(self.BLOC_ETRANGER)),
            "USD 45.300 million")

    def test_date_attribution_depuis_notice_text(self):
        self.assertEqual(
            bm_attributions.date_attribution(
                self._texte(self.BLOC_ETRANGER, d="2026/05/13"), {}), "2026-05-13")

    def test_date_repli_sur_noticedate(self):
        self.assertEqual(
            bm_attributions.date_attribution("<p>rien</p>", {"noticedate": "17-Jul-2026"}),
            "2026-07-17")

    def test_duree_contrat_extraite(self):
        self.assertEqual(
            bm_attributions.duree_contrat(self._texte(self.BLOC_ETRANGER)), "60 Day(s)")

    # -- Fenetre et filtres ----------------------------------------------
    def test_fenetre_mobilisation(self):
        from datetime import date as _d, timedelta as _td
        auj = _d(2026, 7, 18)
        self.assertTrue(bm_attributions.dans_la_fenetre(
            (auj - _td(days=30)).isoformat(), auj, 120))
        self.assertFalse(bm_attributions.dans_la_fenetre(
            (auj - _td(days=400)).isoformat(), auj, 120))
        self.assertFalse(bm_attributions.dans_la_fenetre("", auj, 120))

    def test_groupe_fournitures_ecarte(self):
        rec = {"notice_type": "Contract Award", "procurement_group": "GO",
               "project_ctry_name": "Mali"}
        self.assertEqual(bm_attributions.record_retenu(rec)[1], "groupe")

    def test_pays_hors_perimetre_ecarte(self):
        rec = {"notice_type": "Contract Award", "procurement_group": "CW",
               "project_ctry_name": "Denmark"}
        self.assertEqual(bm_attributions.record_retenu(rec)[1], "pays")

    def test_travaux_pays_a_risque_retenu(self):
        rec = {"notice_type": "Contract Award", "procurement_group": "CW",
               "project_ctry_name": "Mali"}
        self.assertTrue(bm_attributions.record_retenu(rec)[0])

    # -- Schema et deduplication ------------------------------------------
    def test_schema_identique_aux_attributions_ted(self):
        """Garde-fou d'integration : colonnes identiques a celles des
        attributions TED, sinon la lentille Titulaires et la fiche 360 cassent."""
        try:
            import ted_complet_attributions as attrib_ted
        except Exception:
            self.skipTest("ted_complet_attributions indisponible")
        self.assertEqual(bm_attributions.COLONNES, attrib_ted.COLONNES)
        self.assertEqual(bm_attributions.NOM_ONGLET, attrib_ted.NOM_ONGLET)

    def test_toutes_les_colonnes_sont_produites(self):
        rec = {"notice_type": "Contract Award", "procurement_group": "CS",
               "project_ctry_name": "Niger", "id": "OP5", "bid_description": "AT",
               "notice_text": self._texte(self.BLOC_ETRANGER, d=self._recent())}
        ligne = bm_attributions.normaliser(rec)
        for col in bm_attributions.COLONNES:
            self.assertIn(col, ligne, "colonne manquante : {}".format(col))

    def test_construire_deduplique(self):
        rec = {"notice_type": "Contract Award", "procurement_group": "CS",
               "project_ctry_name": "Niger", "id": "OP42", "bid_description": "AT",
               "notice_text": self._texte(self.BLOC_ETRANGER, d=self._recent())}
        sorties, _m = bm_attributions.construire([rec, dict(rec)])
        self.assertEqual(len(sorties), 1)

    def test_attribution_republiee_dedupliquee(self):
        """La BM republie le meme marche sous plusieurs identifiants (constate :
        BETH BETSALEEL SARL trois fois, meme jour, meme montant)."""
        base = {"notice_type": "Contract Award", "procurement_group": "CW",
                "project_ctry_name": "Mali", "bid_description": "Travaux",
                "notice_text": self._texte(self.BLOC_ETRANGER, d=self._recent())}
        a = dict(base, id="OP100")
        b = dict(base, id="OP101")          # identifiant different, meme marche
        sorties, motifs = bm_attributions.construire([a, b])
        self.assertEqual(len(sorties), 1)
        self.assertEqual(motifs["republie"], 1)

    # -- Devises : conversion et lecture par le dashboard ------------------
    def test_devise_non_dupliquee(self):
        """Bug corrige : la sortie valait 'USD USD 7918777.87'."""
        txt = ("<div>Bid Price at Opening</div><div>USD</div>"
               "<div>USD 7918777.87</div>")
        self.assertEqual(bm_attributions.montant_attribue(txt), "USD 7.919 million")

    def test_conversion_devise_locale_en_usd(self):
        """XOF 4 806 034 530 vaut ~8 M USD, pas 4 806 M."""
        txt = "<div>Bid Price at Opening</div><div>XOF</div><div>4806034530</div>"
        self.assertEqual(bm_attributions.montant_attribue(txt), "USD 8.010 million")

    def test_petit_marche_ne_sature_pas_le_score(self):
        """XAF 8 487 678 vaut ~14 000 USD : il doit peser le MINIMUM.
        Bug corrige : il etait lu comme 8,5 M et scorait comme un marche moyen."""
        try:
            import radar_dashboard as dash
        except Exception:
            self.skipTest("radar_dashboard indisponible")
        txt = "<div>Bid Price at Opening</div><div>XAF</div><div>8487678</div>"
        valeur = bm_attributions.montant_attribue(txt)
        self.assertLess(dash._valeur_en_millions(valeur), 1.0)

    def test_gros_marche_bien_lu_par_le_dashboard(self):
        try:
            import radar_dashboard as dash
        except Exception:
            self.skipTest("radar_dashboard indisponible")
        txt = ("<div>Bid Price at Opening</div><div>KZT</div>"
               "<div>KZT 102797683320.75</div>")
        valeur = bm_attributions.montant_attribue(txt)
        self.assertGreater(dash._valeur_en_millions(valeur), 20.0)

    def test_devise_inconnue_conserve_le_brut(self):
        txt = "<div>Bid Price at Opening</div><div>QQQ</div><div>1234567</div>"
        self.assertIn("1234567", bm_attributions.montant_attribue(txt))

    # -- Pays bilingues et accents perdus ---------------------------------
    def test_meme_pays_en_deux_langues_est_local(self):
        """Faux positifs du journal : le titulaire etait ecrit en francais et
        le projet en anglais, d'ou des entreprises LOCALES prises pour des
        etrangeres (RDC, Cameroun, Benin)."""
        for fr, en in (("Congo, Rpublique dmocratique du", "Congo, Democratic Republic of"),
                       ("Cameroun", "Cameroon"), ("Bnin", "Benin")):
            self.assertFalse(bm_attributions.titulaire_etranger(fr, en),
                             "{} / {} devrait etre local".format(fr, en))

    def test_vrais_etrangers_conserves(self):
        for a, b in (("Chine", "Madagascar"), ("China", "Philippines"),
                     ("Turquie", "Niger"), ("Burkina Faso", "Mali")):
            self.assertTrue(bm_attributions.titulaire_etranger(a, b))

    def test_niger_et_nigeria_ne_sont_pas_confondus(self):
        """Piege classique : deux pays voisins aux noms proches."""
        self.assertEqual(bm_attributions.iso3_pays_libre("Niger"), "NER")
        self.assertEqual(bm_attributions.iso3_pays_libre("Nigeria"), "NGA")
        self.assertTrue(bm_attributions.titulaire_etranger("Nigeria", "Niger"))

    def test_les_deux_congo_distingues(self):
        self.assertEqual(bm_attributions.iso3_pays_libre("Congo, Democratic Republic of"), "COD")
        self.assertEqual(bm_attributions.iso3_pays_libre("Congo"), "COG")

    # -- Couverture : tri par date et arret anticipe -----------------------
    def test_page_ancienne_declenche_l_arret(self):
        """Avec le tri par date decroissante, une page entierement anterieure a
        la fenetre signifie qu'il n'y a plus rien d'utile en dessous."""
        from datetime import date as _d, timedelta as _td
        auj = _d(2026, 7, 18)
        vieux = [{"noticedate": (auj - _td(days=900)).strftime("%d-%b-%Y")}] * 3
        self.assertTrue(bm_attributions._page_trop_ancienne(vieux, 180, auj))

    def test_page_recente_ne_coupe_pas(self):
        from datetime import date as _d, timedelta as _td
        auj = _d(2026, 7, 18)
        lot = [{"noticedate": (auj - _td(days=900)).strftime("%d-%b-%Y")},
               {"noticedate": (auj - _td(days=10)).strftime("%d-%b-%Y")}]
        self.assertFalse(bm_attributions._page_trop_ancienne(lot, 180, auj))

    def test_dates_illisibles_ne_coupent_jamais(self):
        """Prudence : sans date exploitable, on ne s'arrete pas."""
        lot = [{"noticedate": ""}, {"autre": "champ"}]
        self.assertFalse(bm_attributions._page_trop_ancienne(lot, 180))

    def test_collecte_demande_le_tri_par_date(self):
        """Le tri serveur evite de saturer le plafond de pages sur des avis
        anciens (577 ecartes hors fenetre au run du 18/07/2026)."""
        vus = {}

        class FausseSession:
            def get(self, url, params=None, timeout=None):
                vus.update(params or {})
                class R:
                    status_code = 200
                    @staticmethod
                    def json():
                        return {"procnotices": []}
                return R()

        bm_attributions.collecte(session=FausseSession())
        self.assertEqual(vus.get("srt"), "noticedate")
        self.assertEqual(vus.get("ord"), "desc")

    # -- Devises completees apres run reel ---------------------------------
    def test_devise_africaine_convertie(self):
        """BIF ressortait non converti au run du 18/07/2026."""
        txt = "<div>Bid Price at Opening</div><div>BIF</div><div>63308997584</div>"
        valeur = bm_attributions.montant_attribue(txt)
        self.assertTrue(valeur.startswith("USD "), valeur)
        self.assertIn("million", valeur)


@unittest.skipIf(signaux_prives is None, "signaux_prives indisponible")
class TestRendementWatchlist(unittest.TestCase):
    """Rendement de la rotation privee (audit juillet 2026). Trois corrections
    verrouillees ici : repartition du quota Adzuna, curseur honnete, et
    colonne pays optionnelle."""

    # -- Repartition du quota Adzuna ------------------------------------
    def test_quota_reparti_donne_une_couverture_a_chacune(self):
        """Le bug corrige : 7 pays x 35 entreprises = 245 appels pour un
        plafond de 120. Les 18 dernieres entreprises n'avaient AUCUNE
        couverture Adzuna. Desormais chacune recoit au moins un portail."""
        q = signaux_prives.quota_pays_adzuna(120, 35, maxi=7)
        self.assertGreaterEqual(q, 1)
        self.assertLessEqual(q, 7)
        # Simulation d'un run complet : personne ne doit finir a zero.
        reste = 120
        couvertures = []
        for i in range(35):
            quota = signaux_prives.quota_pays_adzuna(reste, 35 - i, maxi=7)
            couvertures.append(quota)
            reste -= quota
        self.assertTrue(all(c >= 1 for c in couvertures),
                        "une entreprise se retrouve sans couverture Adzuna")

    def test_quota_nul_si_plus_d_appels(self):
        self.assertEqual(signaux_prives.quota_pays_adzuna(0, 10), 0)
        self.assertEqual(signaux_prives.quota_pays_adzuna(50, 0), 0)

    def test_quota_large_quand_peu_d_entreprises(self):
        # Peu d'entreprises : on peut interroger tous les portails.
        self.assertEqual(signaux_prives.quota_pays_adzuna(120, 2, maxi=7), 7)

    # -- Choix des portails ---------------------------------------------
    def test_colonne_pays_adzuna_prioritaire(self):
        compte = {"entreprise": "Acme", "pays_adzuna": "za, fr"}
        self.assertEqual(signaux_prives.pays_pour_compte(compte, 2), ["za", "fr"])

    def test_colonne_absente_repli_automatique(self):
        # Aucune saisie : on prend les premiers portails par defaut.
        pays = signaux_prives.pays_pour_compte({"entreprise": "Acme"}, 3)
        self.assertEqual(pays, list(signaux_prives.ADZUNA_PAYS)[:3])

    def test_quota_zero_ne_donne_aucun_portail(self):
        self.assertEqual(signaux_prives.pays_pour_compte({"pays_adzuna": "fr"}, 0), [])

    def test_pays_tronques_au_quota(self):
        compte = {"pays_adzuna": "fr,gb,za,de"}
        self.assertEqual(len(signaux_prives.pays_pour_compte(compte, 2)), 2)

    # -- La liste de pays est bien transmise a la collecte ---------------
    def test_collecter_adzuna_respecte_la_liste_fournie(self):
        appeles = []

        def faux(pays, params):
            appeles.append(pays)
            return {"results": []}

        signaux_prives.collecter_adzuna("Acme", fetch=faux, session=object(),
                                        pays=["fr", "za"])
        self.assertEqual(appeles, ["fr", "za"])

    def test_collecter_adzuna_sans_liste_garde_le_defaut(self):
        """Retro-compatibilite : pays=None conserve le comportement historique."""
        appeles = []

        def faux(pays, params):
            appeles.append(pays)
            return {"results": []}

        signaux_prives._ADZUNA_STATS.update({"appels": 0, "coupe": False})
        signaux_prives.collecter_adzuna("Acme", fetch=faux, session=object())
        self.assertEqual(appeles, list(signaux_prives.ADZUNA_PAYS))

    # -- Colonne optionnelle lue depuis la watchlist ---------------------
    def test_watchlist_lit_la_colonne_optionnelle(self):
        valeurs = [["entreprise", "secteur", "actif", "pays_adzuna"],
                   ["Sogea Satom", "BTP", "oui", "fr,za"]]
        comptes = signaux_prives.lire_watchlist_multisecteurs(valeurs)
        self.assertEqual(comptes[0]["pays_adzuna"], "fr,za")

    def test_watchlist_sans_la_colonne_reste_valide(self):
        """La colonne est OPTIONNELLE : son absence ne casse rien."""
        valeurs = [["entreprise", "secteur", "actif"],
                   ["Sogea Satom", "BTP", "oui"]]
        comptes = signaux_prives.lire_watchlist_multisecteurs(valeurs)
        self.assertEqual(comptes[0]["entreprise"], "Sogea Satom")
        self.assertEqual(comptes[0]["pays_adzuna"], "")
        self.assertEqual(signaux_prives.pays_pour_compte(comptes[0], 2),
                         list(signaux_prives.ADZUNA_PAYS)[:2])

    # -- Garde-temps ------------------------------------------------------
    def test_garde_temps_configurable(self):
        self.assertGreater(signaux_prives.MINUTES_MAX, 0)
        self.assertLess(signaux_prives.MINUTES_MAX, 45,
                        "le garde-temps doit rester sous le timeout du job (45 min)")


@unittest.skipIf(ted is None, "ted_complet_v14 indisponible")
class TestSanteModele(unittest.TestCase):
    """Detection d'une panne d'appels au modele. Sans elle, un modele retire
    ferait echouer tous les appels et le run se terminerait VERT en ne
    produisant plus aucune analyse (constate a l'audit : horizon de retrait de
    haiku-4-5 au 15/10/2026)."""

    def setUp(self):
        self._sauve = dict(ted.STATS_LLM)
        ted.STATS_LLM.update({"appels": 0, "echecs": 0,
                              "modele_invalide": 0, "detail": ""})

    def tearDown(self):
        ted.STATS_LLM.update(self._sauve)

    def test_run_sain(self):
        ted.STATS_LLM["appels"] = 50
        ok, msg = ted.sante_llm()
        self.assertTrue(ok)
        self.assertIn("aucun echec", msg)

    def test_quelques_echecs_tolerables(self):
        """Un avis rate n'est pas un incident : le pipeline degrade en silence."""
        ted.STATS_LLM.update({"appels": 100, "echecs": 5})
        ok, _ = ted.sante_llm()
        self.assertTrue(ok)

    def test_echec_massif_signale(self):
        ted.STATS_LLM.update({"appels": 100, "echecs": 95})
        ok, msg = ted.sante_llm()
        self.assertFalse(ok)
        self.assertIn("MASSIVEMENT", msg)

    def test_modele_retire_signale_immediatement(self):
        """Une erreur de MODELE doit alerter des le premier cas : elle exige
        une action (changer la chaine), pas de la patience."""
        ted.STATS_LLM["appels"] = 3
        ted._marquer_echec_llm(
            '{"type":"error","error":{"type":"not_found_error",'
            '"message":"model: claude-xxx"}}')
        ok, msg = ted.sante_llm()
        self.assertFalse(ok)
        self.assertIn("MODELE REFUSE", msg)
        self.assertIn("RADAR_MODELE", msg)

    def test_timeout_compte_mais_n_est_pas_une_erreur_de_modele(self):
        ted.STATS_LLM["appels"] = 3
        ted._marquer_echec_llm("timeout")
        self.assertEqual(ted.STATS_LLM["modele_invalide"], 0)

    def test_pas_d_alerte_sur_trop_peu_d_appels(self):
        """Deux echecs sur trois appels ne prouvent rien : on n'alerte pas."""
        ted.STATS_LLM.update({"appels": 3, "echecs": 3})
        ok, _ = ted.sante_llm()
        self.assertTrue(ok)

    def test_modeles_surchargeables_sans_toucher_au_code(self):
        """Le remede a un retrait doit etre une variable d'environnement."""
        self.assertTrue(ted.MODELE)
        self.assertTrue(ted.MODELE_RAFFINEMENT)


@unittest.skipIf(radar_dashboard is None, "radar_dashboard indisponible")
class TestExportCSV(unittest.TestCase):
    """Export "liste d'appels". La logique est en JavaScript dans le gabarit ;
    ces tests l'EXECUTENT reellement via Node (present sur ubuntu-latest, donc
    en CI) plutot que de se contenter de verifier des chaines de caracteres.
    Sans Node, ils sont ignores et le reste de la suite continue."""

    JS_TESTS = r"""
const m = require(process.argv[2]);
function eq(a, b, nom){
  if(a !== b) { console.error('ECHEC ' + nom + ' : ' + JSON.stringify(a) +
                              ' != ' + JSON.stringify(b)); process.exit(1); }
}
// Le point-virgule est le separateur : un titre qui en contient doit etre
// protege, sinon les colonnes se decalent dans Excel.
eq(m.csvChamp('Securite; convois'), '"Securite; convois"', 'point-virgule');
eq(m.csvChamp('convois "VIP"'), '"convois ""VIP""' + '"', 'guillemets');
eq(m.csvChamp('a\nb'), '"a\nb"', 'saut de ligne');
eq(m.csvChamp('Mali'), 'Mali', 'champ simple non quote');
eq(m.csvChamp(null), '', 'null');
eq(m.csvChamp(undefined), '', 'undefined');
// "n.c." ne doit jamais polluer une liste d'appels.
eq(m.nc('n.c.'), '', 'n.c. nettoye');
eq(m.nc('a@b.com'), 'a@b.com', 'valeur conservee');
const csv = m.exportAvis([{final:8,surete:7,comm:9,action:'contacter',
  win:'immediate',deadline:'2026-08-01',pays:'Mali',zone:'Sahel',agence:'AFD',
  titre:'Securite; convois',nom:'n.c.',email:'a@b.com',tel:'n.c.',
  statut:'nouveau',src:'TED',date_det:'2026-07-10',lien:'http://x'}]);
const L = csv.split('\r\n');
eq(L.length, 2, 'entete + une ligne');
eq(L[0].split(';')[0], 'Score', 'premiere colonne');
eq(L[1].indexOf('"Securite; convois"') > -1, true, 'titre protege');
eq(L[1].indexOf('n.c.') > -1, false, 'aucun n.c. exporte');
const f = m.exportFiches([{nom:'STECOL',prio:'contacter',n:3,zones:['Sahel'],
  secteurs:['BTP'],enr:{nom:'',email:'x@y.com',siren:'',ca:''},
  dernier:'2026-07-01',meilleur:{final:9,titre:'Route',lien:'http://z'}}]);
eq(f.split('\r\n')[0].split(';')[0], 'Entreprise', 'entete fiches');
eq(f.indexOf('Sahel') > -1, true, 'zones jointes');
console.log('OK');
"""

    def _extraire_js(self):
        src = radar_dashboard.GABARIT_HTML
        deb = src.index("function csvChamp(v){")
        fin = src.index("document.getElementById('export').addEventListener")
        return (src[deb:fin] +
                "\nmodule.exports={csvChamp,csvLignes,exportAvis,exportFiches,nc};\n")

    def test_structure_presente_dans_le_gabarit(self):
        html = radar_dashboard.GABARIT_HTML
        for attendu in ('id="export"', "function exporterCSV",
                        "function exportAvis", "function exportFiches",
                        "\\uFEFF"):          # BOM : sans lui, Excel casse les accents
            self.assertIn(attendu, html, "absent du gabarit : {}".format(attendu))

    def test_logique_javascript_reelle(self):
        import shutil, subprocess, tempfile, os as _os
        node = shutil.which("node")
        if not node:
            self.skipTest("Node absent de cet environnement")
        dossier = tempfile.mkdtemp()
        try:
            mod = _os.path.join(dossier, "export.js")
            tst = _os.path.join(dossier, "tests.js")
            with open(mod, "w", encoding="utf-8") as f:
                f.write(self._extraire_js())
            with open(tst, "w", encoding="utf-8") as f:
                f.write(self.JS_TESTS)
            r = subprocess.run([node, tst, mod], capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0,
                             "tests JS en echec :\n{}\n{}".format(r.stdout, r.stderr))
        finally:
            import shutil as _sh
            _sh.rmtree(dossier, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
