# -*- coding: utf-8 -*-
"""Pre-requis d'ACTIVATION EN PRODUCTION.

Deux defauts auraient casse la production s'ils n'avaient pas ete corriges
avant d'allumer les workflows. Ces tests les verrouillent."""

import inspect
import unittest

import bitd_signaux as bitd
import collecteur_projets as cp
import decouverte_projets as dp


class TestEtatSepare(unittest.TestCase):
    """BLOCAGE 1. `radar_etat` par defaut est PARTAGE : signaux_prives y stocke
    son curseur de rotation (entreprises) et sa liste de vus. Les couches
    projets, avec un curseur de semantique differente (pays), auraient ecrase
    cette memoire a chaque run."""

    def test_les_deux_couches_utilisent_un_chemin_dedie(self):
        for module in (cp, dp):
            src = inspect.getsource(module.main)
            self.assertIn("CHEMIN_ETAT", src, module.__name__)
            self.assertIn("chemin=CHEMIN_ETAT", src, module.__name__)

    def test_aucune_ecriture_sur_l_etat_par_defaut(self):
        for module in (cp, dp):
            src = inspect.getsource(module.main)
            self.assertNotIn("radar_etat.sauver(curseur + len(fenetre), vus, "
                             "nouveaux_vus)", src)
            self.assertNotIn("radar_etat.charger()", src, module.__name__)

    def test_fichiers_distincts_entre_les_deux_couches(self):
        self.assertNotIn("radar_etat_decouverte",
                         inspect.getsource(cp.main))
        self.assertNotIn("radar_etat_projets.json",
                         inspect.getsource(dp.main))


class TestPlafondDeTokens(unittest.TestCase):
    """BLOCAGE 2. Un lot de 10 signaux produit ~870 tokens de sortie (mesure du
    shadow run). Le plafond historique de 400 tronquait la reponse : le JSON
    devenait illisible et le lot entier ressortait vide, SANS erreur."""

    def test_appel_llm_accepte_un_plafond(self):
        params = inspect.signature(bitd._appel_llm).parameters
        self.assertIn("max_tokens", params)

    def test_defaut_historique_inchange(self):
        """Les appelants existants (signaux prives) ne doivent rien voir."""
        self.assertEqual(
            inspect.signature(bitd._appel_llm).parameters["max_tokens"].default, 400)

    def test_couches_projets_relevent_le_plafond(self):
        for module in (cp, dp):
            self.assertGreaterEqual(module.MAX_TOKENS_LOT, 1500, module.__name__)

    def test_plafond_reellement_transmis(self):
        for module in (cp, dp):
            src = inspect.getsource(module.classer_lots
                                    if module is cp else module.extraire_par_lots)
            self.assertIn("max_tokens=MAX_TOKENS_LOT", src, module.__name__)


class TestGardeFousDActivation(unittest.TestCase):
    """Ce qui protege un run de production qui tourne sans surveillance."""

    def test_plafonds_de_cout_presents(self):
        self.assertGreater(cp.MAX_LOTS, 0)
        self.assertGreater(dp.MAX_LOTS, 0)
        self.assertGreater(cp.PROJETS_PAR_RUN, 0)
        self.assertGreater(dp.PAYS_PAR_RUN, 0)

    def test_flags_off_par_defaut_dans_le_code(self):
        """L'activation se fait dans les workflows, jamais dans le code."""
        import os
        if not os.environ.get("RADAR_PROJETS"):
            self.assertFalse(cp.ACTIVER)
        if not os.environ.get("RADAR_DECOUVERTE_PROJETS"):
            self.assertFalse(dp.ACTIVER)

    def test_la_promotion_n_ecrit_jamais_dans_le_registre(self):
        src = inspect.getsource(dp.main)
        self.assertNotIn("projets_reference.REGISTRE", src)
        self.assertIn("revue humaine", src)


if __name__ == "__main__":
    unittest.main()


class TestPortéeEcritureSheet(unittest.TestCase):
    """PREMIER RUN DE PRODUCTION, 24/08/2026 : l'ecriture Sheet a echoue en
    "403 insufficient authentication scopes". Cause : les collecteurs
    reutilisaient `signaux_prives._ouvrir_classeur`, qui ouvre le classeur en
    spreadsheets.READONLY. Ecrire exige la portee `spreadsheets`."""

    def test_les_deux_collecteurs_ouvrent_en_ecriture(self):
        for module in (cp, dp):
            src = inspect.getsource(module.ecrire)
            self.assertIn('"https://www.googleapis.com/auth/spreadsheets"]', src,
                          module.__name__)

    def test_readonly_plus_utilise_pour_ecrire(self):
        for module in (cp, dp):
            src = inspect.getsource(module.ecrire)
            self.assertNotIn("sp._ouvrir_classeur(", src, module.__name__)
            self.assertNotIn("readonly", src.lower().replace("readonly :", ""),
                             module.__name__)


class TestRepliMiroirPostgres(unittest.TestCase):
    """Le miroir Postgres etait correctement alimente alors que le Sheet
    echouait. Sans lecture possible, des donnees disponibles restaient
    invisibles."""

    def test_lecture_du_miroir_existe(self):
        import radar_stockage
        self.assertTrue(callable(getattr(radar_stockage, "lire_miroir", None)))

    def test_lecture_ne_leve_jamais(self):
        import radar_stockage
        self.assertEqual(radar_stockage.lire_miroir("onglet_inexistant"), [])

    def test_cockpit_replie_sur_le_miroir(self):
        import radar_cockpit as rc
        for fonction in (rc.charger_projets, rc.charger_candidats_projets):
            self.assertIn("_lire_miroir_pg", inspect.getsource(fonction))

    def test_normaliseurs_partages_entre_sheet_et_miroir(self):
        """Les deux sources doivent produire le meme format."""
        import radar_cockpit as rc
        p = rc._normaliser_projet({"project_id": "X", "maturite": "65",
                                   "timeline_json": '[{"annee":"2026"}]'})
        self.assertEqual(p["maturite"], 65.0)
        self.assertEqual(p["timeline"][0]["annee"], "2026")
        c = rc._normaliser_candidat({"nom": "Y", "confiance": "42",
                                     "signaux_json": "[]"})
        self.assertEqual(c["confiance"], 42.0)
        self.assertEqual(c["signaux"], [])

    def test_valeurs_aberrantes_ne_cassent_pas(self):
        import radar_cockpit as rc
        p = rc._normaliser_projet({"project_id": "X", "maturite": "n/a",
                                   "timeline_json": "{casse"})
        self.assertEqual(p["maturite"], 0)
        self.assertEqual(p["timeline"], [])


class TestLaRotationNeDetruitPlus(unittest.TestCase):
    """RUN DU 24/08/2026, defaut majeur. L'ecriture etait un REMPLACEMENT
    complet (clear + update) alors que chaque run ne traite que quelques
    projets (rotation). Le run 1 avait ecrit INGA3, TANZLNG et MOZLNG ; le
    run 2 les a REMPLACES par CORALSUL, SIMANDOU et LOBITO. Le cockpit
    n'aurait jamais affiche plus de 3 projets sur 22 au registre."""

    @staticmethod
    def _ligne(colonnes, cle, valeur, champ, val_champ):
        l = [""] * len(colonnes)
        l[colonnes.index(cle)] = valeur
        l[colonnes.index(champ)] = str(val_champ)
        return l

    def test_projets_accumules_entre_runs(self):
        L = lambda p, m: self._ligne(cp.COLONNES, "project_id", p, "maturite", m)
        run1 = [L("INGA3_COD", 62), L("TANZLNG_TZA", 50), L("MOZLNG_MOZ", 85)]
        run2 = [L("CORALSUL_MOZ", 73), L("SIMANDOU_GIN", 68), L("LOBITO_AGO", 52)]
        fusion = cp.fusionner_lignes(run1, run2)
        self.assertEqual(len(fusion), 6)
        ids = [l[cp.COLONNES.index("project_id")] for l in fusion]
        self.assertIn("INGA3_COD", ids)
        self.assertIn("LOBITO_AGO", ids)

    def test_reanalyse_met_a_jour_sans_dupliquer(self):
        L = lambda p, m: self._ligne(cp.COLONNES, "project_id", p, "maturite", m)
        fusion = cp.fusionner_lignes([L("INGA3_COD", 62)], [L("INGA3_COD", 70)])
        self.assertEqual(len(fusion), 1)
        self.assertEqual(fusion[0][cp.COLONNES.index("maturite")], "70")

    def test_lignes_sans_identifiant_ignorees(self):
        self.assertEqual(cp.fusionner_lignes([[""] * len(cp.COLONNES)], []), [])

    def test_candidats_accumules_entre_runs(self):
        L = lambda p, c: self._ligne(dp.COLONNES, "project_id_propose", p,
                                     "confiance", c)
        fusion = dp.fusionner_candidats([L("A_MLI", 90), L("B_NER", 50)],
                                        [L("C_GIN", 80), L("A_MLI", 95)])
        self.assertEqual(len(fusion), 3)
        # Le run recent fait foi, et le tri met les mieux notes en tete.
        self.assertEqual(fusion[0][dp.COLONNES.index("confiance")], "95")

    def test_plafond_des_candidats(self):
        L = lambda p, c: self._ligne(dp.COLONNES, "project_id_propose", p,
                                     "confiance", c)
        beaucoup = [L("P{}".format(i), i) for i in range(50)]
        self.assertEqual(len(dp.fusionner_candidats([], beaucoup, plafond=10)), 10)

    def test_lecture_de_l_existant_est_best_effort(self):
        class FeuilleCassee:
            def get_all_values(self):
                raise RuntimeError("quota")

        self.assertEqual(cp._lignes_existantes(FeuilleCassee()), [])
        self.assertEqual(dp._lignes_existantes(FeuilleCassee()), [])

    def test_entetes_differents_repartent_a_neuf(self):
        class Feuille:
            def get_all_values(self):
                return [["colonne_inconnue"], ["x"]]

        self.assertEqual(cp._lignes_existantes(Feuille()), [])
