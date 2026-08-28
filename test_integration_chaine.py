# -*- coding: utf-8 -*-
"""P3.3 — Tests d'INTÉGRATION bout en bout (26/08/2026).

POURQUOI CE FICHIER EXISTE
--------------------------
Les 2020 tests précédents sont tous UNITAIRES. Aucun ne vérifie qu'une ligne
de collecte traverse la chaîne jusqu'à l'écran. C'est un manque coûteux, et la
session du 26/08 en a fait la démonstration : quatre défauts réels ont été
trouvés en regardant le rendu, pas par les tests.

    - `enveloppe` en chaîne brute (« 180000000 EUR ») passée à float() :
      zéro opportunité, page normale, aucun signal ;
    - le même piège dans un second module deux heures plus tard ;
    - `cle_compte` exigeait `ent_cle` là où le JS retombe sur le nom :
      bloc « lecture commerciale » disparu en silence ;
    - `ONGLET_SRC` couvrait 4 sources sur 15 : statuts perdus sur 11 sources.

Ces quatre défauts ont un point commun : **ils vivent à la FRONTIÈRE entre
deux couches**, là où chaque couche est correcte isolément. Un test unitaire
ne peut pas les voir, par construction.

CE QUE CES TESTS VÉRIFIENT
--------------------------
La chaîne réelle, sans réseau ni base :

    ligne de collecte (vrais noms de colonnes)
      -> dash.ligne_vers_lead
        -> opportunites.construire
          -> radar_cockpit.generer_cockpit
            -> HTML servi au navigateur

Les cinq scénarios réclamés par l'audit, plus une classe consacrée aux
frontières entre couches, qui est la vraie leçon de la session.
"""

import datetime
import json
import re
import unittest

import candidats_probables as cd
import comptes as cp
import opportunites as op
import projets as pj
import radar_cockpit as ck
import radar_dashboard as dash
import radar_runs as rr
import radar_stockage as st


AUJ = datetime.date(2026, 8, 26)


# ---------------------------------------------------------------------------
# Entrées RÉELLES : les noms de colonnes sont ceux de COLONNES_SHEET, pas des
# noms inventés pour la commodité du test. Un test qui invente son schéma ne
# teste pas l'intégration, il teste sa propre fiction.
# ---------------------------------------------------------------------------
def ligne_ted(**kw):
    base = {
        "date_maj": "2026-08-01", "score_final": "8.2", "score_surete": "8.0",
        "score_commercial": "8.4", "action_recommandee": "contacter",
        "fenetre_action": "court_terme", "niveau_opportunite_amarante": "fort",
        "titre": "Escorte de convois humanitaires", "acheteur": "PNUD",
        # TED stocke le pays en CODE ISO3, pas en nom lisible. Ecrire
        # « Tchad » ici donnerait un lead avec pays='TCHAD' et zone='Non
        # classé' : mon premier jet du test le faisait, et c'est exactement
        # le genre de fiction qui rend un test d'integration inutile.
        "pays_execution": "TCD", "pays_acheteur": "TCD",
        "type_client": "bailleur_donateur", "type_mobilite": "terrain_isole",
        "profil_personnes_exposees": "expatries",
        "duree_estimee": "longue_ou_residente",
        "accessibilite_commerciale": "facile",
        "securite_existante_detectee": "non",
        "profils_acteurs_probables": "ONG internationales",
        "justification": "Convois en zone rouge, expatriés exposés.",
        "confiance": "haute", "modele": "sonnet", "raffine": "oui",
        "divergence": "non", "source_mode_b": "", 
        "pays_execution_incertitude": "non",
        "publication_number": "TED-2026-0001",
        "lien_avis": "https://ted.europa.eu/udl?uri=TED:NOTICE:0001",
        "deadline": "2026-09-10", "date_publication": "2026-07-28",
        "valeur_estimee": "4000000 EUR", "statut_suivi": "",
    }
    base.update(kw)
    return base


class FausseConnexion:
    """Base factice : enregistre les requêtes au lieu de les exécuter."""

    def __init__(self, statut_courant=None):
        self.journal = []
        self._statut = statut_courant

    def cursor(self):
        return _FauxCurseur(self.journal, self._statut)

    def commit(self):
        self.journal.append(("COMMIT", None))


class _FauxCurseur:
    def __init__(self, journal, statut):
        self.journal = journal
        self._statut = statut
        self._res = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, sql, params=None):
        mot = sql.strip().split()[0].upper()
        table = ""
        for t in ("radar_outcomes", "radar_statuts", "radar_lignes"):
            if t in sql:
                table = t
                break
        self.journal.append(("{} {}".format(mot, table).strip(), params))
        self._res = (self._statut,) if "SELECT statut" in sql else None

    def executemany(self, sql, seq):
        for p in seq:
            self.execute(sql, p)

    def fetchone(self):
        return self._res

    def fetchall(self):
        return []


class TestChaineAvisVersOpportunite(unittest.TestCase):
    """Scénario 1 de l'audit : un avis TED connu produit l'opportunité
    attendue, jusque dans le HTML."""

    def setUp(self):
        self.lead = dash.ligne_vers_lead(ligne_ted(), "TED")

    def test_la_ligne_devient_un_lead_exploitable(self):
        self.assertEqual(self.lead["src"], "TED")
        self.assertEqual(self.lead["pays"], "Tchad")
        self.assertEqual(self.lead["zone"], "Sahel")
        self.assertEqual(self.lead["action"], "contacter")
        self.assertEqual(self.lead["deadline"], "2026-09-10")

    def test_les_composantes_du_score_traversent_la_chaine(self):
        """P1.2 : elles étaient lues du stockage puis jetées une ligne avant
        l'écran. Ce test verrouille le transport, pas le calcul."""
        self.assertEqual(self.lead["acces"], "facile")
        self.assertEqual(self.lead["duree"], "longue_ou_residente")
        self.assertEqual(self.lead["client"], "bailleur_donateur")

    def test_le_lead_devient_une_opportunite_avec_une_action(self):
        [o] = op.construire([self.lead], AUJ)
        self.assertEqual(o["opportunity_id"], "AVIS:TED:TED-2026-0001")
        self.assertTrue(o["action"]["libelle"])
        self.assertIn(o["action"]["urgence"], op.ORDRE_URGENCE)

    def test_l_echeance_pilote_l_urgence(self):
        """Clôture au 10/09, run au 26/08 : quinze jours, donc pas critique
        mais pas ignorable."""
        [o] = op.construire([self.lead], AUJ)
        self.assertEqual(o["jours_avant_cloture"], 15)

    def test_l_opportunite_atteint_le_html(self):
        html = ck.generer_cockpit([self.lead], suivi={"api": True})
        opps = json.loads(re.search(r"^const OPPS=(\[.*?\]);$", html,
                                    re.S | re.M).group(1))
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0]["opportunity_id"], "AVIS:TED:TED-2026-0001")
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])


class TestRattachementAuProjet(unittest.TestCase):
    """Scénario 2 : deux signaux de sources différentes se rejoignent."""

    def test_deux_sources_un_seul_dossier(self):
        avis = dash.ligne_vers_lead(ligne_ted(), "TED")
        avis["projet_id"] = "P178234"
        amont = dict(avis, src="BM", pub="BM-9", titre="Projet route N1",
                     date_det="2026-03-04", lien="https://projects.worldbank.org/x")
        opps = op.construire([avis, amont], AUJ)
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0]["opportunity_id"], "PROJ:P178234")
        self.assertEqual(opps[0]["n_leads"], 2)

    def test_la_convergence_monte_avec_les_axes(self):
        avis = dash.ligne_vers_lead(ligne_ted(), "TED")
        seul = op.construire([avis], AUJ)[0]["convergence"]["n"]
        avis["projet_id"] = "P178234"
        avis["ent_cle"] = "stecol"
        deux = op.construire([avis], AUJ)[0]["convergence"]["n"]
        self.assertGreater(deux, seul)


class TestTransitionModifieLaPriorite(unittest.TestCase):
    """Scénario 3 : une montée de phase change ce qu'il faut faire."""

    def _hist(self, phase, n=2):
        return [{"date": "2026-07-20", "phase": phase, "titre": "x",
                 "lien": ""} for _ in range(n)]

    def test_une_montee_critique_change_l_action(self):
        base = dash.ligne_vers_lead(ligne_ted(), "TED")
        base["deadline"] = ""                      # neutralise l'échéance
        sans = op.construire([base], AUJ)[0]["action"]["libelle"]
        avec = dict(base, montee_importance="critique", montee_recente=True,
                    montee_message="Appel d'offres EPC ouvert")
        apres = op.construire([avec], AUJ)[0]
        self.assertNotEqual(apres["action"]["libelle"], sans)
        self.assertEqual(apres["action"]["urgence"], "critique")

    def test_la_montee_vient_bien_du_moteur_projets(self):
        """Le champ n'est pas inventé par le test : il sort de
        `projets.derniere_montee`."""
        hist = (self._hist("FUNDING_APPROVED")
                + [{"date": "2026-07-20", "phase": "EPC_PROCUREMENT",
                    "titre": "AO", "lien": ""} for _ in range(2)])
        m = pj.derniere_montee(hist, AUJ)
        self.assertEqual(m["importance"], "critique")
        self.assertTrue(m["recente"])


class TestClicContacterProduitUnEvenement(unittest.TestCase):
    """Scénario 4 : le clic laisse une trace exploitable."""

    def test_la_transition_est_journalisee_avant_l_ecrasement(self):
        conn = FausseConnexion(statut_courant="contacte")
        st.definir_statut(conn, "ted_radar", "TED-2026-0001", "gagne", "", 250.0)
        ops = [op_ for op_, _ in conn.journal]
        self.assertLess(ops.index("SELECT radar_statuts"),
                        ops.index("INSERT radar_outcomes"))
        self.assertLess(ops.index("INSERT radar_outcomes"),
                        ops.index("INSERT radar_statuts"))

    def test_l_etat_precedent_est_conserve(self):
        conn = FausseConnexion(statut_courant="contacte")
        st.definir_statut(conn, "ted_radar", "TED-1", "gagne", "", 250.0)
        params = dict(conn.journal)["INSERT radar_outcomes"]
        self.assertIn("contacte", params)          # statut_precedent
        self.assertIn("gagne", params)
        self.assertIn(250.0, params)

    def test_l_onglet_ecrit_est_celui_du_catalogue(self):
        """P3.4 : un onglet vide rendrait l'événement irrécupérable."""
        for src in dash.CATALOGUE_SOURCES:
            self.assertTrue(ck.table_onglets().get(src), src)


class TestSourceMuetteRemonteALEcran(unittest.TestCase):
    """Scénario 5 : si une source se tait, l'application le dit."""

    def _runs(self, volumes):
        return [{"type": "sante", "horodatage": "2026-08-%02d" % (28 - i),
                 "sources": [{"src": "TED", "n": 50},
                             {"src": "EBRD", "n": v}]}
                for i, v in enumerate(volumes)]

    def test_le_moteur_detecte_la_regression(self):
        muettes = rr.sources_muettes(self._runs([0, 0, 0, 12, 14, 11]), 3)
        self.assertEqual([m["src"] for m in muettes], ["EBRD"])

    def test_l_alerte_atteint_le_html(self):
        detail = {"muettes": [{"src": "EBRD", "runs_muets": 3}], "runs": [],
                  "inventaire": {}, "issues": {}, "retro": {}, "quotas": {},
                  "rendement": []}
        html = ck.generer_cockpit([], suivi={"api": True},
                                  detail_sante=detail)
        self.assertIn("muette depuis", html)
        self.assertIn("EBRD", html)

    def test_aucune_regression_est_dit_explicitement(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        self.assertIn("Aucune source en régression", html)


class TestFrontieresEntreCouches(unittest.TestCase):
    """LA VRAIE LEÇON DE LA SESSION.

    Les quatre défauts trouvés à la main vivaient tous à la frontière entre
    deux couches correctes isolément. Cette classe garde ces frontières."""

    def test_les_montants_traversent_sous_leurs_DEUX_formes(self):
        """Piège rencontré deux fois : `valeur` est tantôt une chaîne brute
        (« 4000000 EUR »), tantôt `valeur_meur` déjà convertie."""
        brut = dash.ligne_vers_lead(ligne_ted(), "TED")
        enrichi = ck.enrichir([brut])[0]
        self.assertEqual(op._val([brut], "valeur"), 4.0)
        self.assertEqual(op._val([enrichi], "valeur"), 4.0)
        self.assertEqual(cd._bande(brut.get("valeur")), "1-10M")

    def test_aucun_module_ne_reimplemente_la_lecture_de_montant(self):
        """Trois conversions divergentes seraient trois bugs à venir."""
        with open("candidats_probables.py", encoding="utf-8") as f:
            self.assertIn("from opportunites import _nombre", f.read())

    def test_la_cle_d_entite_est_la_meme_des_deux_cotes(self):
        """`cleEnt` côté JS retombe sur le nom minuscule ; `cle_compte` doit
        faire pareil, sinon le bloc disparaît en silence."""
        html = ck.generer_cockpit([], suivi={"api": True})
        self.assertIn('function cleEnt(nom,entcle){return (entcle||"").trim()'
                      '||String(nom||"").trim().toLowerCase();}', html)
        self.assertEqual(cp.cle_compte("STECOL Corp", ""), "stecol corp")

    def test_la_cle_d_opportunite_n_est_calculee_qu_une_fois(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        self.assertIn("opp:l.opp_cle", html)
        self.assertNotIn('"PROJ:"+', html)

    def test_toutes_les_sources_ont_un_onglet(self):
        manquantes = [s for s in dash.CATALOGUE_SOURCES
                      if s not in dash.ONGLET_PAR_SOURCE]
        self.assertEqual(manquantes, [])

    def test_les_degradations_sont_bruyantes(self):
        """Un best-effort muet a masqué deux défauts. Les chemins critiques
        doivent crier."""
        with open("radar_cockpit.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ATTENTION : opportunites NON calculees", src)
        self.assertIn("ATTENTION : soumissionnaires non calcules", src)


class TestContratDePaysParSource(unittest.TestCase):
    """Frontière trouvée en écrivant ce fichier : les sources n'envoient pas
    le pays sous la même forme, et s'y tromper est SILENCIEUX.

    TED, AFDB, ADB, EBRD, UNGM, IDB, BMP, PROPARCO, DFC -> code ISO3.
    BM, RW -> nom lisible.

    Une ligne TED portant « Tchad » au lieu de « TCD » produit un lead
    pays='TCHAD', zone='Non classé'. Rien ne lève : le lead sort du Sahel,
    perd sa posture, sort des tuiles de théâtre, et n'apparaît plus sur la
    carte (COORDS est indexé sur le nom d'affichage)."""

    ISO = ("TED", "AFDB", "ADB", "EBRD", "UNGM", "IDB", "BMP", "PROPARCO",
           "DFC")

    def test_les_sources_iso_resolvent_le_code(self):
        for src in self.ISO:
            self.assertEqual(dash.resoudre_pays("TCD", src), ("Tchad", "Sahel"),
                             src)

    def test_les_sources_nominatives_resolvent_le_nom(self):
        for src in ("BM", "RW"):
            self.assertEqual(dash.resoudre_pays("Tchad", src),
                             ("Tchad", "Sahel"), src)

    def test_se_tromper_de_forme_degrade_en_silence(self):
        """Le test qui documente le piège : aucune exception, juste un lead
        hors zone."""
        nom, zone = dash.resoudre_pays("Tchad", "TED")
        self.assertEqual(zone, "Non classé")
        self.assertNotEqual(nom, "Tchad")

    def test_le_nom_resolu_est_celui_de_la_carte(self):
        """COORDS est indexé sur le nom d'affichage : un nom non résolu fait
        disparaître le marqueur, sans erreur."""
        nom, _ = dash.resoudre_pays("TCD", "TED")
        self.assertIn(nom, ck.COORDS)

    def test_aucun_pays_du_referentiel_ne_manque_a_la_carte(self):
        """Vérifié sur les 130 pays du référentiel : un pays résolu mais
        absent de COORDS disparaîtrait de la carte sans erreur. La couverture
        est complète aujourd'hui ; ce test le maintient."""
        manquants = [nom for nom, _ in dash.ZONE_PAR_ISO3.values()
                     if nom not in ck.COORDS]
        self.assertEqual(manquants, [])

    def test_pays_vide_ne_casse_pas(self):
        self.assertEqual(dash.resoudre_pays("", "TED"),
                         ("Pays non précisé", "Non classé"))


class TestChaineComplete(unittest.TestCase):
    """Le parcours entier, d'une ligne de Sheet au HTML servi."""

    def test_de_la_ligne_brute_a_la_page(self):
        lignes = [ligne_ted(),
                  ligne_ted(publication_number="TED-2026-0002",
                            titre="Gardiennage de base logistique",
                            accessibilite_commerciale="difficile",
                            securite_existante_detectee="oui",
                            action_recommandee="surveiller", deadline="")]
        leads = [dash.ligne_vers_lead(l, "TED") for l in lignes]
        html = ck.generer_cockpit(leads, suivi={"api": True})
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])
        raw = json.loads(re.search(r"^const RAW=(\[.*?\]), COORDS=", html,
                                   re.S | re.M).group(1))
        self.assertEqual(len(raw), 2)
        self.assertEqual(json.loads(
            re.search(r"^const OPPS=(\[.*?\]);$", html, re.S | re.M).group(1)
        ).__len__(), 2)

    def test_le_lead_difficile_est_moins_prioritaire(self):
        """Bout en bout : accessibilité difficile + sûreté en place doivent
        se traduire par une winability plus basse."""
        facile = dash.ligne_vers_lead(ligne_ted(), "TED")
        dur = dash.ligne_vers_lead(
            ligne_ted(publication_number="TED-2", 
                      accessibilite_commerciale="difficile",
                      securite_existante_detectee="oui"), "TED")
        w_f = op.winability([facile])[0]
        w_d = op.winability([dur])[0]
        self.assertGreater(w_f, w_d)

    def test_page_vide_reste_valide(self):
        html = ck.generer_cockpit([], suivi={"api": True})
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])


if __name__ == "__main__":
    unittest.main()
