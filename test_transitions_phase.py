# -*- coding: utf-8 -*-
"""P1.4 — Émettre les transitions de phase montantes (26/08/2026).

CE QUI MANQUAIT
---------------
Le RECUL était détecté et affiché (`seau["recul"]`, badge « ↓ recul de
phase »). Les MONTÉES ne produisaient rien. Un projet qui passait de
« financement approuvé » à « appel d'offres EPC » changeait de phase
silencieusement -- alors que c'est exactement le moment où il faut agir :
l'EPC qui va être choisi est celui qui déploiera du personnel, et il se
démarche AVANT que le marché sûreté existe.

DEUX CHOIX DE CONCEPTION
------------------------
1. PAS DE DIFF ENTRE DEUX RUNS. Les transitions se dérivent de l'historique,
   qui est déjà daté et trié. C'est sans état, testable, et ça fonctionne
   rétroactivement sur tout ce qui est déjà collecté. Un diff de runs aurait
   exigé un fichier d'état de plus et n'aurait rien vu du passé.

2. CORROBORATION, comme `_phase_max_corroboree`. Un seul article mal classé
   ne doit pas déclencher « contacter maintenant ». Le motif est connu : sur
   la reconstruction ukrainienne, un unique signal « exploitation » suffisait
   à afficher une maturité de 100.

Tests OFFLINE : fonctions pures + contrats du gabarit, aucun réseau.
"""

import datetime
import re
import unittest

import projets as pj
import radar_cockpit as ck


AUJ = datetime.date(2026, 8, 26)


def _h(date, phase, titre="", n=2):
    """`n` occurrences d'une phase, pour satisfaire la corroboration."""
    return [{"date": date, "phase": phase, "titre": titre, "lien": ""}
            for _ in range(n)]


HISTORIQUE = (_h("2024-01-10", "FEASIBILITY", "Étude")
              + _h("2025-06-11", "FUNDING_APPROVED", "BAD approuve 400 M$")
              + _h("2026-07-20", "EPC_PROCUREMENT", "AO EPC lancé"))


class TestDetectionDesMontees(unittest.TestCase):

    def test_les_montees_sont_detectees(self):
        ms = pj.transitions_montantes(HISTORIQUE)
        self.assertEqual([m["vers"] for m in ms],
                         ["FUNDING_APPROVED", "EPC_PROCUREMENT"])

    def test_la_premiere_phase_n_est_pas_une_montee(self):
        """Apparaître en faisabilité n'est pas franchir une étape."""
        ms = pj.transitions_montantes(_h("2024-01-10", "FEASIBILITY"))
        self.assertEqual(ms, [])

    def test_un_signal_isole_ne_declenche_rien(self):
        """LE garde-fou. Un article mal classé annonçant « exploitation »
        déclencherait sinon la plus forte alerte du système."""
        hist = HISTORIQUE + [{"date": "2026-08-20", "phase": "OPERATIONS",
                              "titre": "article douteux", "lien": ""}]
        self.assertNotIn("OPERATIONS",
                         [m["vers"] for m in pj.transitions_montantes(hist)])

    def test_un_aller_retour_ne_reemet_pas_la_montee(self):
        """Un projet qui oscille entre deux phases produirait une alerte à
        chaque va-et-vient, et le signal deviendrait du bruit."""
        hist = (HISTORIQUE
                + _h("2026-07-25", "FUNDING_APPROVED", "recul")
                + _h("2026-08-01", "EPC_PROCUREMENT", "re-montée"))
        vers = [m["vers"] for m in pj.transitions_montantes(hist)]
        self.assertEqual(vers.count("EPC_PROCUREMENT"), 1)

    def test_le_saut_de_plusieurs_rangs_est_mesure(self):
        ms = pj.transitions_montantes(HISTORIQUE)
        self.assertEqual(ms[0]["saut"], 4)      # FEASIBILITY(4) -> FUNDING(8)

    def test_historique_vide_sans_erreur(self):
        self.assertEqual(pj.transitions_montantes([]), [])
        self.assertEqual(pj.derniere_montee([]), {})


class TestSensCommercial(unittest.TestCase):
    """Sans traduction, « EPC_AWARDED » ne dit rien à un commercial."""

    def test_les_etapes_decisives_sont_critiques(self):
        for phase in ("EPC_PROCUREMENT", "EPC_AWARDED"):
            self.assertEqual(pj.SENS_MONTEE[phase][0], "critique")

    def test_chaque_sens_porte_un_message_actionnable(self):
        for phase, (imp, msg) in pj.SENS_MONTEE.items():
            self.assertIn(imp, pj.ORDRE_IMPORTANCE, phase)
            self.assertTrue(msg.strip(), phase)

    def test_toutes_les_phases_nommees_existent(self):
        """Une clé mal orthographiée retomberait silencieusement en
        « faible » et le signal se perdrait."""
        for phase in pj.SENS_MONTEE:
            self.assertIn(phase, pj.PHASES, phase)

    def test_phase_sans_sens_declare_ne_casse_pas(self):
        hist = _h("2024-01-10", "IDEA") + _h("2024-06-01", "PRE_FEASIBILITY")
        ms = pj.transitions_montantes(hist)
        self.assertEqual(ms[0]["importance"], "faible")


class TestFraicheur(unittest.TestCase):
    """Une montée vers EPC_PROCUREMENT la semaine dernière est une urgence ;
    la même il y a trois ans est une ligne d'historique."""

    def test_montee_recente_signalee(self):
        m = pj.derniere_montee(HISTORIQUE, AUJ)
        self.assertEqual(m["vers"], "EPC_PROCUREMENT")
        self.assertEqual(m["age_jours"], 37)
        self.assertTrue(m["recente"])

    def test_montee_ancienne_non_signalee(self):
        vieux = (_h("2018-01-10", "FEASIBILITY")
                 + _h("2019-06-11", "EPC_PROCUREMENT"))
        m = pj.derniere_montee(vieux, AUJ)
        self.assertFalse(m["recente"])
        self.assertGreater(m["age_jours"], pj.JOURS_MONTEE_RECENTE)

    def test_date_illisible_ne_leve_pas(self):
        hist = _h("pas-une-date", "FEASIBILITY") + _h("aussi-faux", "FID")
        m = pj.derniere_montee(hist, AUJ)
        self.assertIsNone(m["age_jours"])
        self.assertFalse(m["recente"])

    def test_seuil_configurable(self):
        self.assertIsInstance(pj.JOURS_MONTEE_RECENTE, int)
        self.assertGreater(pj.JOURS_MONTEE_RECENTE, 0)


class TestBranchement(unittest.TestCase):

    def test_expose_par_le_constructeur(self):
        src = _lire("projets.py")
        self.assertIn('seau["montees"] = transitions_montantes(hist)', src)
        self.assertIn('seau["montee"] = derniere_montee(hist, aujourd)', src)

    def test_serialise_par_le_collecteur(self):
        src = _lire("collecteur_projets.py")
        for champ in ("montee_vers", "montee_date", "montee_importance",
                      "montee_message", "montee_recente"):
            self.assertIn('"{}"'.format(champ), src)

    def test_colonnes_declarees_dans_le_schema(self):
        src = _lire("collecteur_projets.py")
        bloc = src.split('"opportunite_motifs",')[1][:220]
        self.assertIn("montee_vers", bloc)


class TestAffichage(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit([], suivi={"api": True})

    def test_champs_exposes_au_front(self):
        for c in ("mVers", "mDate", "mImp", "mMsg", "mRecente"):
            self.assertIn(c + ":", self.html)

    def test_seule_une_montee_recente_porte_un_badge(self):
        """Sinon tout projet arrivé à maturité resterait signalé
        indéfiniment et le badge ne voudrait plus rien."""
        self.assertIn("if(p.mRecente&&p.mVers)b.push(", self.html)

    def test_section_dediee_dans_le_tiroir(self):
        self.assertIn("Franchissement d'étape", self.html)
        self.assertIn("ce n\\'est plus un signal d\\'action", self.html)

    def test_importance_portee_par_la_couleur(self):
        self.assertIn(".mb.montee.critique{", self.html)
        self.assertIn(".mt.critique{", self.html)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


class TestNonRegression(unittest.TestCase):

    def test_le_recul_est_toujours_detecte(self):
        """Le pendant descendant ne doit pas avoir été perdu au passage."""
        src = _lire("projets.py")
        self.assertIn('seau["recul"] = bool(hist and rang', src)

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        for attendu in ("santeRun", "function blocDecomposition",
                        "function marquerGagne", "const opps=",
                        "function postureNote"):
            self.assertIn(attendu, html)


if __name__ == "__main__":
    unittest.main()
