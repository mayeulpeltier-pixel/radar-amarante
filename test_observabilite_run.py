# -*- coding: utf-8 -*-
"""
Observabilite de run : etat du dernier run par source (02/08/2026).
===================================================================

CE QUE CE FICHIER VERROUILLE
----------------------------
Un bandeau derive des leads DEJA construits (aucune lecture supplementaire)
donne, par source, le volume et la fraicheur du plus recent lead. Objectif :
rendre VISIBLE une source qui s'est tue -- exactement ce qui manquait quand le
digest est tombe en silence.

  - une source du catalogue ABSENTE des leads -> etat "absent" (n=0) ;
  - une source dont le plus recent est vieux -> etat "ancien" (a verifier) ;
  - une source fraiche -> "frais" ; robuste aux formats de date mixtes ;
  - le bandeau est bien serialise et rendu dans le HTML (les deux surfaces,
    statique et application, passent par generer_html).
"""

import unittest
from datetime import date, timedelta

import radar_dashboard as dash


def _lead(src, date_det):
    return {"src": src, "date_det": date_det, "final": 5.0, "titre": "t"}


class TestSanteRun(unittest.TestCase):
    def setUp(self):
        self.auj = date(2026, 8, 11)
        self._sav = (dash.SANTE_FRAIS_JOURS, dash.SANTE_CALME_JOURS)
        dash.SANTE_FRAIS_JOURS = 4
        dash.SANTE_CALME_JOURS = 14

    def tearDown(self):
        dash.SANTE_FRAIS_JOURS, dash.SANTE_CALME_JOURS = self._sav

    def _etat(self, res, src):
        return next(x for x in res["sources"] if x["src"] == src)

    def test_source_fraiche(self):
        leads = [_lead("TED", self.auj.isoformat())]
        r = dash.sante_run(leads, self.auj)
        t = self._etat(r, "TED")
        self.assertEqual(t["n"], 1)
        self.assertEqual(t["etat"], "frais")
        self.assertEqual(t["age"], 0)

    def test_source_absente_du_catalogue_apparait_a_zero(self):
        # Aucun lead : toutes les sources du catalogue sont "absent".
        r = dash.sante_run([], self.auj)
        for s in dash.CATALOGUE_SOURCES:
            e = self._etat(r, s)
            self.assertEqual(e["n"], 0)
            self.assertEqual(e["etat"], "absent")
        self.assertEqual(r["actives"], 0)

    def test_source_ancienne_est_signalee(self):
        vieux = (self.auj - timedelta(days=40)).isoformat()
        r = dash.sante_run([_lead("MIGA", vieux)], self.auj)
        m = self._etat(r, "MIGA")
        self.assertEqual(m["etat"], "ancien")
        self.assertGreaterEqual(r["a_verifier"], 1)

    def test_age_est_celui_du_plus_recent(self):
        leads = [
            _lead("BM", (self.auj - timedelta(days=30)).isoformat()),
            _lead("BM", (self.auj - timedelta(days=2)).isoformat()),
            _lead("BM", (self.auj - timedelta(days=9)).isoformat()),
        ]
        r = dash.sante_run(leads, self.auj)
        b = self._etat(r, "BM")
        self.assertEqual(b["n"], 3)
        self.assertEqual(b["age"], 2)          # freshest
        self.assertEqual(b["etat"], "frais")

    def test_compte_actives_et_a_verifier(self):
        leads = [
            _lead("TED", self.auj.isoformat()),                       # frais
            _lead("BM", (self.auj - timedelta(days=10)).isoformat()), # calme
            _lead("RW", (self.auj - timedelta(days=40)).isoformat()), # ancien
        ]
        r = dash.sante_run(leads, self.auj)
        self.assertEqual(r["actives"], 3)
        # ancien (RW) + toutes les autres sources du catalogue absentes.
        attendus_absents = len(dash.CATALOGUE_SOURCES) - 3
        self.assertEqual(r["a_verifier"], 1 + attendus_absents)

    def test_formats_de_date_mixtes_ne_plantent_pas(self):
        leads = [
            _lead("TED", "2026-08-09"),          # ISO
            _lead("TED", "05/08/2026"),          # JJ/MM/AAAA
            _lead("TED", "pas-une-date"),        # illisible -> ignore pour l'age
        ]
        r = dash.sante_run(leads, self.auj)
        t = self._etat(r, "TED")
        self.assertEqual(t["n"], 3)
        self.assertEqual(t["age"], 2)            # 09/08 vs 11/08 = 2 j (le plus recent)

    def test_source_hors_catalogue_est_listee(self):
        r = dash.sante_run([_lead("XYZ", self.auj.isoformat())], self.auj)
        self.assertTrue(any(x["src"] == "XYZ" for x in r["sources"]))


def _row_ted(final, date_detection, pub, titre="Mission"):
    return {
        "score_final": str(final), "score_surete": str(final),
        "score_commercial": str(final), "action_recommandee": "contacter",
        "fenetre_action": "court_terme", "titre": titre, "acheteur": "A",
        "pays_execution": "MLI", "justification": "j", "confiance": "0.8",
        "modele": "m", "publication_number": pub, "lien_avis": "http://x/" + pub,
        "date_detection": date_detection,
    }


class TestCablageBandeauSante(unittest.TestCase):
    def test_le_bandeau_est_serialise_et_rendu(self):
        leads = dash.construire_leads([_row_ted(7.0, date.today().isoformat(), "TED:1")], [])
        html = dash.generer_html(leads)
        self.assertNotIn("__SANTE_JSON__", html, "Le placeholder doit etre remplace.")
        self.assertIn('"sources"', html, "L'etat par source doit etre serialise.")
        self.assertIn('id="santeRun"', html, "Le conteneur du bandeau doit exister.")
        self.assertIn("function renderSante", html, "Le rendu du bandeau doit exister.")
        self.assertIn("renderSante();", html, "Le bandeau doit etre rendu au demarrage.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
