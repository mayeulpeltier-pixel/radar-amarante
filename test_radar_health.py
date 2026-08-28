# -*- coding: utf-8 -*-
"""P3.1 — Radar Health complet (26/08/2026).

CE QUI EXISTAIT DEJA
--------------------
`radar_runs.sources_muettes()` et `alerter_sources_muettes()` étaient écrites
et testées depuis longtemps. Elles n'étaient affichées NULLE PART. Le bandeau
livré le 25/08 dit « une source s'est-elle tue CE RUN » ; il ne peut pas dire
« une source produisait et ne produit plus depuis trois runs », ce qui est la
vraie question.

CE QUE JE N'AI PAS AFFICHÉ, ET POURQUOI
---------------------------------------
L'audit externe réclamait un « Budget restant : €XX ». Aucun suivi de coût
monétaire n'existe dans le code : `RADAR_ENRICH_BUDGET` est un NOMBRE DE
FICHES par run, pas un montant. Afficher un euro inventé serait exactement le
reproche fait ailleurs au « potentiel Amarante en euros ». Les quotas sont
donc exposés pour ce qu'ils sont, avec la mention explicite à l'écran.

Tests OFFLINE : fonctions pures et contrats du gabarit, aucun réseau.
"""

import re
import unittest

import radar_cockpit as ck
import radar_runs as rr


DETAIL = {
    "muettes": [{"src": "EBRD", "runs_muets": 4}],
    "runs": [{"horo": "2026-08-1%d 06:12" % i, "actives": 9,
              "a_verifier": 2, "total": t}
             for i, t in enumerate([180, 175, 160, 90, 88])],
    "inventaire": {"ted_radar": 4120, "attributions_radar": 890},
    "issues": {"gagne": 3, "perdu": 5},
    "retro": {"mode": "ombre", "peut_activer": False, "n_min": 8},
    "quotas": {"enrichissement": "80"},
}


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


def _run(horo, sources):
    return {"type": "sante", "horodatage": horo,
            "sources": [{"src": s, "n": n} for s, n in sources.items()]}


class TestSourcesMuettes(unittest.TestCase):
    """Le seul vrai signal d'alarme de cet écran. Il existait, invisible."""

    def test_source_en_regression_detectee(self):
        hist = ([_run("2026-08-2%d" % i, {"TED": 50, "EBRD": 0})
                 for i in range(3)]
                + [_run("2026-08-1%d" % i, {"TED": 50, "EBRD": 12})
                   for i in range(3)])
        self.assertEqual([m["src"] for m in rr.sources_muettes(hist, 3)],
                         ["EBRD"])

    def test_source_chroniquement_vide_ne_declenche_rien(self):
        """Sinon l'alerte devient un bruit permanent que plus personne ne lit."""
        hist = [_run("2026-08-2%d" % i, {"TED": 50, "JAMAIS": 0})
                for i in range(6)]
        self.assertEqual(rr.sources_muettes(hist, 3), [])

    def test_historique_trop_court_ne_conclut_pas(self):
        hist = [_run("2026-08-21", {"TED": 0})]
        self.assertEqual(rr.sources_muettes(hist, 3), [])


class TestCollecteEtat(unittest.TestCase):

    def test_best_effort_integral(self):
        """Sans base ni historique, la fonction rend une structure complète
        plutôt que de lever : un indicateur absent ne doit pas en emporter
        d'autres."""
        d = ck.sante_detaillee()
        for cle in ("muettes", "inventaire", "issues", "quotas", "runs",
                    "retro"):
            self.assertIn(cle, d)

    def test_chaque_bloc_echoue_independamment(self):
        src = _lire("radar_cockpit.py")
        bloc = src.split("def sante_detaillee")[1].split("\ndef ")[0]
        self.assertGreaterEqual(bloc.count("except Exception"), 3)

    def test_les_quotas_sont_des_volumes_pas_des_montants(self):
        src = _lire("radar_cockpit.py")
        self.assertIn("NOMBRE DE FICHES par run, pas", src)
        self.assertNotIn("budget_euros", src)


class TestAffichage(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit([], suivi={"api": True},
                                       detail_sante=DETAIL)

    def test_replie_par_defaut(self):
        """Écran d'exploitation, pas information commerciale : l'ouvrir de
        force volerait la place de ce que le commercial vient chercher."""
        self.assertIn('id="santeDetail" style="display:none"', self.html)
        self.assertIn("function basculerSante", self.html)

    def test_les_cinq_sections(self):
        for t in ("Sources en régression", "Tendance de volume",
                  "Lignes en base", "Boucle d'apprentissage",
                  "Quotas de traitement"):
            self.assertIn(t, self.html)

    def test_absence_d_alerte_est_dite_explicitement(self):
        """« Rien » sans phrase se confond avec « pas encore calculé »."""
        self.assertIn("Aucune source en régression", self.html)

    def test_la_retroaction_dit_ce_qui_lui_manque(self):
        """Savoir qu'elle est en ombre ne suffit pas : il faut savoir combien
        d'issues il manque pour en sortir."""
        self.assertIn("volume encore insuffisant", self.html)
        self.assertIn("de chaque côté", self.html)

    def test_la_chute_de_volume_est_interpretee(self):
        """Un graphe sans lecture laisse conclure à une panne là où il n'y a
        qu'une fenêtre de collecte étroite."""
        self.assertIn("étroite qu'une panne", self.html)

    def test_l_absence_de_cout_est_assumee_a_l_ecran(self):
        self.assertIn("aucun suivi de coût en euros n'existe", self.html)

    def test_etat_vide_ne_casse_pas(self):
        html = ck.generer_cockpit([], suivi={"api": True}, detail_sante={})
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])
        self.assertIn("function detailSante", html)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])

    def test_chantiers_precedents_intacts(self):
        for attendu in ("renderAujourdhui", "function blocCompte",
                        "function blocSoumissionnaires", "santeRun"):
            self.assertIn(attendu, self.html)


if __name__ == "__main__":
    unittest.main()
