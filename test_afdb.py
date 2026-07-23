# -*- coding: utf-8 -*-
"""Tests du collecteur AfDB : parsing RSS, extraction type/pays (EN + FR),
filtrage perimetre, FENETRE DE FRAICHEUR, normalisation compatible coeur TED.
Aucun appel reseau ni LLM (fetch injecte, scoring teste via une extraction
simulee).

POURQUOI LES DATES SONT CALCULEES ET NON ECRITES EN DUR (23/07/2026)
--------------------------------------------------------------------
Meme defaut que celui qui a fait tomber `test_adb` le 23/07/2026 : le flux
simule portait des `pubDate` FIGEES (01 au 06 juillet 2026) alors que
`afdb.collecter_et_normaliser` filtre sur une fenetre GLISSANTE
(`datetime.now(utc) - AFDB_JOURS`). Verifie par simulation : au 05/08/2026 le
pipeline ne retenait plus qu'UN avis sur les quatre attendus. Le test serait
donc passe au rouge de lui-meme le **1er aout 2026**, sans aucune modification
de code -- et AfDB, contrairement a ADB, est un collecteur ACTIF.

Rappel du cout reel : dans `radar.yml`, l'etape "Lancer les tests" n'a ni
`continue-on-error` ni `if: always()`. Un test rouge fait echouer le job, ce qui
SAUTE la reconstitution de `service_account.json` et l'etape "Lancer le radar".
Une bombe a retardement dans un test est une panne de production differee.

REGLE POSEE : un fixture date se calcule TOUJOURS a partir de `date.today()` et
de la constante de fenetre du collecteur (`afdb.NB_JOURS_FENETRE`), jamais en
dur. Le test y gagne aussi en force : il verifie desormais EXPLICITEMENT que la
fenetre ecarte un avis perime (`TestFenetreFraicheur`), ce que l'ancienne
version ne testait pas -- elle le subissait.
"""

import unittest
from datetime import date, timedelta

import afdb_radar as afdb
import ted_complet_v14 as ted


# ===========================================================================
# DATES DU FIXTURE : calculees, jamais figees.
# ===========================================================================
# Jours et mois en dur : `strftime("%a"/"%b")` depend de la locale du runner,
# alors que le format RFC 822 attendu par `email.utils.parsedate_to_datetime`
# est anglais. On ne laisse pas la locale de l'hote decider du verdict.
JOURS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MOIS_EN = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")


def _pubdate(d):
    """date -> "Mon, 06 Jul 2026 09:00:00 +0000" (RFC 822, comme le vrai flux)."""
    return "{}, {:02d} {} {} 09:00:00 +0000".format(
        JOURS_EN[d.weekday()], d.day, MOIS_EN[d.month], d.year)


AUJOURDHUI = date.today()

# DANS la fenetre, quelle que soit la valeur de AFDB_JOURS (meme AFDB_JOURS=1).
DATE_RECENTE = AUJOURDHUI - timedelta(days=1)
# HORS fenetre par construction : cale sur la constante du collecteur, donc
# toujours juste meme si quelqu'un elargit AFDB_JOURS a 365.
DATE_PERIMEE = AUJOURDHUI - timedelta(days=afdb.NB_JOURS_FENETRE + 5)

RECENTE = _pubdate(DATE_RECENTE)
PERIMEE = _pubdate(DATE_PERIMEE)


# Flux RSS simule, representatif des cas reels observes. Sept items : quatre
# retenus, une attribution, un pays hors zone, et un avis PERIME sur un pays
# suivi (Mali) qui ne doit tomber QUE sur la fenetre de fraicheur.
FLUX_SIMULE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>AfDB Projects Procurement</title>
  <item>
    <title>GPN - Rwanda - Muvumba Multipurpose Water Resources Development Program</title>
    <link>https://www.afdb.org/en/notice/gpn-rwanda-1</link>
    <pubDate>{recente}</pubDate>
    <description>The Government of Rwanda has received financing... &lt;b&gt;field&lt;/b&gt; supervision.</description>
  </item>
  <item>
    <title>AMI - Djibouti - Recrutement d'un Consultant International pour appui terrain</title>
    <link>https://www.afdb.org/fr/notice/ami-djibouti-2</link>
    <pubDate>{recente}</pubDate>
    <description>La Republique de Djibouti a obtenu un don...</description>
  </item>
  <item>
    <title>EOI - Eritrea - Individual Consultant for Massawa to Tesseney Road</title>
    <link>https://www.afdb.org/en/notice/eoi-eritrea-3</link>
    <pubDate>{recente}</pubDate>
    <description>Feasibility study, detailed engineering design.</description>
  </item>
  <item>
    <title>PPM - Multinational - Guinee-Guinee Bissau - Projet route Boke-Quebo</title>
    <link>https://www.afdb.org/fr/notice/ppm-multi-4</link>
    <pubDate>{recente}</pubDate>
    <description>Amenagement de la route.</description>
  </item>
  <item>
    <title>Contract Awards - Senior Capital Markets Operations Specialist</title>
    <link>https://www.afdb.org/en/notice/award-5</link>
    <pubDate>{recente}</pubDate>
    <description>Attribution.</description>
  </item>
  <item>
    <title>SPN - France - Internal office supplies</title>
    <link>https://www.afdb.org/en/notice/spn-france-6</link>
    <pubDate>{recente}</pubDate>
    <description>Hors zone a risque.</description>
  </item>
  <item>
    <title>GPN - Mali - Programme d'appui au secteur routier (avis ancien)</title>
    <link>https://www.afdb.org/fr/notice/gpn-mali-7</link>
    <pubDate>{perimee}</pubDate>
    <description>Avis publie hors fenetre de fraicheur.</description>
  </item>
</channel></rss>""".format(recente=RECENTE, perimee=PERIMEE)


class TestParsingTitre(unittest.TestCase):
    def test_type_pays_reste(self):
        t, p, r = afdb.parser_titre("GPN - Rwanda - Muvumba Water Program")
        self.assertEqual((t, p), ("GPN", "Rwanda"))
        self.assertEqual(r, "Muvumba Water Program")

    def test_pays_compose_non_coupe(self):
        # 'Cote d'Ivoire' ne doit pas etre coupe (pas de ' - ' interne).
        t, p, r = afdb.parser_titre("EOI - Cote d'Ivoire - Etude filiere")
        self.assertEqual(p, "Cote d'Ivoire")

    def test_type_notice_amont_vs_tender(self):
        self.assertEqual(afdb.type_notice("GPN")[1], "amont")
        self.assertEqual(afdb.type_notice("EOI")[1], "amont")
        self.assertEqual(afdb.type_notice("SPN")[1], "tender")

    def test_attribution_detectee(self):
        self.assertTrue(afdb.type_notice("Contract Awards")[2])
        self.assertTrue(afdb.type_notice("Attribution de contrat")[2])


class TestResolutionPays(unittest.TestCase):
    def test_nom_anglais(self):
        self.assertEqual(afdb.resoudre_iso3("Rwanda", "GPN - Rwanda - X"), "RWA")

    def test_nom_francais_avec_accent(self):
        # 'Guinee' (sans accent) et 'Guinée' (avec) doivent mapper pareil.
        self.assertEqual(afdb.resoudre_iso3("Guinée", "EOI - Guinée - X"), "GIN")

    def test_niger_pas_confondu_avec_nigeria(self):
        self.assertEqual(afdb.resoudre_iso3("Niger", "GPN - Niger - X"), "NER")
        self.assertEqual(afdb.resoudre_iso3("Nigeria", "GPN - Nigeria - X"), "NGA")

    def test_multinational_repli_scan_titre(self):
        # 'Multinational' inconnu -> on scanne le titre, 'Guinee Bissau' trouve.
        iso = afdb.resoudre_iso3("Multinational",
                                 "PPM - Multinational - Guinee-Guinee Bissau - route")
        self.assertIn(iso, ("GNB", "GIN"))  # un pays a risque reconnu dans le titre

    def test_pays_inconnu_renvoie_vide(self):
        self.assertEqual(afdb.resoudre_iso3("Atlantis", "GPN - Atlantis - X"), "")


class TestPipelineComplet(unittest.TestCase):
    def setUp(self):
        self.avis, self.stats = afdb.collecter_et_normaliser(fetch=lambda: FLUX_SIMULE)

    def test_parse_tous_les_items(self):
        # Le PARSING ne filtre rien : les 7 items doivent etre vus, y compris
        # le perime (c'est la collecte qui l'ecartera ensuite).
        self.assertEqual(self.stats["items"], 7)

    def test_attribution_et_france_exclues(self):
        titres = " ".join(a["titre"] for a in self.avis)
        self.assertNotIn("Contract Awards", titres)   # attribution exclue
        self.assertNotIn("France", titres)            # hors zone a risque exclue

    def test_retient_les_bons(self):
        pays = sorted(a["pays_execution"] for a in self.avis)
        # Rwanda, Djibouti, Eritrea, (Multinational -> Guinee ou Guinee-Bissau)
        self.assertIn("RWA", pays)
        self.assertIn("DJI", pays)
        self.assertIn("ERI", pays)
        self.assertEqual(len(self.avis), 4)

    def test_avis_compatible_coeur_ted(self):
        # Un avis normalise doit passer dans ted.calculer_scores sans erreur,
        # avec une extraction simulee (pas d'appel LLM).
        avis = self.avis[0]
        for cle in ("acheteur", "pays_execution", "titre", "cpv", "description"):
            self.assertIn(cle, avis)
        extraction = {
            "deploiement_terrain_reel": True, "type_mobilite": "terrain_isole",
            "profil_personnes_exposees": "expert_international",
            "securite_existante_detectee": False, "type_client": "bailleur_donateur",
            "accessibilite_commerciale": "moyenne", "duree_estimee": "longue_ou_residente",
            "niveau_opportunite_amarante": "fort", "confiance": 0.8,
        }
        surete, commercial, final = ted.calculer_scores(avis, extraction)
        self.assertGreater(final, 0.0)
        self.assertLessEqual(final, 10.0)

    def test_html_nettoye_dans_description(self):
        rwa = [a for a in self.avis if a["pays_execution"] == "RWA"][0]
        self.assertNotIn("<b>", rwa["description"])

    def test_type_notice_et_phase_presents(self):
        for a in self.avis:
            self.assertTrue(a["type_notice"])
            self.assertIn(a["phase"], ("amont", "tender"))


class TestFenetreFraicheur(unittest.TestCase):
    """Le filtre de fraicheur, teste EXPLICITEMENT.

    C'est le test qui manquait : l'ancienne version subissait la fenetre sans
    jamais l'affirmer, si bien que le jour ou elle aurait mordu sur un cas
    legitime, l'echec n'aurait rien appris -- il aurait juste casse le build."""

    def setUp(self):
        self.avis, self.stats = afdb.collecter_et_normaliser(fetch=lambda: FLUX_SIMULE)

    def test_avis_perime_ecarte(self):
        # Le Mali (MLI) est un pays a risque suivi et l'avis est un GPN (donc
        # ni attribution, ni hors zone) : s'il n'apparait pas, c'est bien la
        # fenetre de fraicheur qui l'a ecarte, et rien d'autre.
        self.assertIn("MLI", ted.CODES_PAYS_SUIVIS)
        self.assertNotIn("MLI", [a["pays_execution"] for a in self.avis])

    def test_avis_recent_conserve(self):
        # Meme item, meme pays, meme type : seule la date change. Le contraste
        # isole la fenetre comme unique cause de l'exclusion.
        flux_frais = FLUX_SIMULE.replace(PERIMEE, RECENTE)
        avis, _ = afdb.collecter_et_normaliser(fetch=lambda: flux_frais)
        self.assertIn("MLI", [a["pays_execution"] for a in avis])

    def test_fixture_reste_dans_la_fenetre(self):
        # Garde-fou anti-recidive : si un jour quelqu'un rebascule une date en
        # dur dans ce fichier, cette assertion le dit tout de suite, avec un
        # message clair, plutot que de laisser un test metier tomber au hasard.
        seuil = AUJOURDHUI - timedelta(days=afdb.NB_JOURS_FENETRE)
        self.assertGreaterEqual(
            DATE_RECENTE, seuil,
            "Le fixture 'recent' est sorti de la fenetre : les dates de test "
            "doivent etre calculees depuis date.today(), jamais ecrites en dur.")
        self.assertLess(
            DATE_PERIMEE, seuil,
            "Le fixture 'perime' est entre dans la fenetre : il ne teste plus rien.")


if __name__ == "__main__":
    unittest.main()
