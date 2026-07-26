# -*- coding: utf-8 -*-
"""Collecteur ALERTES VOYAGEURS (FCDO).

POURQUOI CE FICHIER EXISTE (23/07/2026)
---------------------------------------
Premiere brique de la veille "signaux faibles". Le signal n'est pas le niveau
d'alerte d'un pays mais son CHANGEMENT : quand le FCDO fait basculer un pays,
toute entreprise qui y a du personnel doit revoir sa surete, et c'est le moment
ou Amarante a une carte a jouer.

CE QUE CES TESTS VERROUILLENT, ET POURQUOI
------------------------------------------
La sonde (sonde_alertes.py) a revele trois pieges que ce collecteur doit eviter
en permanence. Les tests les figent :

  1. NE PAS se fier a `updated_at`. Il valait 2026-07-23 pour TOUS les pays
     (rebuild technique du site) : s'y fier ferait croire que 123 pays changent
     a chaque run. Seul `public_updated_at` est lie au contenu. -> TestEtat.

  2. NE PAS emettre au premier passage. Sans etat memorise, les 123 pays sont
     "inconnus" ; les traiter comme des changements noierait le dashboard sous
     123 faux signaux. Premier run = on memorise, on se tait. -> TestMemoire.

  3. RESOUDRE les slugs au runtime (option 2). Le croisement nom FR -> slug a
     echoue (0/123). On croise ISO3 -> nom ANGLAIS avec l'index FCDO, ce qui
     survit aux renommages de slug et aux cas irreguliers (COD ->
     democratic-republic-of-the-congo). -> TestResolutionSlugs.

Aucun appel reseau : l'index et les fiches sont injectes.
"""

import unittest

import alertes_voyageurs as av


# Donnees calquees sur la sortie REELLE de la sonde.
FICHE_MALI = {
    "public_updated_at": "2026-07-10T14:24:11+01:00",
    "updated_at": "2026-07-23T19:20:31+01:00",          # piege : rebuild technique
    "details": {
        "alert_status": ["avoid_all_travel_to_whole_country"],
        "change_description": "Updated info about terrorist attacks in Mali.",
    },
}

FICHE_COLOMBIE = {
    "public_updated_at": "2026-06-22T16:51:45+01:00",
    "updated_at": "2026-07-23T18:57:16+01:00",
    "details": {
        "alert_status": ["avoid_all_but_essential_travel_to_parts"],
        "change_description": "Removal of info about Presidential Elections.",
    },
}


def _index(*paires):
    """Fabrique un faux index FCDO (title -> base_path)."""
    return {"links": {"children": [
        {"base_path": "/foreign-travel-advice/" + slug,
         "title": titre + " travel advice"}
        for titre, slug in paires]}}


# ===========================================================================
# NIVEAU ET SEVERITE
# ===========================================================================

class TestNiveau(unittest.TestCase):

    def test_niveau_max_retient_le_plus_severe(self):
        """alert_status est une liste : plusieurs codes possibles. Le pays est
        qualifie par le PLUS grave."""
        self.assertEqual(
            av.niveau_max(["avoid_all_but_essential_travel_to_parts",
                           "avoid_all_travel_to_parts"]),
            "avoid_all_travel_to_parts")

    def test_liste_vide(self):
        self.assertEqual(av.niveau_max([]), "")

    def test_non_liste_tolere(self):
        self.assertEqual(av.niveau_max(None), "")
        self.assertEqual(av.niveau_max("texte"), "")

    def test_severite_ordonnee(self):
        s = av.SEVERITE
        self.assertGreater(s["avoid_all_travel_to_whole_country"],
                           s["avoid_all_but_essential_travel_to_parts"])


# ===========================================================================
# EXTRACTION D'ETAT  (le piege updated_at)
# ===========================================================================

class TestEtat(unittest.TestCase):

    def test_extrait_le_code_le_plus_severe(self):
        etat = av.extraire_etat(FICHE_MALI)
        self.assertEqual(etat["code"], "avoid_all_travel_to_whole_country")
        self.assertEqual(etat["severite"], 5)

    def test_n_utilise_que_public_updated_at(self):
        """LE piege de la sonde : updated_at est un rebuild technique commun a
        tous les pays. L'etat memorise ne doit contenir QUE public_updated_at,
        sinon tout 'change' a chaque run."""
        etat = av.extraire_etat(FICHE_MALI)
        self.assertEqual(etat["public_updated_at"], "2026-07-10T14:24:11+01:00")
        self.assertNotIn("2026-07-23", etat["public_updated_at"])

    def test_fiche_inexploitable_renvoie_none(self):
        self.assertIsNone(av.extraire_etat(None))
        self.assertIsNone(av.extraire_etat("pas un dict"))

    def test_details_absents_ne_font_pas_echouer(self):
        etat = av.extraire_etat({"public_updated_at": "2026-01-01"})
        self.assertEqual(etat["code"], "")
        self.assertEqual(etat["severite"], 0)


# ===========================================================================
# SENS DU CHANGEMENT
# ===========================================================================

class TestSens(unittest.TestCase):

    def test_aggravation(self):
        avant = {"severite": 2}
        apres = {"severite": 5}
        self.assertEqual(av.sens_du_changement(avant, apres), "aggravation")

    def test_allegement(self):
        self.assertEqual(
            av.sens_du_changement({"severite": 5}, {"severite": 2}),
            "allegement")

    def test_lateral(self):
        """Meme severite, contenu revu : ni aggravation ni allegement."""
        self.assertEqual(
            av.sens_du_changement({"severite": 3}, {"severite": 3}),
            "lateral")


# ===========================================================================
# RESOLUTION DES SLUGS (option 2)
# ===========================================================================

class TestResolutionSlugs(unittest.TestCase):

    def test_croisement_par_nom_anglais(self):
        mapping, _ = av.resoudre_slugs(
            lambda url: _index(("Mali", "mali"), ("Colombia", "colombia")))
        self.assertEqual(mapping["MLI"], "mali")
        self.assertEqual(mapping["COL"], "colombia")

    def test_cas_irregulier_resolu_sans_table_de_slug(self):
        """COD -> democratic-republic-of-the-congo : aucune regle mecanique ne
        le donne. Le croisement par nom anglais, si."""
        mapping, _ = av.resoudre_slugs(lambda url: _index(
            ("Democratic Republic of the Congo",
             "democratic-republic-of-the-congo")))
        self.assertEqual(mapping["COD"], "democratic-republic-of-the-congo")

    def test_pays_absent_de_l_index_signale(self):
        mapping, manquants = av.resoudre_slugs(
            lambda url: _index(("Mali", "mali")))
        self.assertIn("MLI", mapping)
        self.assertIn("COL", manquants)

    def test_index_injoignable_ne_leve_pas(self):
        def boom(url):
            raise OSError("reseau coupe")
        mapping, manquants = av.resoudre_slugs(boom)
        self.assertEqual(mapping, {})
        self.assertEqual(len(manquants), len(av.NOM_EN))


# ===========================================================================
# TABLE ISO3 -> NOM ANGLAIS
# ===========================================================================

class TestTable(unittest.TestCase):

    def test_couvre_exactement_le_perimetre(self):
        """La table doit suivre le perimetre radar : ni trou (pays non
        surveille) ni surplus (entree morte)."""
        import ted_complet_v14 as ted
        peri = set(ted.CODES_PAYS_SUIVIS)
        self.assertEqual(set(av.NOM_EN), peri,
                         "table EN desynchronisee du perimetre radar")

    def test_pas_de_nom_vide(self):
        for iso3, nom in av.NOM_EN.items():
            self.assertTrue(nom.strip(), "nom vide pour {}".format(iso3))


# ===========================================================================
# LEAD DE CHANGEMENT
# ===========================================================================

class TestLead(unittest.TestCase):

    def test_lead_complet(self):
        avant = av.extraire_etat(FICHE_COLOMBIE)
        apres = av.extraire_etat(FICHE_MALI)          # plus severe
        lead = av.lead_de_changement("COL", "Colombie", avant, apres,
                                     "Amerique latine")
        self.assertEqual(lead["pays_execution"], "COL")
        self.assertEqual(lead["sens"], "aggravation")
        self.assertIn("Tout voyage deconseille", lead["niveau_apres"])
        self.assertEqual(lead["severite"], 5)

    def test_publication_number_stable_par_pays_et_date(self):
        """La cle porte le pays et la date de contenu : deux runs sur le meme
        changement produisent la meme cle (deduplication naturelle en aval)."""
        etat = av.extraire_etat(FICHE_MALI)
        l1 = av.lead_de_changement("MLI", "Mali", {"severite": 0}, etat, "Sahel")
        l2 = av.lead_de_changement("MLI", "Mali", {"severite": 0}, etat, "Sahel")
        self.assertEqual(l1["publication_number"], l2["publication_number"])
        self.assertIn("FCDO-MLI-2026-07-10", l1["publication_number"])

    def test_schema_ligne_complet(self):
        etat = av.extraire_etat(FICHE_MALI)
        lead = av.lead_de_changement("MLI", "Mali", {"severite": 0}, etat, "Sahel")
        for colonne in av.COLONNES:
            self.assertIn(colonne, lead, "colonne absente du lead : " + colonne)


# ===========================================================================
# MEMOIRE : LE PREMIER RUN NE DOIT PAS EMETTRE
# ===========================================================================

class TestMemoire(unittest.TestCase):
    """Reproduit la logique de comparaison du main, isolee du reseau et de
    Postgres, pour verrouiller le comportement au premier passage."""

    def _detecter(self, precedent, courant_fiches):
        """precedent : {iso3: etat}. courant_fiches : {iso3: fiche}.
        Renvoie la liste des iso3 pour lesquels un signal serait emis."""
        emis = []
        for iso3, fiche in courant_fiches.items():
            etat = av.extraire_etat(fiche)
            avant = precedent.get(iso3)
            if avant is None:
                continue                              # inconnu : pas de signal
            if (avant.get("public_updated_at") != etat["public_updated_at"]
                    or avant.get("code") != etat["code"]):
                emis.append(iso3)
        return emis

    def test_premier_run_n_emet_aucun_signal(self):
        """Aucun etat memorise : 123 pays inconnus. Zero signal, sinon le
        dashboard croulerait sous de faux changements."""
        emis = self._detecter({}, {"MLI": FICHE_MALI, "COL": FICHE_COLOMBIE})
        self.assertEqual(emis, [])

    def test_etat_inchange_n_emet_pas(self):
        precedent = {"MLI": av.extraire_etat(FICHE_MALI)}
        emis = self._detecter(precedent, {"MLI": FICHE_MALI})
        self.assertEqual(emis, [])

    def test_changement_de_niveau_emet(self):
        precedent = {"COL": av.extraire_etat(FICHE_COLOMBIE)}
        # La Colombie bascule sur le niveau du Mali (plus severe).
        emis = self._detecter(precedent, {"COL": FICHE_MALI})
        self.assertEqual(emis, ["COL"])

    def test_nouvelle_date_de_contenu_emet(self):
        """Meme code, mais public_updated_at different : le FCDO a revu la
        fiche. C'est un changement de contenu, on le signale."""
        precedent = {"MLI": av.extraire_etat(FICHE_MALI)}
        fiche_revue = dict(FICHE_MALI, public_updated_at="2026-08-01T09:00:00+01:00")
        emis = self._detecter(precedent, {"MLI": fiche_revue})
        self.assertEqual(emis, ["MLI"])

    def test_rebuild_technique_seul_n_emet_pas(self):
        """Si SEUL updated_at change (rebuild), public_updated_at et le code
        restent identiques : aucun signal. C'est tout l'interet d'ignorer
        updated_at."""
        precedent = {"MLI": av.extraire_etat(FICHE_MALI)}
        rebuild = dict(FICHE_MALI, updated_at="2026-09-99T00:00:00+01:00")
        emis = self._detecter(precedent, {"MLI": rebuild})
        self.assertEqual(emis, [])


if __name__ == "__main__":
    unittest.main()
