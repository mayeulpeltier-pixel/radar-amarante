# -*- coding: utf-8 -*-
"""P0.2 — Fin des ecritures de statut silencieuses (26/08/2026).

TROIS DEFAUTS CORRIGES, dont un tres couteux
--------------------------------------------
1. DEUX ECRITURES CONCURRENTES. Le navigateur postait vers l'Apps Script ET
   vers /api/statut. Deux destinations, aucune transaction : le Sheet et
   Postgres pouvaient diverger sans que personne le sache.

2. ECHEC SILENCIEUX. Le POST Apps Script partait en `mode:"no-cors"`, ce qui
   rend la reponse ILLISIBLE par construction, et les deux appels finissaient
   par `.catch(function(){})`. Le statut etait pose sur l'objet AVANT tout
   appel et le toast de succes s'affichait quoi qu'il arrive.

3. LE PLUS COUTEUX : le payload navigateur ne portait pas de `titre`, alors
   que le script Apps Script refuse tout envoi sans titre
   (`if (!id || !d.titre) return 'missing_fields'`). Le bouton « A contacter »
   n'a donc JAMAIS rien ecrit dans le Sheet depuis le cockpit. Le `no-cors`
   masquait le refus, et l'interface affichait un succes a chaque clic.

NOUVEAU CONTRAT
---------------
/api/statut (Postgres) est la SEULE ecriture faite par le navigateur. Elle est
attendue, sa reponse est lue, l'affichage est annule si elle echoue. La
replication vers le Sheet part du SERVEUR, ou la reponse est lisible, ou le
refus est compte, et ou /sante l'expose.

Tests OFFLINE : contrats du gabarit + fonction pure de payload. Les tests
serveur se sautent proprement si fastapi n'est pas installe.
"""

import re
import unittest

import radar_cockpit as ck

try:
    import radar_app
    PRET = True
except Exception:                       # fastapi absent en local
    PRET = False


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


class TestUneSeuleEcritureCoteNavigateur(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit([_lead()], suivi={"api": True})

    def test_le_navigateur_ne_poste_plus_vers_apps_script(self):
        """LE test central du chantier. S'il tombe, la double ecriture est
        revenue et les deux verites peuvent diverger a nouveau."""
        self.assertNotIn('mode:"no-cors"', self.html)
        self.assertNotIn("fetch(SUIVI_URL", self.html)

    def test_plus_aucune_erreur_avalee_sur_l_ecriture(self):
        """`.catch(function(){})` etait le mecanisme exact du silence."""
        self.assertNotIn('.catch(function(){});}', self.html)

    def test_la_reponse_est_attendue_et_lue(self):
        self.assertIn("async function envoyerStatut", self.html)
        self.assertIn("const r=await fetch(\"/api/statut\"", self.html)
        self.assertIn('if(!r.ok)throw new Error("HTTP "+r.status);', self.html)
        self.assertIn("if(!j||j.ok!==true)throw", self.html)

    def test_retour_arriere_si_l_ecriture_echoue(self):
        """Ne jamais laisser l'ecran affirmer ce que la base ignore."""
        self.assertIn("const avant=l.statut;", self.html)
        self.assertIn("l.statut=avant;go(state.view);", self.html)
        self.assertIn("Action NON sauvegardée", self.html)

    def test_le_toast_distingue_l_echec_du_succes(self):
        """Un echec affiche en gris deux secondes passe pour un succes."""
        self.assertIn("function toast(msg,erreur)", self.html)
        self.assertIn('erreur?"var(--red)":"var(--ink)"', self.html)
        self.assertIn("erreur?5200:2200", self.html)

    def test_page_lecture_seule_le_dit_au_lieu_de_faire_semblant(self):
        self.assertIn("Page en lecture seule : action non enregistrée.",
                      self.html)

    def test_le_titre_manquant_est_desormais_transmis(self):
        """Defaut n°3 : sans `titre`, le script repondait `missing_fields`."""
        self.assertIn("function contexteLead", self.html)
        self.assertIn("titre:l.titre||\"\"", self.html)
        self.assertIn("contexte:contexteLead(l)", self.html)

    def test_surveillance_locale_confirmee_avant_d_etre_gardee(self):
        """Le marqueur localStorage etait pose AVANT le reseau, sans retour
        arriere : un lead pouvait paraitre surveille et ne l'etre nulle part."""
        self.assertIn("async function surveiller(id)", self.html)
        self.assertIn("const ok=await envoyerStatut(l,\"surveille\",\"\");",
                      self.html)
        self.assertIn("if(!ok&&!avant){SURV.delete(cle);majSurv();", self.html)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])


@unittest.skipUnless(PRET, "fastapi absent")
class TestPayloadReplication(unittest.TestCase):
    """Fonction PURE : construit le corps attendu par l'Apps Script."""

    def _s(self, **kw):
        base = dict(onglet="ted_radar", publication_number="OP-1",
                    statut="contacte", motif="", contexte={})
        base.update(kw)
        return radar_app.Statut(**base)

    def test_refuse_de_partir_sans_titre(self):
        """C'est tout l'objet du chantier : ne plus envoyer un appel voue a
        `missing_fields` en esperant que personne ne regarde."""
        self.assertIsNone(radar_app._payload_apps_script(
            self._s(contexte={"id": "x"}), "tk"))

    def test_refuse_de_partir_sans_identifiant(self):
        self.assertIsNone(radar_app._payload_apps_script(
            self._s(publication_number="", contexte={"titre": "T"}), "tk"))

    def test_payload_complet(self):
        p = radar_app._payload_apps_script(
            self._s(contexte={"id": "TED-1", "titre": "Escorte convois",
                              "pays": "Mali", "zone": "Sahel"}), "tk")
        self.assertEqual(p["token"], "tk")
        self.assertEqual(p["id"], "TED-1")
        self.assertEqual(p["titre"], "Escorte convois")
        self.assertEqual(p["statut"], "contacte")

    def test_repli_sur_publication_number_comme_identifiant(self):
        p = radar_app._payload_apps_script(
            self._s(contexte={"titre": "T"}), "tk")
        self.assertEqual(p["id"], "OP-1")

    def test_le_contexte_ne_touche_pas_a_la_cle_d_ecriture(self):
        """Le contexte sert a la replication, jamais a l'ecriture en base."""
        s = self._s(contexte={"titre": "T", "publication_number": "AUTRE"})
        self.assertEqual(s.publication_number, "OP-1")


@unittest.skipUnless(PRET, "fastapi absent")
class TestReplicationJournalisee(unittest.TestCase):
    """La difference exacte avec `.catch(function(){})` : un echec se compte."""

    def setUp(self):
        radar_app.REPLICATION.update(tentees=0, ok=0, echecs=0,
                                     derniere_erreur="")

    def test_echec_compte_et_lisible(self):
        radar_app._noter_replication(False, "refus du script : missing_fields")
        self.assertEqual(radar_app.REPLICATION["echecs"], 1)
        self.assertIn("missing_fields", radar_app.REPLICATION["derniere_erreur"])

    def test_succes_compte(self):
        radar_app._noter_replication(True)
        self.assertEqual(radar_app.REPLICATION["ok"], 1)
        self.assertEqual(radar_app.REPLICATION["echecs"], 0)

    def test_sans_configuration_la_replication_ne_fait_rien(self):
        """Etat par defaut sur Render, et il est valable : Postgres suffit."""
        import os
        for k in ("SUIVI_WEBAPP_URL", "SUIVI_TOKEN"):
            os.environ.pop(k, None)
        s = radar_app.Statut(onglet="t", publication_number="P",
                             statut="contacte", contexte={"titre": "T"})
        self.assertFalse(radar_app.repliquer_vers_sheet(s))
        self.assertEqual(radar_app.REPLICATION["tentees"], 0)

    def test_erreur_tronquee_pour_ne_pas_gonfler_la_reponse(self):
        radar_app._noter_replication(False, "x" * 900)
        self.assertLessEqual(
            len(radar_app.REPLICATION["derniere_erreur"]), 200)


class TestNonRegression(unittest.TestCase):

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit([_lead()], suivi={"api": True})
        for attendu in ("santeRun", "function badgeDeadline",
                        "function celluleScore", "const opps=",
                        "function postureNote", "initAttribFiltres"):
            self.assertIn(attendu, html)

    def test_secret_toujours_absent_des_pages_statiques(self):
        """P0.1 ne doit pas avoir ete defait au passage."""
        html = ck.generer_cockpit(
            [_lead()], suivi={"url": "https://x/exec", "token": "TK",
                              "api": False})
        self.assertNotIn("TK", html)


if __name__ == "__main__":
    unittest.main()
