# -*- coding: utf-8 -*-
"""P1.5 — Back-fill de la vraie date de première détection (26/08/2026).

LE DEFAUT RESIDUEL
------------------
Le 25/08, `lire_onglet` a cessé d'ignorer la colonne `date_detection` : tout
ce qui rentrait dans la fenêtre de collecte ne s'affiche plus « détecté
aujourd'hui ».

Il restait un mensonge plus discret. `radar_lignes.date_detection` vaut
`CURRENT_DATE` à l'INSERTION EN BASE, pas à la première détection réelle. Les
lignes entrées lors du remplissage rétroactif du miroir portent donc toutes la
date de ce remplissage. Faux, mais stable -- alors qu'avant c'était faux ET
glissant. La vraie date vit dans le Sheet, écrite une seule fois à la création
de la ligne.

LE GARDE-FOU CENTRAL
--------------------
`a_corriger` n'accepte qu'une date ANTÉRIEURE. L'opération ne peut que
VIEILLIR une ligne, jamais la rajeunir. Un Sheet corrompu contenant des dates
du jour ne pourrait donc pas recréer le défaut d'origine. C'est ce qui rend ce
script sûr à rejouer.

CE QUE J'AI TROUVÉ EN FAISANT CE CHANTIER
-----------------------------------------
Deux des quatre sous-étapes de P1.5 étaient DÉJÀ FAITES, et mon propre plan
l'ignorait :
  - la mémoire inter-runs n'est plus dans le Sheet depuis longtemps, elle vit
    dans `radar_etat.json` versionné (cf. docstring de `radar_etat`) ;
  - `RADAR_MEMOIRE: "pg"` est déjà posé dans `radar.yml` : la déduplication
    des publications lit déjà Postgres, plus le Sheet.

Tests OFFLINE : fonctions pures, aucune base, aucun réseau.
"""

import datetime
import unittest

import backfill_date_detection as bf


D = datetime.date


class TestLectureDeDate(unittest.TestCase):

    def test_date_iso_lue(self):
        self.assertEqual(bf.date_ou_none("2026-03-04"), D(2026, 3, 4))

    def test_horodatage_tronque_au_jour(self):
        self.assertEqual(bf.date_ou_none("2026-03-04T11:22:33"), D(2026, 3, 4))

    def test_cellule_vide_ignoree(self):
        for vide in ("", "   ", None):
            self.assertIsNone(bf.date_ou_none(vide))

    def test_texte_libre_ignore_plutot_qu_interprete(self):
        """Mieux vaut ignorer une ligne que propager une date inventée :
        c'est exactement le défaut qu'on corrige."""
        for faux in ("hier", "04/03/2026", "2026-13-45", "n.c."):
            self.assertIsNone(bf.date_ou_none(faux), faux)


class TestGardeFou(unittest.TestCase):
    """L'opération ne doit pouvoir que vieillir une ligne."""

    def test_date_anterieure_reprise(self):
        self.assertTrue(bf.a_corriger(D(2026, 3, 4), D(2026, 8, 1)))

    def test_date_posterieure_refusee(self):
        """LE test central. Sinon un Sheet mal rempli recréerait le
        « détecté aujourd'hui » qu'on vient de supprimer."""
        self.assertFalse(bf.a_corriger(D(2026, 8, 25), D(2026, 3, 4)))

    def test_date_identique_sans_ecriture(self):
        """Pas d'UPDATE inutile : le script doit être rejouable à blanc."""
        self.assertFalse(bf.a_corriger(D(2026, 3, 4), D(2026, 3, 4)))

    def test_sheet_sans_date_ne_touche_a_rien(self):
        self.assertFalse(bf.a_corriger(None, D(2026, 8, 1)))

    def test_base_sans_date_accepte_celle_du_sheet(self):
        self.assertTrue(bf.a_corriger(D(2026, 3, 4), None))

    def test_idempotence(self):
        """Rejouer le back-fill après l'avoir appliqué ne doit plus rien
        proposer : c'est ce qui permet de le relancer sans crainte."""
        sheet, pg = D(2026, 3, 4), D(2026, 8, 1)
        self.assertTrue(bf.a_corriger(sheet, pg))
        pg = sheet                                   # après application
        self.assertFalse(bf.a_corriger(sheet, pg))


class TestDoublonsDeCollecte(unittest.TestCase):
    """Une même publication peut apparaître deux fois dans un onglet."""

    def test_la_plus_ancienne_gagne(self):
        """Vérifie la LOGIQUE du code source, pas un commentaire : une
        assertion sur une docstring ne garantit aucun comportement."""
        with open("backfill_date_detection.py", encoding="utf-8") as f:
            bloc = f.read().split("def lignes_sheet")[1].split("\ndef ")[0]
        self.assertIn("min(d, out[pub]) if pub in out else d", bloc)

    def test_la_regle_donne_bien_la_plus_ancienne(self):
        """Rejoue la règle sur un doublon réel."""
        out = {}
        for pub, d in (("OP-1", D(2026, 8, 1)), ("OP-1", D(2026, 3, 4))):
            out[pub] = min(d, out[pub]) if pub in out else d
        self.assertEqual(out["OP-1"], D(2026, 3, 4))


class TestSondeParDefaut(unittest.TestCase):
    """Discipline du projet : mesurer sur données réelles avant d'écrire."""

    def _src(self):
        with open("backfill_date_detection.py", encoding="utf-8") as f:
            return f.read()

    def test_ecriture_conditionnee_a_un_drapeau_explicite(self):
        src = self._src()
        self.assertIn('ap.add_argument("--appliquer", action="store_true"', src)
        self.assertIn("ecrire and corrections", src)

    def test_le_mode_est_annonce(self):
        src = self._src()
        self.assertIn('"ECRITURE" if args.appliquer else "SONDE', src)

    def test_commit_seulement_en_ecriture(self):
        src = self._src()
        self.assertIn("if args.appliquer:\n            conn.commit()", src)

    def test_workflow_en_sonde_par_defaut(self):
        with open("backfill_date.yml", encoding="utf-8") as f:
            y = f.read()
        self.assertIn("default: false", y)
        self.assertIn("workflow_dispatch", y)
        self.assertNotIn("schedule:", y)      # jamais automatique

    def test_le_compte_de_service_est_efface(self):
        with open("backfill_date.yml", encoding="utf-8") as f:
            y = f.read()
        self.assertIn("rm -f service_account.json", y)
        self.assertIn("if: always()", y)


class TestRobustesse(unittest.TestCase):

    def test_onglet_illisible_n_interrompt_pas_le_reste(self):
        """Un onglet absent ne doit pas faire échouer tout le back-fill."""
        with open("backfill_date_detection.py", encoding="utf-8") as f:
            src = f.read()
        bloc = src.split("def lignes_sheet")[1].split("\ndef ")[0]
        self.assertIn("except Exception as e:", bloc)
        self.assertIn("return {}", bloc)

    def test_ligne_absente_du_miroir_ignoree(self):
        with open("backfill_date_detection.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("if pub not in pg:", src)

    def test_ecriture_par_lots(self):
        """Des milliers d'UPDATE unitaires satureraient la base."""
        with open("backfill_date_detection.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("executemany", src)
        self.assertIn("range(0, len(items), 500)", src)


class TestEtapesDejaFaites(unittest.TestCase):
    """Garde documentaire : ces bascules sont acquises, ne pas les refaire."""

    def test_memoire_inter_runs_hors_du_sheet(self):
        with open("radar_etat.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("radar_etat.json", src)
        self.assertIn("SPOF", src)

    def test_deduplication_des_publications_sur_postgres(self):
        with open("radar.yml", encoding="utf-8") as f:
            y = f.read()
        self.assertIn('RADAR_MEMOIRE: "pg"', y)


if __name__ == "__main__":
    unittest.main()
