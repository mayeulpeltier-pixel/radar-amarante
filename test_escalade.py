# -*- coding: utf-8 -*-
"""Critere d'escalade Sonnet lie a la securite deja en place.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Sept collecteurs portaient chacun leur copie du meme critere :

    if e.get("securite_existante_detectee"): return True

Tant que ce champ etait le booleen BRUT du modele, il valait True des qu'une
securite quelconque etait reperee. Depuis le passage a l'enum a quatre
valeurs, `securite_existante_detectee` ne vaut plus True que pour
`interne_client`. Le critere a donc silencieusement cesse de couvrir
`prestataire_tiers`.

Consequence : les leads de DEPLACEMENT CONCURRENTIEL -- les plus interessants
commercialement, et les plus delicats a juger -- ont perdu leur relecture
Sonnet. Aucun test ne l'a vu, parce qu'aucun test ne couvrait ce critere.

Les DEUX cas la meritent, pour des raisons opposees :
  - `interne_client`    : on s'apprete a ECARTER le lead, une erreur coute un
                          marche entier ;
  - `prestataire_tiers` : on s'apprete a le POUSSER en conquete, c'est le
                          jugement le plus fin du pipeline.

Le critere vit desormais dans UNE fonction du coeur,
`ted.escalade_pour_securite`, et ce fichier verrouille les deux choses :
son comportement, et son CABLAGE effectif dans chaque collecteur.

Note sur `ted_complet_boamp.py` : il porte lui aussi une copie du critere,
mais il est CODE MORT (absent de radar_run.py, de radar.yml, et importe par
aucun module). Il n'est donc pas cable ici. Voir le point 9 de la feuille de
route (suppression du code mort).

Aucun appel reseau ni LLM.
"""

import importlib
import inspect
import unittest

import ted_complet_v14 as ted


# Collecteurs dont `merite_escalade` est une fonction de MODULE : on peut
# l'appeler directement.
#   (module, nom, cles de score a fournir)
DIRECTS = [
    ("afdb_radar", "AfDB", {"final_haiku": 0.0}),
    ("adb_radar", "ADB", {"final_haiku": 0.0}),
    ("ebrd_radar", "EBRD", {"final_haiku": 0.0}),
    ("idb_radar", "IDB", {"score": 0.0, "surete": 0.0}),
]

# Collecteurs dont `merite_escalade` est imbrique dans main() : inatteignable
# sans lancer un run complet. On verifie le CABLAGE dans le source.
IMBRIQUES = [
    ("ted_complet_v14", "TED", "escalade_pour_securite(r[\"extraction\"])"),
    ("ted_complet_bm", "Banque Mondiale", "ted.escalade_pour_securite(r[\"extraction\"])"),
    ("ted_complet_reliefweb", "ReliefWeb", "ted.escalade_pour_securite(r[\"extraction\"])"),
]


def _extraction(enum=None, booleen=None, confiance=0.95, normaliser=True):
    """Extraction volontairement TIEDE : confiance haute et scores nuls, pour
    que les autres criteres d'escalade ne se declenchent pas. Ainsi, si
    escalade il y a, elle ne peut venir QUE de la securite.

    `normaliser=True` par defaut, parce que c'est la realite de la production :
    `appeler_llm` passe toujours par `normaliser_securite` avant que le
    resultat n'atteigne le tri d'escalade. Une extraction brute donnerait un
    test plus facile a passer, mais moins fidele -- et surtout, elle masquerait
    la regression : un `prestataire_tiers` NORMALISE porte
    `securite_existante_detectee = False`, ce qui est precisement pourquoi
    l'ancien critere le laissait filer."""
    e = {"confiance": confiance}
    if enum is not None:
        e["securite_existante"] = enum
    if booleen is not None:
        e["securite_existante_detectee"] = booleen
    return ted.normaliser_securite(e) if normaliser else e


# ===========================================================================
# LA FONCTION DU COEUR
# ===========================================================================

class TestCritereDuCoeur(unittest.TestCase):

    def test_interne_client_declenche(self):
        """On va ECARTER le lead : une erreur coute un marche entier."""
        self.assertTrue(ted.escalade_pour_securite(
            _extraction(enum="interne_client")))

    def test_prestataire_tiers_declenche(self):
        """LA regression corrigee. On va POUSSER le lead en conquete : c'est
        le jugement le plus fin du pipeline, il merite Sonnet."""
        self.assertTrue(ted.escalade_pour_securite(
            _extraction(enum="prestataire_tiers")))

    def test_aucune_et_inconnu_ne_declenchent_pas(self):
        """Ces deux valeurs ne disent rien de particulier : les criteres de
        score et de confiance suffisent. Declencher ici reviendrait a escalader
        presque tout, donc a payer Sonnet pour rien."""
        for enum in ("aucune", "inconnu"):
            self.assertFalse(ted.escalade_pour_securite(_extraction(enum=enum)))

    def test_valeur_inattendue_ne_declenche_pas(self):
        self.assertFalse(ted.escalade_pour_securite(
            _extraction(enum="n_importe_quoi")))

    def test_repli_sur_l_ancien_booleen(self):
        """Tolerance de transition, meme logique que normaliser_securite."""
        self.assertTrue(ted.escalade_pour_securite(
            _extraction(booleen=True, normaliser=False)))
        self.assertFalse(ted.escalade_pour_securite(
            _extraction(booleen=False, normaliser=False)))

    def test_l_enum_prime_sur_le_booleen(self):
        """Quand les deux sont presents, l'enum fait foi : c'est lui qui porte
        l'information fine. Un `prestataire_tiers` normalise a justement
        `securite_existante_detectee = False`, et doit quand meme escalader."""
        e = ted.normaliser_securite({"securite_existante": "prestataire_tiers"})
        self.assertFalse(e["securite_existante_detectee"])
        self.assertTrue(ted.escalade_pour_securite(e))

    def test_entrees_degenerees(self):
        for valeur in (None, "", 0, [], "prestataire_tiers"):
            self.assertFalse(ted.escalade_pour_securite(valeur))
        self.assertFalse(ted.escalade_pour_securite({}))

    def test_ne_modifie_pas_l_extraction(self):
        """Fonction de LECTURE. `normaliser_securite` ecrit, celle-ci non :
        confondre les deux ferait muter une extraction au moment du tri."""
        avant = _extraction(enum="prestataire_tiers")
        copie = dict(avant)
        ted.escalade_pour_securite(avant)
        self.assertEqual(avant, copie)


# ===========================================================================
# LE CABLAGE DANS LES COLLECTEURS
# ===========================================================================

class TestCablageDesCollecteurs(unittest.TestCase):

    def _charger(self, module):
        try:
            return importlib.import_module(module)
        except Exception as e:
            self.skipTest("{} indisponible ({})".format(module, e))

    def test_collecteurs_directs_escaladent_le_prestataire_tiers(self):
        """Le cas de regression, teste de bout en bout : score nul, confiance
        haute, donc AUCUN autre critere ne peut declencher. Si l'escalade a
        lieu, c'est bien la securite qui l'a provoquee."""
        for module, nom, scores in DIRECTS:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                r = dict(scores)
                r["extraction"] = _extraction(enum="prestataire_tiers")
                self.assertTrue(mod.merite_escalade(r))

    def test_collecteurs_directs_escaladent_l_interne_client(self):
        for module, nom, scores in DIRECTS:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                r = dict(scores)
                r["extraction"] = _extraction(enum="interne_client")
                self.assertTrue(mod.merite_escalade(r))

    def test_collecteurs_directs_n_escaladent_pas_sans_motif(self):
        """Garde-fou de cout : une extraction tiede sans securite notable ne
        doit PAS partir chez Sonnet, sinon l'escalade perd son sens et la
        facture double."""
        for module, nom, scores in DIRECTS:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                r = dict(scores)
                r["extraction"] = _extraction(enum="aucune")
                self.assertFalse(mod.merite_escalade(r))

    def test_collecteurs_imbriques_appellent_bien_le_critere(self):
        """`merite_escalade` est defini dans main() pour ces trois-la :
        inatteignable sans lancer un run. On verifie donc le cablage dans le
        source, ce qui suffit a detecter un retour en arriere."""
        for module, nom, attendu in IMBRIQUES:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                source = inspect.getsource(mod)
                self.assertIn(attendu, source)

    def test_plus_aucune_lecture_directe_du_booleen_pour_escalader(self):
        """La formulation fautive ne doit pas revenir dans `merite_escalade`.

        On inspecte le source de la FONCTION, pas du module : le coeur cite
        justement la formulation fautive dans la docstring de
        `escalade_pour_securite`, pour expliquer ce qui n'allait pas. Une
        recherche a l'echelle du module confondrait l'explication et la
        faute."""
        fautif = 'get("securite_existante_detectee")'
        for module, nom, _ in DIRECTS:
            with self.subTest(collecteur=nom):
                mod = self._charger(module)
                self.assertNotIn(fautif,
                                 inspect.getsource(mod.merite_escalade),
                                 "critere d'escalade revenu a la lecture directe")


class TestClauseMorteIDB(unittest.TestCase):
    """IDB comparait un BOOLEEN a des valeurs d'ENUM :

        str(e.get("securite_existante_detectee", "")).lower()
            in ("prestataire_tiers", "aucune")

    `str(False).lower()` vaut "false", qui n'est jamais dans ce couple : la
    clause ne pouvait donc JAMAIS etre vraie. Elle avait l'air de couvrir le
    deplacement concurrentiel, et ne couvrait rien du tout."""

    def _idb(self):
        try:
            return importlib.import_module("idb_radar")
        except Exception as e:
            self.skipTest("idb_radar indisponible ({})".format(e))

    def test_la_comparaison_fautive_a_disparu(self):
        source = inspect.getsource(self._idb())
        self.assertNotIn('str(e.get("securite_existante_detectee", "")).lower()',
                         source)

    def test_le_prestataire_tiers_declenche_desormais(self):
        idb = self._idb()
        r = {"score": 0.0, "surete": 0.0,
             "extraction": _extraction(enum="prestataire_tiers")}
        self.assertTrue(idb.merite_escalade(r))

    def test_extraction_absente_escalade_toujours(self):
        """Comportement propre a IDB, volontairement conserve : sans
        extraction, on relit plutot que de trancher a l'aveugle."""
        self.assertTrue(self._idb().merite_escalade(
            {"score": 0.0, "surete": 0.0, "extraction": None}))


if __name__ == "__main__":
    unittest.main()
