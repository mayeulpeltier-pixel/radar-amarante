# -*- coding: utf-8 -*-
"""Sonde declencheurs IE (IE-7) : parsing de la page CJIP (structure relevee
le 30/10/2026 sur justice.gouv.fr), lecture des conventions, flux presse et
verdicts. Offline : page, PDF et RSS sont injectes."""

import datetime
import unittest

import sonde_declencheurs_ie as S

AUJ = datetime.date(2026, 10, 30)

PAGE = """
<h2>Les conventions</h2>
<ul>
<li><h3>FINANCIER - Convention judiciaire d&rsquo;int&eacute;r&ecirc;t public valid&eacute;e le
 18 f&eacute;vrier 2026 par le pr&eacute;sident du tribunal judiciaire de Paris (75) et conclue
 entre le procureur de la R&eacute;publique financier et la soci&eacute;t&eacute; PERIPHERIQUES ET MATERIELS DE CONTROLE SAS</h3>
<ul>
<li><a href="/sites/default/files/2026-03/CJIP_PERIPHERIQUES_MATERIELS%20CONTROLE_SAS_20260210.pdf">10/02/2026 - Convention - PMC PDF - 300 Ko</a></li>
<li><a href="https://www.justice.gouv.fr/sites/default/files/2026-03/CJIP_PMC_OV_20260218.pdf">18/02/2026 - Ordonnance de validation - PMC PDF</a></li>
<li><a href="https://www.justice.gouv.fr/sites/default/files/2026-03/CJIP_PMC_CP_20260218.pdf">18/02/2026 - Communiqu&eacute; de presse - PMC PDF</a></li>
</ul></li>
<li><h3>ENVIRONNEMENT - Convention judiciaire d'int&eacute;r&ecirc;t public en mati&egrave;re environnementale
 valid&eacute;e le 1er ao&ucirc;t 2026 par le pr&eacute;sident du tribunal judiciaire de Metz (57)
 et conclue entre le procureur et la soci&eacute;t&eacute; L'Anhydrite Lorraine</h3>
<ul><li><a href="https://www.justice.gouv.fr/sites/default/files/2026-10/CJIP_L%27Anhydrite_20260824.pdf">24/08/2026 - Convention - L'Anhydrite PDF</a></li></ul></li>
<li><h3>FINANCIER - Convention judiciaire d'int&eacute;r&ecirc;t public valid&eacute;e le 5 d&eacute;cembre 2023
 par le pr&eacute;sident du tribunal judiciaire de Paris et conclue entre le PRF et la soci&eacute;t&eacute; ANCIENNE SA</h3>
<ul><li><a href="https://www.justice.gouv.fr/sites/default/files/2023-12/CJIP_ANCIENNE_20231201.pdf">Convention</a></li></ul></li>
</ul>
<h2>Autre rubrique</h2>
"""

TEXTE_CONVENTION = ("La societe s'engage a mettre en oeuvre un programme de mise en "
                    "conformite d'une duree de trois ans sous le controle de l'Agence "
                    "francaise anticorruption.")

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>CJIP : la societe X paie 5 MEUR - Le Monde</title><link>http://a/1</link>
<pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate><source>Le Monde</source></item>
<item><title>CJIP : la societe X paie 5 MEUR - Les Echos</title><link>http://a/2</link>
<pubDate>Mon, 26 Oct 2026 11:00:00 GMT</pubDate><source>Les Echos</source></item>
<item><title>L'AFA publie son rapport annuel - AFP</title><link>http://a/3</link>
<pubDate>Tue, 27 Oct 2026 10:00:00 GMT</pubDate><source>AFP</source></item>
</channel></rss>"""


class TestPageCJIP(unittest.TestCase):

    def test_entrees_domaines_et_dates(self):
        e = S.parser_page_cjip(PAGE)
        self.assertEqual(len(e), 3)
        self.assertEqual([x["domaine"] for x in e], ["FINANCIER", "ENVIRONNEMENT", "FINANCIER"])
        self.assertEqual(e[0]["validation"], datetime.date(2026, 2, 18))
        self.assertEqual(e[1]["validation"], datetime.date(2026, 8, 1))   # "1er aout"

    def test_societe_et_documents(self):
        e = S.parser_page_cjip(PAGE)[0]
        self.assertEqual(e["societe"], "PERIPHERIQUES ET MATERIELS DE CONTROLE SAS")
        self.assertEqual([p["type"] for p in e["pdfs"]], ["convention", "ordonnance", "communique"])
        self.assertTrue(e["pdfs"][0]["url"].startswith("https://www.justice.gouv.fr/sites/"))
        self.assertEqual(e["mise_en_ligne"], datetime.date(2026, 3, 1))

    def test_page_sans_cjip(self):
        self.assertEqual(S.parser_page_cjip("<h2>Rien</h2><p>vide</p>"), [])


class TestConvention(unittest.TestCase):

    def test_programme_afa_et_duree(self):
        a = S.analyser_convention(TEXTE_CONVENTION)
        self.assertEqual(a, {"afa": True, "programme": True, "duree_mois": 36})

    def test_duree_en_chiffres_et_en_mois(self):
        a = S.analyser_convention("programme de mise en conformite pour une duree de 18 mois, AFA")
        self.assertEqual(a["duree_mois"], 18)

    def test_sans_programme(self):
        a = S.analyser_convention("Amende d'interet public de 2 millions d'euros.")
        self.assertEqual(a, {"afa": False, "programme": False, "duree_mois": None})


class TestSondeCJIP(unittest.TestCase):

    def setUp(self):
        S.PAUSE = 0.0

    def test_seules_les_financieres_recentes_sont_lues(self):
        lus = []

        def get(url, binaire=False):
            if not binaire:
                return PAGE
            lus.append(url)
            return b"pdf"

        entrees, erreurs = S.sonder_cjip(AUJ, get=get, lire_pdf=lambda b: TEXTE_CONVENTION)
        self.assertEqual(erreurs, [])
        self.assertEqual(len(entrees), 2)          # 2023 hors fenetre de 2 ans
        self.assertEqual(len(lus), 1)              # environnementale non lue
        st = S.stats_cjip(entrees)
        self.assertEqual(len(st["avec_programme"]), 1)
        self.assertEqual(S.verdict_cjip(st)[0], "MARGINAL")

    def test_page_injoignable_non_conclu(self):
        def get(url, binaire=False):
            raise RuntimeError("403")

        entrees, erreurs = S.sonder_cjip(AUJ, get=get)
        self.assertEqual(entrees, [])
        self.assertEqual(S.verdict_cjip(S.stats_cjip(entrees))[0], "NON CONCLU")

    def test_pdf_illisible_n_arrete_pas(self):
        def get(url, binaire=False):
            return PAGE if not binaire else b"x"

        def lire(b):
            raise ValueError("PDF corrompu")

        entrees, erreurs = S.sonder_cjip(AUJ, get=get, lire_pdf=lire)
        self.assertEqual(len(erreurs), 1)
        self.assertEqual(len(entrees), 2)


class TestPresse(unittest.TestCase):

    def setUp(self):
        S.PAUSE = 0.0

    def test_dedoublonnage_par_titre_sans_source(self):
        res, err = S.sonder_presse(get=lambda url: RSS, requetes=["cjip"])
        self.assertEqual(err, [])
        self.assertEqual(len(res["cjip"]), 2)

    def test_requete_en_erreur_n_arrete_pas(self):
        def get(url):
            if "AFA" in url:
                raise RuntimeError("503")
            return RSS

        res, err = S.sonder_presse(get=get, requetes=["AFA", "cjip"])
        self.assertEqual(len(err), 1)
        self.assertIn("cjip", res)
        self.assertEqual(S.verdict_presse(res)[0], "A TRIER")
        self.assertEqual(S.verdict_presse({})[0], "NON CONCLU")

    def test_rapport_complet(self):
        def get(url, binaire=False):
            return PAGE if not binaire else b"pdf"

        cj, cj_err = S.sonder_cjip(AUJ, get=get, lire_pdf=lambda b: TEXTE_CONVENTION)
        presse, p_err = S.sonder_presse(get=lambda url: RSS, requetes=["cjip"])
        md = S.rapport(cj, cj_err, presse, p_err, AUJ)
        self.assertIn("## 1. CJIP : MARGINAL", md)
        self.assertIn("PERIPHERIQUES ET MATERIELS DE CONTROLE SAS", md)
        self.assertIn("36 mois", md)


if __name__ == "__main__":
    unittest.main()
