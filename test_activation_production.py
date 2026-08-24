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
