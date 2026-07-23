# -*- coding: utf-8 -*-
"""Chemin d'ECRITURE des collecteurs bailleurs : index positionnel (regle 4)
et preservation de la zone de saisie humaine.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Quatre collecteurs (AfDB, ADB, EBRD, IDB) construisaient leur index de lignes
avec `feuille.get_all_records()`, en direct, sans passer par le coeur. Ils
n'avaient donc AUCUNE des protections apportees a `ted.charger_index_publication`.
Pire : leur fonction `ecrire_resultats` n'etait couverte par AUCUN test. Les
suites `test_afdb`, `test_adb` et `test_ebrd` verifiaient soigneusement le
parsing et le filtrage, puis s'arretaient juste avant la mise en ligne.

C'est exactement le trou par lequel est passe l'incident `bm_radar` : des
numeros de telephone ranges sous `publication_number`, parce que l'index
d'ecriture, lui, lisait par EN-TETE.

Trois modes de defaillance de `get_all_records()`, verifies sur gspread 6.2.1 :

  1. EN-TETE DUPLIQUE   -> `GSpreadException`. Le collecteur s'arrete en fin de
                           run, apres avoir paye les appels au modele.
  2. NUMERISATION       -> "12345678" devient l'entier 12345678, alors que les
                           collecteurs comparent des CHAINES. Plus aucune
                           correspondance, donc chaque avis deja connu est
                           RE-AJOUTE a chaque run. Silencieux.
  3. EN-TETE DESALIGNE  -> l'index se construit sur la colonne voisine.

Ce fichier est TRANSVERSE plutot que reparti dans les quatre suites : c'est le
meme cablage qui est verifie partout, et l'ajouter pour un cinquieme collecteur
ne coutera qu'une ligne dans CABLAGES. Meme idiome que `test_miroir_avis.py`.

Aucun appel reseau, aucun appel LLM, aucune ecriture reelle.
"""

import importlib
import unittest


# (module, constante de schema, nom lisible)
CABLAGES = [
    ("afdb_radar", "COLONNES_AFDB", "AfDB"),
    ("adb_radar", "COLONNES_ADB", "ADB"),
    ("ebrd_radar", "COLONNES_EBRD", "EBRD"),
    ("idb_radar", "COLONNES_IDB", "IDB"),
]


class FeuilleEspion:
    """Doublure de gspread.Worksheet.

    Deux points importants :
      - `get_all_values()` rend une grille BRUTE dont l'EN-TETE peut mentir,
        tandis que les DONNEES suivent le schema. C'est la situation reelle
        d'un onglet dont l'en-tete a derape : seule la lecture positionnelle
        s'en sort.
      - `get_all_records()` LEVE. Si un collecteur y touche encore, le test le
        dit tout de suite, au lieu de laisser passer une regression silencieuse.
    """

    def __init__(self, entete=None, lignes=()):
        self.entete = list(entete) if entete is not None else None
        self.lignes = [list(l) for l in lignes]
        self.maj = []          # lots passes a batch_update
        self.ajouts = []       # lignes passees a append_rows

    def get_all_values(self):
        if self.entete is None and not self.lignes:
            return []
        grille = [] if self.entete is None else [list(self.entete)]
        return grille + [list(l) for l in self.lignes]

    def get_all_records(self):
        raise AssertionError(
            "get_all_records() ne doit plus etre appele sur le chemin "
            "d'ecriture : il numerise les identifiants et leve sur un en-tete "
            "duplique. Utiliser ted.charger_index_publication(feuille, COLONNES).")

    def batch_update(self, lots):
        self.maj.extend(lots)

    def append_rows(self, lignes, value_input_option=None):
        self.ajouts.extend(lignes)


def _resultat(pub):
    """Resultat minimal accepte par les quatre `ligne_depuis_resultat`."""
    return {
        "avis": {"publication_number": pub, "titre": "Escorte de convois",
                 "acheteur": "Bailleur", "pays_execution": "MLI",
                 "deadline": "", "date_publication": ""},
        "extraction": {}, "score": 7.0, "surete": 6.0, "commercial": 8.0,
        "raffine": False, "divergence": False,
    }


def _ligne_pour(mod, colonnes, pub):
    """Ligne rangee selon le SCHEMA, comme le collecteur l'a ecrite."""
    return mod.ligne_depuis_resultat(_resultat(pub))


class TestIndexEcriturePositionnel(unittest.TestCase):

    def _charger(self, module):
        try:
            return importlib.import_module(module)
        except Exception as e:                       # dependance absente
            self.skipTest("{} indisponible ({})".format(module, e))

    # -- Mode 3 : en-tete desaligne (l'incident bm_radar) ------------------
    def test_entete_desaligne_met_a_jour_au_lieu_de_dupliquer(self):
        """Le coeur du sujet : en-tete decale d'une colonne, donnees correctes.

        En lecture par en-tete, l'identifiant recupere est celui de la colonne
        voisine : la correspondance echoue et l'avis est RE-AJOUTE. En lecture
        positionnelle, il est reconnu et MIS A JOUR."""
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                ligne = _ligne_pour(mod, colonnes, "PUB-1")
                entete_decale = ["colonne_parasite"] + list(colonnes[:-1])
                f = FeuilleEspion(entete=entete_decale, lignes=[ligne])
                nb_new, nb_maj = mod.ecrire_resultats(f, [_resultat("PUB-1")])
                self.assertEqual((nb_new, nb_maj), (0, 1))
                self.assertEqual(f.ajouts, [])
                self.assertEqual(len(f.maj), 1)
                # Ligne 2 = premiere ligne de donnees sous l'en-tete.
                self.assertTrue(f.maj[0]["range"].startswith("A2:"))

    # -- Mode 2 : numerisation silencieuse ---------------------------------
    def test_identifiant_numerique_reconnu(self):
        """`get_all_records()` transformait "12345678" en entier, si bien que la
        comparaison avec la CHAINE du collecteur echouait toujours et que l'avis
        etait re-ajoute a chaque run, sans erreur ni test rouge."""
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                for pub in ("12345678", "00123456"):
                    ligne = _ligne_pour(mod, colonnes, pub)
                    f = FeuilleEspion(entete=list(colonnes), lignes=[ligne])
                    nb_new, nb_maj = mod.ecrire_resultats(f, [_resultat(pub)])
                    self.assertEqual((nb_new, nb_maj), (0, 1),
                                     "identifiant {} re-ajoute".format(pub))

    # -- Mode 1 : en-tete duplique -----------------------------------------
    def test_entete_duplique_ne_fait_plus_echouer_le_run(self):
        """`GSpreadException` en fin de run, apres avoir paye les appels au
        modele : le pire moment possible pour tomber."""
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                entete = list(colonnes)
                entete[0] = "publication_number"     # doublon volontaire
                ligne = _ligne_pour(mod, colonnes, "PUB-1")
                f = FeuilleEspion(entete=entete, lignes=[ligne])
                nb_new, nb_maj = mod.ecrire_resultats(f, [_resultat("PUB-1")])
                self.assertEqual((nb_new, nb_maj), (0, 1))

    # -- Cablage : plus aucun get_all_records ------------------------------
    def test_get_all_records_n_est_plus_appele(self):
        """La doublure leve si on y touche. Ce test echoue donc si quelqu'un
        remet un `get_all_records()` sur le chemin d'ecriture."""
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                f = FeuilleEspion(entete=list(colonnes), lignes=[])
                mod.ecrire_resultats(f, [_resultat("PUB-NEUF")])   # ne doit pas lever

    # -- Comportement nominal ----------------------------------------------
    def test_avis_inconnu_est_ajoute_avec_statut_vierge(self):
        """Un nouvel avis part avec `statut_suivi` vide : la zone de saisie
        humaine appartient a l'utilisateur, pas au run."""
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                f = FeuilleEspion(entete=list(colonnes), lignes=[])
                nb_new, nb_maj = mod.ecrire_resultats(f, [_resultat("PUB-NEUF")])
                self.assertEqual((nb_new, nb_maj), (1, 0))
                self.assertEqual(len(f.ajouts), 1)
                ajoutee = f.ajouts[0]
                # Schema + statut_suivi vide + date_detection.
                self.assertEqual(len(ajoutee), len(colonnes) + 2)
                self.assertEqual(ajoutee[len(colonnes)], "nouveau")

    def test_mise_a_jour_ne_touche_pas_la_zone_humaine(self):
        """La plage de mise a jour s'arrete au dernier champ du SCHEMA.

        `statut_suivi` et `date_detection` vivent APRES : un run ne doit jamais
        les ecraser (regle 5). C'est verifie sur la lettre de colonne, donc sur
        ce qui est reellement envoye a l'API."""
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                import ted_complet_v14 as ted
                colonnes = getattr(mod, cst)
                ligne = _ligne_pour(mod, colonnes, "PUB-1")
                f = FeuilleEspion(entete=list(colonnes), lignes=[ligne])
                mod.ecrire_resultats(f, [_resultat("PUB-1")])
                attendue = "A2:{}2".format(ted.lettre_colonne(len(colonnes)))
                self.assertEqual(f.maj[0]["range"], attendue)

    def test_onglet_vierge_ne_fait_pas_echouer(self):
        """Premier run sur un onglet totalement vide : aucune ligne, aucun
        en-tete. L'index doit simplement etre vide."""
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                f = FeuilleEspion(entete=None, lignes=[])
                nb_new, nb_maj = mod.ecrire_resultats(f, [_resultat("PUB-1")])
                self.assertEqual((nb_new, nb_maj), (1, 0))


class TestSchemaDesCollecteurs(unittest.TestCase):
    """Preconditions du cablage. Si l'une saute, les tests ci-dessus
    deviendraient verts pour de mauvaises raisons."""

    def test_chaque_schema_porte_un_publication_number(self):
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                try:
                    mod = importlib.import_module(module)
                except Exception as e:
                    self.skipTest("{} indisponible ({})".format(module, e))
                self.assertIn("publication_number", getattr(mod, cst))

    def test_chaque_collecteur_expose_ecrire_resultats(self):
        for module, cst, nom in CABLAGES:
            with self.subTest(collecteur=nom):
                try:
                    mod = importlib.import_module(module)
                except Exception as e:
                    self.skipTest("{} indisponible ({})".format(module, e))
                self.assertTrue(callable(getattr(mod, "ecrire_resultats", None)))


if __name__ == "__main__":
    unittest.main()
