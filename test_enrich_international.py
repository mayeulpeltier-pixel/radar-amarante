# -*- coding: utf-8 -*-
"""P3.5 — Enrichissement international (26/08/2026).

CE QUE MON PLAN DISAIT, ET CE QUI ÉTAIT VRAI
--------------------------------------------
Le plan v2 annonçait un enrichissement « borgne à l'international ». C'est
imprécis : le repli mondial GLEIF existe déjà et se déclenche quand le
registre français ne trouve rien. Ce qui manque vraiment, c'est sa RICHESSE --
GLEIF donne l'identité et le pays, jamais les dirigeants, l'effectif ni
l'activité.

CE QUE LA VÉRIFICATION A TROUVÉ, ET QUI EST PIRE
------------------------------------------------
`recherche-entreprises.api.gouv.fr` fait une recherche FLOUE. Le code retenait
`results[0]` **sans jamais vérifier que le nom trouvé correspondait au nom
cherché**. Démontré le 26/08 :

    cherché : STECOL CORPORATION   (titulaire chinois)
    retenu  : STECOLE FORMATION SARL
    SIREN   : 812345678
    dirigeant : Jean Dupont (Gérant)
    ville   : LYON

Une fiche enrichie à tort est PIRE qu'une fiche vide : elle a l'air
renseignée, personne ne la re-vérifie, et le commercial appelle un inconnu.

DEUX CORRECTIONS
----------------
1. `noms_compatibles` : le registre FR ne peut plus imposer une identité qui
   ne ressemble pas au nom cherché.
2. Les lignes déjà marquées `etranger=True` (titulaires DFI, attributaires
   étrangers) vont DIRECTEMENT au repli mondial. Le périmètre d'Amarante est
   international : le registre FR est l'exception, pas la règle.

Tests OFFLINE : fonctions pures et injection de `fetch`, aucun réseau.
"""

import unittest

import enrichir_entreprises as en


class TestCorrespondanceDesNoms(unittest.TestCase):
    """Le garde-fou doit bloquer les erreurs SANS bloquer les bonnes
    correspondances : un faux négatif donne une fiche vide, ce qui est mieux
    qu'une fiche fausse mais reste une perte."""

    def test_le_faux_positif_du_26_08_est_bloque(self):
        self.assertFalse(en.noms_compatibles("STECOL CORPORATION",
                                             "STECOLE FORMATION SARL"))

    def test_groupes_distincts_refuses(self):
        for a, b in (("BOUYGUES", "EIFFAGE"),
                     ("CHINA HARBOUR", "CHINA RAILWAY"),
                     ("ORANO", "ORANGE")):
            self.assertFalse(en.noms_compatibles(a, b), "{} / {}".format(a, b))

    def test_filiale_du_bon_groupe_acceptee(self):
        """Un Jaccard classique refuserait « VINCI » vs « VINCI CONSTRUCTION
        GRANDS PROJETS » (25 %). On rapporte au plus petit ensemble."""
        self.assertTrue(en.noms_compatibles(
            "VINCI", "VINCI CONSTRUCTION GRANDS PROJETS"))

    def test_renommage_par_concatenation_accepte(self):
        """Les groupes collent leurs mots en se renommant. Sans le repli sur
        la chaîne concaténée, le garde-fou produirait des faux NÉGATIFS sur
        les noms les plus courants."""
        self.assertTrue(en.noms_compatibles("TOTAL ENERGIES",
                                            "TOTALENERGIES SE"))

    def test_l_ordre_des_jetons_est_preserve(self):
        """La comparaison concaténée en dépend : un ensemble trié donnerait
        « energiestotal » et casserait le cas ci-dessus."""
        self.assertEqual(en._jetons("TOTAL ENERGIES"), ["total", "energies"])

    def test_ponctuation_et_accents_ignores(self):
        self.assertTrue(en.noms_compatibles("SOGEA SATOM", "SOGEA-SATOM SAS"))
        self.assertTrue(en.noms_compatibles("SAINT GOBAIN", "SAINT-GOBAIN"))

    def test_suffixes_juridiques_ignores(self):
        self.assertTrue(en.noms_compatibles("ACME", "ACME FRANCE SARL"))
        self.assertTrue(en.noms_compatibles("ACME", "ACME GMBH"))

    def test_nom_fait_uniquement_de_suffixes_ne_devient_pas_vide(self):
        """Sinon la comparaison dirait « aucune correspondance » au lieu de
        « je ne sais pas », et le comportement deviendrait imprévisible."""
        self.assertTrue(en._jetons("SARL"))
        self.assertEqual(en.correspondance("SARL", "SARL"), 1.0)

    def test_nom_vide_ne_correspond_a_rien(self):
        self.assertEqual(en.correspondance("", "ACME"), 0.0)
        self.assertEqual(en.correspondance("ACME", ""), 0.0)

    def test_seuil_configurable(self):
        self.assertIsInstance(en.SEUIL_CORRESPONDANCE, float)
        self.assertTrue(0 < en.SEUIL_CORRESPONDANCE < 1)


class TestRegistreFrancais(unittest.TestCase):

    def _reponse(self, nom_trouve):
        return {"results": [{
            "siren": "812345678", "nom_complet": nom_trouve,
            "dirigeants": [{"type_dirigeant": "personne physique",
                            "nom": "Dupont", "prenoms": "Jean",
                            "qualite": "Gérant"}],
            "libelle_activite_principale": "Formation continue",
            "tranche_effectif_salarie": "02",
            "siege": {"libelle_commune": "LYON"}}]}

    def test_resultat_incompatible_rejete(self):
        r = en.rechercher_entreprise_gouv(
            "STECOL CORPORATION",
            fetch=lambda *a, **k: self._reponse("STECOLE FORMATION SARL"))
        self.assertIsNone(r)

    def test_resultat_compatible_accepte(self):
        r = en.rechercher_entreprise_gouv(
            "SOGEA SATOM",
            fetch=lambda *a, **k: self._reponse("SOGEA-SATOM SAS"))
        self.assertIsNotNone(r)
        self.assertEqual(r["siren"], "812345678")

    def test_reponse_vide_sans_erreur(self):
        self.assertIsNone(en.rechercher_entreprise_gouv(
            "X", fetch=lambda *a, **k: {"results": []}))
        self.assertIsNone(en.rechercher_entreprise_gouv(
            "X", fetch=lambda *a, **k: None))


class TestAiguillageInternational(unittest.TestCase):
    """Le périmètre d'Amarante est international : interroger le registre
    français pour un titulaire chinois consomme du temps de run pour rien."""

    def _fetchs(self):
        appels = []

        def gouv(*a, **k):
            appels.append("gouv")
            return {"results": [{"siren": "1", "nom_complet": "AUTRE SARL",
                                 "dirigeants": [], "siege": {}}]}

        def gleif(*a, **k):
            appels.append("gleif")
            return {"data": [{"attributes": {"entity": {
                "legalName": {"name": "STECOL CORPORATION"},
                "legalAddress": {"city": "Tianjin", "country": "CN"}}}}]}

        return appels, gouv, gleif

    def test_titulaire_etranger_saute_le_registre_francais(self):
        appels, gouv, gleif = self._fetchs()
        en.enrichir_une({"entreprise": "STECOL CORPORATION", "etranger": True},
                        fetch_gouv=gouv, fetch_gleif=gleif)
        self.assertNotIn("gouv", appels)
        self.assertIn("gleif", appels)

    def test_origine_inconnue_essaie_le_registre_francais(self):
        """Sans information d'origine, on ne présume rien : on essaie, et le
        garde-fou tranche."""
        appels, gouv, gleif = self._fetchs()
        en.enrichir_une({"entreprise": "STECOL CORPORATION"},
                        fetch_gouv=gouv, fetch_gleif=gleif)
        self.assertIn("gouv", appels)
        self.assertIn("gleif", appels)      # rejeté, donc repli mondial

    def test_le_repli_mondial_renseigne_le_pays(self):
        appels, gouv, gleif = self._fetchs()
        ligne = en.enrichir_une(
            {"entreprise": "STECOL CORPORATION", "etranger": True},
            fetch_gouv=gouv, fetch_gleif=gleif)
        self.assertTrue(any("CN" in str(c) for c in (ligne or [])))

    def test_entreprise_sans_nom_ignoree(self):
        self.assertIsNone(en.enrichir_une({"entreprise": "  "}))


class TestNonRegression(unittest.TestCase):

    def test_la_cascade_reste_gouv_puis_gleif(self):
        with open("enrichir_entreprises.py", encoding="utf-8") as f:
            src = f.read()
        i_gouv = src.index("gouv = rechercher_entreprise_gouv(")
        i_gleif = src.index("gleif = rechercher_entreprise_gleif(")
        self.assertLess(i_gouv, i_gleif)

    def test_le_rejet_est_journalise(self):
        """Un rejet muet ferait croire à une entreprise introuvable, alors
        que le registre a bien répondu : la nuance compte au débogage."""
        with open("enrichir_entreprises.py", encoding="utf-8") as f:
            self.assertIn("le registre FR propose", f.read())


if __name__ == "__main__":
    unittest.main()
