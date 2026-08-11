# -*- coding: utf-8 -*-
"""
Persistance des stats de run (02/08/2026).
==========================================

CE QUE CE FICHIER VERROUILLE
----------------------------
Les stats de run (sante par source, KPI de retroaction en ombre) sont persistees
une ligne par run dans l'onglet synthetique 'runs_radar' (table generique de
radar_stockage, aucun nouveau schema), pour tracer la tendance.

Proprietes testees :
  - l'enregistrement est unique par (type, run) -> accumulation, pas d'ecrasement ;
  - les charges (sante / ombre) sont extraites correctement (KPI derives) ;
  - l'ecriture ET la lecture sont BEST-EFFORT : base inactive -> phrase + [],
    jamais d'exception ;
  - round-trip : ce qu'on ecrit se relit, filtre par type et trie par recence.
"""

import unittest

import radar_runs
import radar_stockage


class TestConstruction(unittest.TestCase):
    def test_identifiant_unique_par_type_et_run(self):
        r = radar_runs.construire_enregistrement("ombre", {"n": 3}, horo="2026-08-10T09:00:00+00:00")
        self.assertEqual(r["type"], "ombre")
        self.assertEqual(r["horodatage"], "2026-08-10T09:00:00+00:00")
        self.assertEqual(r["publication_number"], "ombre|2026-08-10T09:00:00+00:00")
        self.assertEqual(r["n"], 3)

    def test_deux_types_meme_run_ne_se_confondent_pas(self):
        h = "2026-08-10T09:00:00+00:00"
        a = radar_runs.construire_enregistrement("sante", {}, horo=h)
        b = radar_runs.construire_enregistrement("ombre", {}, horo=h)
        self.assertNotEqual(a["publication_number"], b["publication_number"])

    def test_horodatage_par_defaut(self):
        r = radar_runs.construire_enregistrement("sante", {})
        self.assertTrue(r["horodatage"])                 # un horodatage est pose


class TestCharges(unittest.TestCase):
    def test_charge_sante(self):
        sante = {"actives": 5, "a_verifier": 2,
                 "sources": [{"src": "TED", "n": 12, "age": 1, "etat": "frais"},
                             {"src": "MIGA", "n": 0, "age": None, "etat": "absent"}]}
        c = radar_runs.charge_sante(sante)
        self.assertEqual(c["actives"], 5)
        self.assertEqual(c["a_verifier"], 2)
        self.assertEqual(len(c["sources"]), 2)
        self.assertEqual(c["sources"][0], {"src": "TED", "n": 12, "age": 1, "etat": "frais"})

    def test_charge_ombre_calcule_le_delta_moyen(self):
        obs = {"n": 4, "actions_changees": 2, "vers_contacter": 1,
               "quittent_contacter": 1, "somme_delta_abs": 5.2}
        c = radar_runs.charge_ombre(obs, "ombre")
        self.assertEqual(c["mode"], "ombre")
        self.assertEqual(c["n"], 4)
        self.assertEqual(c["actions_changees"], 2)
        self.assertAlmostEqual(c["delta_moyen"], 1.3, places=3)

    def test_charge_ombre_vide_delta_zero(self):
        c = radar_runs.charge_ombre({"n": 0, "somme_delta_abs": 0.0})
        self.assertEqual(c["n"], 0)
        self.assertEqual(c["delta_moyen"], 0.0)


class TestBestEffortSansBase(unittest.TestCase):
    """Sans DATABASE_URL : radar_stockage.actif() est faux -> tout est inerte,
    aucune exception ne remonte."""

    def test_enregistrer_ne_leve_pas(self):
        phrase = radar_runs.enregistrer("sante", {"actives": 1})
        self.assertIn("inactif", phrase)

    def test_historique_vide(self):
        self.assertEqual(radar_runs.historique(), [])


class _FauxCurseur:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class TestRoundTripSimule(unittest.TestCase):
    """Ecriture -> lecture, via un radar_stockage simule (pas de vraie base)."""

    def setUp(self):
        self._store = []
        self._sav = {k: getattr(radar_stockage, k) for k in
                     ("actif", "ecrire_miroir", "connexion", "initialiser", "lire_onglet")}
        radar_stockage.actif = lambda: True
        radar_stockage.ecrire_miroir = lambda onglet, lignes: (
            self._store.extend(lignes) or "miroir '{}' : {} ecrite(s)".format(onglet, len(lignes)))
        radar_stockage.connexion = lambda *a, **k: _FauxCurseur()
        radar_stockage.initialiser = lambda conn: None
        radar_stockage.lire_onglet = lambda conn, onglet: list(self._store)

    def tearDown(self):
        for k, v in self._sav.items():
            setattr(radar_stockage, k, v)

    def test_ce_qui_est_ecrit_se_relit(self):
        radar_runs.enregistrer("sante", {"actives": 5}, horo="2026-08-10T08:00:00+00:00")
        radar_runs.enregistrer("ombre", {"n": 3}, horo="2026-08-10T09:00:00+00:00")
        hist = radar_runs.historique()
        self.assertEqual(len(hist), 2)
        # Plus recent d'abord (09:00 avant 08:00).
        self.assertEqual(hist[0]["type"], "ombre")
        self.assertEqual(hist[1]["type"], "sante")

    def test_filtre_par_type(self):
        radar_runs.enregistrer("sante", {"actives": 5}, horo="2026-08-10T08:00:00+00:00")
        radar_runs.enregistrer("ombre", {"n": 3}, horo="2026-08-10T09:00:00+00:00")
        ombres = radar_runs.historique(type_="ombre")
        self.assertEqual(len(ombres), 1)
        self.assertEqual(ombres[0]["type"], "ombre")

    def test_limite(self):
        for i in range(5):
            radar_runs.enregistrer("sante", {"i": i}, horo="2026-08-10T0{}:00:00+00:00".format(i))
        self.assertEqual(len(radar_runs.historique(limite=2)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
