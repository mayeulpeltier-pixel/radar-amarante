# -*- coding: utf-8 -*-
"""P0.1 — Aucun secret dans une page publiee (26/08/2026).

LE DEFAUT CORRIGE
-----------------
`SUIVI_TOKEN` etait passe en environnement a l'etape de generation du workflow,
injecte tel quel comme constante JS dans `public/index.html` et
`public/legacy.html`, puis ces deux fichiers etaient deployes sur Cloudflare
Pages.

    radar.yml:394         SUIVI_TOKEN: ${{ secrets.SUIVI_TOKEN }}
    radar_cockpit.py      .replace("__SUIVI_TOKEN__", json.dumps(...))
    radar_cockpit.py      const SUIVI_URL=..., SUIVI_TOKEN=..., API_STATUT=...;
    radar.yml:420         command: pages deploy public --project-name=...

Le probleme ne dependait PAS de savoir si le site est protege : le jeton se
retrouvait dans un artefact de build, dans l'historique des deploiements Pages,
et dans le cache de tout navigateur ayant ouvert la page une fois. Il donne un
acces en ECRITURE au webapp Apps Script, donc au Sheet.

DEUX LIGNES DE DEFENSE, testees separement
------------------------------------------
1. `assainir_suivi` : une page STATIQUE (api=False) ne recoit ni URL ni jeton.
   Elle est non authentifiee, donc en LECTURE SEULE. Les boutons d'action
   restent sur Render (api=True), derriere authentification.
2. `verifier_absence_secret` : avant d'ecrire un fichier destine a la
   publication, on relit le HTML et on refuse d'ecrire si un secret non vide
   s'y trouve. C'est le filet qui rattrape une regression de la ligne 1, par
   exemple quelqu'un qui recable la variable dans le workflow.

Les deux sont independantes A DESSEIN : la premiere peut etre contournee par
une erreur de configuration, la seconde non.

Tests OFFLINE : fonctions pures et generation HTML, aucun reseau.
"""

import os
import unittest

import radar_cockpit as ck
import radar_dashboard as dash


JETON = "jeton-secret-de-test-ABCD1234"
URL = "https://script.google.com/macros/s/TESTTESTTEST/exec"


def _lead(**kw):
    base = {
        "src": "TED", "pays": "Mali", "zone": "Sahel", "titre": "Escorte",
        "agence": "PNUD", "final": 8.0, "surete": 8.0, "comm": 7.0,
        "action": "contacter", "win": "", "nom": "n.c.", "email": "n.c.",
        "tel": "n.c.", "cible": "", "justif": "", "grp": "AT", "lien": "",
        "ecart": False, "secu": False, "mois": "2026-08", "mois_label": "a",
        "date_det": "2026-08-24", "statut": "nouveau", "motif_ecart": "",
        "deadline": "", "conf": "", "modele": "", "pub": "P1",
        "projet_id": "", "valeur": "", "enveloppe": "", "entreprise": "",
        "sect": "Autre"}
    base.update(kw)
    return base


class TestPageStatiqueSansJeton(unittest.TestCase):
    """Ligne de defense 1."""

    def setUp(self):
        dash.SUIVI_STATIQUE = False

    def test_page_statique_ne_porte_ni_jeton_ni_url(self):
        """LE test central. S'il tombe, un secret part sur Cloudflare."""
        html = ck.generer_cockpit(
            [_lead()], suivi={"url": URL, "token": JETON, "api": False})
        self.assertNotIn(JETON, html)
        self.assertNotIn(URL, html)

    def test_page_statique_annonce_la_lecture_seule(self):
        """Le bouton ne doit pas disparaitre en silence : sans URL ni API,
        `SUIVI_ON` est faux et le tiroir affiche l'explication."""
        html = ck.generer_cockpit(
            [_lead()], suivi={"url": URL, "token": JETON, "api": False})
        self.assertIn('const SUIVI_URL="", SUIVI_TOKEN="", API_STATUT=false;',
                      html)
        self.assertIn("lecture seule", html)

    def test_page_render_conserve_le_jeton(self):
        """Retrocompatibilite : sur Render la page est authentifiee, le jeton
        ne sort pas d'une session identifiee. Le vider ici couperait
        silencieusement l'ecriture Apps Script, ce qui est le chantier P0.2 et
        pas celui-ci."""
        html = ck.generer_cockpit(
            [_lead()], suivi={"url": URL, "token": JETON, "api": True})
        self.assertIn(JETON, html)

    def test_fonction_pure_assainir_suivi(self):
        self.assertEqual(dash.assainir_suivi(URL, JETON, False), ("", ""))
        self.assertEqual(dash.assainir_suivi(URL, JETON, True), (URL, JETON))

    def test_flag_de_diagnostic_restaure_l_ancien_comportement(self):
        """RADAR_SUIVI_STATIQUE=1 existe pour un diagnostic local. Le test
        documente qu'il REMET le jeton : il ne doit jamais etre pose dans un
        workflow qui publie."""
        dash.SUIVI_STATIQUE = True
        self.assertEqual(dash.assainir_suivi(URL, JETON, False), (URL, JETON))


class TestGardeFouAvantEcriture(unittest.TestCase):
    """Ligne de defense 2, independante de la premiere."""

    def setUp(self):
        self._sauv = {k: os.environ.get(k) for k in ("SUIVI_TOKEN",)}
        os.environ["SUIVI_TOKEN"] = JETON

    def tearDown(self):
        for k, v in self._sauv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_refuse_une_page_contenant_un_secret(self):
        with self.assertRaises(RuntimeError):
            dash.verifier_absence_secret("<html>" + JETON + "</html>")

    def test_laisse_passer_une_page_propre(self):
        self.assertTrue(dash.verifier_absence_secret("<html>rien</html>"))

    def test_le_message_d_erreur_ne_republie_pas_le_secret(self):
        """Piege classique : signaler la fuite en recopiant la valeur dans les
        logs du run, qui sont eux aussi consultables."""
        try:
            dash.verifier_absence_secret("<html>" + JETON + "</html>")
            self.fail("aurait du lever")
        except RuntimeError as e:
            self.assertNotIn(JETON, str(e))
            self.assertIn("SUIVI_TOKEN", str(e))

    def test_valeur_trop_courte_ignoree(self):
        """Un jeton de test d'un caractere declencherait un faux positif sur
        n'importe quelle page et rendrait le garde-fou inutilisable."""
        os.environ["SUIVI_TOKEN"] = "x"
        self.assertTrue(dash.verifier_absence_secret("<html>x x x</html>"))

    def test_variable_absente_sans_effet(self):
        os.environ.pop("SUIVI_TOKEN", None)
        self.assertTrue(dash.verifier_absence_secret("<html>rien</html>"))

    def test_couvre_les_autres_secrets_du_workflow(self):
        """Le garde-fou n'est pas specifique au suivi : cle LLM, base, quotas."""
        for nom in ("ANTHROPIC_API_KEY", "DATABASE_URL", "HUNTER_API_KEY"):
            os.environ[nom] = "valeur-secrete-" + nom
            try:
                with self.assertRaises(RuntimeError, msg=nom):
                    dash.verifier_absence_secret("x" + os.environ[nom] + "y")
            finally:
                os.environ.pop(nom, None)


class TestChaineComplete(unittest.TestCase):
    """Ce que ferait reellement le workflow : generer puis verifier."""

    def setUp(self):
        dash.SUIVI_STATIQUE = False
        os.environ["SUIVI_TOKEN"] = JETON
        os.environ["SUIVI_WEBAPP_URL"] = URL

    def tearDown(self):
        os.environ.pop("SUIVI_TOKEN", None)
        os.environ.pop("SUIVI_WEBAPP_URL", None)

    def test_generation_statique_puis_verification_passe(self):
        html = ck.generer_cockpit(
            [_lead()], suivi={"url": URL, "token": JETON, "api": False})
        self.assertTrue(dash.verifier_absence_secret(html))

    def test_si_la_ligne_1_est_contournee_la_ligne_2_bloque(self):
        """Simule la regression : quelqu'un repose RADAR_SUIVI_STATIQUE=1 dans
        le workflow de publication. Le garde-fou doit rattraper."""
        dash.SUIVI_STATIQUE = True
        html = ck.generer_cockpit(
            [_lead()], suivi={"url": URL, "token": JETON, "api": False})
        self.assertIn(JETON, html)
        with self.assertRaises(RuntimeError):
            dash.verifier_absence_secret(html)


class TestNonRegression(unittest.TestCase):

    def setUp(self):
        dash.SUIVI_STATIQUE = False

    def test_page_sans_suivi_du_tout_inchangee(self):
        html = ck.generer_cockpit([_lead()])
        self.assertIn('const SUIVI_URL="", SUIVI_TOKEN="", API_STATUT=false;',
                      html)

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit([_lead()])
        for attendu in ("santeRun", "function badgeDeadline",
                        "function celluleScore", "const opps=",
                        "function postureNote"):
            self.assertIn(attendu, html)


if __name__ == "__main__":
    unittest.main()
