# -*- coding: utf-8 -*-
"""Tests ReliefWeb : alignement sur l'enum securite, levier de deplacement
concurrentiel, et stabilite du schema Sheet.

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
ReliefWeb etait le seul collecteur reste sur l'ANCIEN champ booleen
`securite_existante_detectee`, alors que le coeur avait bascule sur l'enum a
quatre valeurs (aucune / interne_client / prestataire_tiers / inconnu). Deux
consequences, cumulatives :

  1. `PROMPT_RELIEFWEB` demandait un booleen : le modele ne pouvait donc PAS
     distinguer une securite interne (marche ferme) d'un prestataire tiers
     (concurrent en place, donc opportunite de conquete) ;
  2. `analyser_reliefweb` n'appelait jamais `ted.normaliser_securite`, si bien
     que la cle `securite_existante` n'existait meme pas dans l'extraction.

Comportement mesure AVANT correction, sur une offre mentionnant explicitement
"contracted security company" :

    securite signalee par le modele = True  -> score 5.0, action "ignorer"

Autrement dit : les offres ou un CONCURRENT est deja en place, c'est-a-dire
les meilleures cibles de conquete, etaient silencieusement jetees. C'est
l'inverse exact de la doctrine du projet ("logique commerciale, pas de
kill-switch").

APRES correction :

    prestataire_tiers -> score 7.5, action "contacter", [DEPLACEMENT CONCURRENT]
    interne_client    -> score 5.0, action "ignorer"        (marche ferme, ok)
    aucune            -> score 7.5, action "contacter"

Le schema du Sheet n'a PAS bouge : `COLONNES_RW` ne porte que le booleen
derive, exactement comme `COLONNES_SHEET`. Aucune migration d'onglet requise,
et c'est verifie ici (`TestSchemaSheetInchange`) parce qu'ajouter une colonne
au milieu d'un schema positionnel desalignerait toutes les lignes existantes.

Aucun appel reseau ni LLM : `ted.appeler_modele` est remplace par une reponse
figee.
"""

import json
import unittest

import ted_complet_v14 as ted
import ted_complet_reliefweb as rw


# Extraction de reference : une offre terrain qui merite d'etre poussee. Seule
# la valeur de `securite_existante` varie d'un test a l'autre, pour que le
# contraste isole bien la regle etudiee.
BASE = {
    "deploiement_terrain_reel": True,
    "type_mobilite": "terrain_isole",
    "profil_personnes_exposees": "expert_international",
    "type_client": "bailleur_donateur",
    "accessibilite_commerciale": "moyenne",
    "duree_estimee": "longue_ou_residente",
    "niveau_opportunite_amarante": "fort",
    "confiance": 0.8,
    "justification": "Deplacements terrain frequents, besoin d'escorte.",
}

AVIS = {
    "acheteur": "ONG Internationale",
    "organisation": "ONG Internationale",
    "pays_execution": "MLI",
    "titre": "Field Coordinator - Mopti",
    "categorie": "Job",
    "description": "Convoy movements secured by a contracted security company.",
}


class _ModeleFige:
    """Remplace `ted.appeler_modele` le temps d'un test. Compte les appels :
    la reparation JSON coute un appel Sonnet, on veut savoir si elle a eu
    lieu."""

    def __init__(self, reponse):
        self.reponse, self.appels = reponse, 0

    def __call__(self, prompt, modele=None):
        self.appels += 1
        return self.reponse


class _Bascule:
    """Installe un faux modele et le retire proprement, meme si le test echoue."""

    def __init__(self, reponse, reparation=None):
        self.faux = _ModeleFige(reponse)
        self.reparation = reparation

    def __enter__(self):
        self._modele, self._reparer = ted.appeler_modele, ted.reparer_json
        ted.appeler_modele = self.faux
        if self.reparation is not None:
            ted.reparer_json = lambda texte, modele=None: self.reparation
        return self.faux

    def __exit__(self, *a):
        ted.appeler_modele, ted.reparer_json = self._modele, self._reparer
        return False


def _analyser(enum=None, booleen=None, brut=None, reparation=None):
    """Fait tourner analyser_reliefweb sur une reponse modele controlee."""
    if brut is None:
        charge = dict(BASE)
        if enum is not None:
            charge["securite_existante"] = enum
        if booleen is not None:
            charge["securite_existante_detectee"] = booleen
        brut = json.dumps(charge)
    with _Bascule(brut, reparation=reparation):
        return rw.analyser_reliefweb(AVIS)


def _decision(extraction):
    """(score final, action recommandee) pour une extraction donnee."""
    surete, _commercial, final = ted.calculer_scores(
        rw.avis_pour_scoring_rw(AVIS), extraction)
    return final, ted.calculer_action_recommandee(final, extraction,
                                                  surete=surete)


# ===========================================================================
# LE PROMPT DEMANDE BIEN L'ENUM
# ===========================================================================

class TestPromptAligneSurLEnum(unittest.TestCase):

    def test_les_quatre_valeurs_sont_demandees(self):
        for valeur in ("aucune", "interne_client", "prestataire_tiers",
                       "inconnu"):
            self.assertIn(valeur, rw.PROMPT_RELIEFWEB)

    def test_le_schema_de_sortie_reclame_l_enum(self):
        self.assertIn('"securite_existante": "aucune | interne_client'
                      ' | prestataire_tiers | inconnu"', rw.PROMPT_RELIEFWEB)

    def test_l_ancien_booleen_n_est_plus_demande(self):
        """Le modele ne doit plus avoir le choix de repondre par un booleen :
        c'est ce choix qui privait ReliefWeb du levier de deplacement."""
        self.assertNotIn('"securite_existante_detectee": true | false',
                         rw.PROMPT_RELIEFWEB)

    def test_le_prestataire_tiers_est_presente_comme_une_opportunite(self):
        """Sans cette consigne, le modele range spontanement "securite deja en
        place" du cote des signaux negatifs."""
        self.assertIn("OPPORTUNITÉ DE DÉPLACEMENT", rw.PROMPT_RELIEFWEB)
        self.assertIn("À CONSERVER", rw.PROMPT_RELIEFWEB)

    def test_meme_doctrine_que_le_coeur(self):
        """Les deux prompts doivent proposer exactement le meme vocabulaire,
        sinon les extractions ne sont pas comparables d'une source a l'autre."""
        for valeur in ted._SECU_VALEURS:
            self.assertIn(valeur, rw.PROMPT_RELIEFWEB)
            self.assertIn(valeur, ted.PROMPT_TEMPLATE)


# ===========================================================================
# LA NORMALISATION EST APPLIQUEE SUR LES TROIS SORTIES POSSIBLES
# ===========================================================================

class TestNormalisationSystematique(unittest.TestCase):

    def test_json_direct(self):
        ex = _analyser(enum="prestataire_tiers")
        self.assertEqual(ex["securite_existante"], "prestataire_tiers")
        self.assertIn("securite_existante_detectee", ex)

    def test_json_entoure_de_texte_parasite(self):
        """Deuxieme etage de recuperation : sous-chaine entre { et }."""
        charge = json.dumps({**BASE, "securite_existante": "prestataire_tiers"})
        ex = _analyser(brut="Voici l'analyse :\n" + charge + "\nFin.")
        self.assertEqual(ex["securite_existante"], "prestataire_tiers")
        self.assertFalse(ex["securite_existante_detectee"])

    def test_json_repare_par_le_modele(self):
        """Troisieme etage : la reparation Sonnet. Elle passait elle aussi a
        cote de la normalisation."""
        charge = json.dumps({**BASE, "securite_existante": "prestataire_tiers"})
        ex = _analyser(brut="{ ceci n'est pas du JSON", reparation=charge)
        self.assertEqual(ex["securite_existante"], "prestataire_tiers")

    def test_valeur_inconnue_repliee_sur_inconnu(self):
        ex = _analyser(enum="n_importe_quoi")
        self.assertEqual(ex["securite_existante"], "inconnu")
        self.assertFalse(ex["securite_existante_detectee"])

    def test_repli_sur_l_ancien_booleen(self):
        """Tolerance de transition : si le modele repond encore a l'ancien
        schema, la normalisation doit tenir. Elle interprete alors le booleen
        au sens strict (securite interne)."""
        ex = _analyser(booleen=True)
        self.assertEqual(ex["securite_existante"], "interne_client")
        self.assertTrue(ex["securite_existante_detectee"])

    def test_modele_muet_renvoie_none(self):
        with _Bascule(None):
            self.assertIsNone(rw.analyser_reliefweb(AVIS))

    def test_json_irrecuperable_renvoie_none(self):
        ex = _analyser(brut="{ casse", reparation="toujours casse")
        self.assertIsNone(ex)


# ===========================================================================
# LA CONSEQUENCE COMMERCIALE : LE LEVIER DE DEPLACEMENT
# ===========================================================================

class TestLevierDeplacementConcurrent(unittest.TestCase):
    """Le coeur du correctif. Avant, TOUTE securite detectee valait
    "ignorer" ; le prestataire tiers, qui est la meilleure cible de conquete,
    etait donc jete avec le reste."""

    def test_prestataire_tiers_conserve_et_pousse(self):
        ex = _analyser(enum="prestataire_tiers")
        final, action = _decision(ex)
        self.assertFalse(ex["securite_existante_detectee"])
        self.assertNotEqual(action, "ignorer")
        self.assertGreater(final, 5.0)

    def test_prestataire_tiers_marque_dans_la_justification(self):
        ex = _analyser(enum="prestataire_tiers")
        self.assertTrue(ex["justification"].startswith(ted.MARQUEUR_DEPLACEMENT))

    def test_interne_client_reste_ecarte(self):
        """Le kill-switch legitime, lui, doit survivre : quand l'organisation
        gere sa surete en interne, le marche est reellement ferme."""
        ex = _analyser(enum="interne_client")
        self.assertTrue(ex["securite_existante_detectee"])
        self.assertEqual(_decision(ex)[1], "ignorer")

    def test_contraste_tiers_contre_interne(self):
        """Meme offre, meme description, seule l'origine du dispositif change.
        L'ecart doit etre net, sinon la distinction ne sert a rien."""
        tiers = _analyser(enum="prestataire_tiers")
        interne = _analyser(enum="interne_client")
        self.assertGreater(_decision(tiers)[0], _decision(interne)[0])

    def test_aucune_securite_reste_une_bonne_piste(self):
        ex = _analyser(enum="aucune")
        self.assertNotEqual(_decision(ex)[1], "ignorer")

    def test_le_marqueur_arrive_jusqu_au_sheet(self):
        """La colonne `justification` du Sheet porte le marqueur, c'est elle
        que le dashboard lit pour afficher le badge de deplacement (il teste
        le prefixe, sans rien savoir de la source)."""
        ex = _analyser(enum="prestataire_tiers")
        surete, commercial, final = ted.calculer_scores(
            rw.avis_pour_scoring_rw(AVIS), ex)
        ligne = rw.ligne_depuis_resultat_rw({
            "avis": AVIS, "extraction": ex, "surete": surete,
            "commercial": commercial, "score": final,
            "raffine": False, "divergence": False})
        justification = ligne[rw.COLONNES_RW.index("justification")]
        self.assertTrue(justification.startswith(ted.MARQUEUR_DEPLACEMENT))


# ===========================================================================
# LE SCHEMA DU SHEET N'A PAS BOUGE
# ===========================================================================

class TestSchemaSheetInchange(unittest.TestCase):
    """Verification volontairement severe : le schema est POSITIONNEL.
    Inserer une colonne au milieu desalignerait toutes les lignes deja
    ecrites, ce qui est exactement l'incident `bm_radar`. Le correctif ne doit
    donc rien changer au schema, et ces tests l'attestent."""

    COLONNES_ATTENDUES = [
        "date_maj", "score_final", "score_surete", "score_commercial",
        "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
        "titre", "acheteur", "pays_execution",
        "type_client", "type_mobilite", "profil_personnes_exposees",
        "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
        "profils_acteurs_probables", "cible_commerciale_reelle",
        "justification", "confiance",
        "modele", "raffine", "divergence",
        "organisation", "type_contrat", "categorie", "ville", "how_to_apply",
        "publication_number", "lien_avis", "deadline", "date_publication",
    ]

    def test_colonnes_identiques_a_l_octet_pres(self):
        self.assertEqual(list(rw.COLONNES_RW), self.COLONNES_ATTENDUES)

    def test_l_enum_n_est_pas_persiste(self):
        """Seul le booleen derive va au Sheet, comme cote TED. L'enum reste un
        detail interne de l'extraction."""
        self.assertNotIn("securite_existante", rw.COLONNES_RW)
        self.assertIn("securite_existante_detectee", rw.COLONNES_RW)

    def test_meme_choix_que_le_coeur(self):
        self.assertNotIn("securite_existante", ted.COLONNES_SHEET)
        self.assertIn("securite_existante_detectee", ted.COLONNES_SHEET)

    def test_la_ligne_produite_a_la_bonne_longueur(self):
        ex = _analyser(enum="aucune")
        ligne = rw.ligne_depuis_resultat_rw({
            "avis": AVIS, "extraction": ex, "surete": 6.0,
            "commercial": 7.0, "score": 6.5,
            "raffine": False, "divergence": False})
        self.assertEqual(len(ligne), len(rw.COLONNES_RW))

    def test_la_colonne_securite_recoit_un_booleen(self):
        """Pas la chaine de l'enum : le dashboard lit cette colonne avec
        `_vrai()`, qui attend un booleen ou sa representation textuelle."""
        ex = _analyser(enum="prestataire_tiers")
        ligne = rw.ligne_depuis_resultat_rw({
            "avis": AVIS, "extraction": ex, "surete": 6.0,
            "commercial": 7.0, "score": 6.5,
            "raffine": False, "divergence": False})
        valeur = ligne[rw.COLONNES_RW.index("securite_existante_detectee")]
        self.assertIn(valeur, ("False", "True"))


if __name__ == "__main__":
    unittest.main()
