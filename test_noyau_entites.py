# -*- coding: utf-8 -*-
"""P2.1 — Noyau relationnel minimal (26/08/2026).

L'ANGLE MORT DE L'AUDIT EXTERNE
-------------------------------
L'audit réclamait un modèle relationnel en supposant qu'on partait de zéro.
C'est faux : DEUX identités stables existent déjà et sont calculées à chaque
run --

  `ent_cle`   (radar_dashboard._norm_ent) : clé canonique d'entreprise, qui
              fusionne déjà watchlist, signaux privés et titulaires ;
  `projet_id` (P######) : identifiant de projet Banque mondiale.

On ne crée donc pas des identités, on leur donne une PERSISTANCE. Quatre
tables au total (`radar_entreprises`, `radar_projets`, plus `radar_outcomes`
livrée en P1.1 et `radar_statuts` existante), pas les douze réclamées.

CE QUE CES TABLES APPORTENT VRAIMENT
------------------------------------
Le cockpit regroupe déjà les entreprises à chaque rendu, à partir des seuls
leads présents. Il ne peut donc pas savoir DEPUIS QUAND une entreprise est
connue du radar. `premiere_vue` survit à la rotation du corpus, à un filtre, à
un lead écarté. C'est une information neuve, pas une copie de l'écran.

LE PIÈGE ÉVITÉ
--------------
`premiere_vue` ne doit jamais reculer vers le présent, sinon on refabrique
exactement le défaut « détecté aujourd'hui » corrigé le 25/08 -- mais sur les
entreprises cette fois. D'où `LEAST()` côté SQL et `min()` côté dérivation.

Tests OFFLINE : fonctions pures et contrats SQL, aucune base, aucun réseau.
"""

import datetime
import unittest

import radar_dashboard as dash
import radar_stockage as st
# Meme raison que dans test_backfill_date : les workflows ne sont pas a la
# racine du depot, ils sont dans `.github/workflows/`.
from test_workflows import lire_workflow


AUJ = datetime.date(2026, 8, 26)


def _lead(**kw):
    base = {"src": "ATTRIB", "ent_cle": "stecol", "entreprise": "STECOL",
            "origine": "", "etranger_titulaire": False, "zone": "Sahel",
            "pays": "Tchad", "date_det": "2026-03-04", "projet_id": "",
            "titre": ""}
    base.update(kw)
    return base


CORPUS = [
    _lead(entreprise="STECOL CORPORATION", origine="China",
          etranger_titulaire=True, date_det="2026-03-04",
          projet_id="P178234", titre="Route N1"),
    _lead(entreprise="STECOL", zone="Afrique de l'Ouest", pays="Niger",
          date_det="2026-07-15"),
    _lead(src="PRIVÉ", entreprise="Stecol Corp", pays="Mali",
          date_det="2025-11-02", projet_id="P178234", titre="Route N1"),
    _lead(src="TED", ent_cle="", entreprise="", date_det="2026-08-01"),
]


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


class TestDerivationDesEntites(unittest.TestCase):

    def setUp(self):
        self.ents, self.projs = dash.entites_depuis_leads(CORPUS, AUJ)

    def test_les_variantes_de_nom_fusionnent(self):
        """Trois formes du même titulaire, une seule entité : c'est `ent_cle`
        qui fait le travail, pas une seconde résolution locale."""
        self.assertEqual(len(self.ents), 1)
        self.assertEqual(self.ents[0]["ent_cle"], "stecol")

    def test_le_nom_le_plus_complet_est_retenu(self):
        """Les sources tronquent inégalement le même titulaire."""
        self.assertEqual(self.ents[0]["nom"], "STECOL CORPORATION")

    def test_marches_et_signaux_comptes_separement(self):
        """Un marché gagné et un signal de déploiement ne disent pas la même
        chose : les additionner perdrait l'information."""
        self.assertEqual(self.ents[0]["n_marches"], 2)
        self.assertEqual(self.ents[0]["n_signaux"], 1)

    def test_lead_sans_entreprise_ignore(self):
        """Un avis TED sans titulaire ne doit pas créer d'entité vide."""
        self.assertNotIn("", [e["ent_cle"] for e in self.ents])

    def test_zones_agregees_et_triees(self):
        self.assertEqual(self.ents[0]["zones"], "Afrique de l'Ouest, Sahel")

    def test_origine_conservee_meme_si_un_seul_lead_la_porte(self):
        self.assertEqual(self.ents[0]["pays_origine"], "China")

    def test_projet_agrege_sur_son_identifiant(self):
        self.assertEqual(len(self.projs), 1)
        self.assertEqual(self.projs[0]["projet_id"], "P178234")
        self.assertEqual(self.projs[0]["n_leads"], 2)

    def test_corpus_vide_sans_erreur(self):
        self.assertEqual(dash.entites_depuis_leads([], AUJ), ([], []))
        self.assertEqual(dash.entites_depuis_leads(None, AUJ), ([], []))


class TestPremiereVue(unittest.TestCase):
    """LE piège de ce chantier : refabriquer « détecté aujourd'hui », mais sur
    les entreprises."""

    def test_premiere_vue_est_la_plus_ancienne_detection(self):
        ents, _ = dash.entites_depuis_leads(CORPUS, AUJ)
        self.assertEqual(ents[0]["premiere_vue"], "2025-11-02")

    def test_premiere_vue_n_est_pas_la_date_du_run(self):
        ents, _ = dash.entites_depuis_leads(CORPUS, AUJ)
        self.assertNotEqual(ents[0]["premiere_vue"], AUJ.isoformat())

    def test_derniere_vue_est_la_plus_recente(self):
        ents, _ = dash.entites_depuis_leads(CORPUS, AUJ)
        self.assertEqual(ents[0]["derniere_vue"], "2026-07-15")

    def test_lead_sans_date_retombe_sur_le_jour(self):
        """Repli explicite plutôt qu'une date vide qui casserait le LEAST()."""
        ents, _ = dash.entites_depuis_leads(
            [_lead(date_det="")], AUJ)
        self.assertEqual(ents[0]["premiere_vue"], AUJ.isoformat())

    def test_date_tronquee_ou_invalide_retombe_sur_le_jour(self):
        ents, _ = dash.entites_depuis_leads([_lead(date_det="2026-03")], AUJ)
        self.assertEqual(ents[0]["premiere_vue"], AUJ.isoformat())

    def test_le_sql_empeche_premiere_vue_de_reculer(self):
        """Même garde côté base : un run ultérieur ne doit pas rajeunir une
        entreprise connue de longue date."""
        src = _lire("radar_stockage.py")
        self.assertIn("premiere_vue = LEAST(radar_entreprises.premiere_vue,",
                      src)
        self.assertIn("premiere_vue = LEAST(radar_projets.premiere_vue,", src)

    def test_derniere_vue_ne_recule_pas_non_plus(self):
        src = _lire("radar_stockage.py")
        self.assertIn("derniere_vue = GREATEST(radar_entreprises.derniere_vue,",
                      src)


class TestSchema(unittest.TestCase):

    def test_deux_tables_declarees(self):
        for t in ("radar_entreprises", "radar_projets"):
            self.assertIn("CREATE TABLE IF NOT EXISTS " + t, st.SCHEMA_SQL)

    def test_cles_primaires_sur_les_identites_existantes(self):
        self.assertIn("ent_cle      TEXT        PRIMARY KEY", st.SCHEMA_SQL)
        self.assertIn("projet_id    TEXT        PRIMARY KEY", st.SCHEMA_SQL)

    def test_migration_idempotente(self):
        """Rejouable sur une base déjà migrée."""
        self.assertNotIn("CREATE TABLE radar_entreprises", st.SCHEMA_SQL)

    def test_une_origine_connue_n_est_pas_effacee(self):
        """Un run où la source ne porte pas l'origine ne doit pas vider un
        champ déjà renseigné."""
        src = _lire("radar_stockage.py")
        self.assertIn("COALESCE(NULLIF(EXCLUDED.pays_origine, '')", src)

    def test_lecture_tolerante_a_une_base_non_migree(self):
        src = _lire("radar_stockage.py")
        bloc = src.split("def lire_entreprises")[1].split("\ndef ")[0]
        self.assertIn("except Exception", bloc)
        self.assertIn("return {}", bloc)


class TestPersistanceInoffensive(unittest.TestCase):

    def test_desactivable(self):
        import os
        avant = os.environ.get("RADAR_ENTITES")
        os.environ["RADAR_ENTITES"] = "0"
        try:
            self.assertEqual(dash.persister_entites(CORPUS), (0, 0))
        finally:
            if avant is None:
                os.environ.pop("RADAR_ENTITES", None)
            else:
                os.environ["RADAR_ENTITES"] = avant

    def test_sans_base_ne_leve_pas(self):
        """La génération du tableau de bord ne doit pas dépendre de la
        persistance : ces tables ne sont lues par rien pour l'instant."""
        self.assertEqual(dash.persister_entites(CORPUS), (0, 0))

    def test_appelee_avant_la_generation(self):
        src = _lire("radar_dashboard.py")
        i_pers = src.index("persister_entites(leads)")
        i_html = src.index("html = generer_html(leads, lignes_watchlist")
        self.assertLess(i_pers, i_html)

    def test_une_seule_definition_de_la_cle_d_entite(self):
        """Une seconde implémentation produirait deux regroupements
        divergents, donc deux vérités sur « qui est cette entreprise »."""
        src = _lire("radar_dashboard.py")
        self.assertEqual(src.count("def _norm_ent("), 1)
        bloc = src.split("def entites_depuis_leads")[1].split("\ndef ")[0]
        self.assertNotIn("unicodedata", bloc)      # ne recalcule pas la clé
        self.assertIn('l.get("ent_cle")', bloc)


class TestSecretNonPublie(unittest.TestCase):
    """P2.1 ajoute DATABASE_URL à une étape qui PUBLIE. Le garde-fou P0.1
    doit le couvrir, sinon on rouvre la faille qu'on vient de fermer."""

    def test_database_url_couvert_par_le_garde_fou(self):
        import os
        avant = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://u:motdepasse@h.neon.tech/r"
        try:
            with self.assertRaises(RuntimeError):
                dash.verifier_absence_secret(
                    "<html>" + os.environ["DATABASE_URL"] + "</html>")
        finally:
            if avant is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = avant

    def test_workflow_documente_la_raison(self):
        y = lire_workflow("radar.yml")
        if y is None:
            self.skipTest("radar.yml introuvable")
        self.assertIn("DATABASE_URL: ${{ secrets.DATABASE_URL }}", y)
        self.assertIn("verifier_absence_secret", y)


if __name__ == "__main__":
    unittest.main()
