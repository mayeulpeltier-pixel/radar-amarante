# -*- coding: utf-8 -*-
"""Tableau de bord ARISE (IE-2) : preparation (filtres, tris) et rendu HTML.
Tout est pur (generer_html ne touche pas le reseau) ; on injecte des lignes."""

import datetime
import unittest

import dashboard_ie as D

AUJ = datetime.date(2026, 10, 31)


def lead(cle, action="contacter", perimetre="affiche", score=7.0, source="TED",
         titre="Due diligence des tiers", lien="https://ted.europa.eu/x",
         resume="DD integrite", famille="due_diligence", acheteur="EUAA",
         date_limite="2026-12-01", motifs="international ; sensible"):
    return {"cle": cle, "action": action, "perimetre": perimetre, "score": score,
            "source": source, "titre": titre, "lien": lien, "resume": resume,
            "famille": famille, "acheteur": acheteur, "date_limite": date_limite,
            "motifs": motifs}


class TestLeadsAffiches(unittest.TestCase):

    def test_filtre_perimetre_et_action(self):
        leads = [lead("a", action="contacter"), lead("b", action="surveiller"),
                 lead("c", action="ignorer"),
                 lead("d", action="contacter", perimetre="ombre")]
        vus = [l["cle"] for l in D.leads_affiches(leads)]
        self.assertEqual(sorted(vus), ["a", "b"])

    def test_tri_action_puis_score(self):
        leads = [lead("surv", action="surveiller", score=9.0),
                 lead("c1", action="contacter", score=5.0),
                 lead("c2", action="contacter", score=8.0)]
        self.assertEqual([l["cle"] for l in D.leads_affiches(leads)],
                         ["c2", "c1", "surv"])   # contacter avant surveiller, score desc


class TestEcheances(unittest.TestCase):

    def test_tri_renouvele_en_dernier_puis_date(self):
        ech = [{"cle": "r", "fin_estimee": "2026-11-10", "renouvele": "true"},
               {"cle": "proche", "fin_estimee": "2026-11-15", "renouvele": "false"},
               {"cle": "loin", "fin_estimee": "2027-06-01", "renouvele": "false"}]
        self.assertEqual([x["cle"] for x in D.echeances_affichees(ech, AUJ)],
                         ["proche", "loin", "r"])


class TestDeclencheurs(unittest.TestCase):

    def test_tri_par_statut(self):
        decl = [{"cle": "v", "statut": "a_verifier", "validation": "2026-09-01"},
                {"cle": "afa", "statut": "programme_afa", "validation": "2026-08-01"},
                {"cle": "scan", "statut": "illisible", "validation": "2026-10-01"},
                {"cle": "rien", "statut": "sans_programme", "validation": "2026-10-10"}]
        vus = [d["cle"] for d in D.declencheurs_affiches(decl)]
        self.assertEqual(vus, ["afa", "v", "scan"])   # sans_programme exclu


class TestRendu(unittest.TestCase):

    def test_page_complete_et_sections(self):
        html = D.generer_html([lead("a")],
                              [{"cle": "e", "fin_estimee": "2026-11-10",
                                "position": "amarante_sortant", "acheteur": "PACA",
                                "objet": "Parcours IE", "lien": "https://colibre.fr/m/1"}],
                              [{"cle": "d", "statut": "programme_afa", "societe": "PMC",
                                "validation": "2026-02-18", "fin_suivi": "2029-02-18",
                                "lien_communique": "https://justice.gouv.fr/cp"}],
                              genere_le=AUJ)
        self.assertIn("<!doctype html>", html)
        self.assertIn("Radar ARISE", html)
        for attendu in ("Leads a traiter", "Echeances de marches", "Declencheurs CJIP",
                        "2026-10-31", "Due diligence des tiers", "Parcours IE",
                        "Amarante sortant", "Programme AFA", "PMC"):
            self.assertIn(attendu, html)

    def test_echappement_html(self):
        html = D.generer_html([lead("x", titre='Veille <script>alert(1)</script> & co',
                                    lien="javascript:alert(1)")], [], [], genere_le=AUJ)
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;", html)
        # Un lien non http n'est pas rendu comme lien cliquable.
        self.assertNotIn('href="javascript:', html)

    def test_sections_vides(self):
        html = D.generer_html([], [], [], genere_le=AUJ)
        self.assertIn("Aucun lead a traiter", html)
        self.assertIn("Aucune echeance", html)
        self.assertIn("Aucun declencheur", html)

    def test_source_en_attribut_pour_le_filtre(self):
        html = D.generer_html([lead("a", source="UNGM")], [], [], genere_le=AUJ)
        self.assertIn('data-source="UNGM"', html)
        self.assertIn('data-action="contacter"', html)

    def test_montant_douteux_masque(self):
        html = D.generer_html([], [{"cle": "e", "fin_estimee": "2026-11-10",
                                    "objet": "x", "plafond": "99", "montant_douteux": "true"}],
                              [], genere_le=AUJ)
        self.assertNotIn("99 EUR", html)


class TestLectureVide(unittest.TestCase):

    def test_sans_url(self):
        self.assertEqual(D.lire_tables(None), ([], [], []))


if __name__ == "__main__":
    unittest.main()
