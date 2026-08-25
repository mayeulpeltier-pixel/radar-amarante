# -*- coding: utf-8 -*-
"""
TEST OBLIGATOIRE (point 21 du cahier des charges) -- INGA 3 et TANZANIA LNG.
===============================================================================

On rejoue une chronologie d'evenements REELLE (titres du meme registre que ceux
remontes par la sonde) et on verifie que le systeme sait, de bout en bout :

  1. identifier les projets            5. determiner leur phase
  2. regrouper les evenements          6. calculer leur maturite
  3. construire leur timeline          7. calculer leur potentiel Amarante
  4. identifier les entreprises        8. identifier les prochaines etapes
                                       9. produire les prospects

Tout est offline et deterministe : la date de reference est figee.
La classification de phase (faite par LLM en amont) est ici FOURNIE dans les
signaux : ce fichier teste le RAISONNEMENT, pas l'extraction.
"""

import datetime
import unittest

import projets as pj
import projets_reference as ref


AUJ = datetime.date(2026, 8, 22)


def sig(titre, date, phase="", resume="", lien=None, **kw):
    d = {"titre": titre, "date": date, "phase": phase, "resume": resume,
         "lien": lien or "http://x/" + str(abs(hash(titre)) % 99999)}
    d.update(kw)
    return d


# --- Chronologie INGA 3 (celle du cahier des charges) ----------------------
INGA = [
    sig("RD Congo : le gouvernement approuve la loi Grand Inga", "2025-02-10",
        "POLITICAL_ANNOUNCEMENT"),
    sig("World Bank approves $250m for Inga 3", "2025-06-03", "FUNDING_APPROVED",
        resume="The World Bank backs the Inga 3 hydropower project."),
    sig("DRC signs agreement with AFD on Inga 3", "2026-01-20", "MOU",
        resume="AFD and the DRC government sign a memorandum on Grand Inga."),
    sig("AECOM selected for Inga studies", "2026-04-15", "CONSULTANT_SELECTION",
        resume="AECOM appointed for feasibility studies on the Inga dam."),
    sig("South Africa discusses Inga electricity imports", "2026-06-02", "",
        resume="Eskom and the DRC discuss power from the Inga hydropower project."),
]

# --- Chronologie TANZANIA LNG (projet mature mais ENLISE) ------------------
TANZ = [
    sig("Tanzania LNG Plant Production to Last 40+ Years", "2016-09-01", ""),
    # Ce titre ne contient AUCUN alias du projet ("Tanzania and Shell sign...").
    # En production il arrive d'une requete CIBLEE sur le projet : le collecteur
    # connait donc deja le PROJECT_ID et le porte sur le signal. C'est le chemin
    # nominal ; le rattachement textuel n'est qu'un filet pour les signaux
    # venus d'ailleurs (voir TestLimiteDuRattachementTextuel).
    sig("Tanzania and Shell sign host government agreement", "2023-05-22",
        "GOVERNMENT_AGREEMENT", resume="Shell, Equinor and TPDC sign the HGA.",
        project_id="TANZLNG_TZA"),
    sig("Tanzania LNG project talks drag on", "2024-03-11", "",
        resume="Negotiations on the Lindi LNG project stall again."),
    sig("Impairment at Tanzania LNG Project", "2024-11-05", "",
        resume="Equinor books an impairment on the Tanzania LNG project."),
]


class TestIdentificationEtRegroupement(unittest.TestCase):
    """1, 2 : identifier les projets et regrouper leurs evenements."""

    def test_les_deux_projets_sont_identifies(self):
        ps = pj.construire_projets(INGA + TANZ, aujourd=AUJ)
        ids = {p["project_id"] for p in ps}
        self.assertEqual(ids, {"INGA3_COD", "TANZLNG_TZA"})

    def test_les_cinq_articles_inga_sont_un_seul_projet(self):
        ps = pj.construire_projets(INGA, aujourd=AUJ)
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0]["nb_signaux"], 5)

    def test_aecom_inga_studies_est_bien_rattache(self):
        # L'article pivot : alias FAIBLE "inga" + contexte "studies".
        self.assertEqual(pj.rattacher(INGA[3]), "INGA3_COD")

    def test_un_signal_hors_sujet_est_ignore(self):
        ps = pj.construire_projets(
            [sig("Manchester United beat Arsenal", "2026-08-01")], aujourd=AUJ)
        self.assertEqual(ps, [])

    def test_projet_different_ne_se_melange_pas(self):
        self.assertEqual(pj.rattacher(TANZ[3]), "TANZLNG_TZA")   # alias present
        self.assertEqual(pj.rattacher(INGA[1]), "INGA3_COD")


class TestLimiteDuRattachementTextuel(unittest.TestCase):
    """LIMITE ASSUMEE, verrouillee ici pour qu'elle ne soit pas oubliee.

    Un signal qui ne cite AUCUN alias du projet n'est pas rattachable par le
    texte seul. Exemple reel : "Tanzania and Shell sign host government
    agreement" ne contient ni "Tanzania LNG" ni "Lindi LNG". Deux consequences
    operationnelles :
      - les collecteurs qui interrogent un projet NOMMEMENT doivent porter le
        `project_id` sur les signaux qu'ils rapportent (chemin nominal) ;
      - elargir les alias faibles ("tanzania" + "gas") rattacherait n'importe
        quel article gazier tanzanien : ce serait pire que la lacune.
    """

    def test_sans_alias_ni_project_id_pas_de_rattachement(self):
        orphelin = sig("Tanzania and Shell sign host government agreement",
                       "2023-05-22", "GOVERNMENT_AGREEMENT",
                       resume="Shell, Equinor and TPDC sign the HGA.")
        self.assertEqual(pj.rattacher(orphelin), "")

    def test_project_id_fourni_fait_autorite(self):
        porte = dict(sig("Titre sans aucun alias", "2026-01-01", "FID"),
                     project_id="TANZLNG_TZA")
        self.assertEqual(pj.rattacher(porte), "TANZLNG_TZA")


class TestPhaseEtHistorique(unittest.TestCase):
    """5 : determiner la phase, sans jamais ecraser l'historique."""

    def test_phase_courante_inga_est_la_plus_recente(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        self.assertEqual(p["phase_courante"], "CONSULTANT_SELECTION")

    def test_historique_complet_et_chronologique(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        phases = [h["phase"] for h in p["historique"]]
        self.assertEqual(phases, ["POLITICAL_ANNOUNCEMENT", "FUNDING_APPROVED",
                                  "MOU", "CONSULTANT_SELECTION"])
        dates = [h["date"] for h in p["historique"]]
        self.assertEqual(dates, sorted(dates))

    def test_phase_max_atteinte_conservee(self):
        # MOU (rang 6) < CONSULTANT_SELECTION (rang 9) : le max est bien le
        # dernier, mais la memoire du max doit exister explicitement.
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        self.assertEqual(p["phase_max_atteinte"], "CONSULTANT_SELECTION")
        self.assertFalse(p["recul"])

    def test_premiere_detection_et_derniere_maj(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        self.assertEqual(p["premiere_detection"], "2025-02-10")
        self.assertEqual(p["derniere_maj"], "2026-06-02")


class TestTimeline(unittest.TestCase):
    """3 : construire la timeline."""

    def test_timeline_par_annee(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        tl = pj.timeline(p)
        self.assertEqual([b["annee"] for b in tl], ["2025", "2026"])
        self.assertEqual(len(tl[0]["evenements"]), 2)   # 2025 : loi + World Bank
        self.assertEqual(len(tl[1]["evenements"]), 2)   # 2026 : AFD + AECOM


class TestEntreprises(unittest.TestCase):
    """4 : identifier les entreprises impliquees."""

    def test_acteurs_inga_detectes(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        for attendu in ("world bank", "afd", "aecom", "eskom"):
            self.assertIn(attendu, p["acteurs_top"], attendu)

    def test_acteurs_tanzanie_detectes(self):
        p = pj.construire_projets(TANZ, aujourd=AUJ)[0]
        for attendu in ("shell", "equinor", "tpdc"):
            self.assertIn(attendu, p["acteurs_top"], attendu)


class TestMaturite(unittest.TestCase):
    """6 : calculer la maturite (independante d'Amarante)."""

    def test_inga_est_un_projet_structure_ou_avance(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        self.assertGreaterEqual(p["maturite"], 55)
        self.assertLessEqual(p["maturite"], 80)

    def test_projet_dormant_est_penalise(self):
        # Tanzania LNG : dernier signal fin 2024, donc silence > 18 mois.
        p = pj.construire_projets(TANZ, aujourd=AUJ)[0]
        seul_phase = pj.PHASES["GOVERNMENT_AGREEMENT"]["maturite"]
        self.assertLess(p["maturite"], seul_phase)

    def test_paliers(self):
        self.assertEqual(pj.palier_maturite(15), "idée")
        self.assertEqual(pj.palier_maturite(85), "pré-FID / FID")


class TestOpportuniteAmarante(unittest.TestCase):
    """7 : calculer le potentiel Amarante, et surtout l'EXPLIQUER."""

    def test_score_est_explicable(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        op = p["opportunite"]
        self.assertGreater(op["score"], 0)
        self.assertTrue(op["motifs"])
        self.assertIn("Opportunité Amarante", op["phrase"])

    def test_motifs_citent_taille_et_risque_pays(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        texte = " ".join(p["opportunite"]["motifs"])
        self.assertIn("Md$", texte)              # taille du projet
        self.assertIn("risque", texte)           # risque pays

    def test_maturite_et_opportunite_sont_distinctes(self):
        # Tanzania LNG : projet ENORME (42 Md$) mais enlise. Sa maturite chute
        # a cause du silence, alors que sa taille garde une opportunite reelle.
        # Les deux scores ne doivent pas se confondre.
        p = pj.construire_projets(TANZ, aujourd=AUJ)[0]
        self.assertNotEqual(p["maturite"], p["opportunite"]["score"])

    def test_projet_dormant_penalise_dans_l_opportunite(self):
        # Silence de ~21 mois : penalite "dernier signal il y a X mois".
        # Au-dela de 36 mois le motif devient "dormant".
        p = pj.construire_projets(TANZ, aujourd=AUJ)[0]
        self.assertTrue(any("dernier signal" in m or "dormant" in m
                            for m in p["opportunite"]["motifs"]),
                        p["opportunite"]["motifs"])

    def test_motif_dormant_au_dela_de_trois_ans(self):
        vieux = [sig("Tanzania LNG project shelved", "2021-01-05",
                     "GOVERNMENT_AGREEMENT")]
        p = pj.construire_projets(vieux, aujourd=AUJ)[0]
        self.assertTrue(any("dormant" in m for m in p["opportunite"]["motifs"]))

    def test_inga_prioritaire_sur_tanzanie_car_actif(self):
        # Tri par opportunite : un projet vivant en pays rouge passe devant un
        # geant endormi en pays a risque modere.
        ps = pj.construire_projets(INGA + TANZ, aujourd=AUJ)
        self.assertEqual(ps[0]["project_id"], "INGA3_COD")


class TestProchainesEtapesEtProspects(unittest.TestCase):
    """8, 9 : prochaines etapes et prospects generes."""

    def test_prochaine_etape_inga(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        self.assertEqual(p["prochaine_etape"], "FEED")

    def test_fenetre_opportunite_qualitative(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        f = p["fenetre"]
        self.assertGreaterEqual(f["debut"], 2027)
        self.assertIn(f["confiance"], ("faible", "moyenne", "élevée"))

    def test_services_probables_energie(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        self.assertIn("journey management", p["services"])

    def test_prospects_issus_du_projet(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        prospects = pj.prospects(p)
        noms = {x["entreprise"].lower() for x in prospects}
        # Nom CANONIQUE restitue par la base d'acteurs, pas la graphie brute.
        self.assertIn("aecom", noms)
        self.assertNotIn("banque mondiale", noms)  # bailleur, pas un deployeur
        self.assertNotIn("world bank", noms)
        for x in prospects:
            self.assertTrue(x["besoin"])
            self.assertEqual(x["project_id"], "INGA3_COD")
            self.assertTrue(x["role"])             # role qualifie (P6)


class TestAlertes(unittest.TestCase):
    """Section 13 : early warning."""

    def test_alerte_haute_sur_fid(self):
        s = INGA + [sig("Inga 3 reaches final investment decision", "2026-08-01",
                        "FID")]
        p = pj.construire_projets(s, aujourd=AUJ)[0]
        self.assertEqual(p["alerte"], "haute")

    def test_alerte_moyenne_sur_consultant(self):
        p = pj.construire_projets(INGA, aujourd=AUJ)[0]
        self.assertEqual(p["alerte"], "moyenne")

    def test_pas_d_alerte_si_evenement_trop_ancien(self):
        p = pj.construire_projets(TANZ, aujourd=AUJ)[0]
        self.assertEqual(p["alerte"], "aucune")


class TestReculDePhase(unittest.TestCase):
    """Un projet peut RECULER : le systeme doit le voir, pas le masquer."""

    def test_recul_detecte(self):
        s = [sig("Projet Inga 3 atteint la FID", "2026-01-10", "FID"),
             sig("Inga 3 : retour aux etudes de faisabilite", "2026-05-10",
                 "FEASIBILITY")]
        p = pj.construire_projets(s, aujourd=AUJ)[0]
        self.assertEqual(p["phase_courante"], "FEASIBILITY")
        self.assertEqual(p["phase_max_atteinte"], "FID")
        self.assertTrue(p["recul"])


class TestRegistre(unittest.TestCase):

    def test_registre_normalise(self):
        for p in ref.charger_registre():
            for champ in ("project_id", "libelle", "iso3", "secteur",
                          "alias", "alias_faibles", "acteurs", "valeur_musd"):
                self.assertIn(champ, p, p.get("project_id"))
            self.assertNotIn("valuer_musd", p)      # faute de frappe filtree

    def test_ids_uniques(self):
        ids = [p["project_id"] for p in ref.charger_registre()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_projet_par_id(self):
        self.assertIsNotNone(ref.projet_par_id("INGA3_COD"))
        self.assertIsNone(ref.projet_par_id("INEXISTANT"))

    def test_registre_injectable(self):
        faux = [{"project_id": "X", "libelle": "X", "iso3": "MLI",
                 "alias": ["projet x"]}]
        self.assertEqual(pj.rattacher({"titre": "Le projet X avance"}, faux), "X")


if __name__ == "__main__":
    unittest.main()


class TestPhaseDeReference(unittest.TestCase):
    """DOCTRINE DU 24/08/2026, constatee sur un run reel. EACOP, en
    construction depuis 2023 et acheve a 88 %, etait affiche "Protocole
    d'accord", maturite 48 au lieu de 95, alerte "signal precoce", prochaine
    etape "Recherche de financement" et fenetre 2029-2031 -- parce qu'un
    article recent mentionnait un MoU avec un partenaire logistique.

    La phase COURANTE reste chronologique (elle revele l'enlisement), mais
    tout ce qui mesure le CHEMIN PARCOURU se fonde sur la phase la plus
    avancee atteinte."""

    SIGNAUX = [
        {"titre": "Inga 3 construction begins", "date": "2023-04-01",
         "lien": "http://a", "phase": "CONSTRUCTION"},
        {"titre": "Inga 3 pipeline 88% complete", "date": "2026-08-12",
         "lien": "http://b", "phase": "CONSTRUCTION"},
        {"titre": "Inga 3 signs MoU with logistics partner", "date": "2026-08-20",
         "lien": "http://c", "phase": "MOU"},
    ]

    def setUp(self):
        self.p = pj.construire_projets(self.SIGNAUX, aujourd=AUJ)[0]

    def test_phase_courante_reste_chronologique(self):
        """Le recul doit rester VISIBLE : c'est le signal d'un probleme."""
        self.assertEqual(self.p["phase_courante"], "MOU")
        self.assertEqual(self.p["phase_max_atteinte"], "CONSTRUCTION")
        self.assertTrue(self.p["recul"])

    def test_maturite_sur_la_phase_la_plus_avancee(self):
        self.assertEqual(self.p["maturite"], pj.PHASES["CONSTRUCTION"]["maturite"])

    def test_alerte_haute_pour_un_chantier(self):
        self.assertEqual(self.p["alerte"], "haute")

    def test_prochaine_etape_coherente(self):
        self.assertEqual(self.p["prochaine_etape"], "Mise en service")

    def test_fenetre_immediate_et_non_lointaine(self):
        self.assertLessEqual(self.p["fenetre"]["debut"], AUJ.year + 1)

    def test_services_de_phase_chaude(self):
        self.assertIn("support 24/7", self.p["services"])

    def test_opportunite_reflete_la_mobilisation(self):
        texte = " ".join(self.p["opportunite"]["motifs"])
        self.assertIn("mobilisation imminente", texte)

    def test_repli_si_pas_de_phase_max(self):
        """Les appelants qui construisent un projet a la main (shadow run)
        n'ont pas toujours phase_max_atteinte."""
        self.assertEqual(pj.phase_de_reference({"phase_courante": "FID"}), "FID")
        self.assertEqual(pj.phase_de_reference({}), "")

    def test_un_projet_dormant_reste_penalise(self):
        """Le correctif ne doit pas ressusciter un projet muet depuis des ans."""
        vieux = [{"titre": "Tanzania LNG construction begins", "date": "2021-01-05",
                  "lien": "http://x", "phase": "CONSTRUCTION"}]
        q = pj.construire_projets(vieux, aujourd=AUJ)[0]
        self.assertEqual(q["alerte"], "aucune")
        self.assertLess(q["maturite"], pj.PHASES["CONSTRUCTION"]["maturite"])
