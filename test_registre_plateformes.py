# -*- coding: utf-8 -*-
"""Registre des plateformes d'achats : intégrité du catalogue curé,
enrichissement live (jointure volume base) et rendu. Offline, aucun réseau.

Le registre est de la mémoire institutionnelle saisie à la main : le risque
n'est pas un bug de calcul mais une entrée mal formée (clé dupliquée, état hors
vocabulaire) qui fausse la vue et les décisions. Les tests verrouillent ça."""

import datetime
import unittest

import registre_plateformes as R

AUJ = datetime.date(2026, 11, 1)


class TestIntegrite(unittest.TestCase):

    def test_registre_reel_sain(self):
        self.assertEqual(R.valider(R.REGISTRE), [])

    def test_cles_uniques(self):
        cles = [e["cle"] for e in R.REGISTRE]
        self.assertEqual(len(cles), len(set(cles)))

    def test_detecte_cle_dupliquee(self):
        faux = [dict(R.REGISTRE[0]), dict(R.REGISTRE[0])]
        self.assertTrue(any("dupliquée" in a for a in R.valider(faux)))

    def test_detecte_etat_hors_vocabulaire(self):
        faux = [dict(R.REGISTRE[0], etat="bancal")]
        self.assertTrue(any("état invalide" in a for a in R.valider(faux)))

    def test_detecte_champ_manquant(self):
        faux = [{"cle": "x"}]
        anomalies = R.valider(faux)
        self.assertTrue(any("champ manquant" in a for a in anomalies))

    def test_detecte_rendement_et_pole_invalides(self):
        self.assertTrue(any("rendement invalide" in a
                            for a in R.valider([dict(R.REGISTRE[0], rendement="énorme")])))
        self.assertTrue(any("pôle invalide" in a
                            for a in R.valider([dict(R.REGISTRE[0], pole="Mars")])))

    def test_memoire_des_impasses_presente(self):
        """La valeur du registre : garder trace des plateformes écartées/bloquées
        avec leur motif, pas seulement des sources actives."""
        cles = {e["cle"] for e in R.REGISTRE}
        for attendu in ("ariba", "acpr", "prozorro", "boamp"):
            self.assertIn(attendu, cles)
        ariba = next(e for e in R.REGISTRE if e["cle"] == "ariba")
        self.assertEqual(ariba["etat"], "écarté")
        self.assertEqual(ariba["rendement"], "nul")


class TestEnrichirLive(unittest.TestCase):

    def test_jointure_par_onglet(self):
        reg = [{"cle": "a", "onglet": "ted_radar"},
               {"cle": "b", "onglet": "absent_radar"},
               {"cle": "c", "onglet": ""}]
        par = {"ted_radar": {"lignes": 120, "dernier": "2026-10-30"}}
        out = R.enrichir_live(reg, par)
        self.assertEqual(out[0]["volume"], 120)
        self.assertEqual(out[0]["dernier"], "2026-10-30")
        self.assertIsNone(out[1]["volume"])   # onglet absent de la base -> —
        self.assertIsNone(out[2]["volume"])   # pas d'onglet (plateforme sans collecteur)

    def test_par_onglet_depuis_sante(self):
        stats = [{"source": "Operations / ted_radar", "lignes": 120, "dernier": "2026-10-30"},
                 {"source": "IE vigilance", "lignes": 138, "dernier": "2026-11-01"}]
        idx = R.par_onglet_depuis_sante(stats)
        self.assertEqual(idx["ted_radar"]["lignes"], 120)
        self.assertEqual(idx["IE vigilance"]["lignes"], 138)

    def test_enrichir_sans_base_ne_casse_pas(self):
        out = R.enrichir_live(R.REGISTRE, {})
        self.assertEqual(len(out), len(R.REGISTRE))
        self.assertTrue(all(e["volume"] is None for e in out))


class TestRendu(unittest.TestCase):

    def test_markdown_contient_les_colonnes_et_entrees(self):
        md = R.rapport_markdown(R.enrichir_live(R.REGISTRE, {}), AUJ)
        self.assertIn("Registre des plateformes", md)
        self.assertIn("TED (UE)", md)
        self.assertIn("SAP Ariba Discovery", md)

    def test_html_autonome_et_echappe(self):
        reg = [dict(R.REGISTRE[0], nom="A<b>X</b>", volume=5, dernier="2026-11-01")]
        h = R.rapport_html(reg, AUJ)
        self.assertTrue(h.startswith("<!doctype html>"))
        self.assertIn("&lt;b&gt;", h)             # échappement
        self.assertNotIn("<b>X</b>", h)
        self.assertIn("Opérations", h)            # groupe par pôle

    def test_html_groupe_par_pole(self):
        h = R.rapport_html(R.enrichir_live(R.REGISTRE, {}), AUJ)
        for pole in ("Opérations", "IE", "Les deux"):
            self.assertIn(">{}<".format(pole), h)


if __name__ == "__main__":
    unittest.main()
