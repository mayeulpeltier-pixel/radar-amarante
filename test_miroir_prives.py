# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DE CABLAGE DU MIROIR POSTGRES (signaux privés + enrichissement).
========================================================================================

`signaux_prives.py` et `enrichir_entreprises.py` écrivaient dans le Google Sheet
mais JAMAIS dans le miroir Postgres, oubliés lors de la bascule du 22/07/2026 :
leurs onglets (`prive_radar`, `entreprises_enrichies`) étaient gelés dans la
source de vérité depuis 3 mois (détecté par le tableau de santé).

Ces tests verrouillent le branchement : la fonction d'écriture dédiée appelle
`ecrire_miroir` avec LE bon onglet, de façon INCONDITIONNELLE (même liste vide),
et le mapping ligne -> dict est aligné POSITIONNELLEMENT sur les colonnes
(règle 4). La logique interne du miroir est testée ailleurs (test_stockage).
"""

import unittest

import radar_stockage
import bitd_signaux as bitd
import signaux_prives as sp
import enrichir_entreprises as en


class CaptureMiroir:
    """Remplace radar_stockage.ecrire_miroir et capture (onglet, lignes)."""
    def __init__(self):
        self.appels = []
        self._orig = radar_stockage.ecrire_miroir

    def __enter__(self):
        radar_stockage.ecrire_miroir = self._capturer
        return self

    def __exit__(self, *a):
        radar_stockage.ecrire_miroir = self._orig

    def _capturer(self, onglet, lignes):
        self.appels.append((onglet, lignes))
        return "capture: {} ligne(s)".format(len(lignes))


class TestSignauxPrives(unittest.TestCase):

    def setUp(self):
        self._orig = bitd.ecrire_resultats
        bitd.ecrire_resultats = lambda feuille, resultats: len(resultats)  # pas de Sheet

    def tearDown(self):
        bitd.ecrire_resultats = self._orig

    def test_cable_le_miroir_meme_vide(self):
        with CaptureMiroir() as cap:
            sp.ecrire_signaux(feuille=None, resultats=[])
        self.assertEqual(len(cap.appels), 1)
        self.assertEqual(cap.appels[0][0], bitd.NOM_ONGLET_PRIVE)   # "prive_radar"
        self.assertEqual(cap.appels[0][1], [])

    def test_alignement_positionnel(self):
        row = list(range(len(bitd.COLONNES_PRIVE)))
        with CaptureMiroir() as cap:
            sp.ecrire_signaux(feuille=None, resultats=[row])
        dicts = cap.appels[0][1]
        self.assertEqual(dicts[0], dict(zip(bitd.COLONNES_PRIVE, row)))


class TestEnrichissement(unittest.TestCase):

    class FeuilleInerte:
        def append_rows(self, *a, **k):
            raise AssertionError("append_rows appele avec une liste vide")

    def test_cable_le_miroir_meme_vide(self):
        with CaptureMiroir() as cap:
            en.ecrire_enrichies(self.FeuilleInerte(), [])
        self.assertEqual(len(cap.appels), 1)
        self.assertEqual(cap.appels[0][0], en.NOM_ONGLET_ENRICHIES)  # "entreprises_enrichies"
        self.assertEqual(cap.appels[0][1], [])

    def test_alignement_positionnel(self):
        row = list(range(len(en.COLONNES_ENRICHIES)))
        feuille_notee = []

        class Feuille:
            def append_rows(self, lignes, **k):
                feuille_notee.append(lignes)
        with CaptureMiroir() as cap:
            en.ecrire_enrichies(Feuille(), [row])
        self.assertEqual(feuille_notee, [[row]])                    # Sheet écrit aussi
        self.assertEqual(cap.appels[0][1][0], dict(zip(en.COLONNES_ENRICHIES, row)))


if __name__ == "__main__":
    unittest.main()
