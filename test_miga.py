# -*- coding: utf-8 -*-
"""Tests MIGA : parsing des champs Drupal (fixtures tirees du balisage REEL
capture par la sonde), crible categorie FI, resolution pays hote -> ISO3,
detection SPG/Brief, entonnoir de collecte, stabilite du schema Sheet.

Aucun reseau (fetch_liste / fetch_fiche injectes), aucun LLM reel
(ted.appeler_modele remplace puis restaure).
"""

import json
import unittest

import ted_complet_v14 as ted
import miga_radar as mg


# --- Fiche REELLE (Al Ahli Bank, categorie FI = a ecarter) -----------------
FICHE_FI = """
<div class="field"><div class="field__label">Guarantee Holder</div>
  <div class="field__items"><div class="field__item">Al Ahli Bank of Kuwait</div></div></div>
<div class="field"><div class="field__label">Investor Country</div>
  <div class="field__items"><div class="field__item">Kuwait</div></div></div>
<div class="field"><div class="field__label">Host Country </div>
  <div class="field field--name-field-host-country field--type-entity-reference field--label-hidden clearfix field--items">
    <div class="field--item"><a href="/x">Egypt, Arab Republic of</a></div></div></div>
<div class="field"><div class="field__label">Project Type</div>
  <div class="field__item">Non-SIP</div></div>
<div class="field"><div class="field__label">Fiscal Year</div>
  <div class="field__item">2026</div></div>
<div class="field"><div class="field__label">Environmental Category</div>
  <div class="field__item">FI</div></div>
<div><div class="field__label">Project Status</div>
  <div class="field__item">Active</div></div>
<p><strong>Project Description</strong></p><p>On December 23, 2025, MIGA issued a
guarantee of USD 300.0 million to Al Ahli Bank of Kuwait K.S.C.P for its subsidiary
in Egypt.</p>
"""

# --- Fiche synthetique non-FI (projet physique, categorie B, SPG) ----------
FICHE_B = """
<div class="field"><div class="field__label">Guarantee Holder</div>
  <div class="field__items"><div class="field__item">Konexa Power Holdings PCC</div></div></div>
<div class="field"><div class="field__label">Investor Country</div>
  <div class="field__items"><div class="field__item">Mauritius</div></div></div>
<div class="field"><div class="field__label">Host Country </div>
  <div class="field field--name-field-host-country field--items">
    <div class="field--item"><a href="/x">Nigeria</a></div></div></div>
<div class="field"><div class="field__label">Fiscal Year</div>
  <div class="field__item">2026</div></div>
<div class="field"><div class="field__label">Environmental Category</div>
  <div class="field__item">B</div></div>
<div><div class="field__label">Project Status</div>
  <div class="field__item">Proposed</div></div>
<p><strong>Project Description</strong></p><p>This summary covers an application by
Konexa for equity investments in a solar power plant, involving construction on site,
for up to US$16.29 million.</p>
"""

LISTE_HTML = """
<div class="views-row"><div class="feature-projects-data">
  <div class="host-country">Egypt, Arab Republic of</div>
  <div class="title"><a href="/project/al-ahli" hreflang="en">Al Ahli Bank Egypt</a></div>
  <div class="board-date">December 23, 2025</div></div></div>
<div class="views-row"><div class="feature-projects-data">
  <div class="host-country">Nigeria</div>
  <div class="title"><a href="/project/konexa-nbp2" hreflang="en">Konexa NBP2 and Solar</a></div>
  <div class="board-date">December 22, 2025</div></div></div>
"""

FICHES = {"/project/al-ahli": FICHE_FI, "/project/konexa-nbp2": FICHE_B}


class TestParsingChamps(unittest.TestCase):
    def test_champs_fiche_reelle(self):
        self.assertEqual(mg._champ(FICHE_FI, "Guarantee Holder"), "Al Ahli Bank of Kuwait")
        self.assertEqual(mg._champ(FICHE_FI, "Investor Country"), "Kuwait")
        self.assertEqual(mg._champ(FICHE_FI, "Environmental Category"), "FI")
        self.assertEqual(mg._champ(FICHE_FI, "Fiscal Year"), "2026")
        self.assertEqual(mg._champ(FICHE_FI, "Project Status"), "Active")

    def test_host_country_avec_lien_imbrique(self):
        hc = mg._champ(FICHE_FI, "Host Country")
        self.assertIn("Egypt", hc)

    def test_description_et_montant(self):
        a = mg.parser_fiche("/project/konexa-nbp2", "Konexa NBP2", FICHE_B)
        self.assertIn("solar power plant", a["description"])
        self.assertIn("16.29", a["valeur_estimee"])


class TestResolutionPays(unittest.TestCase):
    def test_iso3(self):
        self.assertEqual(mg._iso3_pays("Nigeria"), "NGA")             # pas NER
        self.assertEqual(mg._iso3_pays("Niger"), "NER")
        self.assertEqual(mg._iso3_pays("Somalia"), "SOM")             # pas MLI
        self.assertEqual(mg._iso3_pays("Congo, Democratic Republic of"), "COD")
        self.assertEqual(mg._iso3_pays("Congo, Republic of"), "COG")
        self.assertEqual(mg._iso3_pays("South Sudan"), "SSD")
        self.assertEqual(mg._iso3_pays("Sudan"), "SDN")
        self.assertEqual(mg._iso3_pays("Ukraine"), "UKR")
        self.assertEqual(mg._iso3_pays("Egypt, Arab Republic of"), "EGY")
        self.assertEqual(mg._iso3_pays("Chile"), "")                  # hors perimetre

    def test_iso3_alimente_le_scoring(self):
        a = mg.parser_fiche("/project/konexa-nbp2", "Konexa", FICHE_B)
        self.assertEqual(a["pays_iso3"], "NGA")
        self.assertEqual(mg.avis_pour_scoring(a)["pays_execution"], "NGA")

    def test_fallback_pays_par_titre(self):
        # Fiche sans champ Host Country parsable : le pays vient du titre.
        fiche = ('<div class="field__label">Guarantee Holder</div>'
                 '<div class="field__item">SETRAG</div>'
                 '<div class="field__label">Environmental Category</div>'
                 '<div class="field__item">B</div>'
                 '<p><strong>Project Description</strong></p><p>Railway concession.</p>')
        a = mg.parser_fiche("/project/setrag-gabon-3", "Setrag Gabon 3", fiche)
        self.assertEqual(a["pays_iso3"], "GAB")


class TestDedup(unittest.TestCase):
    def test_dedup_slug_normalise(self):
        # gccia-transmission-line et gccia-transmission-line-0 = meme projet.
        liste = ('<div class="title"><a href="/project/gccia-transmission-line">GCCIA</a></div>'
                 '<div class="title"><a href="/project/gccia-transmission-line-0">GCCIA</a></div>')
        fiche_gccia = ('<div class="field__label">Host Country </div>'
                       '<div class="field--items"><div class="field--item">Iraq</div></div>'
                       '<div class="field__label">Environmental Category</div>'
                       '<div class="field__item">A</div>'
                       '<p><strong>Project Description</strong></p><p>Transmission line, application.</p>')
        avis, c = mg.collecte(
            session=object(),
            fetch_liste=lambda p: liste if p == 0 else "",
            fetch_fiche=lambda slug: fiche_gccia)
        self.assertEqual(c["fiches_lues"], 1)       # le doublon -0 est saute avant fetch
        self.assertEqual(len(avis), 1)
        self.assertEqual(avis[0]["publication_number"], "MIGA:gccia-transmission-line")
        self.assertEqual(avis[0]["pays_iso3"], "IRQ")


class TestTypeDocument(unittest.TestCase):
    def test_detection(self):
        self.assertIn("emis", mg._type_document("MIGA issued a guarantee of USD 300M", ""))
        self.assertIn("propose", mg._type_document("This summary covers an application by X", ""))


class TestEntonnoir(unittest.TestCase):
    def test_crible_fi(self):
        avis, c = mg.collecte(
            session=object(),
            fetch_liste=lambda p: LISTE_HTML if p == 0 else "",
            fetch_fiche=lambda slug: FICHES[slug])
        self.assertEqual(c["fiches_lues"], 2)
        self.assertEqual(c["rejet_categorie_fi"], 1)     # Al Ahli (FI) ecarte
        self.assertEqual(c["retenus"], 1)                 # Konexa (B) retenu
        self.assertEqual(len(avis), 1)
        self.assertEqual(avis[0]["pays_iso3"], "NGA")
        self.assertIn("propose", avis[0]["type_document"])

    def test_deja_vus(self):
        avis, c = mg.collecte(
            session=object(),
            fetch_liste=lambda p: LISTE_HTML if p == 0 else "",
            fetch_fiche=lambda slug: FICHES[slug],
            deja_vus={"MIGA:konexa-nbp2"})
        # Konexa saute (deja connu), Al Ahli lu puis ecarte FI -> 0 retenu.
        self.assertEqual(c["deja_connus"], 1)
        self.assertEqual(c["retenus"], 0)


class TestSchemaSheet(unittest.TestCase):
    def test_ligne_alignee(self):
        self.assertIn("publication_number", mg.COLONNES)
        original = ted.appeler_modele
        self.addCleanup(setattr, ted, "appeler_modele", original)
        ted.appeler_modele = lambda prompt, modele=None: json.dumps({
            "deploiement_terrain_reel": True, "type_mobilite": "chantier",
            "profil_personnes_exposees": "expert_international", "securite_existante": "aucune",
            "type_activite": "supervision_chantier", "type_client": "entreprise_privee",
            "duree_estimee": "longue_ou_residente", "accessibilite_commerciale": "facile",
            "profils_acteurs_probables": ["developpeur energie"],
            "besoin_securite_operationnel_probable": True,
            "niveau_opportunite_amarante": "fort",
            "justification": "Chantier solaire, personnel expatrie expose.",
            "confiance": 0.8})
        a = mg.parser_fiche("/project/konexa-nbp2", "Konexa", FICHE_B)
        extraction = mg.analyser(a)
        self.assertIsNotNone(extraction)
        self.assertIn("securite_existante_detectee", extraction)
        s, c, f = ted.calculer_scores(mg.avis_pour_scoring(a), extraction)
        r = {"avis": a, "extraction": extraction, "surete": s, "commercial": c,
             "score": f, "raffine": False, "divergence": False}
        self.assertEqual(len(mg.ligne_depuis_resultat(r)), len(mg.COLONNES))


class TestFicheExterne(unittest.TestCase):
    # Fiche IFC (redirection MIGA -> disclosures.ifc.org), balisage different :
    # "Company Name" au lieu de "Guarantee Holder", pas de field__item.
    FICHE_IFC = (
        '<h2>Environmental &amp; Social Review Summary</h2>'
        '<strong>Project Number</strong> <div>49419</div>'
        "<strong>Company Name</strong> <div>SOCIETE D'EXPLOITATION DU TRANSGABONAIS</div>"
        '<strong>Country</strong> <div>Gabon</div>'
        '<strong>Environmental Category</strong> <div>A - Significant</div>'
        '<strong>Status</strong> <div>Pending Disbursement</div>'
        '<strong>Sector</strong> <div>Rail Transportation</div>'
        '<h2>Project Description</h2>'
        '<p>SETRAG operates a 648 km railway in Gabon, 40% owned by Meridiam and '
        'part of the Eramet Group. Phase III comprises EUR 704 million of works. '
        'Contractors will deploy 150 to 200 persons across sites. SETRAG employs '
        'over 190 security agents from private security providers.</p>')

    def test_detection_externe(self):
        self.assertTrue(mg._est_fiche_externe(self.FICHE_IFC))
        self.assertFalse(mg._est_fiche_externe(FICHE_FI))     # vraie fiche MIGA

    def test_parsing_ifc_recupere_le_fort(self):
        a = mg.parser_fiche("/project/setrag-gabon-3", "Setrag Gabon 3", self.FICHE_IFC)
        self.assertEqual(a["pays_iso3"], "GAB")               # via Country ou titre
        self.assertIn("SOCIETE", a["acheteur"])                # Company Name recupere
        self.assertTrue(a["categorie_es"].upper().startswith("A"))
        self.assertEqual(a["type_document"], "fiche IFC (ESRS)")
        # Le texte riche part au LLM : sponsors et securite existante presents.
        self.assertIn("Meridiam", a["description"])
        self.assertIn("security agents", a["description"])

    def test_ifc_non_ecarte_par_crible_fi(self):
        # Categorie "A - Significant" ne doit PAS tomber sur le crible FI.
        avis, c = mg.collecte(
            session=object(),
            fetch_liste=lambda p: ('<div class="title"><a href="/project/setrag-gabon-3">'
                                   'Setrag Gabon 3</a></div>') if p == 0 else "",
            fetch_fiche=lambda slug: self.FICHE_IFC)
        self.assertEqual(c["rejet_categorie_fi"], 0)
        self.assertEqual(c["retenus"], 1)
        self.assertEqual(avis[0]["pays_iso3"], "GAB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
