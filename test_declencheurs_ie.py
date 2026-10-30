# -*- coding: utf-8 -*-
"""Declencheurs IE (IE-7, CJIP) : page, lecture des PDF, statut, memoire,
lignes Sheet et Postgres. Offline : page et PDF injectes. Les cas reprennent
les resultats reels de la sonde IE-7 du 30/10/2026."""

import datetime
import unittest

import declencheurs_ie as D

AUJ = datetime.date(2026, 10, 30)

PAGE = """
<h2>Les conventions</h2>
<ul>
<li><h3>FINANCIER - Convention judiciaire d'int&eacute;r&ecirc;t public valid&eacute;e le 18 f&eacute;vrier 2026
 par le pr&eacute;sident du tribunal judiciaire de Paris (75) et conclue entre le procureur de la
 R&eacute;publique financier et la soci&eacute;t&eacute; PERIPHERIQUES ET MATERIELS DE CONTROLE SAS</h3>
<ul>
<li><a href="/sites/default/files/2026-03/CJIP_PMC_20260210.pdf">Convention - PMC PDF</a></li>
<li><a href="https://www.justice.gouv.fr/sites/default/files/2026-03/CJIP_PMC_OV_20260218.pdf">Ordonnance</a></li>
<li><a href="https://www.justice.gouv.fr/sites/default/files/2026-02/CJIP_PMC_CP_20260218.pdf">Communiqu&eacute; de presse</a></li>
</ul></li>
<li><h3>FINANCIER - Convention judiciaire d'int&eacute;r&ecirc;t public valid&eacute;e le 8 janvier 2026
 par le pr&eacute;sident du tribunal judiciaire de Paris (75) et conclue entre le PRF et HSBC Bank plc</h3>
<ul>
<li><a href="https://www.justice.gouv.fr/sites/default/files/2026-01/CJIP_HSBC_20260106.pdf">Convention</a></li>
<li><a href="https://www.justice.gouv.fr/sites/default/files/2026-01/CJIP_HSBC_CP_20260108.pdf">Communiqu&eacute;</a></li>
</ul></li>
<li><h3>ENVIRONNEMENT - Convention judiciaire d'int&eacute;r&ecirc;t public en mati&egrave;re
 environnementale valid&eacute;e le 3 septembre 2026 par le pr&eacute;sident du tribunal judiciaire
 de Metz (57) et conclue entre le procureur et la soci&eacute;t&eacute; L'Anhydrite Lorraine</h3>
<ul><li><a href="https://www.justice.gouv.fr/sites/default/files/2026-10/CJIP_ANH_20260824.pdf">Convention</a></li></ul></li>
<li><h3>FINANCIER - Convention judiciaire d'int&eacute;r&ecirc;t public valid&eacute;e le 4 mars 2022
 par le pr&eacute;sident du tribunal judiciaire de Paris (75) et conclue entre le PRF et la soci&eacute;t&eacute; VIEILLE SA</h3>
<ul><li><a href="https://www.justice.gouv.fr/sites/default/files/2022-03/CJIP_VIEILLE.pdf">Convention</a></li></ul></li>
</ul>"""

PADDING = " Considerant les faits de l'espece." * 30
TEXTE_PMC = ("La CJIP prevoit enfin la mise en place d'un programme de mise en\n"
             "con-\nformite d'une duree de trois ans sous le controle de l'Agence francaise\n"
             "anticorruption." + PADDING)
TEXTE_FISCAL = "Fraude fiscale aggravee. Amende d'interet public de 300 millions." + PADDING


def get(url, binaire=False):
    return PAGE if not binaire else url.encode()


def lire(contenu):
    url = contenu.decode()
    if "SCAN" in url:
        return ""
    return TEXTE_PMC if "PMC" in url else TEXTE_FISCAL


class TestPage(unittest.TestCase):

    def test_entrees_et_champs(self):
        e = D.parser_page(PAGE)
        self.assertEqual([x["domaine"] for x in e],
                         ["FINANCIER", "FINANCIER", "ENVIRONNEMENT", "FINANCIER"])
        pmc = e[0]
        self.assertEqual(pmc["societe"], "PERIPHERIQUES ET MATERIELS DE CONTROLE SAS")
        self.assertEqual(pmc["validation"], datetime.date(2026, 2, 18))
        self.assertEqual(pmc["juridiction"], "Paris (75)")
        self.assertEqual(set(pmc["pdfs"]), {"convention", "ordonnance", "communique"})
        self.assertTrue(pmc["cle"].startswith("https://www.justice.gouv.fr/sites/"))
        self.assertEqual(e[1]["societe"], "HSBC Bank plc")


class TestLectureEtStatut(unittest.TestCase):

    def test_texte_pdf_coupe(self):
        self.assertEqual(D.analyser_texte(TEXTE_PMC),
                         {"afa": True, "programme": True, "duree_mois": 36})

    def test_statuts(self):
        def e(analyse):
            return {"analyse": analyse}
        self.assertEqual(D.statut(e({"programme": True, "afa": True})), "programme_afa")
        self.assertEqual(D.statut(e({"programme": False, "afa": True})), "a_verifier")
        self.assertEqual(D.statut(e({"programme": True, "afa": False})), "a_verifier")
        self.assertEqual(D.statut(e({"programme": False, "afa": False})), "sans_programme")
        self.assertEqual(D.statut(e(None)), "illisible")

    def test_scan_illisible_jamais_lu_comme_sans_programme(self):
        entree = {"societe": "X", "pdfs": {"communique": "SCAN1", "convention": "SCAN2"}}
        D.lire_cjip(entree, get=get, lire_pdf=lire, pause=0)
        self.assertEqual(entree["lecture"], "illisible")
        self.assertEqual(D.statut(entree), "illisible")

    def test_fin_du_suivi_afa(self):
        e = {"validation": datetime.date(2026, 2, 18), "analyse": {"duree_mois": 36}}
        self.assertEqual(D.fin_suivi(e), datetime.date(2029, 2, 18))


class TestExecution(unittest.TestCase):

    def test_financieres_recentes_seulement_et_statuts(self):
        nouvelles, info = D.executer(AUJ, get=get, lire_pdf=lire, pause=0)
        self.assertEqual(info["financieres"], 2)          # 2022 hors 36 mois, ENV exclue
        par_societe = {e["societe"]: e for e in nouvelles}
        self.assertEqual(par_societe["PERIPHERIQUES ET MATERIELS DE CONTROLE SAS"]["statut"],
                         "programme_afa")
        self.assertEqual(par_societe["HSBC Bank plc"]["statut"], "sans_programme")

    def test_memoire_aucune_relecture(self):
        lus = []

        def get_compte(url, binaire=False):
            if binaire:
                lus.append(url)
            return get(url, binaire)

        tout, _ = D.executer(AUJ, get=get, lire_pdf=lire, pause=0)
        deja = {e["cle"] for e in tout}
        nouvelles, info = D.executer(AUJ, deja_vus=deja, get=get_compte, lire_pdf=lire, pause=0)
        self.assertEqual(nouvelles, [])
        self.assertEqual(lus, [])
        self.assertEqual(info["deja_vues"], 2)

    def test_page_injoignable(self):
        def ko(url, binaire=False):
            raise RuntimeError("403")
        nouvelles, info = D.executer(AUJ, get=ko)
        self.assertEqual(nouvelles, [])
        self.assertTrue(info["erreurs"])

    def test_vue_arise_et_lignes(self):
        nouvelles, info = D.executer(AUJ, get=get, lire_pdf=lire, pause=0)
        visibles = [e for e in nouvelles if D.visible_arise(e)]
        self.assertEqual([e["societe"] for e in visibles],
                         ["PERIPHERIQUES ET MATERIELS DE CONTROLE SAS"])
        ligne = D.ligne_sheet(visibles[0], AUJ)
        self.assertEqual(len(ligne), len(D.COLONNES_SHEET))
        self.assertEqual(ligne[D.COLONNES_SHEET.index("fin_suivi_afa")], "2029-02-18")
        self.assertEqual(ligne[-2:], ["", ""])
        self.assertEqual(len(D.valeurs_pg(nouvelles[0])), len(D.COLONNES_PG))
        md = D.rapport_markdown(nouvelles, info, ["aucune (mode debug)"], True)
        self.assertIn("programme_afa", md)

    def test_ecritures_sans_configuration(self):
        self.assertIn("absent", D.ecrire_pg(None, [{}]))
        self.assertIn("non configure", D.ecrire_sheet(None, [["x"]]))
        self.assertIsNone(D.memoire_pg(None))


if __name__ == "__main__":
    unittest.main()
