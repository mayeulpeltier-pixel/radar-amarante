# -*- coding: utf-8 -*-
"""Validité des workflows GitHub Actions (26/08/2026).

L'INCIDENT
----------
Le 26/08, le workflow « Radar Amarante » a DISPARU de l'onglet Actions. Plus
de bouton « Run workflow », plus de déclenchement manuel possible.

Cause : en câblant P2.1, j'ai ajouté `DATABASE_URL` au bloc `env:` de l'étape
« Generer le tableau de bord »... qui en avait déjà un. Clé dupliquée dans un
mapping YAML.

POURQUOI MA VALIDATION N'A RIEN VU
----------------------------------
Je validais avec `yaml.safe_load`, qui accepte les clés dupliquées et garde
silencieusement la dernière. GitHub Actions, lui, REFUSE le fichier et retire
le workflow de la liste. Un fichier « valide » pour Python était donc invalide
en production, et la seule alerte était l'absence du workflow -- que personne
ne remarque avant d'en avoir besoin.

Une validation plus permissive que la production ne valide rien.

CE QUE CE FICHIER GARDE
-----------------------
Trois choses, sur tous les workflows du dépôt :
  - aucune clé dupliquée, à n'importe quel niveau ;
  - un `name` et un bloc `on` (sans quoi le workflow n'apparaît pas) ;
  - `workflow_dispatch` sur les workflows qu'on lance à la main.

Tests OFFLINE : lecture de fichiers, aucun réseau.
"""

import glob
import os
import unittest

import yaml


# ---------------------------------------------------------------------------
# LOCALISATION DES WORKFLOWS -- definition UNIQUE, importee par les autres
# fichiers de test qui lisent un .yml.
# ---------------------------------------------------------------------------
# INCIDENT DU 26/08/2026 : trois tests faisaient `open("radar.yml")`. Ils
# passaient en local parce que mon bac a sable avait une copie a la racine, et
# ils ont echoue en CI ou les workflows vivent dans `.github/workflows/`.
#
# Un test qui passe pour la mauvaise raison est pire qu'un test absent : il
# donne une confiance qu'il ne merite pas. On cherche donc le fichier la ou il
# peut REELLEMENT etre, et on saute proprement s'il n'y est pas.

DOSSIERS_WORKFLOWS = (".github/workflows", ".", "workflows")


def chemin_workflow(nom):
    """Chemin reel d'un workflow, ou None. Fonction PURE (hors acces disque)."""
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


class ChargeurStrict(yaml.SafeLoader):
    """SafeLoader qui REFUSE les clés dupliquées, comme GitHub Actions."""


def _mapping_strict(loader, node, deep=False):
    vus = set()
    for cle_node, _ in node.value:
        cle = loader.construct_object(cle_node, deep=deep)
        if cle in vus:
            raise yaml.YAMLError("clé dupliquée : {!r}".format(cle))
        vus.add(cle)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


ChargeurStrict.construct_mapping = _mapping_strict


def _workflows():
    """Tous les .yml du dépôt qui ressemblent à un workflow, où qu'ils soient."""
    trouves = []
    for dossier in DOSSIERS_WORKFLOWS:
        trouves.extend(sorted(glob.glob(os.path.join(dossier, "*.yml"))))
    out = []
    for chemin in trouves:
        try:
            with open(chemin, encoding="utf-8") as f:
                brut = f.read()
        except OSError:
            continue
        if "jobs:" in brut and ("runs-on" in brut or "uses:" in brut):
            out.append(chemin)
    return out


class TestWorkflows(unittest.TestCase):

    def test_au_moins_un_workflow_trouve(self):
        """Un test qui ne teste rien passerait au vert en silence."""
        self.assertTrue(_workflows(), "aucun workflow trouvé")

    def test_aucune_cle_dupliquee(self):
        """LE test de l'incident. `yaml.safe_load` laisse passer, GitHub non :
        la validation doit être aussi stricte que la production."""
        for chemin in _workflows():
            with self.subTest(chemin):
                with open(chemin, encoding="utf-8") as f:
                    try:
                        yaml.load(f, Loader=ChargeurStrict)
                    except yaml.YAMLError as e:
                        self.fail("{} : {}".format(chemin, e))

    def test_chaque_workflow_a_un_nom(self):
        """Sans `name`, GitHub affiche le chemin du fichier : lisible, mais on
        ne s'y retrouve plus dans la liste."""
        for chemin in _workflows():
            with self.subTest(chemin):
                with open(chemin, encoding="utf-8") as f:
                    d = yaml.load(f, Loader=ChargeurStrict)
                self.assertTrue(d.get("name"), chemin)

    def test_chaque_workflow_a_un_declencheur(self):
        """`on` est parsé par PyYAML comme le booléen True (YAML 1.1) : il
        faut chercher les deux clés, sinon le test passe à côté."""
        for chemin in _workflows():
            with self.subTest(chemin):
                with open(chemin, encoding="utf-8") as f:
                    d = yaml.load(f, Loader=ChargeurStrict)
                self.assertTrue(d.get("on") or d.get(True), chemin)

    def test_le_radar_est_lancable_a_la_main(self):
        """Sans `workflow_dispatch`, plus de bouton « Run workflow » : on ne
        peut plus valider une modification sans attendre le cron."""
        brut = lire_workflow("radar.yml")
        if brut is None:
            self.skipTest("radar.yml introuvable")
        d = yaml.load(brut, Loader=ChargeurStrict)
        decl = d.get("on") or d.get(True) or {}
        self.assertIn("workflow_dispatch", decl)

    def test_le_backfill_est_manuel_et_jamais_planifie(self):
        """Un back-fill rejoué automatiquement serait du bruit : il n'y a rien
        à corriger une fois la correction passée."""
        brut = lire_workflow("backfill_date.yml")
        if brut is None:
            self.skipTest("backfill_date.yml absent")
        d = yaml.load(brut, Loader=ChargeurStrict)
        decl = d.get("on") or d.get(True) or {}
        self.assertIn("workflow_dispatch", decl)
        self.assertNotIn("schedule", decl)

    def test_chaque_etape_a_un_run_ou_un_uses(self):
        """Une étape sans action est ignorée en silence par GitHub."""
        for chemin in _workflows():
            with open(chemin, encoding="utf-8") as f:
                d = yaml.load(f, Loader=ChargeurStrict)
            for nom_job, job in (d.get("jobs") or {}).items():
                for i, etape in enumerate(job.get("steps") or []):
                    with self.subTest("{}:{}:{}".format(chemin, nom_job, i)):
                        self.assertTrue(etape.get("run") or etape.get("uses"),
                                        etape.get("name", "étape %d" % i))


class TestSecretsDansLesWorkflows(unittest.TestCase):
    """Rappel de P0.1 : une étape qui PUBLIE ne doit pas porter le jeton de
    suivi."""

    def test_l_etape_de_generation_ne_porte_pas_le_jeton(self):
        brut = lire_workflow("radar.yml")
        if brut is None:
            self.skipTest("radar.yml introuvable")
        d = yaml.load(brut, Loader=ChargeurStrict)
        for job in (d.get("jobs") or {}).values():
            for etape in job.get("steps") or []:
                if "radar_dashboard.py" in str(etape.get("run", "")):
                    env = etape.get("env") or {}
                    self.assertNotIn("SUIVI_TOKEN", env)
                    self.assertNotIn("SUIVI_WEBAPP_URL", env)

    def test_le_digest_garde_le_jeton(self):
        """Lui s'exécute côté serveur et ne publie rien."""
        brut = lire_workflow("radar.yml")
        if brut is None:
            self.skipTest("radar.yml introuvable")
        d = yaml.load(brut, Loader=ChargeurStrict)
        trouve = False
        for job in (d.get("jobs") or {}).values():
            for etape in job.get("steps") or []:
                if "radar_digest.py" in str(etape.get("run", "")):
                    self.assertIn("SUIVI_TOKEN", etape.get("env") or {})
                    trouve = True
        self.assertTrue(trouve, "étape digest introuvable")


if __name__ == "__main__":
    unittest.main()
