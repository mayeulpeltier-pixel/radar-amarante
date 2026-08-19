# -*- coding: utf-8 -*-
"""Angle DIPLOMATIE ECONOMIQUE (signal precoce) : nouveau type_activite
'delegation_mission' + passe Google News dediee par entreprise, OFF par defaut.

On verifie le cablage sans reseau (mock de la collecte locale) : le flag ajoute
bien une passe, la requete personnalisee la desactive, et le socle (poids,
declencheurs, taxonomie du prompt) est en place.
"""

import unittest
from unittest import mock

import bitd_signaux as bitd
import signaux_prives as sp


class TestSocle(unittest.TestCase):

    def test_poids_precoce(self):
        self.assertEqual(bitd.POIDS_ACTIVITE["delegation_mission"], 0.35)

    def test_declencheurs_fr_et_en(self):
        self.assertIn("MEDEF", bitd.DECLENCHEURS_DIPLO)
        self.assertIn("mission économique", bitd.DECLENCHEURS_DIPLO)
        self.assertIn("trade delegation", bitd.DECLENCHEURS_DIPLO)

    def test_taxonomie_dans_le_prompt(self):
        with open("bitd_signaux.py", encoding="utf-8") as f:
            src = f.read()
        # poids + constante + 2 enums = au moins 4 mentions
        self.assertGreaterEqual(src.count("delegation_mission"), 4)

    def test_defaut_off_et_fenetre_30j(self):
        self.assertFalse(sp.DIPLO_ON)
        self.assertEqual(sp.DIPLO_JOURS, 30)


class TestPasseDiplo(unittest.TestCase):

    def _run(self, requete=""):
        with mock.patch.object(sp, "_collecter_news_locale", return_value=[]) as m, \
             mock.patch.object(sp, "GNEWS_LOCALES", [("fr", "FR", "FR:fr")]):
            sp.collecter_news("Vinci", requete=requete, session=mock.MagicMock())
            return m.call_count

    def test_flag_off_une_seule_passe(self):
        with mock.patch.object(sp, "DIPLO_ON", False):
            self.assertEqual(self._run(), 1)  # requete de base seulement

    def test_flag_on_ajoute_la_passe_diplo(self):
        with mock.patch.object(sp, "DIPLO_ON", True):
            self.assertEqual(self._run(), 2)  # base + diplomatie

    def test_requete_perso_desactive_la_passe(self):
        """Une requete ciblee de la watchlist prime : pas de passe diplo."""
        with mock.patch.object(sp, "DIPLO_ON", True):
            self.assertEqual(self._run(requete='"Vinci" chantier Mali'), 1)


if __name__ == "__main__":
    unittest.main()
