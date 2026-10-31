# -*- coding: utf-8 -*-
"""Sonde declencheurs AMF/ACPR : fonctions pures (date, societe nommee,
classification du motif, verdict) et collecte avec reseau/pypdf injectes.
Offline : aucun acces reseau, aucun PDF reel."""

import datetime
import unittest

import sonde_declencheurs_amf_acpr as S

S.PAUSE = 0.0
AUJ = datetime.date(2026, 10, 31)


class TestAnalyseTitre(unittest.TestCase):

    def test_date_decision(self):
        t = "SAN-2026-01 - Decision de la commission des sanctions du 17 decembre 2025 a l'egard de X"
        self.assertEqual(S.date_decision(t), datetime.date(2025, 12, 17))

    def test_date_1er(self):
        self.assertEqual(
            S.date_decision("Decision du 1er avril 2026 a l'egard de la societe Kerdiz"),
            datetime.date(2026, 4, 1))

    def test_date_absente(self):
        self.assertIsNone(S.date_decision("Decision sans date lisible"))

    def test_societe_nommee(self):
        self.assertTrue(S.societe_nommee("a l'egard de la societe Caceis Bank"))
        self.assertTrue(S.societe_nommee("a l'egard de la banque Saxo Bank A/S"))
        self.assertTrue(S.societe_nommee("a l'egard de MIKOSTA SAS"))

    def test_personnes_seules_non_nommees(self):
        self.assertFalse(S.societe_nommee("a l'egard de MM. Ytane Mamou et Elie Houri"))
        self.assertFalse(S.societe_nommee("a l'egard de Mme Adriana Mar"))


class TestClassationMotif(unittest.TestCase):

    def test_conformite_pertinent(self):
        c = S.classer_motif("Carences du dispositif de conformite et manquements LCB-FT, "
                            "injonction de mise en conformite sous astreinte.")
        self.assertTrue(c["conformite"])
        self.assertTrue(c["remediation"])
        self.assertTrue(c["pertinent"])

    def test_abus_pur_non_pertinent(self):
        c = S.classer_motif("Utilisation d'une information privilegiee et manipulation "
                            "de cours ; manquement declaratif. Abus de marche.")
        self.assertTrue(c["abus"])
        self.assertFalse(c["pertinent"])
        self.assertFalse(c["conformite"])

    def test_conformite_malgre_mention_abus_si_remediation(self):
        # Dossier de dispositif LCB-FT citant un abus de marche en passant, avec
        # injonction : reste pertinent.
        c = S.classer_motif("Defaillances du dispositif LCB-FT et du controle interne. "
                            "Une operation d'initie est evoquee. Injonction de remediation "
                            "du dispositif de conformite.")
        self.assertTrue(c["pertinent"])

    def test_texte_neutre_non_pertinent(self):
        c = S.classer_motif("Prestation de services informatiques et fourniture de mobilier.")
        self.assertFalse(c["conformite"])
        self.assertFalse(c["pertinent"])

    def test_conformite_citee_mais_abus_domine_sans_remediation(self):
        # 'conformite' apparait une fois, mais l'abus de marche domine et aucune
        # remediation n'est ordonnee -> non pertinent pour ARISE.
        c = S.classer_motif("Information privilegiee et manipulation de cours ; "
                            "operation d'initie. Mention accessoire du dispositif de conformite.")
        self.assertTrue(c["conformite"])
        self.assertTrue(c["abus"])
        self.assertFalse(c["remediation"])
        self.assertFalse(c["pertinent"])

    def test_abus_domine(self):
        self.assertTrue(S._abus_domine(
            "information privilegiee information privilegiee manipulation de cours conformite"))
        self.assertFalse(S._abus_domine(
            "conformite conformite lcb-ft information privilegiee"))


class TestFenetre(unittest.TestCase):

    def test_dans_fenetre(self):
        self.assertTrue(S.dans_fenetre(datetime.date(2026, 6, 1), AUJ, 24))
        self.assertFalse(S.dans_fenetre(datetime.date(2023, 1, 1), AUJ, 24))
        self.assertFalse(S.dans_fenetre(None, AUJ, 24))

    def test_borne_incluse(self):
        # Une decision pile sur la borne de la fenetre est incluse (>=).
        limite = AUJ - datetime.timedelta(days=int(24 * 30.44))
        self.assertTrue(S.dans_fenetre(limite, AUJ, 24))
        self.assertFalse(S.dans_fenetre(limite - datetime.timedelta(days=1), AUJ, 24))


class TestExtractionHtml(unittest.TestCase):

    def test_titre_og_et_pdf(self):
        html = ('<meta property="og:title" content="SAN-2025-12 - Decision du 17 '
                'decembre 2025 a l&#039;egard de la societe Caceis Bank | AMF">'
                '<a href="https://www.amf-france.org/sites/x/SAN-2025-12.pdf?1">PDF</a>')
        self.assertIn("Caceis Bank", S.titre_og(html))
        self.assertTrue(S.lien_pdf(html).endswith(".pdf?1"))

    def test_decisions_du_sitemap(self):
        xml = ("<loc>https://www.amf-france.org/fr/sanctions-transactions/"
               "decisions-de-la-commission-des-sanctions/san-2026-01-x</loc>"
               "<loc>https://www.amf-france.org/fr/autre-page</loc>")
        d = S.decisions_du_sitemap(xml)
        self.assertEqual(len(d), 1)


def page(titre, pdf="https://www.amf-france.org/sites/x/d.pdf"):
    return ('<meta property="og:title" content="{}">'
            '<a href="{}">PDF</a>').format(titre, pdf)


class TestSonderAmf(unittest.TestCase):

    def setUp(self):
        base = "https://www.amf-france.org/fr/sanctions-transactions/decisions-de-la-commission-des-sanctions/"
        self.sitemap = "".join(
            "<loc>{}san-{}</loc>".format(base, i) for i in range(1, 5))
        # 4 decisions : 2 pertinentes (societe+conformite), 1 abus, 1 hors fenetre
        self.titres = {
            base + "san-1": "Decision du 20 juillet 2026 a l'egard de la societe Alpha",
            base + "san-2": "Decision du 15 juin 2026 a l'egard de la societe Beta",
            base + "san-3": "Decision du 10 mai 2026 a l'egard de la societe Gamma",
            base + "san-4": "Decision du 3 janvier 2020 a l'egard de la societe VieuxCorp",
        }
        self.pdfs = {
            "san-1": "manquements LCB-FT, dispositif de conformite, injonction de mise en conformite",
            "san-2": "defaillances du controle interne et evaluation des tiers insuffisante",
            "san-3": "information privilegiee et manipulation de cours, abus de marche",
            "san-4": "dispositif de conformite LCB-FT",
        }

    def test_comptage_et_fenetre(self):
        # La page renvoie du vrai HTML (og:title + lien PDF) ; le PDF est route
        # par la decision vue juste avant.
        etat = {"dernier": None}
        def get_texte(url):
            if url == S.SITEMAP_AMF:
                return self.sitemap
            etat["dernier"] = url.rsplit("san-", 1)[-1]
            return page(self.titres[url], "https://www.amf-france.org/sites/x/d.pdf")
        def get_pdf(url):
            return self.pdfs["san-" + etat["dernier"]]
        j = S.sonder_amf(get_texte=get_texte, get_pdf=get_pdf, aujourd=AUJ, mois=6)
        self.assertEqual(j["lues"], 3)              # la 4e est hors fenetre (2020)
        self.assertEqual(j["nommees"], 3)
        self.assertEqual(j["pertinents"], 2)        # san-1 et san-2, pas san-3 (abus)
        self.assertEqual(S.verdict(j)[0], "EXPLOITABLE")   # 2/6 mois = 4/an >= 3

    def test_sitemap_injoignable(self):
        def get_texte(url):
            raise RuntimeError("timeout")
        j = S.sonder_amf(get_texte=get_texte, get_pdf=lambda u: "", aujourd=AUJ)
        self.assertTrue(j["injoignable"])
        self.assertEqual(S.verdict(j)[0], "INJOIGNABLE")


class TestVerdict(unittest.TestCase):

    def test_marginal_sous_seuil(self):
        j = {"mois": 24, "pertinents": 2}           # 1/an < 3
        self.assertEqual(S.verdict(j)[0], "MARGINAL")

    def test_exploitable(self):
        j = {"mois": 24, "pertinents": 12}          # 6/an >= 3
        self.assertEqual(S.verdict(j)[0], "EXPLOITABLE")

    def test_seuil_pile_exploitable(self):
        j = {"mois": 24, "pertinents": 6}           # 3,0/an = seuil, inclus
        self.assertEqual(S.verdict(j)[0], "EXPLOITABLE")


class TestAcpr(unittest.TestCase):

    def test_injoignable_rapporte(self):
        def get_texte(url):
            raise RuntimeError("HTTP 403")
        j = S.sonder_acpr(get_texte=get_texte)
        self.assertTrue(j["injoignable"])
        self.assertIn("403", j["note"])


class TestRapport(unittest.TestCase):

    def test_rapport_complet(self):
        amf = {"canal": "AMF", "mois": 24, "lues": 14, "nommees": 10, "conformite": 9,
               "abus": 3, "remediation": 5, "pertinents": 8,
               "exemples": ["Decision a l'egard de la societe Caceis Bank"],
               "erreurs": []}
        acpr = {"canal": "ACPR", "mois": 24, "injoignable": True, "note": "403"}
        md, (va, vp) = S.rapport(amf, acpr, AUJ)
        self.assertIn("Sonde declencheurs AMF", md)
        self.assertIn("Caceis Bank", md)
        self.assertEqual(va[0], "EXPLOITABLE")
        self.assertEqual(vp[0], "INJOIGNABLE")


if __name__ == "__main__":
    unittest.main()
