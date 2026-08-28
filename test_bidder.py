# -*- coding: utf-8 -*-
"""P2.6 — Bidder Intelligence : qui va soumissionner (26/08/2026).

CE QUE LA v1 CONFONDAIT
-----------------------
`_pertinence` additionnait la fréquence ET un bonus de +2 aux titulaires
ÉTRANGERS, sous le nom de « candidats probables ». Ce sont DEUX questions :

    « qui va soumissionner ? »   -> une prévision, fondée sur l'historique
    « qui intéresse Amarante ? » -> une préférence commerciale

Être étranger ne rend pas une entreprise plus susceptible de soumissionner.
Cela la rend plus intéressante À DÉMARCHER, ce qui est autre chose. Mélangés,
les deux produisent un classement qui se présente comme une prévision sans en
être une : vérifié le 26/08, une étrangère à 3 marchés passait devant une
locale à 5.

Second défaut, plus discret : la récence ne servait qu'à départager les ex
æquo. Un titulaire de 2019 avec 4 marchés devançait un titulaire de 2026 avec
3, chez le même acheteur.

CE QUE JE N'AI PAS AJOUTÉ
-------------------------
L'audit externe proposait consortiums, implantation locale, préqualification,
CPV. Aucun de ces champs n'est collecté. Les inventer donnerait une
probabilité d'apparence savante et sans fondement. Le module n'utilise QUE ce
qui est en base : acheteur, secteur, théâtre, récence, ordre de grandeur.

Tests OFFLINE : fonctions pures et contrats du gabarit, aucun réseau.
"""

import datetime
import re
import unittest

import candidats_probables as cd
import radar_cockpit as ck


AUJ = datetime.date(2026, 8, 26)


def _attrib(ent, sect="BTP / Construction", zone="Sahel",
            agence="Ministere Tchad", mois="2026-05", val=10.0,
            etr=False, orig=""):
    return {"src": "ATTRIB", "entreprise": ent, "sect": sect, "zone": zone,
            "agence": agence, "mois": mois, "valeur_meur": val,
            "etranger_titulaire": etr, "origine": orig}


AVIS = {"sect": "BTP / Construction", "zone": "Sahel",
        "agence": "Ministere Tchad", "valeur_meur": 10.0}

# Le cas qui démonte la v1 : une locale ACTIVE contre une étrangère DORMANTE.
CORPUS = [
    _attrib("CRBC", mois="2026-05", etr=True, orig="China"),
    _attrib("CRBC", mois="2025-11", etr=True, orig="China"),
    _attrib("LOCALE SA", mois="2026-06", orig="Tchad"),
    _attrib("SOGEA", mois="2019-03", agence="BAD", orig="France"),
    _attrib("SOGEA", mois="2018-07", agence="BAD", orig="France"),
    _attrib("SOGEA", mois="2019-09", agence="BAD", orig="France"),
    _attrib("SOGEA", mois="2018-01", agence="BAD", orig="France"),
]


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


class TestSeparationDesDeuxQuestions(unittest.TestCase):
    """LE cœur du chantier."""

    def test_etre_etranger_n_augmente_pas_la_probabilite(self):
        """Une nationalité étrangère est un intérêt commercial, pas un indice
        de participation à un appel d'offres."""
        h = cd.historique_titulaires(CORPUS, AUJ)
        local = dict(h["LOCALE SA"])
        etranger = dict(local, etranger=True, entreprise="X")
        self.assertEqual(cd.probabilite_participation(AVIS, local, AUJ)[0],
                         cd.probabilite_participation(AVIS, etranger, AUJ)[0])

    def test_etre_etranger_augmente_l_interet(self):
        h = cd.historique_titulaires(CORPUS, AUJ)
        self.assertGreater(cd.interet_amarante(h["CRBC"])[0],
                           cd.interet_amarante(h["LOCALE SA"])[0])

    def test_le_tri_suit_la_probabilite_pas_l_interet(self):
        """La question posée est « qui va soumissionner ». Trier par intérêt
        répondrait à une autre question sous le même titre."""
        r = cd.soumissionnaires_probables(AVIS, CORPUS, aujourd=AUJ)
        self.assertEqual([c["probabilite"] for c in r],
                         sorted([c["probabilite"] for c in r], reverse=True))

    def test_les_deux_scores_sont_rendus_separement(self):
        for c in cd.soumissionnaires_probables(AVIS, CORPUS, aujourd=AUJ):
            self.assertIn("probabilite", c)
            self.assertIn("interet", c)
            self.assertTrue(c["motifs"])
            self.assertTrue(c["motifs_interet"])


class TestRecence(unittest.TestCase):
    """Second défaut de la v1 : la récence ne départageait que les ex æquo."""

    def test_un_titulaire_dormant_est_declasse(self):
        r = {c["entreprise"]: c["probabilite"]
             for c in cd.soumissionnaires_probables(AVIS, CORPUS, aujourd=AUJ)}
        self.assertGreater(r["LOCALE SA"], r.get("SOGEA", 0))

    def test_la_v1_classait_l_inverse(self):
        """Témoin du défaut. SOGEA (4 marchés, tous 2018-2019, plus rien
        depuis 7 ans) passait devant LOCALE SA, active cette année chez le
        même acheteur."""
        idx = cd.construire_index(CORPUS)
        ordre = [c["entreprise"]
                 for c in cd.candidats_pour("BTP / Construction", "Sahel", idx)]
        self.assertLess(ordre.index("SOGEA"), ordre.index("LOCALE SA"))

    def test_penalite_au_dela_de_la_memoire(self):
        h = cd.historique_titulaires(CORPUS, AUJ)
        _, motifs = cd.probabilite_participation(AVIS, h["SOGEA"], AUJ)
        self.assertTrue(any("depuis plus de" in m for m in motifs))

    def test_date_illisible_ne_leve_pas(self):
        h = dict(cd.historique_titulaires(CORPUS, AUJ)["CRBC"], derniere="zzz")
        note, motifs = cd.probabilite_participation(AVIS, h, AUJ)
        self.assertTrue(any("non datée" in m for m in motifs))

    def test_ecart_de_mois(self):
        self.assertEqual(cd._mois_ecart("2026-05", AUJ), 3)
        self.assertIsNone(cd._mois_ecart("", AUJ))
        self.assertIsNone(cd._mois_ecart("abc", AUJ))


class TestSignauxUtilises(unittest.TestCase):

    def test_l_acheteur_est_le_signal_le_plus_fort(self):
        """Une administration reconduit souvent un titulaire qu'elle connaît."""
        h = cd.historique_titulaires(CORPUS, AUJ)
        meme = cd.probabilite_participation(AVIS, h["LOCALE SA"], AUJ)[0]
        autre = cd.probabilite_participation(
            dict(AVIS, agence="Autre acheteur"), h["LOCALE SA"], AUJ)[0]
        self.assertGreater(meme, autre)

    def test_secteur_et_theatre_ensemble_valent_plus_que_separement(self):
        h = cd.historique_titulaires(CORPUS, AUJ)
        deux = cd.probabilite_participation(AVIS, h["CRBC"], AUJ)[0]
        un = cd.probabilite_participation(
            dict(AVIS, zone="Asie centrale"), h["CRBC"], AUJ)[0]
        self.assertGreater(deux, un)

    def test_chaque_score_est_justifie(self):
        h = cd.historique_titulaires(CORPUS, AUJ)
        for ent in h:
            self.assertTrue(cd.probabilite_participation(AVIS, h[ent], AUJ)[1],
                            ent)

    def test_probabilite_jamais_certaine(self):
        """Aucune prévision ne mérite 100 % : c'est une déduction, pas un fait."""
        h = cd.historique_titulaires(CORPUS, AUJ)
        for ent in h:
            self.assertLessEqual(
                cd.probabilite_participation(AVIS, h[ent], AUJ)[0], 95)

    def test_les_indices_faibles_sont_ecartes(self):
        """En dessous d'un seuil, ce n'est plus un indice, c'est du bruit."""
        for c in cd.soumissionnaires_probables(AVIS, CORPUS, aujourd=AUJ):
            self.assertGreaterEqual(c["probabilite"], 20)


class TestMontantsTolerants(unittest.TestCase):
    """MÊME PIÈGE QU'EN P2.3, sur un autre module : `valeur` est parfois une
    chaîne brute (« 10000000 EUR »). Le best-effort bruyant l'a attrapé cette
    fois, au lieu de rendre une liste vide en silence."""

    def test_chaine_brute_lue(self):
        self.assertEqual(cd._bande("10000000 EUR"), "10-50M")

    def test_deja_en_millions(self):
        self.assertEqual(cd._bande(10.0), "10-50M")

    def test_illisible_sans_bande_et_sans_erreur(self):
        for faux in ("n.c.", "", None, "abc"):
            self.assertEqual(cd._bande(faux), "")

    def test_une_seule_implementation_de_lecture(self):
        """Trois conversions divergentes seraient trois bugs à venir : ce
        module délègue à celui qui a déjà résolu le problème."""
        src = _lire("candidats_probables.py")
        self.assertIn("from opportunites import _nombre", src)


class TestNonRegressionV1(unittest.TestCase):
    """La v1 reste en place : d'autres appels en dépendent."""

    def test_construire_index_intact(self):
        idx = cd.construire_index(CORPUS)
        for cle in ("secteur_zone", "secteur", "zone"):
            self.assertIn(cle, idx)

    def test_candidats_pour_intact(self):
        idx = cd.construire_index(CORPUS)
        self.assertTrue(cd.candidats_pour("BTP / Construction", "Sahel", idx))

    def test_corpus_vide(self):
        self.assertEqual(cd.historique_titulaires([], AUJ), {})
        self.assertEqual(cd.soumissionnaires_probables(AVIS, [], aujourd=AUJ), [])

    def test_avis_sans_rien(self):
        self.assertIsInstance(
            cd.soumissionnaires_probables({}, CORPUS, aujourd=AUJ), list)


class TestAffichage(unittest.TestCase):

    def setUp(self):
        avis = {"src": "TED", "pays": "Tchad", "zone": "Sahel",
                "titre": "AO route", "agence": "Ministere Tchad",
                "final": 7.0, "surete": 7, "comm": 7, "action": "contacter",
                "win": "", "nom": "n.c.", "email": "n.c.", "tel": "n.c.",
                "cible": "", "justif": "", "grp": "AT", "lien": "",
                "ecart": False, "secu": False, "mois": "2026-08",
                "mois_label": "a", "date_det": "2026-08-01",
                "statut": "nouveau", "motif_ecart": "", "deadline": "",
                "conf": "", "modele": "", "pub": "T1", "projet_id": "",
                "valeur": "10000000 EUR", "enveloppe": "", "entreprise": "",
                "sect": "BTP / Construction"}
        leads = [avis] + [dict(a, action="surveiller",
                               pub="A" + a["entreprise"] + a["mois"],
                               titre="", pays="Tchad", final=6.0, surete=6,
                               comm=6, win="", nom="n.c.", email="n.c.",
                               tel="n.c.", cible="", justif="", grp="s",
                               lien="", ecart=False, secu=False,
                               mois_label="a", date_det="2026-08-01",
                               statut="nouveau", motif_ecart="", deadline="",
                               conf="", modele="", projet_id="",
                               enveloppe="", valeur="10000000 EUR")
                          for a in CORPUS]
        self.html = ck.generer_cockpit(leads, suivi={"api": True})

    def test_bloc_present(self):
        self.assertIn("function blocSoumissionnaires", self.html)
        self.assertIn("Soumissionnaires probables", self.html)

    def test_les_deux_scores_expliques_a_l_ecran(self):
        """Sans l'explication, le lecteur croira à un score unique."""
        self.assertIn("Les deux sont séparés", self.html)
        self.assertIn("chance de soumissionner", self.html)

    def test_calcule_pour_les_avis_a_contacter_seulement(self):
        """Le calculer pour tout le corpus alourdirait la page de centaines
        de listes que personne n'ouvrirait."""
        src = _lire("radar_cockpit.py")
        self.assertIn('l.get("action") == "contacter"', src)

    def test_degradation_bruyante(self):
        src = _lire("radar_cockpit.py")
        self.assertIn("ATTENTION : soumissionnaires non calcules", src)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])

    def test_chantiers_precedents_intacts(self):
        for attendu in ("renderAujourdhui", "function blocCompte",
                        "function blocOpportunite", "santeRun"):
            self.assertIn(attendu, self.html)


if __name__ == "__main__":
    unittest.main()
