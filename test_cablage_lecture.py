# -*- coding: utf-8 -*-
"""
Cablage de LECTURE -- contrat entre les lecteurs de sources et leurs consommateurs.
===================================================================================

POURQUOI CE FICHIER (02/08/2026)
--------------------------------
Deux regressions SILENCIEUSES, meme cause :

  1. `radar_dashboard.lire_onglets` a grossi (10 -> 13 -> 15 valeurs de retour au
     fil des sources ajoutees : UNGM, analyses d'attributions, alertes, MIGA,
     IFC). `radar_digest._charger_leads` en deballait toujours 10. A chaque run,
     le digest levait `ValueError: too many values to unpack`, attrape par un
     `except` qui affichait "lecture du Sheet impossible" (message trompeur) et
     sortait en code 0. L'etape GitHub restait VERTE, aucun e-mail n'etait
     envoye. Le seul canal PUSH vers l'equipe commerciale etait mort sans que
     rien ne l'indique.

  2. `radar_app.lire_onglets_pg` (surface applicative reelle, sur Render) ne
     lisait NI `miga_radar` NI `ifc_radar` et ne les passait pas a
     `construire_leads`. Deux collecteurs Vague 2 valides ecrivaient en base et
     n'apparaissaient JAMAIS dans l'application (le motif "orphelin ReliefWeb").

La cause commune : le nombre de sources lues est un CONTRAT implicite entre le
lecteur (lire_onglets / lire_onglets_pg) et ses consommateurs, et aucun test ne
le verrouillait. Ce fichier le rend explicite :

  - CONTRAT D'ARITE (structurel, via AST) : tout unpack d'un appel a
    lire_onglets / lire_onglets_pg doit deballer EXACTEMENT autant de valeurs que
    la fonction en renvoie. Survit a la croissance : ajouter une source au retour
    sans mettre a jour un consommateur fait echouer le test tant que les deux ne
    sont pas alignes.
  - VISIBILITE FONCTIONNELLE : un lead MIGA et un lead IFC presents en base
    ressortent bien jusqu'aux leads passes au rendu, cote application ET cote
    dashboard statique.
"""

import ast
import inspect
import os
import unittest


ICI = os.path.dirname(os.path.abspath(__file__))


def _arbre(nom_fichier):
    with open(os.path.join(ICI, nom_fichier), encoding="utf-8") as f:
        return ast.parse(f.read(), filename=nom_fichier)


def _fonction(arbre, nom):
    for node in ast.walk(arbre):
        if isinstance(node, ast.FunctionDef) and node.name == nom:
            return node
    return None


def _arite_retour_tuple(func_node):
    """Nombre d'elements du plus grand `return (...)` tuple de la fonction.
    C'est l'arite que promet la fonction a ses appelants."""
    arite = None
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            n = len(node.value.elts)
            if arite is None or n > arite:
                arite = n
    return arite


def _appel_vers(node, nom_appelee):
    """True si `node` est un appel a une fonction nommee `nom_appelee`, que
    l'appel soit direct (lire_onglets(...)) ou par attribut (dash.lire_onglets(...))."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id == nom_appelee
    if isinstance(f, ast.Attribute):
        return f.attr == nom_appelee
    return False


def _sites_unpack(arbre, nom_appelee):
    """Liste des arites de deballage du resultat de `nom_appelee`, dans l'arbre.

    Couvre les DEUX formes rencontrees dans le code :
      - directe :  `(a, b, ...) = lire_onglets(...)`
      - aliasee :  `x = lire_onglets(...)` puis `(a, b, ...) = x`
    La forme aliasee est celle de `charger_leads` (qui garde le tuple brut pour
    le renvoyer). Sans la suivre, on manquerait le seul point d'unpack reel.

    L'alias n'est suivi qu'a l'interieur d'UNE meme fonction (portee locale) :
    suffisant ici, et sans faux positifs entre fonctions."""
    arites = []

    def _dans_portee(corps):
        # Noms locaux lies a un appel a nom_appelee : `x = lire_onglets(...)`.
        alias = set()
        for node in ast.walk(ast.Module(body=list(corps), type_ignores=[])):
            if isinstance(node, ast.Assign) and _appel_vers(node.value, nom_appelee):
                for cible in node.targets:
                    if isinstance(cible, ast.Name):
                        alias.add(cible.id)
        for node in ast.walk(ast.Module(body=list(corps), type_ignores=[])):
            if not isinstance(node, ast.Assign):
                continue
            rhs = node.value
            direct = _appel_vers(rhs, nom_appelee)
            aliase = isinstance(rhs, ast.Name) and rhs.id in alias
            if direct or aliase:
                for cible in node.targets:
                    if isinstance(cible, (ast.Tuple, ast.List)):
                        arites.append(len(cible.elts))

    for node in ast.walk(arbre):
        if isinstance(node, ast.FunctionDef):
            _dans_portee(node.body)
    return arites


# ===========================================================================
# 1. CONTRAT D'ARITE -- chemin Sheet (dashboard + digest)
# ===========================================================================

class TestContratAriteSheet(unittest.TestCase):
    def test_tout_unpack_de_lire_onglets_respecte_l_arite(self):
        """Chaque `(...) = lire_onglets(...)` doit deballer autant de valeurs que
        `lire_onglets` en renvoie. Attrape la regression du digest (10 vs 15)."""
        dash = _arbre("radar_dashboard.py")
        attendue = _arite_retour_tuple(_fonction(dash, "lire_onglets"))
        self.assertIsNotNone(attendue, "lire_onglets doit renvoyer un tuple explicite.")

        sites = []
        for fichier in ("radar_dashboard.py", "radar_digest.py"):
            sites += _sites_unpack(_arbre(fichier), "lire_onglets")

        self.assertTrue(sites, "Aucun consommateur de lire_onglets trouve : contrat vide.")
        for arite in sites:
            self.assertEqual(
                arite, attendue,
                "Un consommateur deballe {} valeurs alors que lire_onglets en "
                "renvoie {}. Aligner l'unpack (cause des runs 'lecture du Sheet "
                "impossible' du digest).".format(arite, attendue))


# ===========================================================================
# 2. CONTRAT D'ARITE -- chemin Postgres (application)
# ===========================================================================

class TestContratAritePostgres(unittest.TestCase):
    def test_tout_unpack_de_lire_onglets_pg_respecte_l_arite(self):
        """Chaque `(...) = lire_onglets_pg(...)` doit deballer autant de valeurs
        que la fonction en renvoie. Verrouille la croissance cote app."""
        app = _arbre("radar_app.py")
        attendue = _arite_retour_tuple(_fonction(app, "lire_onglets_pg"))
        self.assertIsNotNone(attendue, "lire_onglets_pg doit renvoyer un tuple explicite.")

        sites = _sites_unpack(app, "lire_onglets_pg")
        self.assertTrue(sites, "Aucun consommateur de lire_onglets_pg trouve.")
        for arite in sites:
            self.assertEqual(
                arite, attendue,
                "Un consommateur deballe {} valeurs alors que lire_onglets_pg en "
                "renvoie {}.".format(arite, attendue))


# ===========================================================================
# 3. VISIBILITE FONCTIONNELLE -- digest : les leads se chargent sans planter
# ===========================================================================

class TestDigestChargeLesLeads(unittest.TestCase):
    def test_charger_leads_ne_plante_pas_sur_le_tuple_complet(self):
        """Simule un Sheet lu (tuple de retour COMPLET de lire_onglets) et verifie
        que le digest en tire une liste de leads sans ValueError. Sur l'ancien
        code (unpack de 10), ceci levait `too many values to unpack`."""
        import radar_dashboard as dash
        import radar_digest as digest

        # Nombre exact de valeurs que renvoie lire_onglets, mesure sur la source :
        # le stub doit coller au contrat reel pour que le test reste juste si
        # l'arite evolue.
        arite = _arite_retour_tuple(_fonction(_arbre("radar_dashboard.py"), "lire_onglets"))
        # enrichissement est un dict (position 5), le reste des listes ; peu
        # importe pour l'unpack, on renvoie des conteneurs vides typables.
        faux_retour = tuple([] for _ in range(arite))

        original = dash.lire_onglets
        try:
            dash.lire_onglets = lambda *a, **k: faux_retour
            leads = digest._charger_leads("faux_sheet_id", "faux_cs")
        finally:
            dash.lire_onglets = original

        self.assertIsInstance(leads, list)
        self.assertEqual(leads, [], "Onglets vides -> aucun lead attendu.")


# ===========================================================================
# 4. VISIBILITE FONCTIONNELLE -- MIGA & IFC ressortent (app + dashboard)
# ===========================================================================

def _ligne_avis(source_tag, pays, pub):
    """Ligne de collecte plate minimale mais valide (titre non vide -> le lead
    n'est pas filtre par construire_leads)."""
    return {
        "date_maj": "2026-08-01",
        "score_final": "7.0", "score_surete": "6.5", "score_commercial": "7.5",
        "action_recommandee": "contacter", "fenetre_action": "court_terme",
        "titre": "Projet {} en zone a risque".format(source_tag),
        "acheteur": "Entreprise projet {}".format(source_tag),
        "pays_execution": pays,
        "type_document": "SPI",
        "justification": "deploiement cadres et actifs",
        "confiance": "0.8", "modele": "test",
        "publication_number": pub,
        "lien_avis": "https://example.org/{}".format(pub),
        "date_detection": "2026-08-01",
    }


class TestMigaIfcVisiblesDashboardStatique(unittest.TestCase):
    def test_miga_et_ifc_ressortent_via_charger_leads(self):
        """Chemin Sheet : un lead MIGA et un lead IFC en base ressortent bien
        dans les leads construits (aucune source Vague 2 orpheline)."""
        import radar_dashboard as dash

        arite = _arite_retour_tuple(_fonction(_arbre("radar_dashboard.py"), "lire_onglets"))
        # Reconstruit un faux retour nomme, positions MIGA/IFC = les 2 dernieres
        # (ordre du retour reel de lire_onglets). On lit l'ordre depuis la source
        # pour ne pas coder en dur des positions qui pourraient bouger.
        noms = _noms_retour(_fonction(_arbre("radar_dashboard.py"), "lire_onglets"))
        self.assertEqual(len(noms), arite)
        gabarit = {n: {} if n == "enrichissement" else [] for n in noms}
        gabarit["lignes_miga"] = [_ligne_avis("MIGA", "Mozambique", "MIGA:1")]
        gabarit["lignes_ifc"] = [_ligne_avis("IFC", "Nigeria", "IFC:1")]
        faux_retour = tuple(gabarit[n] for n in noms)

        original = dash.lire_onglets
        try:
            dash.lire_onglets = lambda *a, **k: faux_retour
            leads, _onglets = dash.charger_leads("faux", "faux")
        finally:
            dash.lire_onglets = original

        srcs = {l["src"] for l in leads}
        self.assertIn("MIGA", srcs, "Le lead MIGA doit ressortir dans les leads.")
        self.assertIn("IFC", srcs, "Le lead IFC doit ressortir dans les leads.")


def _noms_retour(func_node):
    """Noms des variables du `return (...)` d'une fonction, dans l'ordre.
    Utilise pour poser des donnees sur des positions nommees sans les coder en dur."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            noms = []
            for elt in node.value.elts:
                noms.append(elt.id if isinstance(elt, ast.Name) else "?")
            return noms
    return []


try:
    import fastapi  # noqa: F401
    import radar_app  # noqa: F401
    _APP_DISPONIBLE = True
except Exception:
    _APP_DISPONIBLE = False


@unittest.skipUnless(_APP_DISPONIBLE, "fastapi / radar_app absents en local")
class TestMigaIfcVisiblesApplication(unittest.TestCase):
    def test_generer_page_transmet_miga_et_ifc_au_rendu(self):
        """Chemin Postgres (application reelle) : un lead MIGA et un lead IFC en
        base doivent arriver jusqu'aux leads passes au rendu HTML. Sur l'ancien
        code, lire_onglets_pg ne lisait pas ces onglets -> leads absents."""
        import radar_app
        import radar_dashboard as dash

        lignes_par_onglet = {
            "miga_radar": [_ligne_avis("MIGA", "Mozambique", "MIGA:1")],
            "ifc_radar": [_ligne_avis("IFC", "Nigeria", "IFC:1")],
        }

        capture = {}

        orig_onglet = radar_app.st.lire_onglet
        orig_statuts = radar_app.st.lire_statuts
        orig_html = dash.generer_html
        try:
            radar_app.st.lire_onglet = lambda conn, nom: list(lignes_par_onglet.get(nom, []))
            radar_app.st.lire_statuts = lambda conn: {}
            dash.generer_html = lambda leads, *a, **k: capture.setdefault("leads", leads) or ""
            radar_app.generer_page(object())   # conn factice : jamais touche
        finally:
            radar_app.st.lire_onglet = orig_onglet
            radar_app.st.lire_statuts = orig_statuts
            dash.generer_html = orig_html

        srcs = {l["src"] for l in capture.get("leads", [])}
        self.assertIn("MIGA", srcs, "L'application doit exposer les leads MIGA.")
        self.assertIn("IFC", srcs, "L'application doit exposer les leads IFC.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
