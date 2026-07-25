# -*- coding: utf-8 -*-
"""Socle DETERMINISTE de l'origine du titulaire (chantier B) et garde de
migration de l'onglet partage.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Les collecteurs d'attributions calculaient le pays d'origine du titulaire et
le drapeau `etranger` (`_origine` / `_pays_titulaire`, `_etranger`), s'en
servaient pour filtrer et journaliser, puis les JETAIENT : ces cles prefixees
`_` sont supprimees par `preparer_ligne` et absentes du schema. Le dashboard ne
les voyait jamais. Une attribution non encore analysee par le modele
(attributions_analyse.py) n'affichait donc AUCUNE origine.

Le chantier B persiste ces deux champs, en ajoutant `pays_titulaire` et
`titulaire_etranger` en FIN du schema partage (avant les colonnes humaines).
C'est un SOCLE : reponse immediate, gratuite, calculee des la collecte, que
l'analyse LLM affine ensuite quand elle existe (A prime sur B).

CE QUI EST DELICAT, ET TESTE ICI
--------------------------------
Le schema `attributions_radar` est PARTAGE et lu POSITIONNELLEMENT. Ajouter des
colonnes est le geste qui a cause l'incident bm_radar. Deux garanties :

  1. `publication_number` reste en position 10, AVANT les ajouts : l'index
     d'ecriture le retrouve sur un ancien onglet, donc AUCUNE duplication au
     prochain run (TestEcritureSureSurAncienOnglet) ;
  2. le code ne reecrit JAMAIS l'en-tete d'un onglet existant ni ne decale une
     ligne : la migration (inserer deux colonnes) est MANUELLE, faite une fois.
     Le code se contente d'avertir tant qu'elle n'a pas eu lieu
     (TestGardeMigration).

Aucun appel reseau ni LLM.
"""

import io
import contextlib
import unittest

import ted_complet_v14 as ted
import bm_attributions as bma
import radar_dashboard as dash


# En-tetes de reference.
ANCIEN_ENTETE = [
    "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
    "acheteur", "titre", "cpv", "sous_traitance", "date_publication",
    "publication_number", "lien", "a_demarcher",
    "statut_prospection", "date_detection"]              # 13 + 2 humaines

MIGRE_ENTETE = (ANCIEN_ENTETE[:13]
                + ["pays_titulaire", "titulaire_etranger"]
                + ANCIEN_ENTETE[13:])                    # 15 + 2 humaines


# ===========================================================================
# LE SCHEMA
# ===========================================================================

class TestSchemaEtendu(unittest.TestCase):

    def test_les_deux_colonnes_sont_en_fin_de_schema(self):
        """En FIN, pas au milieu : c'est ce qui garde publication_number a sa
        place et evite le desalignement des lignes deja ecrites."""
        self.assertEqual(bma.COLONNES[-2:], ["pays_titulaire", "titulaire_etranger"])

    def test_publication_number_n_a_pas_bouge(self):
        """Position 10, AVANT les ajouts. C'est l'invariant qui protege les
        anciens onglets de la duplication."""
        self.assertEqual(bma.COLONNES.index("publication_number"), 10)

    def test_les_quatre_collecteurs_partages_ont_le_meme_schema(self):
        import isdb_radar
        import ungm_attributions
        import ted_complet_attributions as tca
        for mod in (isdb_radar, ungm_attributions, tca):
            with self.subTest(collecteur=mod.__name__):
                self.assertEqual(list(mod.COLONNES), list(bma.COLONNES))

    def test_les_colonnes_humaines_restent_apres_les_ajouts(self):
        """statut_prospection / date_detection doivent rester la zone de saisie
        humaine, en aval du schema de donnees."""
        self.assertEqual(bma.TOUTES_COLONNES[-2:],
                         ["statut_prospection", "date_detection"])


# ===========================================================================
# ECRITURE SURE SUR UN ANCIEN ONGLET (pas de duplication)
# ===========================================================================

class TestEcritureSureSurAncienOnglet(unittest.TestCase):
    """La garantie qui rend l'ajout acceptable : sur un onglet encore a
    l'ancien schema, l'index d'ecriture retrouve les identifiants existants,
    parce que publication_number n'a pas bouge de position."""

    def _ligne_ancienne(self, pub):
        d = {c: "" for c in ANCIEN_ENTETE}
        d.update({"publication_number": pub, "gagnant": "Yapi",
                  "statut_prospection": "contacte",
                  "date_detection": "2026-07-01"})
        return [d[c] for c in ANCIEN_ENTETE]

    def test_identifiant_existant_retrouve_malgre_l_ancien_entete(self):
        class Feuille:
            def __init__(s, grille): s.grille = grille
            def get_all_values(s): return [list(r) for r in s.grille]
        feuille = Feuille([ANCIEN_ENTETE, self._ligne_ancienne("BM-1")])
        index = ted.charger_index_publication(feuille, bma.COLONNES)
        self.assertIn("BM-1", index,
                      "identifiant non retrouve -> il serait re-ajoute (doublon)")

    def test_position_de_l_identifiant_identique_dans_les_deux_schemas(self):
        self.assertEqual(ANCIEN_ENTETE.index("publication_number"),
                         bma.COLONNES.index("publication_number"))


# ===========================================================================
# GARDE DE MIGRATION : ne jamais reecrire un onglet existant
# ===========================================================================

class _OngletEspion:
    """Capture tout appel a update() : une reecriture d'en-tete y passerait."""

    def __init__(self, entete):
        self.entete = list(entete)
        self.updates = []

    def row_values(self, n):
        return list(self.entete)

    def update(self, values=None, range_name=None):
        self.updates.append((range_name, values))


def _appliquer_garde(onglet):
    """Reproduit la garde de ouvrir_feuille, isolee du reseau."""
    entete = onglet.row_values(1)
    sortie = io.StringIO()
    with contextlib.redirect_stdout(sortie):
        if entete and "pays_titulaire" not in entete:
            print("  (!) MIGRATION REQUISE")
        elif not entete:
            onglet.update(values=[bma.TOUTES_COLONNES], range_name="A1")
    return sortie.getvalue()


class TestGardeMigration(unittest.TestCase):

    def test_ancien_onglet_avertit_mais_ne_reecrit_rien(self):
        """Le cas de production au prochain run : l'onglet est encore a
        l'ancien schema. Le code doit AVERTIR, et surtout ne toucher a AUCUNE
        cellule -- sinon il ecraserait des statuts humains."""
        onglet = _OngletEspion(ANCIEN_ENTETE)
        journal = _appliquer_garde(onglet)
        self.assertEqual(onglet.updates, [], "reecriture interdite sur onglet existant")
        self.assertIn("MIGRATION", journal)

    def test_onglet_migre_est_silencieux(self):
        """Une fois les deux colonnes inserees a la main, plus d'avertissement
        et toujours aucune reecriture."""
        onglet = _OngletEspion(MIGRE_ENTETE)
        journal = _appliquer_garde(onglet)
        self.assertEqual(onglet.updates, [])
        self.assertNotIn("MIGRATION", journal)

    def test_onglet_vide_recoit_l_entete_complet(self):
        """Creation initiale : la, ecrire l'en-tete est legitime, il n'y a
        aucune donnee a risque."""
        onglet = _OngletEspion([])
        _appliquer_garde(onglet)
        self.assertEqual(len(onglet.updates), 1)
        self.assertEqual(onglet.updates[0][1], [bma.TOUTES_COLONNES])


# ===========================================================================
# LE SOCLE ALIMENTE LE DASHBOARD (attribution non analysee)
# ===========================================================================

class TestSocleDansLeDashboard(unittest.TestCase):
    """Le but du chantier : une attribution NON analysee affiche deja son
    origine, calculee a la collecte."""

    ATTRIB = {
        "gagnant": "Yapi Merkezi", "secteur": "Travaux", "pays_execution": "MLI",
        "valeur_attribuee": "USD 42000000", "acheteur": "BM", "titre": "Route",
        "publication_number": "BM-1", "lien": "http://x",
    }

    def _lead(self, **surcharge):
        attrib = dict(self.ATTRIB, **surcharge)
        return dash.construire_leads([], [], lignes_attrib=[attrib])[0]

    def test_titulaire_etranger_affiche_sans_analyse(self):
        lead = self._lead(pays_titulaire="Turquie", titulaire_etranger="oui")
        self.assertEqual(lead["origine"], "Turquie")
        self.assertTrue(lead["etranger_titulaire"])
        self.assertFalse(lead["analysee"])
        self.assertIn("Turquie", lead["justif"])
        self.assertIn("ETRANGER", lead["justif"])

    def test_titulaire_local_signale(self):
        lead = self._lead(pays_titulaire="Mali", titulaire_etranger="non")
        self.assertFalse(lead["etranger_titulaire"])
        self.assertIn("local", lead["justif"])

    def test_origine_inconnue_ne_prefixe_pas(self):
        """TED attributions n'a pas d'origine : aucun prefixe d'origine
        « Titulaire <pays> (ETRANGER/local) » n'est ajoute. Le libelle
        generique de la justification, lui, subsiste (il commence par
        « Titulaire d'un marche gagne »), ce qui est normal : c'est le prefixe
        d'ORIGINE qu'on ne doit pas inventer."""
        lead = self._lead(pays_titulaire="", titulaire_etranger="")
        self.assertEqual(lead["origine"], "")
        self.assertNotIn("ETRANGER au pays d'exécution", lead["justif"])
        self.assertNotIn("(local).", lead["justif"])

    def test_l_analyse_llm_prime_sur_le_socle(self):
        """A affine B : quand l'analyse existe, ses scores et son origine
        remplacent le socle deterministe."""
        attrib = dict(self.ATTRIB, pays_titulaire="Turkey", titulaire_etranger="oui")
        analyse = {
            "publication_number": "BM-1", "score_final": "9.7",
            "score_surete": "10.0", "score_commercial": "9.2",
            "action_recommandee": "contacter",
            "pays_origine_titulaire": "Turquie", "titulaire_etranger": "True",
            "nature_deploiement": "expatrie_significatif",
            "besoin_surete_probable": "fort",
            "interlocuteur_vise": "directeur ops",
            "justification": "Base vie isolee."}
        lead = dash.construire_leads([], [], lignes_attrib=[attrib],
                                     analyses_attrib=[analyse])[0]
        self.assertEqual(lead["final"], 9.7)         # score analyse, pas socle
        self.assertEqual(lead["origine"], "Turquie")  # forme LLM
        self.assertTrue(lead["analysee"])


if __name__ == "__main__":
    unittest.main()
