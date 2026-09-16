# -*- coding: utf-8 -*-
"""Trier par pertinence, sur tous les onglets (08/09/2026).

LE CONSTAT
----------
« Il n'y a aucun moyen de trier ce qui représente une vraie opportunité. »

Inventaire avant correction :

    ONGLET          tri par score/pertinence ?        tri actif visible ?
    Opportunités    OUI (colonne Score)               NON
    Attributions    NON : pas de colonne Score        NON
    Entreprises     NON : volumes uniquement          sans objet
    Projets         OUI (colonne Opportunité)         NON
    Dossiers        AUCUN tri                         --

Deux problèmes distincts, et le second explique en partie le premier.

1. DES TRIS MANQUANTS. Les Attributions n'avaient pas de colonne Score alors
   que le score existe. Les Entreprises ne proposaient que des VOLUMES
   (valeur, théâtres, marchés, signaux) : aucun ne dit si le compte vaut d'être
   travaillé. Une entreprise à 12 marchés en France vaut moins qu'une à 2
   marchés au Sahel avec des expatriés. La priorité commerciale existait déjà
   (`comptes.priorite_compte`, P2.5), elle n'était ni affichée ni triable.
   Les Dossiers s'affichaient dans l'ordre brut du serveur.

2. UN TRI INVISIBLE. Les quinze flèches « ↕ » restaient identiques quelle que
   soit la colonne active : impossible de savoir sur quoi la liste était triée
   ni dans quel sens. Un tri qu'on ne voit pas revient à ne pas en avoir -- on
   reclique au hasard pour deviner.

Tests OFFLINE : contrats du gabarit et fonctions pures, aucun réseau.
"""

import re
import unittest

import radar_cockpit as ck


def _lead(**kw):
    base = {"src": "ATTRIB", "pays": "Tchad", "zone": "Sahel", "titre": "T",
            "agence": "A", "final": 7.0, "surete": 7, "comm": 7,
            "action": "surveiller", "win": "", "nom": "n.c.", "email": "n.c.",
            "tel": "n.c.", "cible": "", "justif": "", "grp": "AT", "lien": "",
            "ecart": False, "secu": False, "mois": "2026-08",
            "mois_label": "a", "date_det": "2026-09-01", "statut": "nouveau",
            "motif_ecart": "", "deadline": "", "conf": "", "modele": "",
            "pub": "A1", "projet_id": "", "valeur": "10000000 EUR",
            "enveloppe": "", "entreprise": "", "sect": "Extractif / Mines"}
    base.update(kw)
    return base


CORPUS = [
    _lead(pub="A1", entreprise="CRBC", origine="China",
          etranger_titulaire=True, nature="expatrie_significatif"),
    _lead(pub="A2", entreprise="CRBC", pays="Niger",
          zone="Afrique de l'Ouest"),
    _lead(pub="A3", entreprise="CRBC", pays="Mali"),
    _lead(pub="B1", entreprise="LOCALE SA", origine="Tchad"),
    _lead(pub="C1", src="PRIVÉ", action="contacter",
          entreprise="PETITE SARL", pays="France", zone="Europe"),
]


class TestIndicateurDeTri(unittest.TestCase):
    """Un tri invisible revient à ne pas en avoir."""

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS, suivi={"api": True})

    def test_une_seule_fonction_pour_les_trois_tables(self):
        """Trois variantes divergeraient à la première modification."""
        self.assertIn("function majTri(selecteur, attribut, cle, dir)",
                      self.html)
        self.assertEqual(self.html.count("function majTri"), 1)

    def test_branchee_sur_les_trois_tables(self):
        for selecteur, attribut in (('th[data-sort]', '"sort"'),
                                    ('th[data-asort]', '"asort"'),
                                    ('th[data-psort]', '"psort"')):
            self.assertIn('majTri("thead {}", {}'.format(selecteur, attribut),
                          self.html)

    def test_la_fleche_indique_le_sens(self):
        self.assertIn('dir > 0 ? "▲" : "▼"', self.html)
        self.assertIn('actif ? (dir > 0', self.html)

    def test_la_colonne_active_est_mise_en_avant(self):
        self.assertIn('th.classList.toggle("tri-on", actif)', self.html)
        self.assertIn("th.tri-on{color:var(--amarante)}", self.html)

    def test_les_entetes_triables_sont_cliquables_a_l_oeil(self):
        """Sans curseur ni survol, rien n'indique qu'on PEUT trier."""
        self.assertIn("th[data-sort],th[data-asort],th[data-psort]"
                      "{cursor:pointer;user-select:none}", self.html)


class TestAttributions(unittest.TestCase):
    """Le score était calculé et affiché nulle part dans cette table."""

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS, suivi={"api": True})

    def test_colonne_score_presente_et_triable(self):
        self.assertIn('data-asort="score"', self.html)

    def test_la_cellule_reutilise_le_rendu_commun(self):
        """`celluleScore` porte l'étiquette d'échelle et le score d'origine en
        cas de rehausse géo : réécrire la cellule perdrait les deux."""
        self.assertIn("<td>${celluleScore(l)}</td>", self.html)

    def test_l_echelle_est_rappelee_dans_l_entete(self):
        """Un score d'attribution ne se compare pas à un score d'avis : le
        tooltip doit le dire, sinon la colonne invite à la confusion."""
        self.assertIn("Ne se compare pas au score d'un avis", self.html)

    def test_colspan_de_la_ligne_vide_mis_a_jour(self):
        """Une colonne de plus sans colspan ajusté casse l'état vide."""
        self.assertIn('colspan="8"', self.html)
        self.assertNotIn('colspan="7" class="empty">${tous.length', self.html)


class TestEntreprises(unittest.TestCase):
    """L'onglet le plus concerné : que des volumes, aucune pertinence."""

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS, suivi={"api": True})

    def test_priorite_commerciale_proposee_en_premier(self):
        self.assertIn('<option value="priorite">Priorité commerciale</option>',
                      self.html)

    def test_priorite_est_le_tri_par_defaut(self):
        """L'onglet doit répondre à « lesquels valent d'être travaillés »,
        pas à « lesquels ont bougé récemment »."""
        self.assertIn('let firmoState={tri:"priorite"', self.html)

    def test_la_priorite_vient_du_module_comptes(self):
        """Elle est calculée par `priorite_compte` (P2.5), pas recalculée ici :
        deux définitions divergeraient."""
        self.assertIn("const cle=(e)=>(COMPTES[e.cle]||{}).priorite||0;",
                      self.html)

    def test_la_priorite_est_affichee_sur_la_carte(self):
        """Trier sur un chiffre invisible donne un ordre qui paraît
        arbitraire."""
        self.assertIn('<div class="l">priorité</div>', self.html)
        self.assertIn("function prioCouleur", self.html)

    def test_le_motif_de_la_priorite_est_lisible(self):
        self.assertIn('class="fprio"', self.html)
        self.assertIn(".fprio{", self.html)

    def test_la_grille_de_statistiques_suit(self):
        """Cinq statistiques dans une grille de quatre colonnes déborderait."""
        self.assertIn("grid-template-columns:repeat(5,1fr)", self.html)

    def test_le_sens_est_inversable(self):
        self.assertIn("function inverserTriFirmo", self.html)
        self.assertIn('id="ff-sens"', self.html)

    def test_tri_stable_a_valeur_egale(self):
        """Sans départage, l'ordre changeait d'un rendu à l'autre et donnait
        l'impression que la liste bougeait toute seule."""
        self.assertIn("||(cle(b)-cle(a))||a.nom.localeCompare(b.nom)",
                      self.html)


class TestDossiers(unittest.TestCase):
    """Ils s'affichaient dans l'ordre brut du serveur."""

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS, suivi={"api": True})

    def test_selecteur_de_tri_present(self):
        self.assertIn('id="d-tri"', self.html)
        self.assertIn('<option value="score">Meilleur score</option>',
                      self.html)

    def test_score_du_dossier_est_le_MEILLEUR_signal(self):
        """Une moyenne punirait un dossier riche en signaux faibles autour
        d'un signal majeur : un projet vaut ce que vaut son meilleur signal."""
        self.assertIn("function scoreDossier", self.html)
        self.assertIn("Math.max(0, ...((d.timeline||[])", self.html)

    def test_le_selecteur_est_cable(self):
        """Un sélecteur qui ne déclenche rien est pire qu'une absence de
        sélecteur : il promet une action qu'il ne rend pas."""
        self.assertIn("dt.onchange=e=>{dossState.tri=e.target.value", self.html)

    def test_le_sens_est_inversable(self):
        self.assertIn("function inverserTriDoss", self.html)

    def test_tri_alphabetique_traite_a_part(self):
        """Comparer des titres avec une soustraction donnerait NaN."""
        self.assertIn('if(t==="titre")return (a.titre||"")'
                      '.localeCompare(b.titre||"")*dd;', self.html)


class TestNonRegression(unittest.TestCase):

    def test_page_valide_sur_les_cas_limites(self):
        for leads in ([], CORPUS, [_lead(src="TED", action="contacter")]):
            html = ck.generer_cockpit(leads, suivi={"api": True})
            self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])

    def test_les_tris_existants_sont_conserves(self):
        html = ck.generer_cockpit(CORPUS, suivi={"api": True})
        for col in ('data-sort="score"', 'data-sort="valeur"',
                    'data-psort="opportunite"', 'data-asort="valeur"'):
            self.assertIn(col, html)

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit(CORPUS, suivi={"api": True})
        for attendu in ("renderAujourdhui", "function blocCompte",
                        "function celluleScore", "santeRun"):
            self.assertIn(attendu, html)


if __name__ == "__main__":
    unittest.main()
