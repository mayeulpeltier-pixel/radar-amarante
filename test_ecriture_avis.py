# -*- coding: utf-8 -*-
"""Chemin d'ECRITURE de TOUS les ecrivains Sheet : index positionnel (regle 4)
et preservation de la zone de saisie humaine (regle 5).

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
La regle 4 ("LECTURE POSITIONNELLE, JAMAIS PAR EN-TETE") avait ete appliquee au
chemin de LECTURE (memoire inter-runs, dashboard) mais pas au chemin
d'ECRITURE, qui decide pourtant DANS QUELLE LIGNE on ecrit. Douze fonctions
d'ecriture construisaient leur index par en-tete, dont quatre (AfDB, ADB, EBRD,
IDB) en appelant `get_all_records()` en direct, sans meme passer par le coeur.

Et surtout : ces fonctions n'etaient couvertes par presque aucun test. Les
suites verifiaient soigneusement le parsing, le filtrage pays, la fenetre de
fraicheur, puis s'arretaient juste avant la mise en ligne. C'est exactement le
trou par lequel est passe l'incident `bm_radar` : des numeros de telephone
ranges sous `publication_number`.

Trois modes de defaillance de `get_all_records()`, verifies sur gspread 6.2.1 :

  1. EN-TETE DUPLIQUE   -> `GSpreadException`. Le collecteur s'arrete en fin de
                           run, apres avoir paye les appels au modele.
  2. NUMERISATION       -> "12345678" devient l'entier 12345678, alors que les
                           collecteurs comparent des CHAINES. Plus aucune
                           correspondance, donc chaque entree deja connue est
                           RE-AJOUTEE a chaque run. Silencieux.
  3. EN-TETE DESALIGNE  -> l'index se construit sur la colonne voisine.

CHOIX DE CONCEPTION
-------------------
Fichier TRANSVERSE plutot que tests repartis dans douze suites : c'est le meme
cablage partout, et brancher un treizieme ecrivain ne coutera qu'une ligne dans
`ECRIVAINS`. Meme idiome que `test_miroir_avis.py`.

La ligne "deja presente" est construite DEPUIS LE SCHEMA (`colonnes.index(...)`)
et non via le constructeur de ligne de chaque module. C'est volontaire : le test
ne doit dependre que du contrat verifie, la position de `publication_number`,
et non des exigences internes de douze constructeurs differents.

Aucun appel reseau, aucun appel LLM, aucune ecriture reelle.
"""

import importlib
import unittest


# (module, fonction d'ecriture, constante de schema, nature d'entree, nom)
#   nature "avis"        : la fonction recoit des resultats {avis, extraction, ...}
#   nature "attribution" : la fonction recoit des dicts plats deja formes
ECRIVAINS = [
    ("ted_complet_v14", "ecrire_resultats_dans_sheet", "COLONNES_SHEET", "avis", "TED"),
    ("ted_complet_bm", "ecrire_resultats_bm", "COLONNES_BM", "avis", "Banque Mondiale"),
    ("ted_complet_reliefweb", "ecrire_resultats_rw", "COLONNES_RW", "avis", "ReliefWeb"),
    ("afdb_radar", "ecrire_resultats", "COLONNES_AFDB", "avis", "AfDB"),
    ("adb_radar", "ecrire_resultats", "COLONNES_ADB", "avis", "ADB"),
    ("ebrd_radar", "ecrire_resultats", "COLONNES_EBRD", "avis", "EBRD"),
    ("idb_radar", "ecrire_resultats", "COLONNES_IDB", "avis", "IDB"),
    ("ungm_radar", "ecrire", "COLONNES_UNGM", "avis", "UNGM"),
    ("ted_complet_attributions", "ecrire", "COLONNES", "attribution", "TED attributions"),
    ("bm_attributions", "ecrire", "COLONNES", "attribution", "BM attributions"),
    ("ungm_attributions", "ecrire", "COLONNES", "attribution", "UNGM attributions"),
    ("isdb_radar", "ecrire", "COLONNES", "attribution", "IsDB attributions"),
]


class FeuilleEspion:
    """Doublure de gspread.Worksheet.

    Deux points importants :
      - `get_all_values()` rend une grille BRUTE dont l'EN-TETE peut mentir,
        tandis que les DONNEES suivent le schema. C'est la situation reelle
        d'un onglet dont l'en-tete a derape : seule la lecture positionnelle
        s'en sort.
      - `get_all_records()` LEVE. Si un ecrivain y touche encore, le test le
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

    def update(self, *a, **k):
        raise AssertionError("update() interdit : zone de saisie humaine")

    def update_cell(self, *a, **k):
        raise AssertionError("update_cell() interdit : zone de saisie humaine")


def _entree(nature, colonnes, pub):
    """Entree minimale acceptee par l'ecrivain, selon sa nature."""
    if nature == "attribution":
        a = {c: "" for c in colonnes}
        a["publication_number"] = pub
        a["_nb_gagnants"] = 1          # exige par ted_complet_attributions.ligne
        a["gagnant"] = "TITULAIRE SA"
        return a
    return {
        "avis": {"publication_number": pub, "titre": "Escorte de convois",
                 "acheteur": "Bailleur", "pays_execution": "MLI",
                 "pays_acheteur": "MLI", "deadline": "", "date_publication": ""},
        "extraction": {}, "score": 7.0, "final": 7.0, "surete": 6.0,
        "commercial": 8.0, "raffine": False, "divergence": False,
    }


def _ligne_existante(colonnes, pub):
    """Ligne rangee SELON LE SCHEMA, telle que le collecteur l'a ecrite.

    Construite depuis `colonnes` et non via le constructeur du module : le test
    ne doit dependre que du contrat verifie, la position de l'identifiant."""
    ligne = [""] * len(colonnes)
    ligne[list(colonnes).index("publication_number")] = pub
    return ligne


class TestIndexEcriturePositionnel(unittest.TestCase):
    """Invariant commun aux douze ecrivains, malgre leurs differences de
    signature :

      - une entree DEJA PRESENTE n'est JAMAIS re-ajoutee (`append_rows`) ;
      - une entree INCONNUE est ajoutee exactement une fois.

    Les ecrivains d'avis mettent la ligne a jour, ceux d'attributions
    l'ignorent : dans les deux cas, `ajouts` doit rester vide. C'est
    precisement la garantie anti-doublon que la lecture par en-tete perdait."""

    def _charger(self, module):
        try:
            return importlib.import_module(module)
        except Exception as e:                       # dependance absente
            self.skipTest("{} indisponible ({})".format(module, e))

    def _ecrire(self, mod, fonction, feuille, entree):
        return getattr(mod, fonction)(feuille, [entree])

    # -- Mode 3 : en-tete desaligne (l'incident bm_radar) ------------------
    def test_entete_desaligne_ne_provoque_plus_de_doublon(self):
        """Le coeur du sujet : en-tete decale d'une colonne, donnees correctes.

        En lecture par en-tete, l'identifiant recupere est celui de la colonne
        voisine : la correspondance echoue et l'entree est RE-AJOUTEE. En
        lecture positionnelle, elle est reconnue."""
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                entete_decale = ["colonne_parasite"] + list(colonnes[:-1])
                f = FeuilleEspion(entete=entete_decale,
                                  lignes=[_ligne_existante(colonnes, "PUB-1")])
                neuf, connu = self._ecrire(mod, fonction, f,
                                           _entree(nature, colonnes, "PUB-1"))
                self.assertEqual(f.ajouts, [], "entree connue re-ajoutee")
                self.assertEqual((neuf, connu), (0, 1))

    # -- Mode 2 : numerisation silencieuse ---------------------------------
    def test_identifiant_numerique_reconnu(self):
        """`get_all_records()` transformait "12345678" en entier, si bien que la
        comparaison avec la CHAINE de l'ecrivain echouait toujours et que
        l'entree etait re-ajoutee a chaque run, sans erreur ni test rouge.

        "00123456" verifie en plus que le zero de tete survit : c'est un
        identifiant, pas un nombre."""
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                for pub in ("12345678", "00123456"):
                    f = FeuilleEspion(entete=list(colonnes),
                                      lignes=[_ligne_existante(colonnes, pub)])
                    neuf, connu = self._ecrire(mod, fonction, f,
                                               _entree(nature, colonnes, pub))
                    self.assertEqual(f.ajouts, [],
                                     "identifiant {} re-ajoute".format(pub))
                    self.assertEqual((neuf, connu), (0, 1))

    # -- Mode 1 : en-tete duplique -----------------------------------------
    def test_entete_duplique_ne_fait_plus_echouer_le_run(self):
        """`GSpreadException` en fin de run, apres avoir paye les appels au
        modele : le pire moment possible pour tomber."""
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                entete = list(colonnes)
                entete[0] = "publication_number"     # doublon volontaire
                f = FeuilleEspion(entete=entete,
                                  lignes=[_ligne_existante(colonnes, "PUB-1")])
                neuf, connu = self._ecrire(mod, fonction, f,
                                           _entree(nature, colonnes, "PUB-1"))
                self.assertEqual((neuf, connu), (0, 1))

    # -- Cablage : plus aucun get_all_records ------------------------------
    def test_get_all_records_n_est_plus_appele(self):
        """La doublure leve si on y touche. Ce test echoue donc si quelqu'un
        remet un `get_all_records()` sur le chemin d'ecriture."""
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                f = FeuilleEspion(entete=list(colonnes), lignes=[])
                self._ecrire(mod, fonction, f,
                             _entree(nature, colonnes, "PUB-NEUF"))

    # -- Comportement nominal ----------------------------------------------
    def test_entree_inconnue_ajoutee_une_seule_fois(self):
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                f = FeuilleEspion(entete=list(colonnes), lignes=[])
                neuf, connu = self._ecrire(mod, fonction, f,
                                           _entree(nature, colonnes, "PUB-NEUF"))
                self.assertEqual((neuf, connu), (1, 0))
                self.assertEqual(len(f.ajouts), 1)

    def test_zone_de_saisie_humaine_preservee(self):
        """Regle 5 : `statut_suivi` / `statut_prospection` et `date_detection`
        vivent APRES le dernier champ du schema. Un run ne doit jamais les
        ecraser. Verifie sur ce qui part reellement vers l'API : la plage
        `batch_update` doit s'arreter a la derniere colonne du schema, et
        `update()` / `update_cell()` ne doivent jamais etre appeles (la
        doublure leve si c'est le cas)."""
        import ted_complet_v14 as ted
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                f = FeuilleEspion(entete=list(colonnes),
                                  lignes=[_ligne_existante(colonnes, "PUB-1")])
                self._ecrire(mod, fonction, f,
                             _entree(nature, colonnes, "PUB-1"))
                attendue = "A2:{}2".format(ted.lettre_colonne(len(colonnes)))
                for lot in f.maj:            # vide pour les ecrivains en ajout seul
                    self.assertEqual(lot["range"], attendue)

    def test_onglet_vierge_ne_fait_pas_echouer(self):
        """Premier run sur un onglet totalement vide : ni ligne, ni en-tete.
        L'index doit simplement etre vide, sans exception."""
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = getattr(mod, cst)
                f = FeuilleEspion(entete=None, lignes=[])
                neuf, connu = self._ecrire(mod, fonction, f,
                                           _entree(nature, colonnes, "PUB-1"))
                self.assertEqual((neuf, connu), (1, 0))


class TestSchemasDesEcrivains(unittest.TestCase):
    """Preconditions du cablage. Si l'une saute, les tests ci-dessus
    deviendraient verts pour de mauvaises raisons."""

    def _charger(self, module):
        try:
            return importlib.import_module(module)
        except Exception as e:
            self.skipTest("{} indisponible ({})".format(module, e))

    def test_chaque_schema_porte_un_publication_number(self):
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                self.assertIn("publication_number",
                              getattr(self._charger(module), cst))

    def test_chaque_ecrivain_est_appelable(self):
        for module, fonction, cst, nature, nom in ECRIVAINS:
            with self.subTest(ecrivain=nom):
                self.assertTrue(
                    callable(getattr(self._charger(module), fonction, None)))

    def test_les_quatre_ecrivains_d_attributions_partagent_le_schema(self):
        """Ils ecrivent tous dans le MEME onglet `attributions_radar`. Un
        schema qui divergerait produirait des lignes decalees dans un onglet
        commun, donc exactement l'incident que la regle 4 doit prevenir."""
        reference, onglet = None, None
        for module, fonction, cst, nature, nom in ECRIVAINS:
            if nature != "attribution":
                continue
            with self.subTest(ecrivain=nom):
                mod = self._charger(module)
                colonnes = list(getattr(mod, cst))
                cible = getattr(mod, "NOM_ONGLET", None)
                if reference is None:
                    reference, onglet = colonnes, cible
                self.assertEqual(colonnes, reference)
                self.assertEqual(cible, onglet)


if __name__ == "__main__":
    unittest.main()
