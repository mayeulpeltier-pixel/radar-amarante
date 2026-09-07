# -*- coding: utf-8 -*-
"""Réglages d'environnement : une valeur vide n'est pas une valeur (26/08/2026).

L'INCIDENT
----------
    File "collecteur_projets.py", line 57, in <module>
        PROJETS_PAR_RUN = int(os.environ.get("RADAR_PROJETS_PAR_RUN", "6"))
    ValueError: invalid literal for int() with base 10: ''

`os.environ.get(nom, defaut)` ne rend le défaut que si la clé est ABSENTE. Or
GitHub Actions définit toujours la variable et y met une chaîne VIDE quand la
valeur n'est pas fournie :

    RADAR_PROJETS_PAR_RUN: ${{ github.event.inputs.projets_par_run }}

Champ laissé vide au lancement -> variable présente, valeur "" -> `int("")`
lève, et le collecteur meurt À L'IMPORT, avant d'avoir rien fait.

**Une valeur non renseignée ne doit jamais être plus dangereuse qu'une valeur
absente.**

L'AMPLEUR
---------
Le dépôt comptait 141 occurrences de `int(os.environ.get(...))`, toutes
exposées. 135 ont été converties vers `config_env` ; les 6 restantes
utilisaient déjà le motif sûr `os.environ.get(...) or "défaut"`.

`radar_retroaction` avait sa propre parade (`_f`), utilisée par lui seul :
recopier la solution dans chaque fichier aurait produit autant de variantes à
maintenir.

Tests OFFLINE : aucune dépendance, aucun réseau.
"""

import ast
import glob
import os
import re
import unittest

import config_env


class TestValeurVide(unittest.TestCase):
    """LE cas de l'incident."""

    def setUp(self):
        self._sauv = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._sauv)

    def test_chaine_vide_vaut_le_defaut(self):
        os.environ["X"] = ""
        self.assertEqual(config_env.entier("X", "6"), 6)

    def test_espaces_seuls_valent_le_defaut(self):
        os.environ["X"] = "   "
        self.assertEqual(config_env.entier("X", "6"), 6)

    def test_cle_absente_vaut_le_defaut(self):
        os.environ.pop("X", None)
        self.assertEqual(config_env.entier("X", "6"), 6)

    def test_valeur_fournie_est_respectee(self):
        os.environ["X"] = "12"
        self.assertEqual(config_env.entier("X", "6"), 12)

    def test_le_cas_exact_du_run(self):
        """Reproduction : champ `projets_par_run` laissé vide au lancement."""
        os.environ["RADAR_PROJETS_PAR_RUN"] = ""
        self.assertEqual(
            config_env.entier("RADAR_PROJETS_PAR_RUN", "6"), 6)


class TestJamaisDException(unittest.TestCase):
    """Une valeur illisible ne doit pas tuer un collecteur au chargement : un
    run dégradé vaut mieux qu'un run mort avant d'avoir commencé."""

    def setUp(self):
        self._sauv = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._sauv)

    def test_texte_illisible_retombe_sur_le_defaut(self):
        for faux in ("abc", "6 projets", "-", "NaN?"):
            os.environ["X"] = faux
            self.assertEqual(config_env.entier("X", "6"), 6, faux)

    def test_decimal_accepte_pour_un_entier(self):
        """« 6.0 » est un 6 mal tapé, pas une erreur de configuration."""
        os.environ["X"] = "6.0"
        self.assertEqual(config_env.entier("X", "3"), 6)

    def test_virgule_decimale_acceptee(self):
        os.environ["X"] = "0,85"
        self.assertEqual(config_env.reel("X", "1.0"), 0.85)

    def test_reel_illisible_retombe_sur_le_defaut(self):
        os.environ["X"] = "beaucoup"
        self.assertEqual(config_env.reel("X", "1.5"), 1.5)

    def test_l_erreur_est_signalee(self):
        """Retomber sur le défaut en silence masquerait une configuration
        fautive pendant des semaines."""
        with open("config_env.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("illisible, valeur par defaut", src)


class TestTexteEtBooleen(unittest.TestCase):

    def setUp(self):
        self._sauv = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._sauv)

    def test_texte_vide_vaut_le_defaut(self):
        os.environ["X"] = ""
        self.assertEqual(config_env.texte("X", "ombre"), "ombre")

    def test_booleen_tolerant_sur_la_forme(self):
        for vrai in ("1", "true", "TRUE", "oui", "on", "actif"):
            os.environ["X"] = vrai
            self.assertTrue(config_env.booleen("X"), vrai)
        for faux in ("0", "false", "non", "off", "inactif"):
            os.environ["X"] = faux
            self.assertFalse(config_env.booleen("X", True), faux)

    def test_valeur_inconnue_applique_le_defaut(self):
        """« peut-être » n'est pas « non » : appliquer le défaut est plus
        honnête que de trancher au hasard."""
        os.environ["X"] = "bof"
        self.assertTrue(config_env.booleen("X", True))
        self.assertFalse(config_env.booleen("X", False))


class TestCouvertureDuDepot(unittest.TestCase):
    """Le garde-fou : plus aucun module ne doit pouvoir mourir à l'import à
    cause d'une variable vide."""

    MOTIF = re.compile(r"(int|float)\(os\.environ\.get\(\s*[\"'][A-Z0-9_]+[\"']\s*,")

    def _modules(self):
        return [f for f in sorted(glob.glob("*.py"))
                if not f.startswith("test_") and f != "config_env.py"]

    # Modules qui lisent une variable REELLEMENT alimentee par une expression
    # pouvant rendre "" : `${{ github.event.inputs.* }}` ou `${{ vars.* }}`.
    # Les autres lisent des valeurs fixees en dur dans les workflows : le
    # defaut positionnel y est sans danger, et convertir 39 fichiers de plus
    # aurait coute plus cher en relecture que le risque couvert.
    EXPOSES = ("collecteur_projets.py", "decouverte_projets.py",
               "shadow_run_projets.py", "sonde_delegation.py",
               "sonde_montant_bailleurs.py", "sonde_projets.py",
               "sonde_rattachement.py", "sonde_requetes.py")

    def test_les_modules_exposes_n_ont_plus_la_forme_piegeuse(self):
        """C'est cette forme précise qui est piégeuse : le défaut n'est rendu
        que si la clé est ABSENTE, pas si elle est vide."""
        coupables = []
        for f in self.EXPOSES:
            if not os.path.exists(f):
                continue
            with open(f, encoding="utf-8") as fh:
                for n, ligne in enumerate(fh, 1):
                    if self.MOTIF.search(ligne):
                        coupables.append("{}:{}".format(f, n))
        self.assertEqual(coupables, [],
                         "forme piégeuse restante : {}".format(coupables))

    def test_les_modules_exposes_utilisent_config_env(self):
        for f in self.EXPOSES:
            if not os.path.exists(f):
                continue
            with open(f, encoding="utf-8") as fh:
                self.assertIn("config_env.", fh.read(), f)

    def test_la_liste_des_exposes_correspond_aux_workflows(self):
        """GARDE : si un workflow se met à passer une nouvelle variable par
        `inputs` ou `vars`, le module qui la lit doit rejoindre la liste."""
        import re as _re
        chemins = []
        for dossier in (".github/workflows", ".", "workflows"):
            chemins.extend(glob.glob(os.path.join(dossier, "*.yml")))
        vulnerables = set()
        for c in chemins:
            with open(c, encoding="utf-8") as fh:
                for m in _re.finditer(
                        r"^\s*([A-Z0-9_]+):\s*\$\{\{\s*"
                        r"(github\.event\.inputs|vars|inputs)\.", fh.read(), _re.M):
                    vulnerables.add(m.group(1))
        if not vulnerables:
            self.skipTest("workflows introuvables")
        oublies = []
        for f in self._modules():
            if f in self.EXPOSES:
                continue
            with open(f, encoding="utf-8") as fh:
                src = fh.read()
            for v in vulnerables:
                if _re.search(r'(int|float)\(os\.environ\.get\(\s*"%s"' % v, src):
                    oublies.append("{} lit {}".format(f, v))
        self.assertEqual(oublies, [], "module exposé non converti : {}".format(oublies))

    def test_les_formes_alternatives_sont_sures(self):
        """`os.environ.get(X) or "d"` gère la chaîne vide : ces occurrences
        sont légitimes et n'avaient pas besoin d'être converties."""
        motif = re.compile(r"os\.environ\.get\([^)]*\)\s*or\s*[\"']")
        trouve = False
        for f in self._modules():
            with open(f, encoding="utf-8") as fh:
                if motif.search(fh.read()):
                    trouve = True
        self.assertTrue(trouve, "aucune forme alternative trouvée")

    def test_tous_les_modules_parsent(self):
        """La conversion a touché 42 fichiers : aucun ne doit être cassé."""
        for f in self._modules():
            with self.subTest(f):
                with open(f, encoding="utf-8") as fh:
                    ast.parse(fh.read())

    def test_config_env_ne_depend_que_de_la_bibliotheque_standard(self):
        """Il est importé par des modules qui tournent sans aucune dépendance
        installée : lui en donner une les casserait tous."""
        with open("config_env.py", encoding="utf-8") as f:
            arbre = ast.parse(f.read())
        importes = set()
        for n in ast.walk(arbre):
            if isinstance(n, ast.Import):
                importes.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                importes.add(n.module.split(".")[0])
        self.assertEqual(importes, {"os"})


class TestCollecteurQuiPlantait(unittest.TestCase):
    """Le module qui a fait échouer le run."""

    def setUp(self):
        self._sauv = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._sauv)

    def test_import_avec_variable_vide(self):
        import importlib
        os.environ["RADAR_PROJETS_PAR_RUN"] = ""
        os.environ["RADAR_PROJETS_LOT"] = ""
        import collecteur_projets
        importlib.reload(collecteur_projets)
        self.assertEqual(collecteur_projets.PROJETS_PAR_RUN, 6)
        self.assertEqual(collecteur_projets.TAILLE_LOT, 10)


if __name__ == "__main__":
    unittest.main()
