# -*- coding: utf-8 -*-
"""Validité des workflows GitHub Actions (26/08/2026).

L'INCIDENT D'ORIGINE
--------------------
Le workflow « Radar Amarante » a DISPARU de l'onglet Actions. Plus de bouton
« Run workflow ».

Cause : en câblant P2.1, `DATABASE_URL` a été ajouté au bloc `env:` de l'étape
« Generer le tableau de bord »... qui en avait déjà un. Clé dupliquée dans un
mapping YAML. GitHub refuse le fichier et retire le workflow de la liste.

`yaml.safe_load` accepte les clés dupliquées et garde silencieusement la
dernière : la validation était donc plus permissive que la production, ce qui
ne valide rien.

LE SECOND INCIDENT, CELUI DE CE FICHIER
---------------------------------------
Première version : `import yaml` en tête. PyYAML n'est pas installé en CI, et
comme deux autres fichiers de test importent celui-ci, l'import a fait tomber
TROIS modules d'un coup. 2100 tests étaient devenus otages d'une dépendance
ajoutée pour un seul contrôle.

Un garde-fou qui tombe faute de dépendance ne garde rien.

Le contrôle qui compte -- la détection des clés dupliquées -- est donc écrit
SANS PyYAML. Vérifié sur les 23 workflows du dépôt : accord parfait avec un
chargeur YAML strict, zéro désaccord. Les contrôles STRUCTURELS (name, on,
steps) utilisent PyYAML s'il est présent et se sautent proprement sinon.

Tests OFFLINE : lecture de fichiers, aucun réseau.
"""

import glob
import os
import re
import unittest

try:                                    # PyYAML est un CONFORT, pas un prérequis
    import yaml
except ImportError:                     # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# LOCALISATION DES WORKFLOWS -- définition UNIQUE, importée par les autres
# fichiers de test qui lisent un .yml.
# ---------------------------------------------------------------------------
# Trois tests faisaient `open("radar.yml")`. Ils passaient en local parce que
# le bac à sable avait une copie à la racine, et ont échoué en CI où les
# workflows vivent dans `.github/workflows/`. Un test qui passe pour la
# mauvaise raison est pire qu'un test absent.

DOSSIERS_WORKFLOWS = (".github/workflows", ".", "workflows")


def chemin_workflow(nom):
    """Chemin réel d'un workflow, ou None s'il est introuvable."""
    for dossier in DOSSIERS_WORKFLOWS:
        chemin = os.path.join(dossier, nom)
        if os.path.exists(chemin):
            return chemin
    return None


def lire_workflow(nom):
    """Contenu d'un workflow, ou None s'il est introuvable."""
    chemin = chemin_workflow(nom)
    if not chemin:
        return None
    with open(chemin, encoding="utf-8") as f:
        return f.read()


def workflows():
    """Tous les .yml du dépôt qui ressemblent à un workflow, où qu'ils soient."""
    out = []
    for dossier in DOSSIERS_WORKFLOWS:
        for chemin in sorted(glob.glob(os.path.join(dossier, "*.yml"))):
            try:
                with open(chemin, encoding="utf-8") as f:
                    brut = f.read()
            except OSError:
                continue
            if "jobs:" in brut and ("runs-on" in brut or "uses:" in brut):
                out.append(chemin)
    return out


# ---------------------------------------------------------------------------
# DÉTECTION DES CLÉS DUPLIQUÉES -- sans dépendance, à dessein
# ---------------------------------------------------------------------------
def cles_dupliquees(texte):
    """[(ligne, clé, ligne_précédente)] des clés dupliquées dans un MÊME
    mapping. Fonction PURE, sans dépendance.

    Pile de blocs indentés. Un tiret de liste ouvre un nouveau mapping ; un
    bloc scalaire (`|` ou `>`) est sauté intégralement, son contenu n'étant
    pas du YAML.

    Accord vérifié avec un chargeur YAML strict sur les 23 workflows du
    dépôt : zéro désaccord."""
    pile = []                 # [(indentation, {clé: n_ligne})]
    doublons = []
    bloc_scalaire = None      # indentation du bloc littéral à ignorer
    for n, brut in enumerate(texte.splitlines(), 1):
        if not brut.strip() or brut.lstrip().startswith("#"):
            continue
        indent = len(brut) - len(brut.lstrip())
        if bloc_scalaire is not None:
            if indent > bloc_scalaire:
                continue
            bloc_scalaire = None
        corps = brut.lstrip()
        tiret = corps.startswith("- ")
        if tiret:
            corps = corps[2:].lstrip()
            indent += 2
        while pile and pile[-1][0] > indent:
            pile.pop()
        if tiret and pile and pile[-1][0] == indent:
            pile.pop()        # nouvel élément de liste = mapping neuf
        m = re.match(r"([A-Za-z_][\w\-.]*)\s*:(\s|$)", corps)
        if not m:
            continue
        cle = m.group(1)
        valeur = corps[m.end():].strip()
        if not pile or pile[-1][0] < indent:
            pile.append((indent, {}))
        vues = pile[-1][1]
        if cle in vues:
            doublons.append((n, cle, vues[cle]))
        else:
            vues[cle] = n
        if valeur.startswith("|") or valeur.startswith(">"):
            bloc_scalaire = indent
    return doublons


def _charger(brut):
    """Mapping du workflow, ou None si PyYAML est absent."""
    return None if yaml is None else yaml.safe_load(brut)


class TestClesDupliquees(unittest.TestCase):
    """LE test de l'incident. Il tourne TOUJOURS, dépendance ou pas."""

    def test_au_moins_un_workflow_trouve(self):
        """Un test qui ne teste rien passerait au vert en silence."""
        self.assertTrue(workflows(), "aucun workflow trouvé")

    def test_aucune_cle_dupliquee(self):
        for chemin in workflows():
            with self.subTest(chemin):
                with open(chemin, encoding="utf-8") as f:
                    doublons = cles_dupliquees(f.read())
                self.assertEqual(
                    doublons, [],
                    "{} : clé dupliquée {}".format(chemin, doublons))

    def test_le_detecteur_voit_l_incident_reel(self):
        """Reproduction du cas du 26/08 : deux `DATABASE_URL` dans un `env:`."""
        casse = ("jobs:\n  radar:\n    steps:\n      - name: X\n"
                 "        env:\n          A: 1\n          A: 2\n")
        self.assertEqual([d[1] for d in cles_dupliquees(casse)], ["A"])

    def test_pas_de_faux_positif_sur_les_listes(self):
        """Deux étapes portent toutes deux `name:` : ce sont deux mappings
        différents, pas un doublon."""
        ok = ("jobs:\n  j:\n    steps:\n      - name: A\n        run: x\n"
              "      - name: B\n        run: y\n")
        self.assertEqual(cles_dupliquees(ok), [])

    def test_pas_de_faux_positif_sur_les_blocs_litteraux(self):
        """Le contenu d'un `run: |` est du shell, pas du YAML : un `echo a:`
        répété n'y est pas une clé."""
        ok = ("jobs:\n  j:\n    steps:\n      - run: |\n"
              "          echo a: 1\n          echo a: 2\n        name: X\n")
        self.assertEqual(cles_dupliquees(ok), [])

    def test_meme_cle_a_des_niveaux_differents_est_licite(self):
        ok = "a:\n  env:\n    X: 1\nb:\n  env:\n    X: 2\n"
        self.assertEqual(cles_dupliquees(ok), [])

    def test_commentaires_ignores(self):
        self.assertEqual(cles_dupliquees("env:\n  # A: 1\n  A: 2\n"), [])

    @unittest.skipIf(yaml is None, "PyYAML absent")
    def test_accord_avec_un_chargeur_yaml_strict(self):
        """Le détecteur maison doit dire la même chose qu'un YAML strict."""

        class Strict(yaml.SafeLoader):
            pass

        def _map(loader, node, deep=False):
            vus = set()
            for k, _ in node.value:
                cle = loader.construct_object(k, deep=deep)
                if cle in vus:
                    raise yaml.YAMLError("dupliquée")
                vus.add(cle)
            return yaml.SafeLoader.construct_mapping(loader, node, deep)

        Strict.construct_mapping = _map
        for chemin in workflows():
            with self.subTest(chemin):
                with open(chemin, encoding="utf-8") as f:
                    brut = f.read()
                try:
                    yaml.load(brut, Loader=Strict)
                    ref = False
                except yaml.YAMLError:
                    ref = True
                self.assertEqual(bool(cles_dupliquees(brut)), ref, chemin)


@unittest.skipIf(yaml is None, "PyYAML absent : contrôles structurels sautés")
class TestStructure(unittest.TestCase):
    """Contrôles de confort : utiles, mais aucun ne doit bloquer la CI si
    PyYAML n'est pas installé."""

    def test_chaque_workflow_a_un_nom(self):
        for chemin in workflows():
            with self.subTest(chemin):
                with open(chemin, encoding="utf-8") as f:
                    self.assertTrue(_charger(f.read()).get("name"), chemin)

    def test_chaque_workflow_a_un_declencheur(self):
        """`on` est lu par PyYAML comme le booléen True (YAML 1.1) : il faut
        chercher les deux clés, sinon le test passe à côté."""
        for chemin in workflows():
            with self.subTest(chemin):
                with open(chemin, encoding="utf-8") as f:
                    d = _charger(f.read())
                self.assertTrue(d.get("on") or d.get(True), chemin)

    def test_chaque_etape_a_un_run_ou_un_uses(self):
        """Une étape sans action est ignorée en silence par GitHub."""
        for chemin in workflows():
            with open(chemin, encoding="utf-8") as f:
                d = _charger(f.read())
            for nom_job, job in (d.get("jobs") or {}).items():
                for i, etape in enumerate(job.get("steps") or []):
                    with self.subTest("{}:{}:{}".format(chemin, nom_job, i)):
                        self.assertTrue(etape.get("run") or etape.get("uses"),
                                        etape.get("name", "étape %d" % i))

    def test_le_radar_est_lancable_a_la_main(self):
        """Sans `workflow_dispatch`, plus de bouton « Run workflow » : on ne
        peut plus valider une modification sans attendre le cron."""
        brut = lire_workflow("radar.yml")
        if brut is None:
            self.skipTest("radar.yml introuvable")
        d = _charger(brut)
        self.assertIn("workflow_dispatch", d.get("on") or d.get(True) or {})

    def test_le_backfill_est_manuel_et_jamais_planifie(self):
        """Un back-fill rejoué automatiquement serait du bruit : il n'y a rien
        à corriger une fois la correction passée."""
        brut = lire_workflow("backfill_date.yml")
        if brut is None:
            self.skipTest("backfill_date.yml introuvable")
        d = _charger(brut)
        decl = d.get("on") or d.get(True) or {}
        self.assertIn("workflow_dispatch", decl)
        self.assertNotIn("schedule", decl)


class TestSecretsDansLesWorkflows(unittest.TestCase):
    """Rappel de P0.1 : une étape qui PUBLIE ne doit pas porter le jeton de
    suivi. Contrôle textuel, donc sans dépendance."""

    def test_l_etape_de_generation_ne_porte_pas_le_jeton(self):
        brut = lire_workflow("radar.yml")
        if brut is None:
            self.skipTest("radar.yml introuvable")
        bloc = brut.split("Generer le tableau de bord")[-1]
        bloc = bloc.split("run: python radar_dashboard.py")[0]
        self.assertNotIn("SUIVI_TOKEN:", bloc)
        self.assertNotIn("SUIVI_WEBAPP_URL:", bloc)

    def test_le_digest_garde_le_jeton(self):
        """Lui s'exécute côté serveur et ne publie rien."""
        brut = lire_workflow("radar.yml")
        if brut is None:
            self.skipTest("radar.yml introuvable")
        bloc = brut.split("Envoyer le digest hebdo")[-1]
        bloc = bloc.split("run: python radar_digest.py")[0]
        self.assertIn("SUIVI_TOKEN:", bloc)


if __name__ == "__main__":
    unittest.main()
