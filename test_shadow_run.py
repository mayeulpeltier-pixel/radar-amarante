# -*- coding: utf-8 -*-
"""Harnais de shadow run : on verifie surtout l'INNOCUITE (aucune ecriture,
aucune promotion) et la fiabilite de l'instrumentation, avant de le lancer sur
des donnees reelles. Tout est offline."""

import unittest

import projets_reference as ref
import shadow_run_projets as sh


class TestInnocuite(unittest.TestCase):
    """Garanties promises a l'utilisateur, verifiees sur le CODE EXECUTABLE.

    On analyse l'AST plutot que le texte brut : la docstring mentionne
    legitimement les fonctions interdites pour expliquer qu'elles ne sont pas
    appelees, et une simple recherche textuelle levait un faux positif."""

    @staticmethod
    def _noms_appeles():
        import ast
        arbre = ast.parse(open(sh.__file__.replace(".pyc", ".py")).read())
        noms = set()
        for n in ast.walk(arbre):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    noms.add(f.id)
                elif isinstance(f, ast.Attribute):
                    noms.add(f.attr)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = getattr(n, "module", None)
                noms.add(mod or "")
                for a in n.names:
                    noms.add(a.name)
        return noms

    def test_aucune_ecriture_sheet_ni_postgres(self):
        appels = self._noms_appeles()
        for interdit in ("_ouvrir_classeur", "radar_stockage", "ecrire_miroir",
                         "add_worksheet", "append_row", "update"):
            self.assertNotIn(interdit, appels, interdit)

    def test_aucune_sauvegarde_de_memoire(self):
        appels = self._noms_appeles()
        self.assertNotIn("sauver", appels)
        self.assertNotIn("radar_etat", appels)

    def test_aucune_promotion(self):
        appels = self._noms_appeles()
        self.assertNotIn("promouvoir", appels)
        self.assertNotIn("registre_enrichi", appels)
        self.assertNotIn("entree_registre", appels)

    def test_registre_intact_apres_import(self):
        # On teste l'INVARIANCE, pas un nombre fige : le registre grossit au
        # fil des decouvertes validees a la main.
        avant = len(ref.charger_registre())
        import shadow_run_projets  # noqa: F401  (re-import volontaire)
        self.assertEqual(len(ref.charger_registre()), avant)
        self.assertGreaterEqual(avant, 19)


class TestInstrumentation(unittest.TestCase):

    def setUp(self):
        sh.USAGE.update({"in": 0, "out": 0, "appels": 0, "tronques": 0})
        del sh.ERREURS[:]

    def test_cout_calcule_sur_le_tarif_verifie(self):
        sh.USAGE["in"], sh.USAGE["out"] = 1000000, 1000000
        self.assertAlmostEqual(sh.cout_usd(), 6.0)      # 1$ in + 5$ out

    def test_cout_nul_sans_appel(self):
        self.assertEqual(sh.cout_usd(), 0.0)

    def test_doublons_liens_et_titres(self):
        arts = [{"lien": "http://a", "titre": "Projet X avance"},
                {"lien": "http://a", "titre": "Projet X avance"},
                {"lien": "http://b", "titre": "Projet X avance"},
                {"lien": "http://c", "titre": "Autre sujet"}]
        d = sh.mesurer_doublons(arts)
        self.assertEqual(d["liens_dupliques"], 1)
        self.assertEqual(d["titres_dupliques"], 2)   # 3 memes titres -> 2 doublons
        self.assertEqual(d["liens_uniques"], 3)

    def test_mesure_du_bug_socle(self):
        signaux = [{"lien": "http://a"}, {"lien": "http://a"}, {"lien": "http://b"}]
        b = sh.mesurer_bug_socle(signaux)
        self.assertEqual(b["impact"], 1)

    def test_bug_socle_sans_signaux(self):
        self.assertEqual(sh.mesurer_bug_socle([])["impact"], 0)

    def test_score_amarante_sans_toucher_au_registre(self):
        c = {"nom": "Projet Test", "iso3": "COD", "secteur": "mines",
             "montant_musd": 3000, "phase": "CONSTRUCTION", "nb_signaux": 3,
             "derniere_maj": "2026-08-01", "acteurs_top": ["ivanhoe mines"]}
        avant = len(ref.charger_registre())
        score = sh.score_amarante(c)
        self.assertGreater(score, 0)
        self.assertEqual(len(ref.charger_registre()), avant)   # registre intact

    def test_famille_de_source(self):
        self.assertIn("developpement",
                      sh.famille_source({"lien": "https://www.worldbank.org/x"}).lower())
        self.assertIn("locale",
                      sh.famille_source({"lien": "https://blog.example/x"}).lower())


class TestPaysCibles(unittest.TestCase):

    def test_les_trois_pays_sont_au_referentiel(self):
        import pays_projets_reference as pref
        for iso3 in ("TZA", "COD", "GIN"):
            self.assertIsNotNone(pref.pays_par_iso3(iso3), iso3)

    def test_langues_attendues(self):
        import pays_projets_reference as pref
        self.assertIn("sw", pref.pays_par_iso3("TZA")["langues"])
        self.assertIn("fr", pref.pays_par_iso3("COD")["langues"])
        self.assertIn("fr", pref.pays_par_iso3("GIN")["langues"])


if __name__ == "__main__":
    unittest.main()


class TestBugTupleTexteDesBlocs(unittest.TestCase):
    """REGRESSION. Le premier shadow run a produit 0 candidat parce que
    `ted.texte_des_blocs` renvoie (texte, motif) et que le harnais renvoyait le
    TUPLE. `parser_reponse` faisait alors str(tuple) et n'y trouvait aucun JSON
    valide : chaque lot ressortait vide. Ce test verrouille le depaquetage."""

    def test_appel_renvoie_une_chaine_pas_un_tuple(self):
        import json as _json
        import os
        import ted_complet_v14 as ted

        charge = {"content": [{"type": "text", "text": '[{"n":1,"projet":"X"}]'}],
                  "usage": {"input_tokens": 10, "output_tokens": 5},
                  "stop_reason": "end_turn"}

        class Rep:
            def raise_for_status(self):
                pass

            def json(self):
                return charge

        class Sess:
            def post(self, *a, **k):
                return Rep()

        ancien = ted.session_robuste
        ted.session_robuste = lambda *a, **k: Sess()
        os.environ["ANTHROPIC_API_KEY"] = "test"
        try:
            out = sh.appel_llm_mesure("prompt")
        finally:
            ted.session_robuste = ancien
        self.assertIsInstance(out, str)
        self.assertEqual(_json.loads(out)[0]["projet"], "X")

    def test_le_parseur_comprend_la_sortie(self):
        import decouverte_projets as dp
        # Le modele repond en JSON INDENTE, donc multi-lignes : c'est le cas
        # reel. Sur une chaine, le parseur comprend.
        reel = '[\n  {"n": 1, "projet": "X"},\n  {"n": 2, "projet": "Y"}\n]'
        self.assertEqual(dp.parser_reponse(reel, 2)[0]["projet"], "X")
        # Passe en TUPLE, str() echappe les sauts de ligne en "\\n" litteraux :
        # le JSON devient invalide et TOUT le lot ressort vide. C'est
        # exactement ce qui s'est produit lors du premier shadow run.
        self.assertEqual(dp.parser_reponse((reel, ""), 2)[0]["projet"], "")

    def test_reponse_sur_une_seule_ligne_survivait_par_chance(self):
        """Precision honnete : le bug ne se voyait PAS sur une reponse tenant
        sur une ligne, ce qui explique qu'il ait passe les tests hors ligne."""
        import decouverte_projets as dp
        une_ligne = '[{"n":1,"projet":"X"}]'
        self.assertEqual(dp.parser_reponse((une_ligne, ""), 1)[0]["projet"], "X")
