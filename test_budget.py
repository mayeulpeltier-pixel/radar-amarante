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
from datetime import date
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


class TestPrioriteRisqueFraicheur(unittest.TestCase):
    """Avec une file d'attente de plusieurs runs, l'ordre de passage decide
    quelles offres seront analysees TROP TARD. Le risque domine, la fraicheur
    departage."""

    AUJOURD_HUI = date(2026, 7, 22)

    def _f(self, iso):
        return (iso or "").strip()

    def test_fraicheur_decroissante(self):
        rw = ted_complet_reliefweb
        f = lambda d: rw.facteur_fraicheur(d, self.AUJOURD_HUI)
        self.assertEqual(f("2026-07-22"), 1.0)                 # aujourd'hui
        self.assertGreater(f("2026-07-19"), f("2026-07-05"))   # 3 j > 17 j
        self.assertAlmostEqual(f("2026-06-22"), 0.4, places=2)  # 30 j : plancher
        self.assertAlmostEqual(f("2025-01-01"), 0.4, places=2)  # jamais sous 0.4

    def test_date_absente_ou_illisible_ni_favorisee_ni_condamnee(self):
        rw = ted_complet_reliefweb
        for brut in ("", None, "pas une date", "2026-13-45"):
            v = rw.facteur_fraicheur(brut, self.AUJOURD_HUI)
            self.assertEqual(v, 0.6, "valeur inattendue pour {!r}".format(brut))

    def test_le_risque_domine_la_fraicheur(self):
        """Une offre somalienne de trois semaines doit rester prioritaire sur
        une offre du jour dans un pays peu expose."""
        rw = ted_complet_reliefweb
        somalie_ancienne = {"pays_iso3": "SOM", "date_publication": "2026-07-01"}
        senegal_du_jour = {"pays_iso3": "SEN", "date_publication": "2026-07-22"}
        self.assertGreater(rw.priorite_analyse(somalie_ancienne, self.AUJOURD_HUI),
                           rw.priorite_analyse(senegal_du_jour, self.AUJOURD_HUI))

    def test_a_risque_egal_le_plus_recent_passe_devant(self):
        """LE cas du 22/07/2026 : 278 offres en attente, une somalienne de 28
        jours passait avant une ukrainienne d'hier. Meme tier de risque."""
        rw = ted_complet_reliefweb
        somalie_28j = {"pays_iso3": "SOM", "date_publication": "2026-06-24"}
        ukraine_1j = {"pays_iso3": "UKR", "date_publication": "2026-07-21"}
        self.assertGreater(rw.priorite_analyse(ukraine_1j, self.AUJOURD_HUI),
                           rw.priorite_analyse(somalie_28j, self.AUJOURD_HUI))

    def test_le_tri_precede_le_plafond(self):
        """Trier apres avoir tronque ne servirait a rien."""
        src = _source(ted_complet_reliefweb, "main")
        tri = src.index("priorite_analyse")
        plafond = src.index("MAX_AVIS_LLM_RW:")
        self.assertLess(tri, plafond)


if __name__ == "__main__":
    unittest.main(verbosity=2)
