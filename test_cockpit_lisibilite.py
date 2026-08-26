# -*- coding: utf-8 -*-
"""Coherence visuelle et lisibilite des onglets du cockpit (25/08/2026).

QUATRE DEFAUTS CORRIGES
-----------------------
1. SEMANTIQUE DE COULEUR CONTRADICTOIRE. La priorite « a contacter » etait
   VERTE dans les tableaux (`.pill.contacter`) et ROUGE sur la carte
   (`PRIO_COLOR`) et dans les KPI. Deux surfaces de la meme page disaient
   deux choses de la meme donnee. Unifie sur l'amarante, ce qui libere en
   prime le rouge pour ce qu'il doit signaler : une ALERTE (echeance
   imminente, aggravation geopolitique), jamais une priorite.

2. ECHELLES DE SCORE CONFLATIONNEES (feuille de route, pt 3). L'en-tete de
   colonne annoncait « risque zone + secteur + montant » pour TOUTES les
   lignes -- c'est la formule des ATTRIBUTIONS. Un avis est score par analyse
   LLM, un signal prive par intensite. Trois moteurs, trois echelles, aucune
   comparable. Chaque score porte desormais son etiquette.

3. ONGLET ATTRIBUTIONS SANS AUCUNE FACETTE, quand les Opportunites en ont
   cinq. Sur 40 pays et une douzaine de sources, la liste etait a peine
   exploitable. Et la pastille par defaut « attribué » empruntait la classe
   `contacter`, donc la couleur d'un lead a traiter.

4. ORIGINE D'ENTREPRISE PRISE AU HASARD. `if(l.pays_tit&&!e.origine)` gardait
   la PREMIERE valeur rencontree : deux attributions donnant deux pays
   affichaient celui qui sortait en tete du tri, silencieusement. Et le meme
   emplacement affichait tantot un pays, tantot un secteur.

Tests OFFLINE : generation HTML pure, aucune base, aucun reseau.
"""

import json
import re
import unittest

import radar_cockpit as ck


def _lead(**kw):
    base = {
        "src": "TED", "pays": "Mali", "zone": "Sahel", "titre": "Escorte convois",
        "agence": "PNUD", "final": 8.0, "surete": 8.0, "comm": 7.0,
        "action": "contacter", "win": "court_terme", "nom": "n.c.",
        "email": "n.c.", "tel": "n.c.", "cible": "c", "justif": "j",
        "grp": "AT", "lien": "", "ecart": False, "secu": False,
        "mois": "2026-08", "mois_label": "août 2026", "date_det": "2026-08-24",
        "statut": "nouveau", "motif_ecart": "", "deadline": "2026-09-15",
        "conf": "", "modele": "", "pub": "P1", "projet_id": "",
        "valeur": "2000000 EUR", "enveloppe": "", "entreprise": "",
        "sect": "BTP / Construction"}
    base.update(kw)
    return base


CORPUS = [
    _lead(pub="P1"),
    _lead(pub="P2", src="PRIVÉ", valeur="500000 EUR", entreprise="Acme"),
    _lead(pub="P3", src="ATTRIB", action="surveiller", pays="Tchad",
          valeur="50000000 EUR", entreprise="Acme SA", origine="China",
          etranger_titulaire=True),
    _lead(pub="P4", src="ATTRIB", action="surveiller", pays="Niger",
          valeur="3000000 EUR", entreprise="Acme SA", origine="China"),
    _lead(pub="P5", src="ATTRIB", action="surveiller", pays="Mali",
          valeur="1000000 EUR", entreprise="Acme SA", origine="Turquie"),
]


class TestCouleursDePriorite(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS)

    def test_pastille_et_marqueur_carte_disent_la_meme_chose(self):
        """LA garde : `.pill.contacter` et `PRIO_COLOR.contacter` doivent
        porter la meme couleur. Elles etaient vert et rouge."""
        self.assertIn(".pill.contacter{background:var(--amarante-soft);"
                      "color:var(--amarante)}", self.html)
        self.assertIn('PRIO_COLOR={contacter:"#8E2649"', self.html)

    def test_legende_de_carte_alignee(self):
        self.assertIn('style="background:#8E2649"></span>À contacter', self.html)
        self.assertNotIn('style="background:#C0392B"></span>À contacter', self.html)

    def test_rouge_reserve_aux_alertes(self):
        """Le rouge ne doit plus designer une priorite : il reste sur
        l'echeance imminente et la rehausse geopolitique."""
        self.assertIn(".jx.urgent{background:var(--red-soft);color:var(--red)}",
                      self.html)
        self.assertIn(".mb.geo{background:var(--red-soft);color:var(--red)}",
                      self.html)
        self.assertNotIn('c:"var(--red)",cs:"var(--red-soft)"', self.html)


class TestEchellesDeScore(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS)

    def test_trois_echelles_declarees(self):
        for cle in ("avis:[", "prive:[", "attrib:["):
            self.assertIn(cle, self.html)

    def test_entete_ne_pretend_plus_une_formule_unique(self):
        """Le tooltip annoncait la formule des attributions pour tout le
        monde. C'etait faux pour les avis comme pour les signaux."""
        self.assertNotIn("Score = risque zone + secteur + montant", self.html)
        self.assertIn("Trois échelles distinctes", self.html)

    def test_chaque_score_porte_son_etiquette(self):
        self.assertIn("function celluleScore", self.html)
        self.assertIn('<span class="ech"', self.html)
        self.assertIn("+`<td>${celluleScore(l)}</td>`", self.html)

    def test_avertissement_de_non_comparabilite_present(self):
        self.assertIn("Ne se compare pas au score d'une attribution", self.html)
        self.assertIn("ce n'est PAS une analyse sûreté", self.html)

    def test_rehausse_geo_toujours_visible_dans_la_cellule(self):
        """La cellule a change : le score d'origine barre ne doit pas avoir
        disparu au passage."""
        self.assertIn('class="sfbase"', self.html)


class TestFiltresAttributions(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS)

    def test_facettes_presentes(self):
        for champ in ("af-zone", "af-sect", "af-orig", "af-etr"):
            self.assertIn(champ, self.html)
        self.assertIn("function resetAttrib", self.html)

    def test_une_seule_barre_de_recherche(self):
        """La barre du haut reste affichee sur cet onglet : y ajouter un second
        champ aurait cree deux recherches concurrentes a l'ecran. C'est elle qui
        pilote la table, et elle n'est plus morte hors Opportunites."""
        self.assertNotIn('id="af-q"', self.html)
        self.assertIn("if(state.q){const q=state.q.toLowerCase();", self.html)
        self.assertIn('else if(state.view==="attrib")renderAttrib();', self.html)
        self.assertIn("rc.disabled=!actif;", self.html)

    def test_export_csv_suit_les_filtres_de_la_vue(self):
        """L'export deversait tout le corpus depuis l'onglet Attributions,
        filtres compris ou non. Il suit desormais ce qui est affiche."""
        self.assertIn('state.view==="attrib"?attribFiltres():LEADS', self.html)

    def test_filtres_initialises_au_chargement(self):
        self.assertIn("initFilters();initAttribFiltres();go(\"overview\");",
                      self.html)

    def test_tri_par_entete_sans_collision_avec_les_opportunites(self):
        """Deux tables triables coexistent : data-sort (opportunités) et
        data-asort (attributions). Les confondre re-trierait la mauvaise."""
        self.assertIn('th[data-asort]', self.html)
        self.assertIn('th[data-sort]', self.html)
        self.assertIn('data-asort="ts"', self.html)

    def test_pastille_attribue_est_neutre(self):
        """« attribué » est un ETAT, pas une priorite. La classe `contacter`
        lui donnait la couleur d'un lead a traiter."""
        self.assertIn('<span class="pill neutre">attribué</span>', self.html)
        self.assertNotIn('<span class="pill contacter">attribué</span>', self.html)
        self.assertIn(".pill.neutre{", self.html)

    def test_recurrence_calculee_hors_filtre(self):
        """« 4 marchés gagnés » doit rester vrai quand un filtre n'en montre
        qu'un : le compteur se calcule sur tout le corpus."""
        self.assertIn("const cnt={};tous.forEach", self.html)

    def test_kpi_rappellent_le_total_quand_un_filtre_est_actif(self):
        self.assertIn('at.length!==tous.length?"sur "+tous.length+" au total"',
                      self.html)

    def test_champs_manquants_signales(self):
        """Un champ vide doit se voir, pas se fondre dans la liste."""
        self.assertIn("sans origine identifiée", self.html)


class TestOrigineEntreprise(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS)

    def test_origine_arbitree_et_non_prise_au_hasard(self):
        self.assertIn("e.origines[l.pays_tit]=(e.origines[l.pays_tit]||0)+1",
                      self.html)
        self.assertNotIn("if(l.pays_tit&&!e.origine)e.origine=l.pays_tit",
                         self.html)

    def test_majorite_l_emporte_et_le_desaccord_est_trace(self):
        self.assertIn("const og=Object.keys(e.origines)"
                      ".sort((a,b)=>e.origines[b]-e.origines[a]);", self.html)
        self.assertIn("e.originesAutres=og.slice(1);", self.html)

    def test_divergence_signalee_a_l_ecran(self):
        self.assertIn("fmeta-warn", self.html)
        self.assertIn("Sources divergentes", self.html)

    def test_origine_et_secteur_dans_deux_emplacements_distincts(self):
        """Le meme slot affichait tantot « China », tantot « Luxe ». Deux
        fiches cote a cote devenaient illisibles."""
        self.assertNotIn('${e.origine||e.secteurSuivi||"origine n.c."}', self.html)
        self.assertIn("fmeta-sect", self.html)


class TestNonRegression(unittest.TestCase):

    def test_aucun_placeholder_non_remplace(self):
        for leads in ([], CORPUS, [c for c in CORPUS if c["src"] == "ATTRIB"]):
            self.assertEqual(re.findall(r"__[A-Z_]+__",
                                        ck.generer_cockpit(leads)), [])

    def test_page_sans_attribution_reste_complete(self):
        html = ck.generer_cockpit([_lead()])
        self.assertIn("Aucune attribution collectée", html)
        self.assertIn("initAttribFiltres", html)

    def test_separation_pipeline_intacte(self):
        """Le chantier precedent ne doit pas avoir ete defait au passage."""
        html = ck.generer_cockpit(CORPUS)
        self.assertIn('const opps=()=>actifs().filter(l=>l.src!=="ATTRIB")', html)
        self.assertIn("marchés encore à saisir", html)


if __name__ == "__main__":
    unittest.main()
