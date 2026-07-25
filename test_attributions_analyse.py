# -*- coding: utf-8 -*-
"""Analyse des attributions par le modele.

POURQUOI CE MODULE EXISTE (23/07/2026)
--------------------------------------
Les quatre collecteurs d'attributions ecrivaient 13 colonnes brutes, sans
jamais appeler le modele. Dans le dashboard, `attribution_vers_lead` renvoyait
donc le MEME chiffre pour surete, commercial et final (un calcul deterministe
zone + secteur + valeur), une justification en gabarit fixe identique pour
toutes les lignes, et "n.c." partout ou l'enrichissement francais ne matchait
pas. L'onglet Titulaires etait un annuaire, pas une liste de prospection.

`attributions_analyse.py` produit une vraie analyse dans une TABLE SEPAREE,
jointe sur `publication_number`. Table separee et non colonnes ajoutees :
l'onglet source est partage par quatre collecteurs, lu positionnellement, et
suivi de deux colonnes de saisie humaine -- y inserer des colonnes
desalignerait toutes les lignes existantes (l'incident bm_radar).

Ce fichier verrouille les proprietes qui comptent :
  - le scoring produit DEUX axes distincts, pas un chiffre recopie ;
  - une fourniture pure ne peut pas passer pour un prospect ;
  - un titulaire etranger prime sur un titulaire local a chantier egal ;
  - la lecture de l'onglet source est POSITIONNELLE (regle 4) ;
  - la memoire precede le plafond de budget (invariant partage avec les avis) ;
  - la normalisation ne laisse passer aucune valeur hors vocabulaire.

Aucun appel reseau ni LLM : `ted.appeler_modele` est remplace par une reponse
figee.
"""

import json
import unittest

import ted_complet_v14 as ted
import attributions_analyse as aa


ATTRIB = {
    "gagnant": "Yapi Merkezi Insaat", "pays_execution": "MLI",
    "acheteur": "Banque Mondiale",
    "titre": "Construction de la route Bamako-Segou, lot 2",
    "secteur": "Travaux", "valeur_attribuee": "USD 42 000 000",
    "cpv": "45233120", "sous_traitance": "partielle",
    "publication_number": "BM-1", "date_publication": "2026-07-10",
}


def _extraction(**surcharge):
    base = {
        "pays_origine_titulaire": "Turquie", "titulaire_etranger": True,
        "nature_deploiement": "expatrie_significatif",
        "profils_deployes": "ingenieurs et chefs de chantier",
        "duree_chantier": "longue_ou_residente",
        "exposition_terrain": "site_isole", "besoin_surete_probable": "fort",
        "interlocuteur_vise": "directeur des operations Afrique de l'Ouest",
        "justification": "Base vie isolee sur l'axe Bamako-Segou.",
        "confiance": 0.85,
    }
    base.update(surcharge)
    return aa.normaliser(base)


class _Modele:
    """Remplace ted.appeler_modele par une reponse figee, et la restaure."""

    def __init__(self, reponse):
        self.reponse = reponse

    def __enter__(self):
        self._avant = ted.appeler_modele
        ted.appeler_modele = lambda prompt, modele=None: self.reponse
        return self

    def __exit__(self, *a):
        ted.appeler_modele = self._avant
        return False


# ===========================================================================
# PROMPT
# ===========================================================================

class TestPrompt(unittest.TestCase):

    def test_le_marche_est_pose_comme_deja_attribue(self):
        """Tout le cadrage repose la-dessus : la question n'est plus si le
        marche existe, mais qui va etre expose."""
        p = aa.construire_prompt(ATTRIB)
        self.assertIn("DEJA ATTRIBUE", p)
        self.assertIn("Yapi Merkezi Insaat", p)
        self.assertIn("Bamako-Segou", p)

    def test_les_champs_manquants_ont_un_repli_lisible(self):
        """Une attribution sans gagnant nomme ne doit pas injecter 'None' dans
        le prompt : le repli remplace chaque champ vide par un libelle lisible."""
        p = aa.construire_prompt({})           # tout est absent
        self.assertIn("titulaire non nomme", p)
        self.assertIn("non precise", p)
        self.assertNotIn("None", p)

    def test_les_quatre_vocabulaires_sont_demandes(self):
        p = aa.construire_prompt(ATTRIB)
        for valeur in ("expatrie_significatif", "aucun_deploiement",
                       "site_isole", "longue_ou_residente"):
            self.assertIn(valeur, p)


# ===========================================================================
# NORMALISATION
# ===========================================================================

class TestNormalisation(unittest.TestCase):

    def test_valeurs_hors_vocabulaire_repliees(self):
        e = aa.normaliser({"nature_deploiement": "beaucoup de gens",
                           "duree_chantier": "un certain temps",
                           "exposition_terrain": "dehors",
                           "besoin_surete_probable": "enorme"})
        self.assertEqual(e["nature_deploiement"], "inconnue")
        self.assertEqual(e["duree_chantier"], "inconnue")
        self.assertEqual(e["exposition_terrain"], "inconnue")
        self.assertEqual(e["besoin_surete_probable"], "inconnu")

    def test_casse_et_espaces_toleres(self):
        e = aa.normaliser({"nature_deploiement": "  Expatrie_Significatif "})
        self.assertEqual(e["nature_deploiement"], "expatrie_significatif")

    def test_etranger_force_en_booleen(self):
        self.assertIs(aa.normaliser({"titulaire_etranger": "true"})
                      ["titulaire_etranger"], True)
        self.assertIs(aa.normaliser({"titulaire_etranger": ""})
                      ["titulaire_etranger"], False)

    def test_confiance_bornee(self):
        self.assertEqual(aa.normaliser({"confiance": 5})["confiance"], 1.0)
        self.assertEqual(aa.normaliser({"confiance": -1})["confiance"], 0.0)
        self.assertEqual(aa.normaliser({"confiance": "abc"})["confiance"], 0.5)

    def test_entrees_degenerees(self):
        self.assertIsNone(aa.normaliser(None))
        self.assertIsNone(aa.normaliser("texte"))


# ===========================================================================
# SCORING : DEUX AXES, PAS UN CHIFFRE RECOPIE
# ===========================================================================

class TestScoring(unittest.TestCase):

    def test_surete_et_commercial_different(self):
        """LE defaut corrige : le dashboard renvoyait surete == commercial ==
        final. Ici les deux axes mesurent des choses distinctes et doivent
        differer sur un cas realiste."""
        s, c, f = aa.calculer_scores(ATTRIB, _extraction())
        self.assertNotEqual(s, c)

    def test_titulaire_etranger_prime_sur_local(self):
        """A chantier identique, seule l'origine change. L'entreprise etrangere
        expatrie du personnel : c'est le meilleur prospect."""
        etranger = aa.calculer_scores(ATTRIB, _extraction(
            titulaire_etranger=True, nature_deploiement="expatrie_significatif"))
        local = aa.calculer_scores(ATTRIB, _extraction(
            titulaire_etranger=False, nature_deploiement="local_uniquement"))
        self.assertGreater(etranger[2], local[2])

    def test_fourniture_pure_n_est_pas_un_prospect(self):
        """Garde-fou central : 'aucun_deploiement' = personne d'expose. La
        surete tombe a zero et le commercial est plafonne, quel que soit le
        montant du marche."""
        _s, _c, f = aa.calculer_scores(ATTRIB, _extraction(
            nature_deploiement="aucun_deploiement"))
        s, c, _f = aa.calculer_scores(ATTRIB, _extraction(
            nature_deploiement="aucun_deploiement"))
        self.assertEqual(s, 0.0)
        self.assertLessEqual(c, 2.0)
        self.assertEqual(aa.action_recommandee(f, _extraction(
            nature_deploiement="aucun_deploiement")), "ignorer")

    def test_zone_a_risque_pese(self):
        """Meme titulaire, meme deploiement : un chantier au Mali doit scorer
        en surete au-dessus d'un chantier dans un pays calme."""
        risque = aa.calculer_scores({**ATTRIB, "pays_execution": "MLI"},
                                    _extraction())
        calme = aa.calculer_scores({**ATTRIB, "pays_execution": "FRA"},
                                   _extraction())
        self.assertGreater(risque[0], calme[0])

    def test_confiance_basse_tempere(self):
        fort = aa.calculer_scores(ATTRIB, _extraction(confiance=0.9))
        faible = aa.calculer_scores(ATTRIB, _extraction(confiance=0.2))
        self.assertLess(faible[2], fort[2])

    def test_scores_bornes_dix(self):
        s, c, f = aa.calculer_scores(ATTRIB, _extraction())
        for v in (s, c, f):
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 10.0)

    def test_extraction_absente_score_nul(self):
        self.assertEqual(aa.calculer_scores(ATTRIB, None), (0.0, 0.0, 0.0))


class TestPoidsValeur(unittest.TestCase):

    def test_gros_marche_pese_plus(self):
        self.assertGreater(aa.poids_valeur("USD 42 000 000"),
                           aa.poids_valeur("USD 800 000"))

    def test_montant_illisible_neutre(self):
        """Ni prime ni peine, meme prudence que les dates ailleurs."""
        self.assertEqual(aa.poids_valeur("montant n.c."), 0.35)
        self.assertEqual(aa.poids_valeur(""), 0.35)

    def test_espaces_insecables_geres(self):
        self.assertGreater(aa.poids_valeur("USD 42\u00a0000\u00a0000"), 0.8)


# ===========================================================================
# ACTION RECOMMANDEE
# ===========================================================================

class TestAction(unittest.TestCase):

    def test_local_toujours_ignore(self):
        self.assertEqual(
            aa.action_recommandee(9.0, _extraction(
                nature_deploiement="local_uniquement")), "ignorer")

    def test_fort_besoin_et_bon_score_contacter(self):
        e = _extraction(besoin_surete_probable="fort")
        _s, _c, f = aa.calculer_scores(ATTRIB, e)
        self.assertEqual(aa.action_recommandee(f, e), "contacter")

    def test_score_moyen_surveiller(self):
        e = _extraction(nature_deploiement="mixte", exposition_terrain="bureau",
                        besoin_surete_probable="faible")
        self.assertEqual(aa.action_recommandee(4.0, e), "surveiller")


# ===========================================================================
# PRIORISATION : AVANT LE PLAFOND
# ===========================================================================

class TestPriorisation(unittest.TestCase):

    def test_zone_a_risque_avant_zone_calme(self):
        attribs = [
            {"pays_execution": "FRA", "valeur_attribuee": "USD 50 000 000"},
            {"pays_execution": "MLI", "valeur_attribuee": "USD 1 000 000"},
        ]
        ordre = [a["pays_execution"] for a in aa.prioriser(attribs)]
        self.assertEqual(ordre[0], "MLI")

    def test_a_zone_egale_le_gros_marche_devant(self):
        attribs = [
            {"pays_execution": "MLI", "valeur_attribuee": "USD 500 000"},
            {"pays_execution": "MLI", "valeur_attribuee": "USD 40 000 000"},
        ]
        ordre = [a["valeur_attribuee"] for a in aa.prioriser(attribs)]
        self.assertIn("40", ordre[0])

    def test_priorisation_ne_jette_rien(self):
        attribs = [{"pays_execution": p} for p in ("MLI", "FRA", "AFG")]
        self.assertEqual(len(aa.prioriser(attribs)), 3)


# ===========================================================================
# LECTURE POSITIONNELLE DE L'ONGLET SOURCE (regle 4)
# ===========================================================================

class _FeuilleSource:
    def __init__(self, entete, lignes):
        self.grille = [list(entete)] + [list(l) for l in lignes]

    def get_all_values(self):
        return [list(r) for r in self.grille]


def _ligne_source(entete, **valeurs):
    import bm_attributions as bma
    d = {c: "" for c in bma.COLONNES}
    d.update(valeurs)
    return [d[c] for c in bma.COLONNES]


class TestLecturePositionnelle(unittest.TestCase):

    def setUp(self):
        import bm_attributions as bma
        self.entete = list(bma.COLONNES)

    def test_lecture_nominale(self):
        src = _FeuilleSource(self.entete, [
            _ligne_source(self.entete, publication_number="P1",
                          gagnant="Yapi Merkezi", pays_execution="MLI")])
        attribs = aa.lire_attributions(src)
        self.assertEqual(len(attribs), 1)
        self.assertEqual(attribs[0]["gagnant"], "Yapi Merkezi")

    def test_entete_desaligne_ne_fausse_pas_les_champs(self):
        """L'incident bm_radar : en-tete decale d'une colonne. En lecture
        positionnelle, le schema fait foi et 'gagnant' reste 'gagnant'."""
        entete_faux = ["parasite"] + self.entete[:-1]
        src = _FeuilleSource(entete_faux, [
            _ligne_source(self.entete, publication_number="P1",
                          gagnant="Sinohydro", pays_execution="AFG")])
        attribs = aa.lire_attributions(src)
        self.assertEqual(attribs[0]["gagnant"], "Sinohydro")
        self.assertEqual(attribs[0]["pays_execution"], "AFG")

    def test_lignes_sans_identifiant_ignorees(self):
        src = _FeuilleSource(self.entete, [
            _ligne_source(self.entete, publication_number="", gagnant="X"),
            _ligne_source(self.entete, publication_number="P2", gagnant="Y")])
        self.assertEqual([a["publication_number"]
                          for a in aa.lire_attributions(src)], ["P2"])

    def test_onglet_vide(self):
        self.assertEqual(aa.lire_attributions(_FeuilleSource(self.entete, [])), [])


# ===========================================================================
# LIGNE PRODUITE
# ===========================================================================

class TestLignePourSheet(unittest.TestCase):

    def test_longueur_conforme_au_schema(self):
        ligne = aa.ligne_pour_sheet(ATTRIB, _extraction(),
                                    aa.calculer_scores(ATTRIB, _extraction()))
        self.assertEqual(len(ligne), len(aa.COLONNES))

    def test_publication_number_en_tete(self):
        self.assertEqual(aa.COLONNES[0], "publication_number")
        ligne = aa.ligne_pour_sheet(ATTRIB, _extraction(),
                                    aa.calculer_scores(ATTRIB, _extraction()))
        self.assertEqual(ligne[0], "BM-1")

    def test_les_trois_scores_sont_distincts_dans_la_ligne(self):
        """La preuve, au niveau de la ligne ecrite, que le defaut d'origine
        (trois fois le meme chiffre) est corrige."""
        ligne = aa.ligne_pour_sheet(ATTRIB, _extraction(),
                                    aa.calculer_scores(ATTRIB, _extraction()))
        i = aa.COLONNES.index
        self.assertNotEqual(ligne[i("score_surete")],
                            ligne[i("score_commercial")])

    def test_schema_disjoint_de_l_onglet_source(self):
        """Table SEPAREE : les seuls champs communs avec l'onglet source sont
        la clef de jointure et le drapeau `titulaire_etranger`, que le socle
        deterministe (chantier B) pose a la collecte et que l'analyse LLM
        affine ensuite. Tout le reste de l'analyse (scores, interlocuteur,
        nature du deploiement) est propre a la table d'analyse : si ce
        recouvrement grossissait, l'interet de la table separee disparaitrait."""
        import bm_attributions as bma
        communs = set(aa.COLONNES) & set(bma.COLONNES)
        self.assertEqual(communs, {"publication_number", "titulaire_etranger"})


# ===========================================================================
# ANALYSE DE BOUT EN BOUT (sans reseau)
# ===========================================================================

class TestAnalyseComplete(unittest.TestCase):

    REPONSE = json.dumps({
        "pays_origine_titulaire": "Turquie", "titulaire_etranger": True,
        "nature_deploiement": "expatrie_significatif",
        "profils_deployes": "ingenieurs et chefs de chantier en base vie",
        "duree_chantier": "longue_ou_residente",
        "exposition_terrain": "site_isole", "besoin_surete_probable": "fort",
        "interlocuteur_vise": "directeur des operations Afrique de l'Ouest",
        "justification": "Rotation d'expatries sur 30 mois sur l'axe routier.",
        "confiance": 0.85})

    def test_analyse_json_direct(self):
        with _Modele(self.REPONSE):
            e = aa.analyser_une(ATTRIB)
        self.assertEqual(e["pays_origine_titulaire"], "Turquie")
        self.assertTrue(e["titulaire_etranger"])

    def test_analyse_json_entoure_de_texte(self):
        with _Modele("Voici :\n" + self.REPONSE + "\nVoila."):
            e = aa.analyser_une(ATTRIB)
        self.assertEqual(e["nature_deploiement"], "expatrie_significatif")

    def test_modele_muet_renvoie_none(self):
        with _Modele(None):
            self.assertIsNone(aa.analyser_une(ATTRIB))

    def test_de_l_attribution_a_la_ligne_ecrite(self):
        with _Modele(self.REPONSE):
            e = aa.analyser_une(ATTRIB)
        scores = aa.calculer_scores(ATTRIB, e)
        ligne = aa.ligne_pour_sheet(ATTRIB, e, scores)
        i = aa.COLONNES.index
        self.assertEqual(ligne[i("action_recommandee")], "contacter")
        self.assertEqual(ligne[i("pays_origine_titulaire")], "Turquie")
        self.assertTrue(float(ligne[i("score_final")]) > 8.0)


if __name__ == "__main__":
    unittest.main()
