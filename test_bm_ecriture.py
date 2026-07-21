# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS D'ECRITURE DES ATTRIBUTIONS BM.
========================================================

POURQUOI CE FICHIER
-------------------
`bm_attributions.ecrire()` porte une promesse CRITIQUE : ne JAMAIS reecrire
une ligne existante, car la colonne `statut_prospection` est une zone de
saisie HUMAINE. Une regression ici ecraserait silencieusement le travail de
suivi commercial. Cette promesse n'etait verrouillee par aucun test : c'est
desormais le role de ce fichier.

On y ajoute trois angles morts du parseur reperes a la relecture :
  - le plafond `maxi` d'`extraire_gagnants` (groupements XXL) ;
  - `valeur_label` en mise en forme "meme ligne" ("Project:P178566-...") ;
  - la garde anti-date de `_lire_montant` ("2026/07/01" n'est pas un montant).

Aucun reseau, aucun Sheet reel : la feuille est une doublure en memoire qui
enregistre ce qu'on lui demande. Decouverte automatique par la CI via
`python -m unittest discover -p "test_*.py"` : aucun cablage a faire.
"""

import os
import unittest
from datetime import date

try:
    import bm_attributions
except Exception:                                     # dependance absente en local
    bm_attributions = None


# ===========================================================================
# DOUBLURE DE FEUILLE GOOGLE SHEETS (en memoire, aucune ecriture reelle)
# ===========================================================================

class FausseFeuille:
    """Imite le strict necessaire de gspread.Worksheet :
      - get_all_records() : lignes existantes sous forme de dicts (ce que lit
        ted.charger_index_publication) ;
      - append_rows()     : enregistre ce que `ecrire` AJOUTE ;
      - update()/update_cell() : pieges. `ecrire` ne doit JAMAIS les appeler,
        c'est precisement la promesse testee (zone de saisie humaine)."""

    def __init__(self, lignes_existantes=None):
        self._records = list(lignes_existantes or [])
        self.ajouts = []                  # lots passes a append_rows
        self.reecritures = 0              # compteur d'appels interdits

    def get_all_records(self):
        return list(self._records)

    def append_rows(self, lignes, value_input_option=None):
        self.ajouts.append(list(lignes))

    # -- Appels interdits : toute reecriture d'une ligne existante ---------
    def update(self, *a, **k):
        self.reecritures += 1

    def update_cell(self, *a, **k):
        self.reecritures += 1


def _attribution(pub="BM-001", gagnant="STECOL CORPORATION"):
    """Attribution minimale mais complete : toutes les colonnes officielles."""
    base = {c: "" for c in bm_attributions.COLONNES}
    base.update({
        "date_maj": date.today().isoformat(),
        "gagnant": gagnant,
        "secteur": "Travaux / BTP",
        "pays_execution": "MLI",
        "valeur_attribuee": "USD 45.300 million",
        "acheteur": "Banque Mondiale",
        "titre": "Route RN6 · titulaire China · duree 60 Day(s)",
        "date_publication": "2026-07-01",
        "publication_number": pub,
        "lien": "https://exemple.invalid/{}".format(pub),
        "a_demarcher": "oui",
        # Exige par ligne() du collecteur TED (cle technique, non persistee).
        "_nb_gagnants": 1,
    })
    return base


@unittest.skipIf(bm_attributions is None, "bm_attributions indisponible")
class TestEcritureAttributionsBM(unittest.TestCase):
    """La garde d'ecriture : ajouter sans jamais toucher a l'existant."""

    # -- La promesse centrale ---------------------------------------------
    def test_ligne_existante_jamais_reecrite(self):
        """Un avis deja present est IGNORE, pas mis a jour : la colonne
        `statut_prospection` (saisie humaine) doit survivre a tous les runs."""
        existante = {"publication_number": "BM-001",
                     "statut_prospection": "contacte le 15/07"}
        feuille = FausseFeuille([existante])
        ajoutees, ignorees = bm_attributions.ecrire(
            feuille, [_attribution(pub="BM-001")])
        self.assertEqual((ajoutees, ignorees), (0, 1))
        self.assertEqual(feuille.ajouts, [])            # rien ajoute
        self.assertEqual(feuille.reecritures, 0)        # rien reecrit

    def test_melange_nouvelles_et_deja_vues(self):
        """Run realiste : une attribution connue, une nouvelle. Seule la
        nouvelle part dans le Sheet, la connue est comptee ignoree."""
        feuille = FausseFeuille([{"publication_number": "BM-001",
                                  "statut_prospection": "en cours"}])
        ajoutees, ignorees = bm_attributions.ecrire(
            feuille, [_attribution(pub="BM-001"),
                      _attribution(pub="BM-002", gagnant="AK ZHOL KURYLYS")])
        self.assertEqual((ajoutees, ignorees), (1, 1))
        self.assertEqual(len(feuille.ajouts), 1)        # un seul lot append
        self.assertEqual(len(feuille.ajouts[0]), 1)     # d'une seule ligne
        self.assertEqual(feuille.reecritures, 0)

    def test_aucune_nouvelle_aucun_append(self):
        """Sans nouveaute, `append_rows` n'est pas appele du tout : pas
        d'ecriture a vide qui consommerait le quota de l'API Sheets."""
        feuille = FausseFeuille([{"publication_number": "BM-001",
                                  "statut_prospection": "perdu"}])
        bm_attributions.ecrire(feuille, [_attribution(pub="BM-001")])
        self.assertEqual(feuille.ajouts, [])

    # -- Forme des lignes ajoutees ----------------------------------------
    def test_nouvelle_ligne_statut_vide_et_date_detection(self):
        """Une nouvelle ligne = colonnes officielles + statut VIDE (a remplir
        par l'humain) + date de detection du jour. Ni plus, ni moins."""
        feuille = FausseFeuille()
        bm_attributions.ecrire(feuille, [_attribution(pub="BM-010")])
        ligne = feuille.ajouts[0][0]
        self.assertEqual(len(ligne), len(bm_attributions.TOUTES_COLONNES))
        self.assertEqual(ligne[-2], "")                            # statut vierge
        self.assertEqual(ligne[-1], date.today().isoformat())      # detection

    def test_ligne_pour_sheet_respecte_l_ordre_des_colonnes(self):
        """L'onglet est PARTAGE avec les attributions TED : chaque valeur doit
        tomber dans SA colonne, sinon la lentille Titulaires lit de travers."""
        a = _attribution(pub="BM-020")
        ligne = bm_attributions.ligne_pour_sheet(a)
        self.assertEqual(len(ligne), len(bm_attributions.COLONNES))
        idx = bm_attributions.COLONNES.index
        self.assertEqual(ligne[idx("gagnant")], "STECOL CORPORATION")
        self.assertEqual(ligne[idx("publication_number")], "BM-020")
        self.assertEqual(ligne[idx("pays_execution")], "MLI")

    # -- Robustesse -------------------------------------------------------
    def test_sans_publication_number_on_ajoute_quand_meme(self):
        """Prudence inversee : un identifiant vide ne matche jamais l'index,
        la ligne part donc en ajout (on prefere un doublon potentiel a une
        perte silencieuse de lead)."""
        feuille = FausseFeuille([{"publication_number": "BM-001",
                                  "statut_prospection": "x"}])
        a = _attribution(pub="")
        ajoutees, ignorees = bm_attributions.ecrire(feuille, [a])
        self.assertEqual((ajoutees, ignorees), (1, 0))


@unittest.skipIf(bm_attributions is None, "bm_attributions indisponible")
class TestAnglesMortsParseurBM(unittest.TestCase):
    """Trois comportements du parseur jamais verrouilles jusqu'ici."""

    def test_plafond_gagnants_respecte(self):
        """Groupement de 6 entreprises : seules les `maxi` premieres sortent,
        sans doublon, dans l'ordre du document."""
        bloc = "<div><u><b>Awarded Bidder(s):</b></u></div>" + "".join(
            "<div>ENTREPRISE {} SARL ({})</div>".format(i, 100000 + i)
            for i in range(1, 7))
        noms = bm_attributions.extraire_gagnants(bloc, maxi=4)
        self.assertEqual(len(noms), 4)
        self.assertEqual(noms[0], "ENTREPRISE 1 SARL")
        self.assertEqual(noms[-1], "ENTREPRISE 4 SARL")

    def test_valeur_label_sur_la_meme_ligne(self):
        """Mise en forme compacte observee : 'Project:P178566-...' (valeur
        collee a l'etiquette, sans saut de ligne)."""
        lignes = bm_attributions.texte_en_lignes(
            "<p><b>Project:</b>P178566-Food Systems Resilience</p>")
        self.assertEqual(
            bm_attributions.valeur_label(lignes, "Project"),
            "P178566-Food Systems Resilience")

    def test_une_date_n_est_pas_un_montant(self):
        """Garde anti-date : '2026/07/01' sous une etiquette de montant doit
        etre rejete, pas lu comme 2 milliards."""
        self.assertEqual(bm_attributions._lire_montant("2026/07/01", "USD"), "")


@unittest.skipIf(bm_attributions is None, "bm_attributions indisponible")
class TestMiroirPostgresAttributions(unittest.TestCase):
    """Etape 2 du cap produit : chaque `ecrire` d'attributions alimente aussi
    le miroir Postgres, en best-effort absolu. Quatre proprietes verrouillees,
    pour LES QUATRE collecteurs de l'onglet partage (TED, BM, UNGM, IsDB)."""

    MODULES = ("ted_complet_attributions", "bm_attributions",
               "ungm_attributions", "isdb_radar")

    def _appeler_ecrire(self, module):
        """Appelle module.ecrire avec une feuille doublure et une attribution
        minimale, en enregistrant ce qui part vers le miroir."""
        import importlib
        import radar_stockage
        mod = importlib.import_module(module)
        appels = []
        original = radar_stockage.ecrire_miroir
        radar_stockage.ecrire_miroir = (
            lambda onglet, lignes: appels.append((onglet, list(lignes)))
            or "miroir factice")
        try:
            mod.ecrire(FausseFeuille(), [_attribution(pub="PG-1")])
        finally:
            radar_stockage.ecrire_miroir = original
        return appels

    def test_les_quatre_collecteurs_alimentent_le_miroir(self):
        for module in self.MODULES:
            appels = self._appeler_ecrire(module)
            self.assertEqual(len(appels), 1,
                             "{} n'appelle pas le miroir".format(module))
            onglet, lignes = appels[0]
            self.assertEqual(onglet, "attributions_radar", module)
            self.assertEqual(lignes[0]["publication_number"], "PG-1", module)

    def test_le_miroir_recoit_tout_pas_seulement_le_nouveau(self):
        """Le remplissage retroactif repose la-dessus : une attribution deja
        dans le Sheet part quand meme vers le miroir (qui a sa propre
        memoire, ON CONFLICT DO NOTHING)."""
        import radar_stockage
        feuille = FausseFeuille([{"publication_number": "PG-1",
                                  "statut_prospection": "contacte"}])
        appels = []
        original = radar_stockage.ecrire_miroir
        radar_stockage.ecrire_miroir = (
            lambda onglet, lignes: appels.append(list(lignes)) or "ok")
        try:
            bm_attributions.ecrire(feuille, [_attribution(pub="PG-1")])
        finally:
            radar_stockage.ecrire_miroir = original
        self.assertEqual(len(appels[0]), 1)     # transmis malgre "deja connu"
        self.assertEqual(feuille.ajouts, [])    # et toujours rien au Sheet

    def test_miroir_casse_run_intact(self):
        """Un miroir qui leve (bug, module corrompu) ne doit couter aucun
        lead : l'ecriture Sheet aboutit et le compte est juste."""
        import radar_stockage

        def bombe(_onglet, _lignes):
            raise RuntimeError("panne simulee")

        original = radar_stockage.ecrire_miroir
        radar_stockage.ecrire_miroir = bombe
        feuille = FausseFeuille()
        try:
            ajoutees, ignorees = bm_attributions.ecrire(
                feuille, [_attribution(pub="PG-2")])
        finally:
            radar_stockage.ecrire_miroir = original
        self.assertEqual((ajoutees, ignorees), (1, 0))
        self.assertEqual(len(feuille.ajouts), 1)

    def test_sans_configuration_message_inactif(self):
        """Comportement reel d'aujourd'hui (DATABASE_URL absent en test) :
        le vrai miroir repond 'inactif', sans exception."""
        import radar_stockage
        avant = os.environ.pop("DATABASE_URL", None)
        try:
            feuille = FausseFeuille()
            ajoutees, _ = bm_attributions.ecrire(
                feuille, [_attribution(pub="PG-3")])
            self.assertEqual(ajoutees, 1)
        finally:
            if avant is not None:
                os.environ["DATABASE_URL"] = avant


if __name__ == "__main__":
    unittest.main()
