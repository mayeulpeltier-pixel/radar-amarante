# -*- coding: utf-8 -*-
"""Socle DETERMINISTE du statut de selection et du filet titulaire (17/08/2026).

POURQUOI CE FICHIER EXISTE
--------------------------
Sonde v2 (jetable) : l'API search TED expose `winner-name` (nom du titulaire en
clair) et `winner-selection-status` (selec-w / clos-nw / open-nw) DES la
collecte. On s'en sert pour deux gains, sans toucher au schema partage
`attributions_radar` :

  1. FILET titulaire : quand ni SPARQL ni le PDF ne donnent de gagnant, on
     retombe sur `winner-name` (gratuit, deja dans la notice).
  2. SIGNAL re-tender : une attribution 100% infructueuse (tous lots clos-nw)
     n'a pas de titulaire mais annonce une re-publication -> la colonne
     EXISTANTE `a_demarcher` passe a "re-tender".

CE QUI EST DELICAT, ET TESTE ICI
--------------------------------
  - L'API aplatit les statuts en liste plate SANS cle de jointure vers les
    noms (cardinalites differentes : 6 statuts vs 25 noms sur 10759-2026). On
    n'apparie donc JAMAIS par index : on AGREGE au niveau notice.
  - Le statut vit dans une clef prefixee `_` : il ne doit PAS entrer dans le
    schema Sheet (invariant positionnel des COLONNES partagees).

Aucun appel reseau ni LLM : tout est injecte.
"""

import unittest

import ted_complet_attributions as att


# Notice reelle observee a la sonde v2 (structure fidele : winner-name est un
# dict multilingue de listes redondantes, winner-selection-status une liste
# plate de cardinalite differente).
NOTICE_10759 = {
    "publication-number": "10759-2026",
    "notice-title": "Napenergia 240 Solaranlagen fur Ukraine",
    "notice-type": "can-standard",
    "place-of-performance": "UKR",
    "classification-cpv": "09331000",
    "publication-date": "2026-01-08",
    "winner-selection-status": [
        "selec-w", "selec-w", "selec-w", "selec-w", "selec-w", "clos-nw"],
    "winner-name": {"deu": ["Yandalux Solar GmbH"] * 25},
}


class TestStatutSelection(unittest.TestCase):

    def test_partielle_si_gagnant_et_clos(self):
        """5 selec-w + 1 clos-nw -> partielle (au moins un lot infructueux)."""
        self.assertEqual(att.statut_selection(NOTICE_10759), "partielle")

    def test_attribuee_si_uniquement_gagnants(self):
        self.assertEqual(
            att.statut_selection({"winner-selection-status": ["selec-w", "selec-w"]}),
            "attribuee")

    def test_infructueuse_si_tout_clos(self):
        self.assertEqual(
            att.statut_selection({"winner-selection-status": ["clos-nw", "clos-nw"]}),
            "infructueuse")

    def test_en_cours_si_ouvert_sans_gagnant(self):
        self.assertEqual(
            att.statut_selection({"winner-selection-status": ["open-nw"]}),
            "en_cours")

    def test_vide_si_champ_absent(self):
        self.assertEqual(att.statut_selection({}), "")

    def test_tolere_forme_str_et_casse(self):
        """Champ parfois renvoye en str simple, casse variable."""
        self.assertEqual(
            att.statut_selection({"winner-selection-status": "SELEC-W"}),
            "attribuee")


class TestNomsUniques(unittest.TestCase):

    def test_aplati_et_dedup_dict_multilingue(self):
        """25 doublons "Yandalux" -> un seul nom."""
        self.assertEqual(
            att._noms_uniques(NOTICE_10759["winner-name"]),
            ["Yandalux Solar GmbH"])

    def test_ordre_preserve_sur_liste(self):
        self.assertEqual(
            att._noms_uniques(["Alpha", "Beta", "alpha", "Gamma"]),
            ["Alpha", "Beta", "Gamma"])

    def test_vide_si_absent(self):
        self.assertEqual(att._noms_uniques(None), [])


class TestFiletTitulaire(unittest.TestCase):
    """normaliser : winner-name comble un parse sans gagnant, sans l'ecraser."""

    def _parse_vide(self):
        return {"gagnants": [], "total": "", "sous_traitance": False}

    def test_filet_comble_quand_pdf_sparql_muets(self):
        a = att.normaliser(NOTICE_10759, self._parse_vide())
        self.assertEqual(a["gagnant"], "Yandalux Solar GmbH")
        self.assertEqual(a["_nb_gagnants"], 1)

    def test_filet_ne_supplante_pas_un_gagnant_existant(self):
        """Un gagnant deja extrait (PDF/SPARQL) prime : pas de doublon API."""
        parse = {"gagnants": [{"nom": "ACME Security Ltd", "valeur": "1 000 000 EUR"}],
                 "total": "1 000 000 EUR", "sous_traitance": False}
        a = att.normaliser(NOTICE_10759, parse)
        self.assertEqual(a["gagnant"], "ACME Security Ltd")
        self.assertEqual(a["_nb_gagnants"], 1)

    def test_infructueuse_reste_sans_titulaire(self):
        """Tout clos-nw + winner-name vide -> pas de gagnant invente."""
        notice = {"publication-number": "1-2026", "place-of-performance": "MLI",
                  "winner-selection-status": ["clos-nw"], "winner-name": {}}
        a = att.normaliser(notice, self._parse_vide())
        self.assertEqual(a["gagnant"], "(gagnant non publie)")
        self.assertEqual(a["_statut_selection"], "infructueuse")


class TestSignalRetender(unittest.TestCase):
    """ligne() : le signal re-tender passe par la colonne EXISTANTE a_demarcher."""

    def _ligne_dict(self, a):
        vals = att.ligne(a)
        return dict(zip(att.COLONNES, vals))

    def test_infructueuse_donne_re_tender(self):
        a = att.normaliser(
            {"publication-number": "2-2026", "place-of-performance": "NER",
             "winner-selection-status": ["clos-nw"], "winner-name": {}},
            {"gagnants": [], "total": "", "sous_traitance": False})
        self.assertEqual(self._ligne_dict(a)["a_demarcher"], "re-tender")

    def test_avec_titulaire_donne_oui(self):
        a = att.normaliser(NOTICE_10759,
                           {"gagnants": [], "total": "", "sous_traitance": False})
        self.assertEqual(self._ligne_dict(a)["a_demarcher"], "oui")

    def test_sans_statut_ni_gagnant_donne_verifier(self):
        a = att.normaliser(
            {"publication-number": "3-2026", "place-of-performance": "TCD"},
            {"gagnants": [], "total": "", "sous_traitance": False})
        self.assertEqual(self._ligne_dict(a)["a_demarcher"], "verifier")


class TestInvariantSchema(unittest.TestCase):
    """Le statut ne doit PAS entrer dans le schema partage."""

    def test_statut_est_une_clef_interne(self):
        a = att.normaliser(NOTICE_10759,
                           {"gagnants": [], "total": "", "sous_traitance": False})
        self.assertIn("_statut_selection", a)
        self.assertNotIn("statut_selection", att.COLONNES)

    def test_ligne_a_bien_longueur_du_schema(self):
        a = att.normaliser(NOTICE_10759,
                           {"gagnants": [], "total": "", "sous_traitance": False})
        self.assertEqual(len(att.ligne(a)), len(att.COLONNES))


class TestChampsRequete(unittest.TestCase):
    """Les deux champs doivent etre demandes a l'API."""

    def test_corps_demande_winner_name_et_statut(self):
        fields = att._corps(1, True)["fields"]
        self.assertIn("winner-name", fields)
        self.assertIn("winner-selection-status", fields)


class TestNoticeTypesValides(unittest.TestCase):
    """Garde-fou : `notice-type` ne doit contenir que des valeurs acceptees par
    l'expert search TED. `can-tport` (invalide, absent du SDK) faisait echouer
    la requete filtree en 400 et forcait la degradation a chaque run."""

    def test_pas_de_can_tport(self):
        self.assertNotIn("can-tport", att.NOTICE_TYPES_ATTRIB)

    def test_valeurs_dans_le_referentiel_sdk(self):
        # Seules familles d'attribution existantes dans eForms-SDK.
        valides = {"can-standard", "can-social"}
        self.assertTrue(set(att.NOTICE_TYPES_ATTRIB).issubset(valides))

    def test_query_filtree_inclut_le_type(self):
        q = att._query(include_type=True)
        self.assertIn("can-standard", q)
        self.assertNotIn("can-tport", q)


if __name__ == "__main__":
    unittest.main()
