# -*- coding: utf-8 -*-
"""P3.4 — Statuts perdus sur 11 sources sur 15 (26/08/2026).

CE QUE CE CHANTIER DEVAIT ÊTRE
------------------------------
« Trancher ReliefWeb : collecté à chaque run, jamais lu par le dashboard. »
C'était le constat de l'audit de juillet, que mon propre plan v2 a recopié.

**C'EST FAUX.** ReliefWeb est collecté (`radar_run.py`, étape dédiée) ET lu
(`radar_dashboard` : `leads += [ligne_vers_lead(r, "RW") for r in lignes_rw]`).
Il figure dans `CATALOGUE_SOURCES` et dans `SRC_BOOSTABLES`. Troisième fois
que mon plan porte une affirmation « déjà fait / pas fait » non vérifiée.

CE QUE LA VÉRIFICATION A TROUVÉ À LA PLACE
------------------------------------------
La table `ONGLET_SRC` du cockpit ne couvrait que **4 sources sur 15** :

    ONGLET_SRC = {TED, BM, PRIVÉ, ATTRIB}

Les onze autres -- AFDB, ADB, EBRD, UNGM, RW, MIGA, IFC, IDB, BMP, PROPARCO,
DFC -- tombaient sur `ONGLET_SRC[l.src] || ""`.

Or `superposer_statuts` relit les statuts sur la clé `(onglet,
publication_number)`. Un statut écrit avec un onglet VIDE est écrit
réellement, et n'est **jamais retrouvé**. Marquer « à contacter » un avis EBRD
partait dans le vide, et le lead réapparaissait « nouveau » au rechargement.

Le legacy avait la table complète depuis toujours. Le cockpit ne l'avait
jamais reprise.

LA CORRECTION DE FOND
---------------------
La table n'est plus écrite à la main dans le JS : elle est **générée depuis
Python** à partir d'une définition unique (`dash.ONGLET_PAR_SOURCE`), et un
garde-fou refuse de produire la page si une source du catalogue n'a pas
d'onglet. Mieux vaut un run rouge qu'un statut écrit dans le vide, qui ne se
récupère pas après coup.

Tests OFFLINE : aucune base, aucun réseau.
"""

import json
import re
import unittest

import radar_cockpit as ck
import radar_dashboard as dash


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


class TestReliefWebNEstPasOrphelin(unittest.TestCase):
    """Garde documentaire : ne pas reprogrammer un chantier sans objet."""

    def test_collecte_par_l_orchestrateur(self):
        src = _lire("radar_run.py")
        self.assertIn("import ted_complet_reliefweb", src)
        self.assertIn("COLLECTEUR RELIEFWEB", src)

    def test_lu_par_le_dashboard(self):
        src = _lire("radar_dashboard.py")
        self.assertIn('ligne_vers_lead(r, "RW") for r in (lignes_rw or [])', src)

    def test_present_dans_le_catalogue_et_les_boosts(self):
        self.assertIn("RW", dash.CATALOGUE_SOURCES)
        self.assertIn("RW", dash.SRC_BOOSTABLES)


class TestTableOnglets(unittest.TestCase):
    """LE défaut réel."""

    def test_toutes_les_sources_du_catalogue_ont_un_onglet(self):
        """S'il manque une source, ses statuts partent dans le vide."""
        manquantes = [s for s in dash.CATALOGUE_SOURCES
                      if s not in dash.ONGLET_PAR_SOURCE]
        self.assertEqual(manquantes, [])

    def test_les_onze_sources_oubliees_sont_couvertes(self):
        for src in ("AFDB", "ADB", "EBRD", "UNGM", "RW", "MIGA", "IFC",
                    "IDB", "BMP", "PROPARCO", "DFC"):
            self.assertIn(src, dash.ONGLET_PAR_SOURCE, src)

    def test_aucun_onglet_vide(self):
        for src, onglet in dash.ONGLET_PAR_SOURCE.items():
            self.assertTrue(onglet.strip(), src)

    def test_definition_unique_cote_python(self):
        """La table n'est plus recopiée à la main dans le JS : une source
        ajoutée au catalogue ne peut plus être oubliée."""
        src = _lire("radar_cockpit.py")
        self.assertIn("const ONGLET_SRC=__ONGLET_SRC_JSON__;", src)
        self.assertNotIn('const ONGLET_SRC={TED:"ted_radar"', src)

    def test_injectee_complete_dans_la_page(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        table = json.loads(re.search(r"^const ONGLET_SRC=(\{.*?\});$",
                                     html, re.S | re.M).group(1))
        self.assertEqual(len(table), len(dash.ONGLET_PAR_SOURCE))
        self.assertEqual(table["RW"], "reliefweb_radar")
        self.assertEqual(table["EBRD"], "ebrd_radar")


class TestGardeFou(unittest.TestCase):
    """Un run rouge vaut mieux qu'un statut écrit dans le vide."""

    def test_source_sans_onglet_fait_echouer_la_generation(self):
        sauv = dash.ONGLET_PAR_SOURCE.pop("RW")
        try:
            with self.assertRaises(RuntimeError):
                ck.table_onglets()
        finally:
            dash.ONGLET_PAR_SOURCE["RW"] = sauv

    def test_le_message_nomme_la_source_et_le_remede(self):
        sauv = dash.ONGLET_PAR_SOURCE.pop("UNGM")
        try:
            ck.table_onglets()
            self.fail("aurait dû lever")
        except RuntimeError as e:
            self.assertIn("UNGM", str(e))
            self.assertIn("ONGLET_PAR_SOURCE", str(e))
        finally:
            dash.ONGLET_PAR_SOURCE["UNGM"] = sauv

    def test_table_complete_ne_leve_pas(self):
        self.assertEqual(len(ck.table_onglets()), len(dash.ONGLET_PAR_SOURCE))


class TestChaineDeRelecture(unittest.TestCase):
    """Ce qui rend le défaut irrécupérable, et pourquoi il fallait le corriger
    à la source plutôt qu'en aval."""

    def test_la_relecture_se_fait_sur_le_couple(self):
        src = _lire("radar_app.py")
        self.assertIn('cle = (nom, str(ligne.get("publication_number", "")'
                      ' or ""))', src)
        self.assertIn("if cle[1] and cle in statuts:", src)

    def test_le_client_envoie_bien_l_onglet(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        self.assertIn('onglet:ONGLET_SRC[l.src]||""', html)


class TestNonRegression(unittest.TestCase):

    def test_page_toujours_valide(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        for attendu in ("renderAujourdhui", "function detailSante",
                        "function blocCompte", "function marquerGagne",
                        "santeRun"):
            self.assertIn(attendu, html)


if __name__ == "__main__":
    unittest.main()
