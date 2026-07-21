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
    # (module, fonction d'ecriture, onglet attendu, constructeur, colonnes)
    ("ted_complet_v14", "ecrire_resultats_dans_sheet", "ted_radar",
     "ligne_depuis_resultat", "COLONNES_SHEET"),
    ("ted_complet_bm", "ecrire_resultats_bm", "bm_radar",
     "ligne_depuis_resultat_bm", "COLONNES_BM"),
    ("ted_complet_reliefweb", "ecrire_resultats_rw", "reliefweb_radar",
     "ligne_depuis_resultat_rw", "COLONNES_RW"),
    ("afdb_radar", "ecrire_resultats", "afdb_radar",
     "ligne_depuis_resultat", "COLONNES_AFDB"),
    ("ebrd_radar", "ecrire_resultats", "ebrd_radar",
     "ligne_depuis_resultat", "COLONNES_EBRD"),
    ("ungm_radar", "ecrire", "ungm_radar",
     "ligne_depuis_resultat", "COLONNES_UNGM"),
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
        for module, fonction, onglet_attendu, _b, _c in CABLAGES:
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

        for module, fonction, _onglet, _b, _c in CABLAGES:
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

    def test_le_miroir_recoit_la_forme_plate_du_sheet(self):
        """CANONICALISATION (etape 3 du cap produit) : le miroir doit recevoir
        des dicts PLATS alignes sur les colonnes du Sheet -- la forme que lit
        le dashboard -- avec publication_number a la RACINE. On patche le
        constructeur de ligne pour une valeur deterministe par colonne."""
        for module, fonction, _onglet, builder, colonnes in CABLAGES:
            with self.subTest(module=module):
                try:
                    mod = importlib.import_module(module)
                except Exception as e:
                    self.skipTest("{} indisponible ({})".format(module, e))
                cols = getattr(mod, colonnes)
                self.assertIn("publication_number", cols, module)
                appels = []
                orig_miroir = radar_stockage.ecrire_miroir
                orig_builder = getattr(mod, builder)
                radar_stockage.ecrire_miroir = (
                    lambda onglet, lignes: appels.append(list(lignes)) or "ok")
                setattr(mod, builder, lambda r: [c + "!" for c in cols])
                try:
                    getattr(mod, fonction)(FeuilleInerte(), [])
                    # Une "resultat" opaque suffit : le constructeur est patche.
                    class FeuillePermissive(FeuilleInerte):
                        def append_rows(self, *a, **k):
                            pass
                        def batch_update(self, *a, **k):
                            pass
                    appels.clear()
                    getattr(mod, fonction)(FeuillePermissive(),
                                           [{"avis": {}}])
                finally:
                    radar_stockage.ecrire_miroir = orig_miroir
                    setattr(mod, builder, orig_builder)
                ligne = appels[0][0]
                self.assertEqual(set(ligne), set(cols), module)
                self.assertEqual(ligne["publication_number"],
                                 "publication_number!", module)


if __name__ == "__main__":
    unittest.main(verbosity=2)
