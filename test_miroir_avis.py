# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DE CABLAGE DU MIROIR POSTGRES (onglets d'AVIS).
========================================================================

Les six ecrivains d'avis (TED, BM, AfDB, EBRD, ReliefWeb, UNGM) doivent tous
appeler le miroir avec LEUR onglet et la liste complete des resultats.
L'astuce de test : appeler chaque `ecrire*` avec une liste VIDE. La boucle
d'ecriture Sheet ne fait alors rien (aucune fixture a construire), mais le
hook miroir, inconditionnel, doit quand meme partir -- c'est precisement le
cablage qu'on verifie, module par module, sans reseau ni base.

La logique interne du miroir (deduplication, pannes, extraction du
publication_number imbrique) est testee dans test_stockage.py ; ici on ne
verifie QUE le branchement.
"""

import importlib
import unittest

import radar_stockage


CABLAGES = [
    # (module, fonction d'ecriture, onglet attendu)
    ("ted_complet_v14", "ecrire_resultats_dans_sheet", "ted_radar"),
    ("ted_complet_bm", "ecrire_resultats_bm", "bm_radar"),
    ("ted_complet_reliefweb", "ecrire_resultats_rw", "reliefweb_radar"),
    ("afdb_radar", "ecrire_resultats", "afdb_radar"),
    ("ebrd_radar", "ecrire_resultats", "ebrd_radar"),
    ("ungm_radar", "ecrire", "ungm_radar"),
]


class FeuilleInerte:
    """Doublure minimale : liste vide, et toute ecriture est interdite (une
    liste de resultats vide ne doit produire AUCUN appel Sheet)."""

    def get_all_records(self):
        return []

    def append_rows(self, *a, **k):
        raise AssertionError("append_rows appele avec une liste vide")

    def batch_update(self, *a, **k):
        raise AssertionError("batch_update appele avec une liste vide")


class TestCablageMiroirAvis(unittest.TestCase):

    def test_les_six_ecrivains_d_avis_alimentent_le_miroir(self):
        for module, fonction, onglet_attendu in CABLAGES:
            with self.subTest(module=module):
                try:
                    mod = importlib.import_module(module)
                except Exception as e:
                    self.skipTest("{} indisponible ({})".format(module, e))
                appels = []
                original = radar_stockage.ecrire_miroir
                radar_stockage.ecrire_miroir = (
                    lambda onglet, lignes: appels.append((onglet, list(lignes)))
                    or "miroir factice")
                try:
                    getattr(mod, fonction)(FeuilleInerte(), [])
                finally:
                    radar_stockage.ecrire_miroir = original
                self.assertEqual(len(appels), 1,
                                 "{}.{} n'appelle pas le miroir".format(
                                     module, fonction))
                self.assertEqual(appels[0], (onglet_attendu, []),
                                 module)

    def test_un_miroir_qui_explose_ne_casse_aucun_ecrivain(self):
        """Meme garantie que cote attributions : la panne du miroir est une
        ligne de journal, jamais une exception remontee au collecteur."""
        def bombe(_onglet, _lignes):
            raise RuntimeError("panne simulee")

        for module, fonction, _onglet in CABLAGES:
            with self.subTest(module=module):
                try:
                    mod = importlib.import_module(module)
                except Exception as e:
                    self.skipTest("{} indisponible ({})".format(module, e))
                original = radar_stockage.ecrire_miroir
                radar_stockage.ecrire_miroir = bombe
                try:
                    resultat = getattr(mod, fonction)(FeuilleInerte(), [])
                finally:
                    radar_stockage.ecrire_miroir = original
                self.assertEqual(tuple(resultat), (0, 0), module)


if __name__ == "__main__":
    unittest.main(verbosity=2)
