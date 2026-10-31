# -*- coding: utf-8 -*-
"""Declencheurs IE - sanctions AMF : fonctions pures (date, societe, motif,
statut), execution (fenetre, memoire, hors-cible) et lignes de sortie. Table et
onglet partages avec les CJIP. Offline : reseau et pypdf injectes."""

import datetime
import unittest

import declencheurs_amf_ie as A
import declencheurs_ie as D

A.PAUSE = 0.0
AUJ = datetime.date(2026, 10, 31)


class TestTitre(unittest.TestCase):

    def test_date(self):
        self.assertEqual(
            A.date_decision("Decision du 17 decembre 2025 a l'egard de la societe X"),
            datetime.date(2025, 12, 17))
        self.assertEqual(
            A.date_decision("Decision du 1er avril 2026 a l'egard de Kerdiz"),
            datetime.date(2026, 4, 1))
        self.assertIsNone(A.date_decision("sans date"))

    def test_societe_nommee(self):
        self.assertTrue(A.societe_nommee("a l'egard de la societe Caceis Bank"))
        self.assertTrue(A.societe_nommee("a l'egard de MIKOSTA SAS"))
        self.assertFalse(A.societe_nommee("a l'egard de MM. Mamou et Houri"))

    def test_nom_societe(self):
        self.assertEqual(
            A.nom_societe("Decision du 17 decembre 2025 a l'egard de la societe Caceis Bank"),
            "Caceis Bank")
        # Coupe avant la personne physique citee.
        self.assertEqual(
            A.nom_societe("Decision a l'egard de la societe Uzes Gestion et de MM. Dupont"),
            "Uzes Gestion")
        # Variante avec separateur ', de M.' (cas reel M Capital Partners).
        self.assertEqual(
            A.nom_societe("Decision a l'egard de la societe M Capital Partners, de M. Rudy Secco"),
            "M Capital Partners")

    def test_lien_pdf_et_titre(self):
        page = ('<meta property="og:title" content="SAN-2025-12 a l&#039;egard '
                'de la societe Caceis Bank | AMF">'
                '<a href="https://www.amf-france.org/sites/x/d.pdf?1">PDF</a>')
        self.assertIn("Caceis Bank", A.titre_og(page))
        self.assertTrue(A.lien_pdf(page).endswith("d.pdf?1"))

    def test_decisions_du_sitemap(self):
        xml = ("<loc>https://www.amf-france.org/fr/sanctions-transactions/"
               "decisions-de-la-commission-des-sanctions/san-1</loc>"
               "<loc>https://www.amf-france.org/fr/autre</loc>")
        self.assertEqual(len(A.decisions_du_sitemap(xml)), 1)


class TestMotifEtStatut(unittest.TestCase):

    def test_conformite_pertinent(self):
        c = A.classer_motif("Carences du dispositif de conformite LCB-FT, injonction "
                            "de mise en conformite.")
        self.assertTrue(c["pertinent"])

    def test_abus_pur_non_pertinent(self):
        c = A.classer_motif("Information privilegiee, manipulation de cours, abus de marche.")
        self.assertFalse(c["pertinent"])
        self.assertFalse(c["conformite"])

    def test_conformite_citee_mais_abus_domine_sans_remediation(self):
        c = A.classer_motif("Information privilegiee et manipulation de cours ; operation "
                            "d'initie. Mention du dispositif de conformite.")
        self.assertTrue(c["conformite"])
        self.assertFalse(c["pertinent"])

    def test_texte_neutre_non_pertinent(self):
        c = A.classer_motif("Prestation de services informatiques et fourniture de mobilier.")
        self.assertFalse(c["conformite"])
        self.assertFalse(c["pertinent"])

    def test_abus_domine_egalite(self):
        # Autant d'abus que de conformite -> l'abus ne domine PAS (strict).
        self.assertFalse(A._abus_domine("information privilegiee lcb-ft"))
        self.assertTrue(A._abus_domine("information privilegiee manipulation de cours lcb-ft"))

    def test_statut_pertinent(self):
        e = {"societe": "Caceis Bank", "analyse": {"pertinent": True}, "incomplet": False}
        self.assertEqual(A.statut(e), "sanction_conformite")
        self.assertTrue(A.visible_arise(dict(e, statut="sanction_conformite")))

    def test_statut_abus(self):
        e = {"societe": "X", "analyse": {"pertinent": False}, "incomplet": False}
        self.assertEqual(A.statut(e), "abus_marche")
        self.assertFalse(A.visible_arise(dict(e, statut="abus_marche")))

    def test_statut_hors_cible_sans_societe(self):
        e = {"societe": "", "analyse": {"pertinent": True}, "incomplet": False}
        self.assertEqual(A.statut(e), "hors_cible")

    def test_statut_illisible_et_incomplet(self):
        self.assertEqual(A.statut({"societe": "X", "analyse": None, "incomplet": False}),
                         "illisible")
        self.assertEqual(A.statut({"societe": "X", "analyse": None, "incomplet": True}),
                         "incomplet")
        self.assertTrue(A.visible_arise({"statut": "illisible"}))


def _sitemap(n):
    base = ("https://www.amf-france.org/fr/sanctions-transactions/"
            "decisions-de-la-commission-des-sanctions/san-")
    return "".join("<loc>{}{}</loc>".format(base, i) for i in range(1, n + 1))


class TestExecuter(unittest.TestCase):

    def setUp(self):
        self.base = ("https://www.amf-france.org/fr/sanctions-transactions/"
                     "decisions-de-la-commission-des-sanctions/san-")
        self.titres = {
            "1": "Decision du 20 juillet 2026 a l'egard de la societe Alpha",   # pertinent
            "2": "Decision du 15 juin 2026 a l'egard de la societe Beta",        # abus
            "3": "Decision du 10 mai 2026 a l'egard de MM. Jean Martin",         # hors cible
            "4": "Decision du 3 janvier 2020 a l'egard de la societe VieuxCorp", # hors fenetre
        }
        self.pdfs = {
            "1": "defaillances du dispositif de conformite LCB-FT, injonction de mise en conformite. " * 10,
            "2": "information privilegiee et manipulation de cours, abus de marche. " * 10,
            "3": "manquement declaratif. " * 30,
            "4": "dispositif de conformite. " * 30,
        }
    def _get(self, url, binaire=False):
        if url == A.SITEMAP:
            return _sitemap(4)
        if "/sites/" in url:                                # PDF statique
            return url.rsplit("/", 1)[-1].replace(".pdf", "").encode()
        if url.startswith(self.base):
            ident = url[len(self.base):]
            return ('<meta property="og:title" content="{}">'
                    '<a href="https://www.amf-france.org/sites/x/{}.pdf">PDF</a>'
                    ).format(self.titres[ident], ident)
        raise RuntimeError("url inattendue : {}".format(url))

    def _lire_pdf(self, contenu):
        return self.pdfs[contenu.decode()]

    def test_fenetre_statuts_et_comptage(self):
        nouv, info = A.executer(aujourd=AUJ, get=self._get, lire_pdf=self._lire_pdf,
                                fenetre_mois=24, pause=0)
        self.assertEqual(info["dans_fenetre"], 3)        # la 4e (2020) exclue
        self.assertEqual(info["lues"], 3)
        st = {e["societe"] or e["cle"][-1]: e["statut"] for e in nouv}
        self.assertEqual(st["Alpha"], "sanction_conformite")
        self.assertEqual(st["Beta"], "abus_marche")
        self.assertEqual([e for e in nouv if e["statut"] == "hors_cible"][0]["societe"], "")
        montrees = [e for e in nouv if A.visible_arise(e)]
        self.assertEqual([e["societe"] for e in montrees], ["Alpha"])

    def test_memoire_deduplique(self):
        deja = {self.base + "1"}
        nouv, info = A.executer(aujourd=AUJ, deja_vus=deja, get=self._get,
                                lire_pdf=self._lire_pdf, fenetre_mois=24, pause=0)
        self.assertNotIn("Alpha", [e["societe"] for e in nouv])
        self.assertEqual(info["deja_vues"], 1)

    def test_borne_fenetre_incluse(self):
        # Une decision pile sur la borne est incluse (date < limite exclut, pas <=).
        limite = D.ajouter_mois(AUJ, -24)
        base = self.base
        def get(url, binaire=False):
            if url == A.SITEMAP:
                return "<loc>{}9</loc>".format(base)
            if "/sites/" in url:
                return b"defaillances du dispositif de conformite LCB-FT, injonction. " * 10
            titre = "Decision du {} {} {} a l'egard de la societe Borne".format(
                limite.day, {10: "octobre"}.get(limite.month, "janvier"), limite.year)
            return ('<meta property="og:title" content="{}">'
                    '<a href="https://www.amf-france.org/sites/x/9.pdf">P</a>').format(titre)
        nouv, info = A.executer(aujourd=AUJ, get=get, lire_pdf=lambda c: c.decode(),
                                fenetre_mois=24, pause=0)
        self.assertEqual(info["dans_fenetre"], 1)

    def test_sitemap_injoignable(self):
        def get(url, binaire=False):
            raise RuntimeError("timeout")
        nouv, info = A.executer(aujourd=AUJ, get=get)
        self.assertEqual(nouv, [])
        self.assertEqual(len(info["erreurs"]), 1)


class TestLecture(unittest.TestCase):

    def test_pdf_illisible_court(self):
        e = {"societe": "X", "lien_convention": "u"}
        A.lire_decision(e, get=lambda u, binaire=False: b"", lire_pdf=lambda c: "court")
        self.assertEqual(e["statut"] if "statut" in e else A.statut(e), "illisible")
        self.assertIsNone(e["analyse"])

    def test_pdf_erreur_incomplet(self):
        e = {"societe": "X", "lien_convention": "u"}
        def lire(c):
            raise RuntimeError("pdf casse")
        err = A.lire_decision(e, get=lambda u, binaire=False: b"x", lire_pdf=lire)
        self.assertTrue(e["incomplet"])
        self.assertEqual(len(err), 1)


class TestSorties(unittest.TestCase):

    def _entree(self):
        return {"cle": "https://www.amf-france.org/x", "societe": "Caceis Bank",
                "validation": datetime.date(2025, 12, 17), "statut": "sanction_conformite",
                "analyse": {"conformite": True, "abus": False}, "lecture": "pdf",
                "lien_communique": "https://www.amf-france.org/x",
                "lien_convention": "https://www.amf-france.org/x/d.pdf"}

    def test_ligne_sheet_schema_partage(self):
        ligne = A.ligne_sheet(self._entree(), AUJ)
        self.assertEqual(len(ligne), len(D.COLONNES_SHEET))
        self.assertEqual(ligne[D.COLONNES_SHEET.index("type")], "sanction AMF")
        self.assertEqual(ligne[D.COLONNES_SHEET.index("juridiction")], "AMF")
        self.assertEqual(ligne[D.COL_CLE], "https://www.amf-france.org/x")
        self.assertEqual(ligne[-2:], ["", ""])

    def test_valeurs_pg_schema_partage(self):
        v = A.valeurs_pg(self._entree())
        self.assertEqual(len(v), len(D.COLONNES_PG))
        self.assertEqual(v[D.COLONNES_PG.index("type")], "sanction AMF")
        self.assertEqual(v[D.COLONNES_PG.index("juridiction")], "AMF")

    def test_ecritures_sans_configuration(self):
        self.assertIn("absent", A.ecrire_pg(None, [{}]))
        self.assertIn("rien a ecrire", A.ecrire_pg("postg:///x", []))


if __name__ == "__main__":
    unittest.main()
