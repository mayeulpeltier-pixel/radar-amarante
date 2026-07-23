# -*- coding: utf-8 -*-
"""Tests ADB : parsing de la page tenders (format reel colle par l'utilisateur),
resolution pays via Country/Economy en clair, filtrage hors-zone, FENETRE DE
FRAICHEUR, garde-fou JS, compatibilite coeur TED. Aucun appel reseau ni LLM.

POURQUOI LES DATES SONT CALCULEES ET NON ECRITES EN DUR (23/07/2026)
--------------------------------------------------------------------
La version precedente de ce fichier portait des dates FIGEES ("22 Jun 2026")
alors que `adb.collecter_et_normaliser` filtre sur une fenetre GLISSANTE
(`date.today() - ADB_JOURS`). Le 23/07/2026, la notice Papouasie-Nouvelle-Guinee
est sortie de la fenetre toute seule : le test est passe au rouge SANS qu'une
seule ligne de code ait bouge.

Consequence en production : dans `radar.yml`, l'etape "Lancer les tests" n'a ni
`continue-on-error` ni `if: always()`. Un test rouge fait echouer le job, ce qui
SAUTE la reconstitution de `service_account.json` et l'etape "Lancer le radar" :
plus aucune collecte. Une bombe a retardement dans un test est donc une panne de
production differee.

REGLE POSEE : un fixture date se calcule TOUJOURS a partir de `date.today()` et
de la constante de fenetre du collecteur (`adb.NB_JOURS_FENETRE`), jamais en dur.
Le test devient de surcroit PLUS fort qu'avant : il verifie explicitement que la
fenetre de fraicheur ecarte bien une notice perimee (`TestFenetreFraicheur`), ce
que l'ancienne version ne testait pas -- elle le subissait.
"""

import unittest
from datetime import date, timedelta

import adb_radar as adb
import ted_complet_v14 as ted


# ===========================================================================
# DATES DU FIXTURE : calculees, jamais figees.
# ===========================================================================
# Abreviations de mois en dur : `strftime("%b")` depend de la locale du runner,
# alors que `adb._date_iso` attend l'anglais ("%d %b %Y"). On ne laisse pas la
# locale de l'hote decider si le test passe.
MOIS_EN = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")


def _date_adb(d):
    """date -> "30 Jun 2026", le format exact de la colonne Posting Date."""
    return "{:02d} {} {}".format(d.day, MOIS_EN[d.month], d.year)


AUJOURDHUI = date.today()

# DANS la fenetre, quelle que soit la valeur de ADB_JOURS (meme ADB_JOURS=1).
DATE_RECENTE = AUJOURDHUI - timedelta(days=1)
# HORS fenetre par construction : on se cale sur la constante du collecteur,
# donc le test reste juste meme si quelqu'un elargit ADB_JOURS a 365.
DATE_PERIMEE = AUJOURDHUI - timedelta(days=adb.NB_JOURS_FENETRE + 5)
# Echeance a venir (les avis retenus doivent avoir une date limite credible).
DATE_ECHEANCE = AUJOURDHUI + timedelta(days=40)

RECENTE = _date_adb(DATE_RECENTE)
PERIMEE = _date_adb(DATE_PERIMEE)
ECHEANCE = _date_adb(DATE_ECHEANCE)


# Format REEL de la page tenders (colle depuis adb.org), enrichi de cas de
# controle : Afghanistan (suivi), Viet Nam (hors zone), Regional (sans pays),
# Nepal (pays SUIVI mais notice PERIMEE -> doit tomber sur la seule fenetre).
PAGE_REELLE = """Showing 1 - 12 of 50079 results for "*"
Sort ByRelevanceDeadlineDate Posted
Status: ActiveDeadline: {echeance}
[58321-001: L4741-SRI: Mahaweli Water Security Investment Program (MWSIP) Stage 2 Project](https://www.adb.org/sites/default/files/tenders/sri4741-ifb-ncpcp-5b.pdf)
Country/Economy: Sri LankaSector: Agriculture, natural resources and rural developmentPosting Date: {recente}
Notice Type:Invitation for BidsApproval Number:4741
Status: ActiveDeadline: {echeance}
[58040-001: 58040-001 PNG - Urban Water Supply and Sanitation Security and Resilience Improvement Project [UWSSSRIP-Plant-01] [Extended]](https://www.adb.org/sites/default/files/tenders/png58040-001-uwsssrip-plant-01-ifb-ext.pdf)
Country/Economy: Papua New GuineaSector: Water and other urban infrastructure and servicesPosting Date: {recente}
Notice Type:Invitation for BidsApproval Number:4802
Status: ActiveDeadline: {echeance}
[59111-002: L1234-AFG: Kabul Resilient Infrastructure - Prequalification of Contractors](https://www.adb.org/sites/default/files/tenders/afg59111-pq.pdf)
Country/Economy: AfghanistanSector: TransportPosting Date: {recente}
Notice Type:PrequalificationApproval Number:1234
Status: ActiveDeadline: {echeance}
[60222-001: L5678-VIE: Ho Chi Minh Urban Rail - Individual Consultant](https://www.adb.org/sites/default/files/tenders/vie60222.pdf)
Country/Economy: Viet NamSector: TransportPosting Date: {recente}
Notice Type:Individual - ConsultingApproval Number:5678
Status: ActiveDeadline: {echeance}
[61333-001: Regional Capacity Building - Firm Consulting](https://www.adb.org/sites/default/files/tenders/reg61333.pdf)
Country/Economy: RegionalSector: Public sector managementPosting Date: {recente}
Notice Type:Firm - ConsultingApproval Number:9999
Status: ActiveDeadline: {echeance}
[62444-001: L9012-NEP: Kathmandu Valley Water Supply - Old Notice](https://www.adb.org/sites/default/files/tenders/nep62444.pdf)
Country/Economy: NepalSector: Water and other urban infrastructure and servicesPosting Date: {perimee}
Notice Type:Invitation for BidsApproval Number:9012
""".format(recente=RECENTE, perimee=PERIMEE, echeance=ECHEANCE)


class TestTexteBrut(unittest.TestCase):
    def test_ancre_pdf_html_preservee(self):
        html = '<a href="https://x.org/a.pdf">Titre Projet</a> Country/Economy: Nepal'
        t = adb._texte_brut(html)
        self.assertIn("[Titre Projet](https://x.org/a.pdf)", t)

    def test_balises_retirees(self):
        self.assertNotIn("<div>", adb._texte_brut("<div>x</div>"))


class TestParsingNotices(unittest.TestCase):
    def setUp(self):
        self.notices = adb.parser_notices(PAGE_REELLE)

    def test_nombre_notices(self):
        # Le PARSING ne filtre rien : les 6 notices doivent etre vues, y
        # compris la perimee (c'est la collecte qui l'ecartera ensuite).
        self.assertEqual(len(self.notices), 6)

    def test_pays_en_clair(self):
        pays = [n["pays_clair"] for n in self.notices]
        self.assertIn("Sri Lanka", pays)
        self.assertIn("Papua New Guinea", pays)
        self.assertIn("Nepal", pays)

    def test_reference_extraite(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertEqual(sri["reference"], "58321-001")

    def test_lien_pdf_extrait(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertTrue(sri["lien"].endswith("sri4741-ifb-ncpcp-5b.pdf"))

    def test_deadline_et_posting(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertEqual(sri["deadline"], ECHEANCE)
        self.assertEqual(sri["posting_date"], RECENTE)

    def test_type_notice_texte(self):
        sri = [n for n in self.notices if n["pays_clair"] == "Sri Lanka"][0]
        self.assertIn("Invitation for Bids", sri["type_notice_txt"])


class TestResolutionPays(unittest.TestCase):
    def test_pays_suivi(self):
        self.assertEqual(adb.resoudre_iso3("Sri Lanka")[0], "LKA")
        self.assertEqual(adb.resoudre_iso3("Papua New Guinea")[0], "PNG")
        self.assertEqual(adb.resoudre_iso3("Afghanistan")[0], "AFG")

    def test_hors_zone_signale(self):
        iso, hz = adb.resoudre_iso3("Viet Nam")
        self.assertEqual(iso, "")
        self.assertEqual(hz, "Viet Nam")

    def test_regional_ignore(self):
        self.assertEqual(adb.resoudre_iso3("Regional"), ("", ""))


class TestTypeNotice(unittest.TestCase):
    def test_amont(self):
        self.assertEqual(adb.type_notice("Prequalification")[1], "amont")
        self.assertEqual(adb.type_notice("Advance notice")[1], "amont")

    def test_tender(self):
        self.assertEqual(adb.type_notice("Invitation for Bids")[1], "tender")
        self.assertEqual(adb.type_notice("Individual - Consulting")[1], "tender")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.avis, self.stats = adb.collecter_et_normaliser(
            fetch=lambda url=None: PAGE_REELLE)

    def test_garde_fou_non_declenche(self):
        self.assertFalse(self.stats["page_vide"])

    def test_retient_zones_suivies(self):
        pays = sorted(a["pays_execution"] for a in self.avis)
        # Sri Lanka, PNG, Afghanistan gardes ; Viet Nam (hors zone), Regional
        # (sans pays) et Nepal (perime) exclus.
        self.assertEqual(pays, ["AFG", "LKA", "PNG"])

    def test_vietnam_signale(self):
        self.assertIn("Viet Nam", self.stats["pays_hors_zone"])

    def test_afghanistan_est_amont(self):
        afg = [a for a in self.avis if a["pays_execution"] == "AFG"][0]
        self.assertEqual(afg["phase"], "amont")   # Prequalification

    def test_deadline_convertie_iso(self):
        sri = [a for a in self.avis if a["pays_execution"] == "LKA"][0]
        self.assertEqual(sri["deadline"], DATE_ECHEANCE.isoformat())

    def test_reference_comme_publication_number(self):
        sri = [a for a in self.avis if a["pays_execution"] == "LKA"][0]
        self.assertEqual(sri["publication_number"], "58321-001")

    def test_avis_compatible_coeur_ted(self):
        avis = self.avis[0]
        extraction = {
            "deploiement_terrain_reel": True, "type_mobilite": "terrain_isole",
            "profil_personnes_exposees": "expert_international",
            "securite_existante_detectee": False, "type_client": "bailleur_donateur",
            "accessibilite_commerciale": "moyenne", "duree_estimee": "moyenne",
            "niveau_opportunite_amarante": "moyen", "confiance": 0.8,
        }
        s, c, f = ted.calculer_scores(avis, extraction)
        self.assertGreaterEqual(f, 0.0)
        self.assertLessEqual(f, 10.0)


class TestFenetreFraicheur(unittest.TestCase):
    """Le filtre de fraicheur, teste EXPLICITEMENT.

    C'est le test qui manquait : l'ancienne version subissait la fenetre sans
    jamais l'affirmer, si bien que le jour ou elle a mordu sur un cas legitime,
    l'echec n'a rien appris -- il a juste casse le build."""

    def setUp(self):
        self.avis, self.stats = adb.collecter_et_normaliser(
            fetch=lambda url=None: PAGE_REELLE)

    def test_notice_perimee_ecartee(self):
        # Nepal est un pays SUIVI (NPL) : s'il n'apparait pas, c'est bien la
        # fenetre de fraicheur qui l'a ecarte, et rien d'autre.
        self.assertIn("NPL", ted.CODES_PAYS_SUIVIS)
        self.assertNotIn("NPL", [a["pays_execution"] for a in self.avis])

    def test_notice_recente_conservee(self):
        # Meme secteur, meme type de notice, meme pays suivi : seule la date
        # change. Le contraste isole la fenetre comme unique cause.
        page_fraiche = PAGE_REELLE.replace(PERIMEE, RECENTE)
        avis, _ = adb.collecter_et_normaliser(fetch=lambda url=None: page_fraiche)
        self.assertIn("NPL", [a["pays_execution"] for a in avis])

    def test_fixture_reste_dans_la_fenetre(self):
        # Garde-fou anti-recidive : si un jour quelqu'un rebascule une date en
        # dur dans ce fichier, cette assertion le dit tout de suite, avec un
        # message clair, plutot que de laisser un test metier tomber au hasard.
        seuil = AUJOURDHUI - timedelta(days=adb.NB_JOURS_FENETRE)
        self.assertGreaterEqual(
            DATE_RECENTE, seuil,
            "Le fixture 'recent' est sorti de la fenetre : les dates de test "
            "doivent etre calculees depuis date.today(), jamais ecrites en dur.")
        self.assertLess(
            DATE_PERIMEE, seuil,
            "Le fixture 'perime' est entre dans la fenetre : il ne teste plus rien.")


class TestGardeFouJS(unittest.TestCase):
    def test_page_vide_declenche_garde_fou(self):
        # Page rendue en JS = pas de notices -> garde-fou.
        avis, stats = adb.collecter_et_normaliser(
            fetch=lambda url=None: "<html><body><div id='app'></div></body></html>")
        self.assertTrue(stats["page_vide"])
        self.assertEqual(avis, [])


if __name__ == "__main__":
    unittest.main()
