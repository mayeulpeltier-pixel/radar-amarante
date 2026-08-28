# -*- coding: utf-8 -*-
"""P3.6 — Cycle de vie du candidat de découverte (26/08/2026).

CE QUI EXISTAIT
---------------
Deux états : `candidat` et `promu`. Un candidat non promouvable retombait dans
un sac indistinct nommé « attente », qui mélange trois situations très
différentes :

  - une piste qui n'a qu'un article et n'ira probablement nulle part ;
  - une piste qui coche presque tous les critères et n'attend qu'un signal ;
  - une piste qui a cessé de bouger il y a huit mois.

Les traiter pareil oblige l'humain à ré-arbitrer le même tas à chaque run, et
la file grossit indéfiniment jusqu'à ce que plus personne ne la relise.

CE QUE CE CHANTIER AJOUTE
-------------------------
Quatre états lus depuis les critères DÉJÀ en place, et surtout **ce qui manque
pour avancer**. `manque_pour_promouvoir` interroge les mêmes composantes que
`promouvable` ; elle ne les redéfinit pas. Une seconde définition divergerait
à la première modification de doctrine.

L'état `dormant` est celui qui manquait le plus : sans lui, rien ne sort
jamais de la file d'arbitrage.

Tests OFFLINE : fonctions pures, aucun réseau.
"""

import datetime
import unittest

import decouverte_projets as dp


AUJ = datetime.date(2026, 8, 26)


def _c(**kw):
    """Candidat promouvable par la VOIE PRESSE, que l'on dégrade ensuite."""
    # `secteur` et `montant_musd` sont INDISPENSABLES : `promouvable` finit par
    # `pertinent_pour_amarante`, qui juge le déploiement humain probable et le
    # risque pays. Mon premier jet les omettait, et le candidat « nominal »
    # était en fait rejeté comme hors périmètre. Le test d'accord entre les
    # deux fonctions l'a signalé immédiatement : c'est exactement son rôle.
    base = {"nom": "Mine de cuivre de Kolwezi", "iso3": "TCD",
            "confiance": 70, "confiance_llm": 60, "nb_signaux": 3,
            "nb_sources": 2, "poids_sources": 1.2, "meilleure_fiabilite": 0.7,
            "nb_sources_presse": 1, "sources_officielles": [],
            "secteur": "mines", "montant_musd": 800,
            "signaux": [{"titre": "mégaprojet minier"}],
            "derniere_maj": "2026-08-20"}
    base.update(kw)
    return base


class TestAccordAvecPromouvable(unittest.TestCase):
    """LE test structurant : les deux fonctions doivent toujours dire la même
    chose. Si elles divergent, l'écran ment sur la règle appliquée."""

    def _cas(self):
        return [
            _c(),
            _c(nb_signaux=1, nb_sources=1, poids_sources=0.9,
               sources_officielles=["BM"], nb_sources_presse=1),
            _c(nb_signaux=1, nb_sources=1, poids_sources=0.9,
               sources_officielles=["BM"], nb_sources_presse=0),
            _c(nb_sources=1, poids_sources=0.6),
            _c(nb_signaux=1, nb_sources=1, poids_sources=0.4,
               meilleure_fiabilite=0.4),
            _c(sans_nom=True),
            _c(confiance_llm=20),
            _c(iso3=""),
            _c(confiance=10),
            _c(meilleure_fiabilite=0.3),
        ]

    def test_manque_vide_si_et_seulement_si_promouvable(self):
        for c in self._cas():
            promouvable = dp.promouvable(c)
            manques = dp.manque_pour_promouvoir(c)
            self.assertEqual(promouvable, not manques,
                             "désaccord sur {}".format(c))

    def test_un_candidat_promouvable_n_a_aucun_reproche(self):
        self.assertEqual(dp.manque_pour_promouvoir(_c()), [])

    def test_les_deux_voies_sont_alternatives(self):
        """Ne pas reprocher à un candidat promu par la presse de ne pas avoir
        de source officielle, ni l'inverse."""
        officiel = _c(nb_signaux=1, nb_sources=1, poids_sources=0.9,
                      sources_officielles=["BM"], nb_sources_presse=1)
        self.assertEqual(dp.manque_pour_promouvoir(officiel), [])
        self.assertEqual(dp.manque_pour_promouvoir(_c()), [])


class TestEtats(unittest.TestCase):

    def _etat(self, **kw):
        return dp.etat_candidat(_c(**kw), AUJ)["etat"]

    def test_promouvable(self):
        self.assertEqual(self._etat(), "promouvable")

    def test_surveillance_quand_il_y_a_de_la_traction(self):
        """Une piste avec une source officielle ou déjà plusieurs signaux
        mérite d'être revue au prochain run, pas d'être noyée."""
        self.assertEqual(self._etat(nb_sources=1, poids_sources=0.6),
                         "surveillance")
        self.assertEqual(
            self._etat(nb_signaux=1, nb_sources=1, poids_sources=0.9,
                       sources_officielles=["BM"], nb_sources_presse=0),
            "surveillance")

    def test_candidat_nu(self):
        self.assertEqual(
            self._etat(nb_signaux=1, nb_sources=1, poids_sources=0.4,
                       meilleure_fiabilite=0.4), "candidat")

    def test_dormant_sort_de_la_file(self):
        """L'état qui manquait le plus : sans lui, la file d'arbitrage ne
        cesse jamais de grossir."""
        e = dp.etat_candidat(_c(nb_sources=1, poids_sources=0.6,
                                derniere_maj="2025-06-01"), AUJ)
        self.assertEqual(e["etat"], "dormant")
        self.assertGreater(e["jours_sans_signal"], dp.JOURS_DORMANCE_CANDIDAT)

    def test_un_promouvable_ancien_reste_promouvable(self):
        """La dormance ne doit pas masquer une piste prête à arbitrer : elle
        ne s'applique qu'à ce qui est encore bloqué."""
        self.assertEqual(
            dp.etat_candidat(_c(derniere_maj="2025-01-01"), AUJ)["etat"],
            "promouvable")

    def test_date_absente_ou_illisible_ne_leve_pas(self):
        for d in ("", "hier", "2026-13-45"):
            e = dp.etat_candidat(_c(derniere_maj=d), AUJ)
            self.assertIsNone(e["jours_sans_signal"])


class TestLisibiliteDuManque(unittest.TestCase):
    """« Ce candidat n'est pas promouvable » n'aide personne. Il faut savoir
    ce qui lui manque pour décider d'aller chercher ou d'abandonner."""

    def test_le_manque_est_chiffre(self):
        m = dp.manque_pour_promouvoir(_c(nb_sources=1, poids_sources=0.6))
        self.assertTrue(any("1/2 sources" in x for x in m))
        self.assertTrue(any("0.60/1.00" in x for x in m))

    def test_le_manque_de_corroboration_est_nomme(self):
        """Motif documenté : un avis DFI isolé faisait recopier le
        portefeuille de la Banque Mondiale au lieu de découvrir."""
        m = dp.manque_pour_promouvoir(
            _c(nb_signaux=1, nb_sources=1, poids_sources=0.9,
               sources_officielles=["BM"], nb_sources_presse=0))
        self.assertTrue(any("non corroborée par la presse" in x for x in m))

    def test_les_bloqueurs_absolus_court_circuitent_le_reste(self):
        """Sans pays ni nom, détailler le poids de preuve est du bruit."""
        self.assertEqual(dp.manque_pour_promouvoir(_c(iso3="")),
                         ["pays non identifié"])

    def test_le_doute_du_modele_est_dit_en_clair(self):
        m = dp.manque_pour_promouvoir(_c(confiance_llm=20))
        self.assertTrue(any("le modèle doute" in x for x in m))

    def test_jamais_de_liste_vide_sur_un_non_promouvable(self):
        for c in (_c(iso3=""), _c(confiance=10), _c(nb_signaux=0, nb_sources=0,
                                                    poids_sources=0)):
            self.assertTrue(dp.manque_pour_promouvoir(c))


class TestSerialisation(unittest.TestCase):

    def test_les_colonnes_sont_declarees(self):
        for col in ("manque", "jours_sans_signal"):
            self.assertIn(col, dp.COLONNES)

    def test_la_ligne_porte_l_etat_et_le_manque(self):
        ligne = dp.ligne_candidat(_c(nb_sources=1, poids_sources=0.6),
                                  aujourd=AUJ)
        d = dict(zip(dp.COLONNES, ligne))
        self.assertEqual(d["statut"], "surveillance")
        self.assertIn("sources distinctes", d["manque"])

    def test_un_promu_n_affiche_aucun_manque(self):
        d = dict(zip(dp.COLONNES, dp.ligne_candidat(_c(), promu=True,
                                                    aujourd=AUJ)))
        self.assertEqual(d["statut"], "promu")
        self.assertEqual(d["manque"], "")

    def test_toutes_les_valeurs_sont_du_texte(self):
        """Le Sheet ne stocke ni listes ni None."""
        for ligne in (dp.ligne_candidat(_c(), aujourd=AUJ),
                      dp.ligne_candidat(_c(derniere_maj=""), aujourd=AUJ)):
            for v in ligne:
                self.assertIsInstance(v, str)

    def test_separateur_compatible_avec_le_contenu(self):
        """Les manques contiennent des «/» et des «:» : la virgule les
        couperait mal, le « | » non."""
        d = dict(zip(dp.COLONNES,
                     dp.ligne_candidat(_c(nb_sources=1, poids_sources=0.6),
                                       aujourd=AUJ)))
        self.assertIn(" | ", d["manque"])


class TestFiltreDePertinence(unittest.TestCase):
    """Ce filtre était le maillon que j'avais oublié dans
    `manque_pour_promouvoir`. Le test d'accord l'a signalé au premier
    lancement, ce qui est exactement son rôle."""

    def test_un_projet_hors_perimetre_est_bloque_avec_son_motif(self):
        """Motif documenté : le run du 24/08 promouvait de l'assainissement à
        Kinshasa et de la mobilité urbaine à Karachi -- des projets réels, à
        faible déploiement expatrié."""
        m = dp.manque_pour_promouvoir(_c(secteur="infrastructure",
                                         montant_musd=20, signaux=[]))
        self.assertTrue(m)
        self.assertTrue(any("déploiement faible" in x or "périmètre" in x
                            for x in m), m)

    def test_le_motif_de_rejet_est_celui_du_moteur(self):
        """On ne paraphrase pas : on rend le motif tel que
        `pertinent_pour_amarante` le formule."""
        c = _c(secteur="infrastructure", montant_musd=20, signaux=[])
        self.assertEqual(dp.manque_pour_promouvoir(c),
                         [dp.pertinent_pour_amarante(c)[1]])

    def test_les_secteurs_a_deploiement_lourd_passent(self):
        for sect in ("energie", "mines", "transport"):
            self.assertEqual(dp.manque_pour_promouvoir(_c(secteur=sect)), [],
                             sect)


class TestNonRegression(unittest.TestCase):

    def test_promouvoir_inchange(self):
        promus, attente = dp.promouvoir([_c()], registre=[])
        self.assertEqual(len(promus), 1)
        self.assertEqual(attente, [])

    def test_un_candidat_bloque_reste_en_attente(self):
        promus, attente = dp.promouvoir([_c(iso3="")], registre=[])
        self.assertEqual(promus, [])
        self.assertEqual(len(attente), 1)


if __name__ == "__main__":
    unittest.main()
