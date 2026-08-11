# -*- coding: utf-8 -*-
"""
Cadence : rotation A DEUX VITESSES des signaux prives (02/08/2026).
===================================================================

CE QUE CE FICHIER VERROUILLE
----------------------------
Un balayage modulaire unique met ~3 mois a couvrir ~865 entites -- trop lent
pour des signaux de deploiement qui vivent quelques semaines. Correctif : une
TETE prioritaire (attributaires = deploiement en cours, priorite_socle "haute")
scannee a CHAQUE run via un curseur dedie, et une QUEUE (watchlist curee +
defense) qui tourne normalement via le curseur general.

Proprietes testees :
  - la tete ne contient QUE des attributaires ; le reste va en queue ;
  - la tete est couverte en ceil(n_prio / taille_tete) runs, INDEPENDAMMENT de
    la taille de la queue (c'est tout l'interet : decoupler la latence des
    comptes chauds de la masse) ;
  - la fenetre ne depasse jamais le budget ; aucun doublon ;
  - les curseurs n'avancent que des comptes REELLEMENT traites (honnetes sur un
    arret anticipe) ;
  - sans attributaire, comportement identique a l'ancienne rotation simple ;
  - le second curseur survit en round-trip et reste retrocompatible.
"""

import os
import tempfile
import unittest
from datetime import date, timedelta

import signaux_prives as sp
import radar_etat


def _cpt(nom, prio="moyenne"):
    return {"entreprise": nom, "priorite_socle": prio}


def _attrib(nom, date_attr=None):
    # Attributaire RECENT par defaut (date du jour) : reste en tete prioritaire.
    return {"entreprise": nom, "priorite_socle": "haute",
            "secteur": "Attributaire (marche gagne)",
            "date_attribution": date_attr or date.today().isoformat()}


class TestPartitionTete(unittest.TestCase):
    def test_seuls_les_attributaires_sont_en_tete(self):
        comptes = [_attrib("A1"), _cpt("W1"), _cpt("W2", "haute".replace("haute", "moyenne")),
                   _attrib("A2"), _cpt("D1")]
        comp = sp.composer_fenetre_deux_vitesses(comptes, 35, "10", 0, 0)
        noms_tete = {c["entreprise"] for c in comp["slice_prio"]}
        self.assertEqual(noms_tete, {"A1", "A2"})
        self.assertEqual(comp["n_prio"], 2)
        self.assertEqual(comp["n_reste"], 3)

    def test_est_prioritaire(self):
        recent = date.today().isoformat()
        self.assertTrue(sp._est_prioritaire({"priorite_socle": "haute", "date_attribution": recent}))
        self.assertTrue(sp._est_prioritaire({"priorite_socle": " Haute ", "date_attribution": recent}))
        self.assertFalse(sp._est_prioritaire({"priorite_socle": "haute"}))      # sans date -> queue
        self.assertFalse(sp._est_prioritaire({"priorite_socle": "moyenne", "date_attribution": recent}))
        self.assertFalse(sp._est_prioritaire({}))


class TestTailleTete(unittest.TestCase):
    def test_bornee_par_pool_et_fenetre(self):
        self.assertEqual(sp.tete_pour("10", 35, 25), 10)
        self.assertEqual(sp.tete_pour("10", 35, 4), 4)      # pool plus petit
        self.assertEqual(sp.tete_pour("40", 35, 100), 35)   # jamais > fenetre
        self.assertEqual(sp.tete_pour(None, 35, 100), 10)   # defaut
        self.assertEqual(sp.tete_pour("0", 35, 100), 0)     # tete desactivee
        self.assertEqual(sp.tete_pour("abc", 35, 100), 10)  # illisible -> defaut


class TestFenetre(unittest.TestCase):
    def test_ne_depasse_pas_le_budget_et_sans_doublon(self):
        comptes = [_attrib("A%d" % i) for i in range(25)] + [_cpt("R%d" % i) for i in range(100)]
        comp = sp.composer_fenetre_deux_vitesses(comptes, 35, "10", 0, 0)
        self.assertLessEqual(len(comp["fenetre"]), 35)
        self.assertEqual(len(comp["slice_prio"]), 10)
        self.assertEqual(len(comp["slice_reste"]), 25)      # 35 - 10
        noms = [c["entreprise"] for c in comp["fenetre"]]
        self.assertEqual(len(noms), len(set(noms)), "Aucun doublon dans la fenetre.")

    def test_sans_attributaire_comportement_identique_a_l_ancien(self):
        comptes = [_cpt("R%d" % i) for i in range(50)]
        comp = sp.composer_fenetre_deux_vitesses(comptes, 35, "10", 0, 0)
        self.assertEqual(comp["slice_prio"], [])
        self.assertEqual(len(comp["fenetre"]), 35)
        # Rotation positionnelle simple sur la queue, comme avant.
        self.assertEqual([c["entreprise"] for c in comp["fenetre"]],
                         ["R%d" % i for i in range(35)])


class TestLatenceTeteDecoupleeDeLaQueue(unittest.TestCase):
    def test_tete_couverte_en_peu_de_runs_meme_avec_grosse_queue(self):
        n_prio, tete = 25, 10
        comptes = ([_attrib("A%d" % i) for i in range(n_prio)]
                   + [_cpt("R%d" % i) for i in range(500)])   # grosse queue
        cur = cur_prio = 0
        vus_prio, vus_queue = set(), set()
        runs = 0
        while runs < 6:
            comp = sp.composer_fenetre_deux_vitesses(comptes, 35, str(tete), cur, cur_prio)
            for c in comp["slice_prio"]:
                vus_prio.add(c["entreprise"])
            for c in comp["slice_reste"]:
                vus_queue.add(c["entreprise"])
            cur_prio, cur = sp.avancer_curseurs(comp, len(comp["fenetre"]))
            runs += 1
            if len(vus_prio) == n_prio:
                break
        self.assertEqual(len(vus_prio), n_prio, "Toute la tete doit etre couverte.")
        self.assertLessEqual(runs, 3, "ceil(25/10) = 3 runs, quelle que soit la queue.")
        self.assertLess(len(vus_queue), 500, "La queue, elle, n'est pas encore bouclee.")


class TestAvanceCurseurs(unittest.TestCase):
    def _comp(self):
        comptes = [_attrib("A%d" % i) for i in range(25)] + [_cpt("R%d" % i) for i in range(100)]
        return sp.composer_fenetre_deux_vitesses(comptes, 35, "10", 0, 0)

    def test_fenetre_entiere_traitee(self):
        comp = self._comp()                      # 10 tete + 25 queue
        pp, pq = sp.avancer_curseurs(comp, len(comp["fenetre"]))
        self.assertEqual(pp, 10)                 # 0 + 10
        self.assertEqual(pq, 25)                 # 0 + 25

    def test_arret_dans_la_tete_ne_bouge_pas_la_queue(self):
        comp = self._comp()
        pp, pq = sp.avancer_curseurs(comp, 4)    # arret apres 4 (dans la tete)
        self.assertEqual(pp, 4)
        self.assertEqual(pq, 0, "La queue n'a pas ete entamee : son curseur ne bouge pas.")

    def test_arret_dans_la_queue(self):
        comp = self._comp()
        pp, pq = sp.avancer_curseurs(comp, 17)   # 10 tete + 7 queue
        self.assertEqual(pp, 10)
        self.assertEqual(pq, 7)

    def test_wrap_around(self):
        comptes = [_attrib("A%d" % i) for i in range(12)]     # que des prioritaires
        comp = sp.composer_fenetre_deux_vitesses(comptes, 35, "10", 0, 10)
        # debut_prio = 10 ; tete = min(10,12,35)=10 ; traite 10 -> (10+10)%12 = 8
        pp, pq = sp.avancer_curseurs(comp, len(comp["fenetre"]))
        self.assertEqual(pp, 8)


class TestRecenceAttributaire(unittest.TestCase):
    """La tete = attributaires RECENTS seulement (deploiement en cours)."""

    def setUp(self):
        self.auj = date(2026, 8, 11)

    def test_recent_est_prioritaire(self):
        recent = (self.auj - timedelta(days=60)).isoformat()
        self.assertTrue(sp._est_prioritaire(
            {"priorite_socle": "haute", "date_attribution": recent}, self.auj))

    def test_ancien_retombe_en_queue(self):
        vieux = (self.auj - timedelta(days=400)).isoformat()   # > 12 mois
        self.assertFalse(sp._est_prioritaire(
            {"priorite_socle": "haute", "date_attribution": vieux}, self.auj))

    def test_sans_date_pas_prioritaire(self):
        self.assertFalse(sp._est_prioritaire({"priorite_socle": "haute"}, self.auj))

    def test_non_haute_jamais_prioritaire(self):
        recent = (self.auj - timedelta(days=10)).isoformat()
        self.assertFalse(sp._est_prioritaire(
            {"priorite_socle": "moyenne", "date_attribution": recent}, self.auj))

    def test_composer_exclut_les_attributaires_anciens_de_la_tete(self):
        vieux = _attrib("VIEUX", (date.today() - timedelta(days=500)).isoformat())
        recent = _attrib("RECENT")                              # date du jour
        comptes = [vieux, recent] + [_cpt("R%d" % i) for i in range(30)]
        comp = sp.composer_fenetre_deux_vitesses(comptes, 35, "10", 0, 0)
        tete = {c["entreprise"] for c in comp["slice_prio"]}
        self.assertIn("RECENT", tete)
        self.assertNotIn("VIEUX", tete, "Un vieux marche n'est plus 'en cours' -> queue.")
        # VIEUX n'est pas perdu : il est dans la queue.
        queue = {c["entreprise"] for c in comp["slice_reste"]}
        self.assertIn("VIEUX", queue)


class TestSeedCaptureDate(unittest.TestCase):
    def test_capture_la_date_la_plus_recente_par_societe(self):
        valeurs = [["gagnant", "pays_execution", "date_publication"],
                   ["Acme Security", "Mali", "2026-01-10"],
                   ["Acme Security", "Niger", "2026-07-01"],   # plus recent
                   ["Beta Defense", "Tchad", ""]]
        comptes = sp.seed_depuis_attributions(valeurs)
        par_nom = {c["entreprise"]: c for c in comptes}
        self.assertIn("Acme Security", par_nom)
        self.assertEqual(par_nom["Acme Security"]["date_attribution"], "2026-07-01")
        self.assertEqual(par_nom["Beta Defense"]["date_attribution"], "")


class TestSecondCurseurEtat(unittest.TestCase):
    def _chemin(self):
        d = tempfile.mkdtemp()
        return os.path.join(d, "etat.json")

    def test_round_trip(self):
        ch = self._chemin()
        radar_etat.sauver(7, ["a", "b"], ["c"], chemin=ch, curseur_prio=3)
        self.assertEqual(radar_etat.charger_prio(ch), 3)
        cur, vus = radar_etat.charger(ch)
        self.assertEqual(cur, 7)                 # curseur general inchange
        self.assertEqual(vus, ["a", "b", "c"])

    def test_retrocompatible_fichier_sans_la_cle(self):
        ch = self._chemin()
        # Ecrit un etat "ancien" sans curseur_prio (sauver par defaut = 0, mais on
        # simule l'absence de cle en ecrivant a la main).
        import json
        with open(ch, "w", encoding="utf-8") as f:
            json.dump({"curseur": 5, "vus": []}, f)
        self.assertEqual(radar_etat.charger_prio(ch), 0)   # absente -> 0, pas d'erreur
        cur, vus = radar_etat.charger(ch)
        self.assertEqual(cur, 5)

    def test_defaut_zero_si_non_precise(self):
        ch = self._chemin()
        radar_etat.sauver(2, [], chemin=ch)      # pas de curseur_prio -> 0
        self.assertEqual(radar_etat.charger_prio(ch), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
