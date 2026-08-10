# -*- coding: utf-8 -*-
"""
Retroaction en mode OMBRE (item 7, volet observation -- 02/08/2026).
====================================================================

CE QUE CE FICHIER VERROUILLE
----------------------------
RADAR_RETROACTION a desormais TROIS etats : off (defaut), ombre, actif.

  - MODE OMBRE : on charge les multiplicateurs et on CALCULE ce que la
    retroaction ferait (combien d'actions changeraient), mais on n'applique
    RIEN au score renvoye. C'est l'etape de mesure avant de brancher en direct.
  - MODE ACTIF : on applique au score (comportement historique).
  - MODE OFF : rien, scoring strictement inchange.

Doctrine testee : en ombre, le score et l'action RENVOYES sont ceux SANS
retroaction ; seul l'accumulateur d'observation enregistre l'effet potentiel.
La regle d'action est UNIQUE (_action_pour), partagee par le score reel et
l'observation, pour qu'ils ne divergent jamais.
"""

import unittest

import signaux_prives as sp
import radar_retroaction as retro

try:
    import ted_complet_v14 as ted
    _ISO = next(iter(ted.CODES_PAYS_SUIVIS))
    _TED_OK = True
except Exception:
    _TED_OK = False


class TestModeRetroaction(unittest.TestCase):
    def test_off_par_defaut(self):
        self.assertEqual(sp._mode_retroaction("0"), "off")
        self.assertEqual(sp._mode_retroaction(""), "off")
        self.assertEqual(sp._mode_retroaction("nimportequoi"), "off")

    def test_actif(self):
        for v in ("1", "actif", "ACTIF", "on", "live"):
            self.assertEqual(sp._mode_retroaction(v), "actif")

    def test_ombre(self):
        for v in ("ombre", "OMBRE", "shadow", "observation", "dry"):
            self.assertEqual(sp._mode_retroaction(v), "ombre")


class TestActionPour(unittest.TestCase):
    def test_seuils(self):
        import bitd_signaux as bitd
        self.assertEqual(sp._action_pour(bitd.SEUIL_CONTACTER, 0.9), "contacter")
        self.assertEqual(sp._action_pour(bitd.SEUIL_SURVEILLER, 0.9), "surveiller")
        self.assertEqual(sp._action_pour(bitd.SEUIL_SURVEILLER - 0.1, 0.9), "ignorer")

    def test_garde_fou_confiance_plafonne_contacter(self):
        import bitd_signaux as bitd
        self.assertEqual(sp._action_pour(bitd.SEUIL_CONTACTER, 0.1), "surveiller")


class TestEnregistrerOmbre(unittest.TestCase):
    """Les compteurs d'observation (deterministes, sans dependance au scoring)."""

    def test_montee_vers_contacter(self):
        obs = sp.nouvel_observateur_ombre()
        sp._enregistrer_ombre(obs, "surveiller", "contacter", 5.0, 5.6)
        self.assertEqual(obs["n"], 1)
        self.assertEqual(obs["actions_changees"], 1)
        self.assertEqual(obs["vers_contacter"], 1)
        self.assertEqual(obs["quittent_contacter"], 0)
        self.assertAlmostEqual(obs["somme_delta_abs"], 0.6, places=6)

    def test_sortie_de_contacter(self):
        obs = sp.nouvel_observateur_ombre()
        sp._enregistrer_ombre(obs, "contacter", "surveiller", 6.0, 5.2)
        self.assertEqual(obs["actions_changees"], 1)
        self.assertEqual(obs["vers_contacter"], 0)
        self.assertEqual(obs["quittent_contacter"], 1)

    def test_action_inchangee_ne_compte_pas_de_changement(self):
        obs = sp.nouvel_observateur_ombre()
        sp._enregistrer_ombre(obs, "surveiller", "surveiller", 4.0, 4.3)
        self.assertEqual(obs["n"], 1)
        self.assertEqual(obs["actions_changees"], 0)
        self.assertAlmostEqual(obs["somme_delta_abs"], 0.3, places=6)


class TestResumeOmbre(unittest.TestCase):
    def test_aucune_observation_renvoie_none(self):
        self.assertIsNone(sp.resume_ombre(None))
        self.assertIsNone(sp.resume_ombre(sp.nouvel_observateur_ombre()))

    def test_phrase_quand_observations(self):
        obs = sp.nouvel_observateur_ombre()
        sp._enregistrer_ombre(obs, "surveiller", "contacter", 5.0, 5.6)
        phrase = sp.resume_ombre(obs)
        self.assertIsNotNone(phrase)
        self.assertIn("OMBRE", phrase)
        self.assertIn("Rien n'a ete applique", phrase)


@unittest.skipUnless(_TED_OK, "ted_complet_v14 indisponible")
class TestScorerSignalModes(unittest.TestCase):
    """Integration : mode off / ombre / actif au niveau de scorer_signal."""

    def _extraction(self, **kw):
        base = {"type_activite": "implantation", "imminence": "immediate", "confiance": 0.9}
        base.update(kw)
        return base

    def setUp(self):
        self._sav = (sp._RETRO, sp._RETRO_MODE, sp._RETRO_OMBRE)
        # Score BRUT de reference (retroaction off).
        sp._RETRO, sp._RETRO_MODE, sp._RETRO_OMBRE = None, "off", None
        self.brut = sp.scorer_signal(self._extraction(), "haute", iso3=_ISO)
        self.assertIsNotNone(self.brut, "Pays de test doit etre suivi.")
        # Multiplicateurs qui poussent le secteur 'implantation' au maximum borne.
        self.mults = {"secteur": {"implantation": retro.MULT_MAX}, "zone": {}}
        self.mult_effectif = retro.mult_pour(self.mults, "implantation", self.brut["zone"])

    def tearDown(self):
        sp._RETRO, sp._RETRO_MODE, sp._RETRO_OMBRE = self._sav

    def test_ombre_n_applique_rien_mais_observe(self):
        sp._RETRO, sp._RETRO_MODE = self.mults, "ombre"
        sp._RETRO_OMBRE = sp.nouvel_observateur_ombre()
        r = sp.scorer_signal(self._extraction(), "haute", iso3=_ISO)
        # Le score/action RENVOYES sont ceux SANS retroaction.
        self.assertEqual(r["final"], self.brut["final"])
        self.assertEqual(r["action"], self.brut["action"])
        # L'observateur a enregistre exactement 1 signal, avec le bon delta.
        obs = sp._RETRO_OMBRE
        self.assertEqual(obs["n"], 1)
        attendu_ombre = round(self.brut["final"] * self.mult_effectif, 1)
        self.assertAlmostEqual(obs["somme_delta_abs"],
                               abs(attendu_ombre - self.brut["final"]), places=6)
        # Le changement d'action enregistre correspond a la regle unique.
        attendu_action = sp._action_pour(attendu_ombre, 0.9)
        self.assertEqual(obs["actions_changees"],
                         1 if attendu_action != self.brut["action"] else 0)

    def test_actif_applique_le_multiplicateur_au_score(self):
        sp._RETRO, sp._RETRO_MODE, sp._RETRO_OMBRE = self.mults, "actif", None
        r = sp.scorer_signal(self._extraction(), "haute", iso3=_ISO)
        self.assertEqual(r["final"], round(self.brut["final"] * self.mult_effectif, 1))

    def test_off_laisse_le_score_intact(self):
        sp._RETRO, sp._RETRO_MODE, sp._RETRO_OMBRE = None, "off", None
        r = sp.scorer_signal(self._extraction(), "haute", iso3=_ISO)
        self.assertEqual(r["final"], self.brut["final"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
