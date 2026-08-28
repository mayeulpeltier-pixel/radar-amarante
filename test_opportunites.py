# -*- coding: utf-8 -*-
"""P2.2 — L'objet Opportunity (26/08/2026).

LE PROBLEME
-----------
L'unité de travail était le LEAD : une ligne d'une source. Or une même occasion
commerciale se manifeste plusieurs fois, par des canaux différents :

    un projet BM détecté en mars
      -> un signal de recrutement en juin (l'entreprise mobilise)
        -> un avis d'appel d'offres en août (le marché existe enfin)

Trois lignes, trois onglets, trois scores, aucun lien. Le commercial les
découvrait séparément sans voir qu'il regardait trois fois la même histoire.

CE QUE CE CHANTIER NE FAIT PAS, ET C'EST DELIBERE
-------------------------------------------------
Aucun renseignement nouveau. Les cinq dimensions sont des RE-EXPRESSIONS de
signaux déjà collectés et déjà pondérés ailleurs. Inventer un score à partir de
champs non mesurés donnerait un chiffre d'apparence savante et sans contenu --
le reproche exact fait à l'idée d'un « potentiel en euros » tant que les
effectifs expatriés ne sont pas sourcés.

Et ces dimensions ne sont PAS CALIBREES tant que la boucle de rétroaction n'a
pas d'issues gagné/perdu (P1.1). D'ici là elles ordonnent, elles ne prédisent
pas -- ce que l'interface dit explicitement.

Tests OFFLINE : fonctions pures et contrats du gabarit, aucun réseau.
"""

import datetime
import re
import unittest

import opportunites as op
import radar_cockpit as ck


AUJ = datetime.date(2026, 8, 26)


def _lead(**kw):
    base = {"src": "TED", "pays": "Mali", "zone": "Sahel", "titre": "Escorte",
            "final": 7.0, "surete": 7.0, "valeur": 0, "enveloppe": 0,
            "deadline": "", "win": "", "acces": "", "duree": "", "client": "",
            "secu": False, "nature": "", "besoin": "", "date_det": "2026-08-01",
            "statut": "nouveau", "pub": "OP-1", "projet_id": "", "ent_cle": "",
            "lien": "https://ted.europa.eu/x", "sect": "BTP", "grp": ""}
    base.update(kw)
    return base


HISTOIRE = [
    _lead(src="BM", projet_id="P178234", titre="Route N1 Tchad",
          date_det="2026-03-04", enveloppe=180.0,
          lien="https://projects.worldbank.org/p1"),
    _lead(src="PRIVÉ", projet_id="P178234", date_det="2026-06-12",
          ent_cle="stecol", nature="expatrie_significatif", besoin="fort",
          lien="https://news.google.com/a"),
    _lead(src="TED", projet_id="P178234", date_det="2026-08-01",
          deadline="2026-09-10", win="court_terme", acces="facile",
          client="bailleur_donateur", valeur=4.0),
]


class TestIdentite(unittest.TestCase):

    def test_le_projet_rassemble_tout(self):
        """Le regroupement le plus utile : un projet identifié réunit l'avis,
        le titulaire et les signaux."""
        for l in HISTOIRE:
            self.assertEqual(op.cle_opportunite(l), "PROJ:P178234")

    def test_a_defaut_l_entreprise_et_le_theatre(self):
        """La zone est dans la clé à dessein : le même groupe au Sahel et en
        Asie centrale, ce sont deux conversations commerciales."""
        a = op.cle_opportunite(_lead(ent_cle="stecol", zone="Sahel"))
        b = op.cle_opportunite(_lead(ent_cle="stecol", zone="Asie centrale"))
        self.assertEqual(a, "ENT:stecol:Sahel")
        self.assertNotEqual(a, b)

    def test_a_defaut_l_avis_seul(self):
        """On ne perd jamais un lead au regroupement."""
        self.assertEqual(op.cle_opportunite(_lead(pub="OP-9")),
                         "AVIS:TED:OP-9")

    def test_lead_sans_rien_de_stable(self):
        self.assertEqual(op.cle_opportunite({}), "")

    def test_priorite_du_projet_sur_l_entreprise(self):
        """Un lead portant les deux doit rejoindre le dossier PROJET, le plus
        englobant. Sinon un même projet se scinderait par entreprise."""
        self.assertEqual(
            op.cle_opportunite(_lead(projet_id="P1", ent_cle="stecol")),
            "PROJ:P1")


class TestPiegeGoogleNews(unittest.TestCase):
    """LE piège de la corroboration, documenté depuis longtemps : N flux
    d'actualité différents résolvent tous vers news.google.com. Les compter
    séparément gonflerait la confiance, l'inverse exact de l'objectif."""

    def test_trois_flux_google_comptent_pour_un(self):
        leads = [_lead(src="PRIVÉ", lien="https://news.google.com/a"),
                 _lead(src="DIPLO", lien="https://news.google.com/b"),
                 _lead(src="BITD", lien="https://news.google.com/c")]
        self.assertEqual(op.sources_distinctes(leads), 1)

    def test_sources_reellement_distinctes_comptees(self):
        self.assertEqual(op.sources_distinctes(HISTOIRE), 3)

    def test_une_seule_source_signalee_comme_non_corroboree(self):
        note, motifs = op.confiance([_lead()])
        self.assertLess(note, 50)
        self.assertTrue(any("non corroborée" in m for m in motifs))

    def test_la_corroboration_fait_monter_la_confiance(self):
        seul, _ = op.confiance([HISTOIRE[0]])
        trois, _ = op.confiance(HISTOIRE)
        self.assertGreater(trois, seul)


class TestDimensions(unittest.TestCase):
    """Chaque dimension doit rendre une note ET ses motifs : une note sans
    justification n'est pas auditable (cf. P1.3)."""

    def test_chaque_dimension_justifie_toujours(self):
        for f in (op.attractivite, op.winability, op.fit, op.confiance):
            note, motifs = f(HISTOIRE)
            self.assertTrue(0 <= note <= 100, f.__name__)
            self.assertTrue(motifs, f.__name__)
        note, motifs = op.timing(HISTOIRE, AUJ)
        self.assertTrue(motifs)

    def test_lead_vide_ne_leve_pas(self):
        for f in (op.attractivite, op.winability, op.fit, op.confiance):
            self.assertTrue(f([_lead()])[1], f.__name__)

    def test_montant_absent_ni_penalise_ni_recompense(self):
        """Un marché non chiffré ne doit pas être traité comme un marché à
        zéro euro : l'absence d'information n'est pas une information."""
        sans, motifs = op.attractivite([_lead(valeur=0, enveloppe=0)])
        petit, _ = op.attractivite([_lead(valeur=0.4)])
        self.assertGreater(sans, petit)
        self.assertTrue(any("non chiffré" in m for m in motifs))

    def test_avis_cloture_ecrase_le_timing(self):
        note, motifs = op.timing([_lead(deadline="2026-01-01")], AUJ)
        self.assertLess(note, 20)
        self.assertTrue(any("clôturé" in m for m in motifs))

    def test_winability_reprend_l_accessibilite_deja_ponderee(self):
        """La dimension que l'audit réclamait en croyant qu'elle n'existait
        pas. Elle existait, fondue dans le score commercial."""
        facile, _ = op.winability([_lead(acces="facile")])
        difficile, _ = op.winability([_lead(acces="difficile")])
        self.assertGreater(facile, difficile)

    def test_surete_en_place_penalise_la_winability(self):
        avec, motifs = op.winability([_lead(secu=True)])
        sans, _ = op.winability([_lead(secu=False)])
        self.assertLess(avec, sans)
        self.assertTrue(any("déloger" in m for m in motifs))

    def test_absence_de_deploiement_penalise_le_fit(self):
        expat, _ = op.fit([_lead(nature="expatrie_significatif")])
        aucun, _ = op.fit([_lead(nature="aucun_deploiement")])
        self.assertGreater(expat, aucun)

    def test_notes_toujours_bornees(self):
        extreme = _lead(acces="difficile", secu=True,
                        client="etat_administration_locale",
                        nature="aucun_deploiement", besoin="faible")
        for f in (op.attractivite, op.winability, op.fit, op.confiance):
            note, _ = f([extreme])
            self.assertTrue(0 <= note <= 100, f.__name__)


class TestPriorite(unittest.TestCase):

    def test_la_confiance_attenue_sans_effacer(self):
        """Une opportunité magnifique attestée par une seule source de l'an
        dernier ne doit pas trôner en tête. Mais l'atténuation est bornée :
        sinon on cacherait les dossiers qu'il faut aller vérifier."""
        fort = {"attractivite": (90, []), "timing": (90, []),
                "winability": (90, []), "fit": (90, []), "confiance": (90, [])}
        faible = dict(fort, confiance=(10, []))
        self.assertGreater(op.priorite(fort), op.priorite(faible))
        self.assertGreater(op.priorite(faible), 0.65 * op.priorite(fort))

    def test_les_poids_somment_a_un(self):
        self.assertAlmostEqual(sum(op.POIDS.values()), 1.0)

    def test_la_confiance_n_est_pas_dans_les_poids(self):
        """Elle atténue, elle ne s'additionne pas : une preuve solide n'est
        pas une qualité commerciale."""
        self.assertNotIn("confiance", op.POIDS)


class TestConstruction(unittest.TestCase):

    def setUp(self):
        self.opps = op.construire(HISTOIRE + [_lead(pub="OP-9")], AUJ)

    def test_trois_signaux_une_opportunite(self):
        proj = [o for o in self.opps if o["opportunity_id"] == "PROJ:P178234"]
        self.assertEqual(len(proj), 1)
        self.assertEqual(proj[0]["n_leads"], 3)
        self.assertEqual(proj[0]["sources"], ["BM", "PRIVÉ", "TED"])

    def test_l_histoire_groupee_prime_sur_l_avis_isole(self):
        self.assertEqual(self.opps[0]["opportunity_id"], "PROJ:P178234")

    def test_dates_bornees_par_les_detections(self):
        proj = self.opps[0]
        self.assertEqual(proj["premiere_vue"], "2026-03-04")
        self.assertEqual(proj["derniere_vue"], "2026-08-01")

    def test_les_attributions_sont_exclues(self):
        """Un marché déjà gagné par un tiers n'est pas une opportunité à
        saisir. L'inclure regonflerait le pipeline, ce que la séparation du
        25/08 a précisément supprimé."""
        avec = op.construire(HISTOIRE + [_lead(src="ATTRIB", pub="A1")], AUJ)
        self.assertEqual(sum(o["n_leads"] for o in avec),
                         sum(o["n_leads"] for o in op.construire(HISTOIRE, AUJ)))

    def test_les_leads_ecartes_sont_exclus(self):
        for statut in ("non_pertinent", "écarté", "perdu"):
            self.assertEqual(op.construire([_lead(statut=statut)], AUJ), [])

    def test_dossier_dormant_signale(self):
        vieux = op.construire([_lead(date_det="2024-01-01")], AUJ)
        self.assertTrue(vieux[0]["dormante"])

    def test_corpus_vide(self):
        self.assertEqual(op.construire([], AUJ), [])
        self.assertEqual(op.construire(None, AUJ), [])

    def test_serialisation_a_plat(self):
        """Ni dict imbriqué ni liste : le Sheet et le SQL n'en stockent pas."""
        for ligne in op.serialiser(self.opps):
            for v in ligne.values():
                self.assertIsInstance(v, (str, int, float), ligne)

    def test_separateur_de_motifs_compatible(self):
        """Les motifs contiennent des virgules : une virgule les couperait."""
        ligne = op.serialiser(self.opps)[0]
        self.assertIn(" | ", ligne["motifs"])


class TestIntegration(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(HISTOIRE, suivi={"api": True})

    def test_cle_calculee_cote_python_seulement(self):
        """Même discipline que `ent_cle` : le JS lit la clé, il ne la
        recalcule pas. Deux implémentations = deux regroupements divergents."""
        self.assertIn("opp:l.opp_cle", self.html)
        self.assertNotIn('"PROJ:"+', self.html)

    def test_opportunites_injectees(self):
        opps = re.search(r"^const OPPS=(\[.*?\]);$", self.html,
                         re.S | re.M).group(1)
        self.assertIn("PROJ:P178234", opps)

    def test_bloc_masque_sur_un_signal_isole(self):
        """Sur un avis seul, les cinq dimensions n'ajouteraient rien à la
        décomposition juste en dessous : répéter dévalue."""
        self.assertIn("if(!o||o.n_leads<2)return \"\";", self.html)

    def test_l_interface_dit_que_ce_n_est_pas_calibre(self):
        """Ne pas laisser croire à une prédiction : ces notes réordonnent des
        indices, elles ne prédisent pas encore."""
        self.assertIn("pas encore calibrées sur les issues réelles", self.html)

    def test_table_et_upsert_declares(self):
        import radar_stockage as st
        self.assertIn("CREATE TABLE IF NOT EXISTS radar_opportunites",
                      st.SCHEMA_SQL)
        with open("radar_stockage.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("premiere_vue = LEAST(radar_opportunites.premiere_vue,",
                      src)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])

    def test_chantiers_precedents_intacts(self):
        for attendu in ("santeRun", "function marquerGagne",
                        "function blocDecomposition", "const opps=",
                        "function postureNote"):
            self.assertIn(attendu, self.html)


class TestConvergence(unittest.TestCase):
    """P2.3 — La convergence se mesure en AXES INDEPENDANTS, pas en volume."""

    def test_les_sept_axes_sont_toujours_rendus(self):
        """Un axe NON atteint est une information : il dit ce qui manque pour
        se décider. Les masquer donnerait une liste flatteuse et inutile."""
        c = op.convergence([_lead()])
        self.assertEqual(c["total"], 7)
        self.assertEqual(len(c["axes"]), 7)
        for a in c["axes"]:
            self.assertTrue(a["detail"], a["cle"])

    def test_histoire_complete_atteint_tous_les_axes(self):
        c = op.convergence(HISTOIRE, {"Mali"})
        self.assertEqual(c["n"], 7)

    def test_LE_PIEGE_du_volume(self):
        """L'audit externe comptait « 32 recruitment signals » comme une
        corroboration. C'est UNE seule : l'entreprise recrute. Trente-deux
        annonces du même employeur se répètent, elles ne se confirment pas."""
        spam = [_lead(src="PRIVÉ", ent_cle="stecol",
                      lien="https://news.google.com/%d" % i)
                for i in range(32)]
        c = op.convergence(spam)
        self.assertLessEqual(c["n"], 3)
        self.assertEqual(op.sources_distinctes(spam), 1)

    def test_une_vraie_convergence_bat_un_gros_volume(self):
        """Le test qui valide toute la doctrine du chantier."""
        spam = [_lead(src="PRIVÉ", ent_cle="s",
                      lien="https://news.google.com/%d" % i)
                for i in range(32)]
        self.assertGreater(op.confiance(HISTOIRE, {"Mali"})[0],
                           op.confiance(spam)[0])

    def test_les_axes_alimentent_la_dimension_existante(self):
        """La convergence n'est pas un score parallèle : deux chiffres à
        réconcilier, c'est deux vérités."""
        note, motifs = op.confiance(HISTOIRE, {"Mali"})
        self.assertTrue(any("axes de corroboration" in m for m in motifs))

    def test_axe_contexte_alimente_par_la_posture(self):
        sans = op.convergence(HISTOIRE, set())["n"]
        avec = op.convergence(HISTOIRE, {"Mali"})["n"]
        self.assertEqual(avec, sans + 1)

    def test_corpus_vide_sans_erreur(self):
        c = op.convergence([], None)
        self.assertEqual(c["n"], 0)
        self.assertEqual(c["total"], 7)


class TestMontantsTolerants(unittest.TestCase):
    """BUG RÉEL trouvé le 26/08 par le contrôle de rendu, pas par un test.

    Selon l'endroit de la chaîne, un lead porte `valeur` en CHAÎNE brute
    (« 180000000 EUR ») ou `valeur_meur` déjà convertie. Un `float()` sec
    levait sur les leads non enrichis, et le best-effort transformait
    l'exception en « aucune opportunité » : une dégradation SILENCIEUSE, page
    normale et zéro dossier commercial."""

    def test_chaine_brute_lue(self):
        self.assertEqual(op._val([{"enveloppe": "180000000 EUR"}],
                                 "enveloppe"), 180.0)

    def test_variante_meur_prioritaire(self):
        """Seule `_meur` garantit l'unité : elle doit primer sur le brut."""
        self.assertEqual(op._val([{"enveloppe": "zzz", "enveloppe_meur": 180.0}],
                                 "enveloppe"), 180.0)

    def test_euros_convertis_en_millions(self):
        """Sans cette correction, un marché de 4 M€ deviendrait 4 000 000 M€."""
        self.assertEqual(op._val([{"valeur": "4000000"}], "valeur"), 4.0)

    def test_petit_nombre_deja_en_millions(self):
        self.assertEqual(op._val([{"valeur": 4.0}], "valeur"), 4.0)

    def test_illisible_vaut_zero_sans_lever(self):
        for faux in ("n.c.", "", None, "abc", {}):
            self.assertEqual(op._nombre(faux), 0.0)

    def test_aucun_float_sec_ne_subsiste(self):
        """Deux points de conversion existaient ; le second (axe financement)
        est tombé au premier correctif et n'a été vu qu'au second contrôle."""
        with open("opportunites.py", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('float(l.get("enveloppe") or 0)', src)
        self.assertNotIn('float(l.get("final") or 0)', src)

    def test_la_degradation_est_bruyante(self):
        """Un best-effort muet a masqué ce bug. Il doit crier maintenant."""
        with open("radar_cockpit.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ATTENTION : opportunites NON calculees", src)


class TestAffichageConvergence(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(HISTOIRE, suivi={"api": True})

    def test_checklist_rendue(self):
        self.assertIn("axes de corroboration", self.html)
        self.assertIn('class="cv-r', self.html)

    def test_le_piege_est_explique_a_l_ecran(self):
        """Le lecteur doit comprendre pourquoi 32 signaux ne font pas 32
        preuves, sinon il trouvera le compteur trop sévère."""
        self.assertIn("pas trente-deux", self.html)

    def test_pays_aggraves_derives_de_la_posture(self):
        """Une seule source de vérité sur « ce pays se dégrade-t-il »."""
        with open("radar_cockpit.py", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('aggraves = {v.get("pays") for v in (posture or {}).values()',
                      src)


class TestProchaineAction(unittest.TestCase):
    """P2.4 — Une opportunité sans prochaine action est une ligne de plus à
    lire. Avec, c'est une décision prise.

    L'action est DÉDUITE de ce qui manque (axes de convergence) et de ce qui
    presse (échéance), jamais choisie arbitrairement. L'ORDRE des règles est
    la doctrine : ce qui est irréversible passe avant ce qui est améliorable.
    """

    def _opp(self, leads):
        return op.construire(leads, AUJ)[0]

    def test_l_irreversible_passe_avant_tout(self):
        """Une clôture ne se rattrape pas ; une vérification, si."""
        o = self._opp([_lead(deadline="2026-08-30", projet_id="P1",
                             ent_cle="x", lien="https://a.fr")])
        self.assertEqual(o["action"]["urgence"], "critique")
        self.assertEqual(o["action"]["libelle"], "Répondre ou renoncer")

    def test_dossier_travaille_appelle_une_suite(self):
        """Pas une reprise à zéro : « relancer », pas « prendre contact »."""
        o = self._opp([_lead(statut="contacte", projet_id="P1", ent_cle="x",
                             lien="https://a.fr")])
        self.assertEqual(o["action"]["libelle"], "Relancer et qualifier")

    def test_non_corrobore_appelle_une_verification_pas_un_contact(self):
        """Engager du temps commercial sur un seul indice, c'est le gaspiller."""
        o = self._opp([_lead(pub="SEUL")])
        self.assertIn("Vérifier", o["action"]["libelle"])
        self.assertEqual(o["action"]["urgence"], "faible")

    def test_sans_acteur_il_n_y_a_personne_a_appeler(self):
        o = self._opp([_lead(src="BM", projet_id="P1", enveloppe=50.0,
                             lien="https://a.fr")])
        self.assertIn("Identifier", o["action"]["libelle"])

    def test_amont_sans_marche_est_l_avantage_pas_un_manque(self):
        """Le marché sûreté qui n'existe pas encore est précisément la fenêtre
        où Amarante peut se positionner en premier."""
        o = self._opp([_lead(src="PRIVÉ", projet_id="P1", ent_cle="x",
                             enveloppe=50.0, nature="mixte",
                             lien="https://news.google.com/a")])
        self.assertIn("avant publication", o["action"]["libelle"])
        self.assertEqual(o["action"]["urgence"], "haute")

    def test_toute_opportunite_a_une_action(self):
        """Aucun dossier ne doit rester sans suite proposée."""
        for leads in ([_lead()], HISTOIRE, [_lead(statut="contacte")]):
            for o in op.construire(leads, AUJ):
                self.assertTrue(o["action"]["libelle"])
                self.assertIn(o["action"]["urgence"], op.ORDRE_URGENCE)

    def test_l_urgence_ne_contamine_pas_le_score(self):
        """Une action urgente sur un mauvais dossier reste un mauvais
        dossier : l'urgence trie, elle ne note pas."""
        urgent = self._opp([_lead(deadline="2026-08-28")])
        self.assertEqual(urgent["action"]["urgence"], "critique")
        self.assertLess(urgent["priorite"], 70)

    def test_serialisation_de_l_action(self):
        ligne = op.serialiser(op.construire(HISTOIRE, AUJ))[0]
        for c in ("action", "action_motif", "action_urgence"):
            self.assertIn(c, ligne)
            self.assertIsInstance(ligne[c], str)


class TestHomeAction(unittest.TestCase):
    """La vue d'ensemble répondait à « que contient mon radar ? ». Juste, et
    inerte : rien n'y disait par où commencer."""

    def setUp(self):
        self.html = ck.generer_cockpit(HISTOIRE, suivi={"api": True})

    def test_bloc_rendu_en_tete_de_vue(self):
        self.assertIn('<div id="aujourdhui"></div>', self.html)
        self.assertIn("function renderAujourdhui", self.html)
        i_auj = self.html.index('id="aujourdhui"')
        i_th = self.html.index('id="theatres"')
        self.assertLess(i_auj, i_th)

    def test_appele_a_l_ouverture(self):
        self.assertIn('if(v==="overview"){renderAujourdhui();', self.html)

    def test_tri_par_urgence_pas_par_score(self):
        """Le tri par score remonterait les gros dossiers lointains devant les
        petits qui se ferment demain."""
        self.assertIn("URG_ORDRE[b.action.urgence]-URG_ORDRE[a.action.urgence]",
                      self.html)
        self.assertIn("irrattrapable", self.html)

    def test_dossiers_clos_et_dormants_exclus(self):
        self.assertIn('!o.dormante', self.html)
        self.assertIn('o.statut!=="gagne"&&o.statut!=="perdu"', self.html)

    def test_etat_vide_redige(self):
        """Un bloc vide sans phrase laisse croire à une panne."""
        self.assertIn("Rien ne presse aujourd", self.html)

    def test_les_quatre_sections_existent(self):
        for titre in ("À traiter", "Échéances sous 30 jours",
                      "Mouvements concurrents", "Contexte géopolitique"):
            self.assertIn(titre, self.html)

    def test_ouverture_sur_le_lead_le_plus_significatif(self):
        """Une opportunité groupe plusieurs leads : le tiroir doit s'ouvrir
        sur celui qui porte le plus d'information."""
        self.assertIn("function ouvrirOpp", self.html)
        self.assertIn("LEADS.filter(x=>x.opp===id).sort", self.html)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])


if __name__ == "__main__":
    unittest.main()
