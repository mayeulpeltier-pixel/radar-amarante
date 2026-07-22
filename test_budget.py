# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DE L'ORDRE MEMOIRE / PLAFOND.
======================================================

LE BUG DU 22/07/2026
--------------------
Banque Mondiale et ReliefWeb appliquaient leur plafond d'appels LLM AVANT le
filtre memoire. Consequence mesuree sur un run reel : 150 places retenues dont
146 deja analysees, soit 4 avis neufs traites, pendant que des centaines de
candidats attendaient sans jamais avoir leur tour (le tri par risque etant
stable, c'etaient toujours les memes qui passaient la barre). AfDB et EBRD
faisaient deja correctement l'inverse.

Ce fichier verrouille l'invariant pour les QUATRE collecteurs a budget :
    memoire d'abord, plafond ensuite, sur les avis NEUFS.

Methode : on inspecte le code source plutot que d'executer les collecteurs
(qui exigent reseau, Sheet et cles LLM). C'est un test structurel, volontaire :
il attrape la regression exacte qui s'est produite, celle d'un plafond remonte
au-dessus du filtre.
"""

import inspect
import re
import unittest

import afdb_radar
import ebrd_radar
import ted_complet_bm
import ted_complet_reliefweb


# (module, fonction, motif du plafond, motif du filtre memoire)
COLLECTEURS = [
    (ted_complet_bm, "main", r"MAX_AVIS_LLM_BM", r"deja_vus\b"),
    (ted_complet_reliefweb, "main", r"MAX_AVIS_LLM_RW", r"deja_vus\b"),
    (afdb_radar, "main", r"MAX_AVIS_LLM", r"deja_vus\b"),
    (ebrd_radar, "main", r"MAX_AVIS_LLM", r"deja_vus\b"),
]


def _source(module, fonction):
    return inspect.getsource(getattr(module, fonction))


class TestOrdreMemoirePlafond(unittest.TestCase):

    def test_la_memoire_precede_le_plafond(self):
        """L'invariant central : le budget d'analyse doit servir a DECOUVRIR,
        pas a redecouvrir."""
        for module, fonction, motif_plafond, motif_memoire in COLLECTEURS:
            with self.subTest(module=module.__name__):
                src = _source(module, fonction)
                # Premiere utilisation du plafond pour TRONQUER la liste
                # (les simples definitions/impressions ne comptent pas).
                troncature = re.search(
                    r"\[:\s*" + motif_plafond + r"\s*\]", src)
                memoire = re.search(motif_memoire + r"\s*=", src)
                self.assertIsNotNone(
                    memoire, "{} : filtre memoire introuvable".format(module.__name__))
                if troncature is None:
                    # Troncature ecrite en deux temps (BM/RW) : on repere
                    # l'affectation qui coupe la liste.
                    troncature = re.search(
                        r"avis_normalises\s*=\s*avis_normalises\[:\s*"
                        + motif_plafond, src)
                self.assertIsNotNone(
                    troncature,
                    "{} : troncature par le plafond introuvable".format(
                        module.__name__))
                self.assertLess(
                    memoire.start(), troncature.start(),
                    "{} : le plafond tronque AVANT le filtre memoire, le "
                    "budget sera gaspille sur des avis deja connus".format(
                        module.__name__))

    def test_les_plafonds_sont_pilotables(self):
        """Chacun doit pouvoir etre abaisse sans toucher au code, pour lisser
        le cout d'absorption d'un retard."""
        import os
        for variable, module, attribut in [
                ("BM_BUDGET", ted_complet_bm, "MAX_AVIS_LLM_BM"),
                ("RELIEFWEB_BUDGET", ted_complet_reliefweb, "MAX_AVIS_LLM_RW"),
                ("AFDB_BUDGET", afdb_radar, "MAX_AVIS_LLM"),
                ("EBRD_BUDGET", ebrd_radar, "MAX_AVIS_LLM")]:
            with self.subTest(variable=variable):
                src = inspect.getsource(module)
                self.assertIn(variable, src,
                              "{} n'est pas pilotable par {}".format(
                                  attribut, variable))
                self.assertIsInstance(getattr(module, attribut), int)

    def test_le_reliquat_est_annonce(self):
        """Un plafond atteint doit dire combien d'avis restent en attente,
        sinon le retard est invisible (c'est ce qui a masque le bug)."""
        for module in (ted_complet_bm, ted_complet_reliefweb):
            with self.subTest(module=module.__name__):
                src = _source(module, "main")
                self.assertIn("en_attente", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
