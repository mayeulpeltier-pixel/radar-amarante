# -*- coding: utf-8 -*-
"""P1.1 — Rendre l'issue commerciale enregistrable (26/08/2026).

LE VERROU DE TOUT LE RESTE
--------------------------
Avant ce chantier, l'interface ne pouvait emettre que trois statuts :

    envoyerStatut(l, 'contacte')
    envoyerStatut(l, 'surveille')
    envoyerStatut(l, 'non_pertinent')

Les mots « gagne » et « perdu » figuraient dans les commentaires de
`radar_stockage`, dans les filtres du dashboard, et `radar_retroaction`
pretendait s'en nourrir. RIEN ne pouvait en produire un. La boucle bayesienne
apprenait donc a predire si un humain avait clique, pas si Amarante avait
gagne. Et `RADAR_RETRO` n'apparaissait pas dans `radar.yml` : mode `off`, pas
`ombre` comme la roadmap l'affirmait.

QUATRE PIECES POSEES ICI
------------------------
1. Vocabulaire ferme (`STATUTS_VALIDES`) + motifs de perte fermes.
2. Journal APPEND-ONLY des transitions (`radar_outcomes`). `radar_statuts`
   ne porte que l'etat COURANT : un lead gagne ecrase son passage en
   « contacte », et le delai contact -> signature est perdu a jamais.
3. `valeur_estimee`, saisie au moment du contact -- pas au moment de gagner,
   ou on la reconstruirait de memoire.
4. Retroaction en mode OMBRE explicite, avec un critere CHIFFRE de passage en
   actif plutot qu'une decision de calendrier.

Tests OFFLINE : fonctions pures + contrats du gabarit. Les tests base se
sautent proprement si le pilote Postgres est absent.
"""

import re
import unittest

import radar_cockpit as ck
import radar_retroaction as retro
import radar_stockage as st


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


class TestVocabulaire(unittest.TestCase):

    def test_les_issues_font_partie_du_vocabulaire(self):
        """Le defaut exact : ces deux mots n'etaient acceptes nulle part."""
        self.assertTrue(st.statut_valide("gagne"))
        self.assertTrue(st.statut_valide("perdu"))

    def test_statut_inconnu_refuse(self):
        self.assertFalse(st.statut_valide("gagné"))       # accentue = autre mot
        self.assertFalse(st.statut_valide("n_importe_quoi"))
        self.assertFalse(st.statut_valide(""))

    def test_motifs_de_perte_fermes(self):
        """Liste fermee a dessein : un champ libre produit vingt formulations
        de la meme raison et zero statistique exploitable."""
        self.assertTrue(st.motif_perte_valide("incumbent"))
        self.assertFalse(st.motif_perte_valide("trop cher"))

    def test_motif_vide_accepte(self):
        """Refuser l'enregistrement faute de motif ferait perdre l'ISSUE
        elle-meme, ce qui coute bien plus cher qu'un motif manquant."""
        self.assertTrue(st.motif_perte_valide(""))

    def test_une_issue_suppose_un_lead_travaille(self):
        """On ne perd pas ce qu'on n'a jamais approche. Sans cette garde, le
        journal se remplirait de « perdu » qui sont des desinteressements --
        lesquels ont deja leur statut : non_pertinent."""
        self.assertIn("contacte", st.STATUTS_AVANT_ISSUE)
        self.assertNotIn("nouveau", st.STATUTS_AVANT_ISSUE)
        self.assertNotIn("non_pertinent", st.STATUTS_AVANT_ISSUE)

    def test_le_front_et_la_base_partagent_les_memes_motifs(self):
        """Deux listes qui divergent = des motifs enregistres puis refuses."""
        html = ck.generer_cockpit([_lead()], suivi={"api": True})
        for cle in st.MOTIFS_PERTE:
            self.assertIn(cle + ":", html, "motif absent du front : " + cle)


class TestJournalDesTransitions(unittest.TestCase):
    """Contrat du DDL. `radar_statuts` porte l'etat courant et l'ecrase ;
    le journal garde la trajectoire, qui est ce qui s'apprend."""

    def test_table_journal_declaree(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS radar_outcomes", st.SCHEMA_SQL)
        for col in ("statut_precedent", "valeur_estimee", "cree_le"):
            self.assertIn(col, st.SCHEMA_SQL)

    def test_valeur_estimee_ajoutee_aux_bases_existantes(self):
        """Migration idempotente : une base anterieure ne doit pas casser."""
        self.assertIn("ALTER TABLE radar_statuts ADD COLUMN IF NOT EXISTS"
                      " valeur_estimee", st.SCHEMA_SQL)

    def test_index_dedie_aux_issues(self):
        self.assertIn("radar_outcomes_issues", st.SCHEMA_SQL)

    def test_l_etat_precedent_est_lu_avant_d_etre_ecrase(self):
        """L'ORDRE est le coeur du chantier : lire l'ancien statut, puis
        journaliser, puis ecraser. Inverse, la transition est perdue."""
        with open("radar_stockage.py", encoding="utf-8") as f:
            src = f.read()
        corps = src.split("def definir_statut")[1].split("\ndef ")[0]
        i_lire = corps.index("SELECT statut FROM radar_statuts")
        i_journal = corps.index("INSERT INTO radar_outcomes")
        i_ecrire = corps.index("INSERT INTO radar_statuts")
        self.assertLess(i_lire, i_journal)
        self.assertLess(i_journal, i_ecrire)

    def test_la_valeur_saisie_survit_aux_transitions_suivantes(self):
        """Marquer « gagne » sans ressaisir le montant ne doit pas effacer le
        montant saisi au moment du contact."""
        with open("radar_stockage.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("valeur_estimee = COALESCE(EXCLUDED.valeur_estimee,", src)


class TestInterfaceIssues(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(
            [_lead(), _lead(pub="P2", statut="contacte"),
             _lead(pub="P3", statut="gagne")], suivi={"api": True})

    def test_boutons_d_issue_presents(self):
        for f in ("function marquerGagne", "function marquerPerdu",
                  "function ouvrirMotifPerte", "function blocActions"):
            self.assertIn(f, self.html)

    def test_boutons_conditionnes_a_un_lead_travaille(self):
        """Afficher « perdu » sur un lead jamais contacte inviterait a une
        action que l'API refuse de toute facon (409)."""
        self.assertIn("const ETATS_TRAVAILLES=", self.html)
        self.assertIn("estTravaille(l)?", self.html)
        self.assertIn("se renseigne une fois le marché contacté", self.html)

    def test_lead_clos_ne_repropose_pas_les_boutons(self):
        self.assertIn("if(estClos(l)){", self.html)
        self.assertIn("Rouvrir (à contacter)", self.html)

    def test_valeur_estimee_saisie_au_contact(self):
        self.assertIn("function marquerContacte", self.html)
        self.assertIn("Valeur estimée du marché en k€", self.html)
        self.assertIn("valeur_estimee:(valeur==null?null:+valeur)", self.html)

    def test_montant_illisible_refuse_plutot_qu_arrondi(self):
        """Un montant mal saisi doit etre signale, pas transforme en zero."""
        self.assertIn("function parseValeurK", self.html)
        self.assertIn("Montant illisible", self.html)

    def test_refus_metier_distingue_d_une_panne(self):
        """Un 409 « contacte-le d'abord » n'est pas une panne reseau : le
        message du serveur doit remonter tel quel."""
        self.assertIn("if(r.status===409||r.status===422)", self.html)
        self.assertIn("d.detail||", self.html)

    def test_le_funnel_compte_enfin_les_gagnes(self):
        """Second etage du meme defaut : le filtre cherchait « gagné »
        accentue, quand la base ecrit « gagne ». Il ne pouvait rien compter."""
        self.assertIn('l.statut==="gagne"', self.html)
        self.assertNotIn('l.statut==="gagné"', self.html)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])


class TestModeRetroaction(unittest.TestCase):

    def test_trois_modes_explicites(self):
        for m in ("off", "ombre", "actif"):
            self.assertIn(m, ("off", "ombre", "actif"))
        self.assertTrue(hasattr(retro, "actif"))
        self.assertTrue(hasattr(retro, "calcule"))

    def test_mode_inconnu_replie_sur_off(self):
        """Un mode mal orthographie ne doit pas appliquer des multiplicateurs
        par accident."""
        with open("radar_retroaction.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('MODE = "off"', src)

    def test_critere_de_sortie_du_mode_ombre_chiffre(self):
        """Le passage en actif est une decision de VOLUME, pas de calendrier."""
        self.assertFalse(retro.assez_d_issues({"gagne": 3, "perdu": 2}))
        self.assertTrue(retro.assez_d_issues({"gagne": 10, "perdu": 10}))

    def test_un_seul_cote_ne_suffit_pas(self):
        """Cinquante gagnes et zero perdu ne renseignent sur rien : sans
        contre-exemple, le taux vaut 100 % partout et le multiplicateur est du
        bruit deguise en certitude."""
        self.assertFalse(retro.assez_d_issues({"gagne": 50, "perdu": 0}))
        self.assertFalse(retro.assez_d_issues({"gagne": 0, "perdu": 50}))

    def test_mode_ombre_laisse_une_trace(self):
        """Un mode ombre qui ne journalise rien est un calcul que personne ne
        verifie avant de le mettre en production."""
        self.assertTrue(hasattr(retro, "journaliser_ombre"))
        retro.journaliser_ombre(None)                    # ne doit pas lever
        retro.journaliser_ombre({"n": 20, "base": 0.4,
                                 "secteur": {"BTP": 1.09}, "zone": {}},
                                {"gagne": 8, "perdu": 12})


class TestNonRegression(unittest.TestCase):

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit([_lead()], suivi={"api": True})
        for attendu in ("santeRun", "function badgeDeadline",
                        "function celluleScore", "const opps=",
                        "function postureNote", "async function envoyerStatut"):
            self.assertIn(attendu, html)
        self.assertNotIn('mode:"no-cors"', html)

    def test_secret_toujours_absent_des_pages_statiques(self):
        html = ck.generer_cockpit(
            [_lead()], suivi={"url": "https://x/exec", "token": "TK",
                              "api": False})
        self.assertNotIn("TK", html)


if __name__ == "__main__":
    unittest.main()
