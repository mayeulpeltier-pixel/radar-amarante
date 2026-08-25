# -*- coding: utf-8 -*-
"""Date de PREMIERE detection cote APPLICATION (Postgres).

LE DEFAUT CORRIGE
-----------------
Les collecteurs n'ecrivent pas `date_detection` dans le JSONB : cette colonne
vit a part (append en fin de ligne cote Sheet, colonne dediee cote base). En
relisant `donnees` seul, l'application n'avait donc pas de date de detection et
retombait sur `date_maj` -- qui vaut la date du RUN et que le miroir rafraichit
a chaque passage. Consequence visible : « detecte aujourd'hui » sur des lignes
connues depuis des mois, badge « nouveau » generalise, graphe des detections
ecrase sur le mois courant, tri par fraicheur neutralise.

Tests OFFLINE (fonction pure) : aucune base requise.
"""

import os
import unittest
from datetime import date

import radar_stockage as st
import radar_dashboard as dash


class TestInjectionDate(unittest.TestCase):

    def setUp(self):
        st.DATE_DET_PG = True

    def test_colonne_rendue_a_la_ligne(self):
        """Le cas nominal : ligne du miroir, sans date dans le JSONB."""
        d = st.injecter_date_detection({"gagnant": "X"}, date(2026, 3, 4))
        self.assertEqual(d["date_detection"], "2026-03-04")

    def test_valeur_du_jsonb_fait_autorite(self):
        """Les lignes du rattrapage portent la date reelle du Sheet. Elle prime
        sur la colonne, qui n'est que la date de premiere ecriture EN BASE."""
        d = st.injecter_date_detection(
            {"gagnant": "X", "date_detection": "2025-11-02"}, date(2026, 3, 4))
        self.assertEqual(d["date_detection"], "2025-11-02")

    def test_colonne_vide_ne_fabrique_rien(self):
        """Mieux vaut pas de date qu'une date inventee."""
        d = st.injecter_date_detection({"gagnant": "X"}, None)
        self.assertNotIn("date_detection", d)

    def test_entree_non_modifiee(self):
        """Pas d'effet de bord : relire deux fois la meme ligne est sur."""
        src = {"gagnant": "X"}
        st.injecter_date_detection(src, date(2026, 3, 4))
        self.assertNotIn("date_detection", src)

    def test_flag_off_restitue_le_comportement_anterieur(self):
        st.DATE_DET_PG = False
        d = st.injecter_date_detection({"gagnant": "X"}, date(2026, 3, 4))
        self.assertNotIn("date_detection", d)

    def test_type_inattendu_traverse_sans_casser(self):
        self.assertEqual(st.injecter_date_detection("brut", date(2026, 3, 4)),
                         "brut")


class TestEffetSurLAffichage(unittest.TestCase):
    """La preuve par le rendu : c'est ce que voit l'utilisateur qui compte."""

    def setUp(self):
        st.DATE_DET_PG = True
        self.brut = {
            "date_maj": date.today().isoformat(),   # rafraichi a chaque run
            "gagnant": "STECOL CORPORATION", "secteur": "Travaux",
            "pays_execution": "ETH", "valeur_attribuee": "12000000 USD",
            "acheteur": "ERA", "titre": "Route X",
            "publication_number": "OP-123", "lien": "http://x",
            "pays_titulaire": "China", "titulaire_etranger": "oui"}

    def test_avant_correctif_la_date_est_celle_du_run(self):
        lead = dash.attribution_vers_lead(self.brut)
        self.assertEqual(lead["date_det"], date.today().isoformat(),
                         "temoin du defaut : sans date_detection, on lit date_maj")

    def test_apres_correctif_la_date_est_celle_de_la_premiere_detection(self):
        ligne = st.injecter_date_detection(self.brut, date(2026, 3, 4))
        lead = dash.attribution_vers_lead(ligne)
        self.assertEqual(lead["date_det"], "2026-03-04")
        self.assertEqual(lead["mois"], "2026-03")

    def test_le_tri_par_fraicheur_redevient_discriminant(self):
        """`rang_tri` attenue le score par l'age. Avec une date figee a
        aujourd'hui, l'attenuation ne s'appliquait jamais : le tri
        « Importance » se reduisait au score brut."""
        ancien = dash.attribution_vers_lead(
            st.injecter_date_detection(self.brut, date(2026, 3, 4)))
        recent = dash.attribution_vers_lead(
            st.injecter_date_detection(dict(self.brut), date.today()))
        self.assertLess(dash.rang_tri(ancien["final"], ancien["date_det"]),
                        dash.rang_tri(recent["final"], recent["date_det"]))

    def test_sante_des_sources_redevient_capable_de_voir_une_source_muette(self):
        """Avec date_maj, chaque source paraissait « fraiche » a chaque run :
        le detecteur de mort silencieuse etait aveugle par construction."""
        vieux = dash.attribution_vers_lead(
            st.injecter_date_detection(self.brut, date(2026, 3, 4)))
        etats = {l["src"]: l["etat"]
                 for l in dash.sante_run([vieux], date(2026, 8, 25))["sources"]}
        self.assertEqual(etats["ATTRIB"], "ancien")


if __name__ == "__main__":
    unittest.main()
