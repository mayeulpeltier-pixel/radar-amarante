# -*- coding: utf-8 -*-
"""Posture de theatre dynamique et vue Projets non alimentee (25/08/2026).

DEUX DEFAUTS CORRIGES
---------------------
1. POSTURE FIGEE. Les tuiles de theatre lisaient `RISQUE_ZONE`, une table
   CONSTANTE : la posture d'un theatre ne bougeait jamais. L'application
   pouvait afficher « Posture jaune » sur une zone dont l'onglet Geopolitique
   signalait, deux clics plus loin, une aggravation severe. Depuis le
   rapatriement du boost geo, les SCORES bougeaient mais pas la posture :
   l'incoherence etait devenue interne a la meme page.

   La rehausse vient de `dash._boost_par_pays`, la MEME source que celle qui
   rehausse les scores : memes seuils, meme fenetre, meme decroissance. Une
   seule verite sur « ce pays s'aggrave-t-il ».

2. VUE PROJETS NON ALIMENTEE. Tant que les collecteurs Project Intelligence
   sont a l'arret, l'onglet affichait quatre KPI a zero, des facettes vides et
   un tableau vide. Le message d'explication EXISTAIT (je l'avais rate en
   audit et decrit l'onglet comme muet, c'etait faux) mais il arrivait tout en
   bas, sous l'echafaudage. On masque l'echafaudage.

Tests OFFLINE : fonctions pures et generation HTML, aucune base, aucun reseau.
"""

import json
import re
import unittest

import radar_cockpit as ck
import radar_dashboard as dash


def _lead(**kw):
    base = {
        "src": "TED", "pays": "Mali", "zone": "Sahel", "titre": "Escorte",
        "agence": "PNUD", "final": 8.0, "surete": 8.0, "comm": 7.0,
        "action": "contacter", "win": "", "nom": "n.c.", "email": "n.c.",
        "tel": "n.c.", "cible": "", "justif": "", "grp": "AT", "lien": "",
        "ecart": False, "secu": False, "mois": "2026-08", "mois_label": "a",
        "date_det": "2026-08-24", "statut": "nouveau", "motif_ecart": "",
        "deadline": "", "conf": "", "modele": "", "pub": "P1",
        "projet_id": "", "valeur": "2000000 EUR", "enveloppe": "",
        "entreprise": "", "sect": "Autre"}
    base.update(kw)
    return base


LEADS = [_lead(), _lead(pub="P2", pays="Ghana", zone="Afrique de l'Ouest")]

AGGRAVATION_GHANA = [{"pays_execution": "GHA", "pays_nom": "Ghana",
                      "severite": "4", "sens": "aggravation",
                      "motif": "Troubles frontaliers", "date_maj": "2026-08-24"}]


class TestPostureDynamique(unittest.TestCase):

    def setUp(self):
        ck.GEO_BOOST_ON = True

    def test_sans_alerte_la_posture_est_le_socle(self):
        """Retrocompatibilite stricte : rien ne bouge sans contexte."""
        p = ck.postures(LEADS, [])
        self.assertEqual(p["Afrique de l'Ouest"]["boost"], 0.0)
        self.assertEqual(p["Afrique de l'Ouest"]["niveau"],
                         p["Afrique de l'Ouest"]["base"])

    def test_aggravation_recente_releve_la_posture(self):
        p = ck.postures(LEADS, AGGRAVATION_GHANA)["Afrique de l'Ouest"]
        self.assertGreater(p["niveau"], p["base"])
        self.assertEqual(p["pays"], "Ghana")
        self.assertEqual(p["motif"], "Troubles frontaliers")

    def test_la_tuile_change_effectivement_de_couleur(self):
        """Le test qui compte : 4.0 (orange) doit franchir 4.5 (rouge). Une
        rehausse qui ne change rien a l'ecran ne sert a rien."""
        base = ck.postures(LEADS, [])["Afrique de l'Ouest"]["niveau"]
        haut = ck.postures(LEADS, AGGRAVATION_GHANA)["Afrique de l'Ouest"]["niveau"]
        self.assertLess(base, 4.5)
        self.assertGreaterEqual(haut, 4.5)

    def test_meme_source_que_la_rehausse_des_scores(self):
        """Postures et scores doivent designer le meme pays au meme moment,
        sinon l'application se contredit a nouveau, autrement."""
        par_pays = dash._boost_par_pays(AGGRAVATION_GHANA)
        self.assertIn("Ghana", par_pays)
        self.assertEqual(ck.postures(LEADS, AGGRAVATION_GHANA)["Afrique de l'Ouest"]["boost"],
                         round(par_pays["Ghana"][0], 2))

    def test_niveau_borne_a_cinq(self):
        """Le Sahel est deja au plafond : il ne doit pas sortir de l'echelle."""
        p = ck.postures(LEADS, [{"pays_execution": "MLI", "pays_nom": "Mali",
                                 "severite": "4", "sens": "aggravation",
                                 "motif": "x", "date_maj": "2026-08-24"}])
        self.assertLessEqual(p["Sahel"]["niveau"], 5.0)

    def test_un_allegement_ne_descend_jamais_une_posture(self):
        """Baisser la garde sur un signal d'amelioration serait le mauvais sens
        de l'erreur."""
        p = ck.postures(LEADS, [{"pays_execution": "GHA", "pays_nom": "Ghana",
                                 "severite": "4", "sens": "amelioration",
                                 "motif": "x", "date_maj": "2026-08-24"}])
        self.assertEqual(p["Afrique de l'Ouest"]["boost"], 0.0)

    def test_pas_d_empilement_entre_deux_pays(self):
        """La plus forte aggravation gagne, on n'additionne pas."""
        deux = AGGRAVATION_GHANA + [{"pays_execution": "MLI", "pays_nom": "Mali",
                                     "severite": "2", "sens": "aggravation",
                                     "motif": "y", "date_maj": "2026-08-24"}]
        p = ck.postures(LEADS, deux)
        self.assertLessEqual(p["Afrique de l'Ouest"]["boost"], dash.BOOST_GEO_MAX)

    def test_alerte_sur_un_pays_inconnu_du_radar_sans_effet(self):
        """Pas de referentiel supplementaire : une alerte sur un pays ou le
        radar n'a rien vu n'a pas de theatre a rehausser."""
        p = ck.postures(LEADS, [{"pays_execution": "PER", "pays_nom": "Pérou",
                                 "severite": "4", "sens": "aggravation",
                                 "motif": "x", "date_maj": "2026-08-24"}])
        self.assertTrue(all(v["boost"] == 0.0 for v in p.values()))

    def test_flag_off_fige_les_tuiles(self):
        ck.GEO_BOOST_ON = False
        p = ck.postures(LEADS, AGGRAVATION_GHANA)
        self.assertEqual(p["Afrique de l'Ouest"]["boost"], 0.0)

    def test_alertes_malformees_ne_cassent_rien(self):
        p = ck.postures(LEADS, [{"n_importe": "quoi"}, None])
        self.assertTrue(all(v["boost"] == 0.0 for v in p.values()))


class TestRenduPosture(unittest.TestCase):

    def setUp(self):
        ck.GEO_BOOST_ON = True
        self.html = ck.generer_cockpit(LEADS, geo_alertes=AGGRAVATION_GHANA)

    def test_posture_injectee_et_lue_par_le_front(self):
        p = json.loads(re.search(r"^const POSTURE=(\{.*?\});$",
                                 self.html, re.S | re.M).group(1))
        self.assertGreater(p["Afrique de l'Ouest"]["boost"], 0)
        self.assertIn("const p=(POSTURE&&POSTURE[z])||null;", self.html)

    def test_repli_sur_la_table_constante_conserve(self):
        """Une page servie depuis un cache sans POSTURE doit rester lisible."""
        self.assertIn("(RISQUE[z]||1.5)", self.html)

    def test_la_rehausse_est_annoncee_pas_maquillee(self):
        """Ne jamais presenter un niveau rehausse comme s'il etait le socle."""
        self.assertIn("function postureNote", self.html)
        self.assertIn("socle ${p.base} → ${p.niveau}", self.html)
        self.assertIn("${postureNote(z)}", self.html)

    def test_posture_vide_ne_casse_pas_la_page(self):
        html = ck.generer_cockpit(LEADS, posture={})
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])
        self.assertIn("function posture(z)", html)


class TestVueProjetsNonAlimentee(unittest.TestCase):

    def test_echafaudage_masque_quand_rien_a_montrer(self):
        html = ck.generer_cockpit(LEADS)
        self.assertIn("const vide=!PROJETS.length&&!CANDPROJ.length;", html)
        self.assertIn('["kpis-proj","p-filtres"]', html)
        self.assertIn('id="p-filtres"', html)

    def test_explication_donnee_a_la_place(self):
        html = ck.generer_cockpit(LEADS)
        self.assertIn("Vue en attente d'alimentation", html)
        self.assertIn("radar.yml", html)
        self.assertIn("Rien n'est cassé", html)

    def test_bouton_top20_masque_par_defaut(self):
        """Un bouton de tri sur une table vide n'a rien a trier."""
        html = ck.generer_cockpit(LEADS)
        self.assertIn('id="p-top" onclick="toggleTop()" style="display:none"', html)
        self.assertIn('if(bt&&PROJETS.length)bt.style.display="";', html)


class TestNonRegression(unittest.TestCase):

    def setUp(self):
        ck.GEO_BOOST_ON = True
        ck.SANTE_ON = True

    def test_aucun_placeholder_sur_les_cas_limites(self):
        for leads, al in (([], []), (LEADS, []), (LEADS, AGGRAVATION_GHANA)):
            self.assertEqual(re.findall(
                r"__[A-Z_]+__", ck.generer_cockpit(leads, geo_alertes=al)), [])

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit(LEADS, geo_alertes=AGGRAVATION_GHANA)
        for attendu in ('const opps=()=>actifs().filter(l=>l.src!=="ATTRIB")',
                        "function celluleScore", "function badgeDeadline",
                        'PRIO_COLOR={contacter:"#8E2649"', "santeRun"):
            self.assertIn(attendu, html)


if __name__ == "__main__":
    unittest.main()
