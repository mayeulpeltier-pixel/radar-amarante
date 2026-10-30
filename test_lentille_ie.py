# -*- coding: utf-8 -*-
"""Lentille IE (IE-3) : pre-filtre, validation de l'extraction, bareme et
chaine d'analyse, testes offline sur les intitules REELS remontes par les
sondes IE-0 (BOAMP) et IE-1 (DECP) du 30/10/2026. Aucun appel reseau :
l'appel LLM est injecte."""

import json
import unittest

import lentille_ie as L


def ext(**kw):
    base = {"famille_offre": "etude_strategique", "type_acheteur": "region_agence_dev",
            "format": "accord_cadre_mono", "barrieres_entree": "moyenne",
            "achat_outil": False, "dimension_internationale": False,
            "sensibilite": False, "resume": "", "confiance": 0.9}
    base.update(kw)
    return L.valider(base)


class TestPrefiltreSurIntitulesReels(unittest.TestCase):

    def test_references_ie_retenues_par_le_titre(self):
        for titre in ("Cellule d'Intelligence Stratégique",
                      "24N0423 - Prestations d'intelligence économique",
                      "Prestations d’expertises pour le compte de l’Agence française anticorruption (AFA)",
                      "Evaluation de l'intégrité des tiers : solution digitale (relance)",
                      "Prestation en vue de la rédaction d'une cartographie des risques d'atteintes à la probité",
                      "Veille Economique Locale et Veille Sectorielle",
                      "RFQ for Provision of Enhanced Due Diligence on a bank in Myanmar",
                      "Provision of OSINT (Open-source intelligence) Reports on Migration"):
            self.assertEqual(L.prefiltre(titre)[0], "ie_titre", titre)

    def test_clause_anticorruption_du_corps_ignoree(self):
        # IE-0 : ~165 faux positifs (Region Reunion, CCI Bordeaux, GHT).
        classe, _ = L.prefiltre("Travaux de signalisation horizontale",
                                "clause anticorruption ; respect de la loi Sapin II")
        self.assertEqual(classe, "sans_terme")

    def test_terme_ie_specifique_dans_le_corps_retenu(self):
        classe, termes = L.prefiltre("Accompagnement à la stratégie d'attractivité",
                                     "dont un volet d'intelligence économique")
        self.assertEqual(classe, "ie_corps")
        self.assertIn("intelligence economique", termes)

    def test_bruit_et_hors_perimetre(self):
        self.assertEqual(L.prefiltre("Enquête de satisfaction des touristes")[0], "bruit")
        self.assertEqual(L.prefiltre('Vérifications dites de "due diligence" immobilières')[0],
                         "bruit")
        self.assertEqual(L.prefiltre("Observatoire du multilatéralisme en Indopacifique")[0],
                         "hors_analyste")
        self.assertEqual(L.prefiltre("Observatoire de la sécurité économique")[0], "ie_titre")

    def test_evenements_cyber_ecartes(self):
        # IE-6 (30/10) : Limoges Metropole, "organisation d'un CTF OSINT".
        for titre in ("1A RENCONTRES PROFESSIONNELLES DE LA CYBER LOT 2 ORGANISATION D UN CTF OSINT",
                      "RENCONTRES PROFESSIONNELLES DE LA CYBERSECURITE (EN 2 LOTS) - OSINT"):
            self.assertEqual(L.prefiltre(titre)[0], "bruit", titre)
        self.assertEqual(L.prefiltre("Rapports OSINT sur les migrations")[0], "ie_titre")

    def test_ambigus_et_vides(self):
        self.assertEqual(L.prefiltre("Revue de presse quotidienne")[0], "a_mesurer")
        self.assertEqual(L.prefiltre("Business intelligence dashboard licences")[0], "a_mesurer")
        self.assertEqual(L.prefiltre("Fourniture de papier")[0], "sans_terme")
        self.assertEqual(L.prefiltre("2025_000314_SGA_SDPAMG_BPI")[0], "sans_terme")

    def test_seules_les_classes_ie_partent_au_llm(self):
        self.assertTrue(L.merite_llm("ie_titre"))
        self.assertTrue(L.merite_llm("ie_corps"))
        for c in ("a_mesurer", "bruit", "hors_analyste", "sans_terme"):
            self.assertFalse(L.merite_llm(c))


class TestValidation(unittest.TestCase):

    def test_json_entoure_de_texte_et_de_blocs(self):
        brut = 'Voici :\n```json\n{"famille_offre": "veille_strategique", "confiance": 0.8}\n```'
        self.assertEqual(L.parser_json(brut)["famille_offre"], "veille_strategique")

    def test_reponses_inexploitables(self):
        for t in (None, "", "pas de json", "{cassé", "[1, 2]"):
            self.assertIsNone(L.parser_json(t), repr(t))
        self.assertIsNone(L.valider(None))

    def test_valeurs_hors_grille_ramenees_au_prudent(self):
        v = L.valider({"famille_offre": "espionnage", "type_acheteur": "?",
                       "format": "x", "barrieres_entree": "enorme",
                       "achat_outil": "oui", "confiance": "7"})
        self.assertEqual(v["famille_offre"], "hors_ie")
        self.assertEqual(v["type_acheteur"], "autre")
        self.assertEqual(v["format"], "inconnu")
        self.assertEqual(v["barrieres_entree"], "moyenne")
        self.assertTrue(v["achat_outil"])
        self.assertEqual(v["confiance"], 1.0)

    def test_prompt_formate_sans_erreur_d_accolades(self):
        p = L.construire_prompt({"titre": "Cellule {IE}", "acheteur": "AURA",
                                 "description": "x" * 5000})
        self.assertIn("Cellule {IE}", p)
        self.assertIn('"famille_offre"', p)
        self.assertLess(len(p), 8000)


class TestBareme(unittest.TestCase):

    def test_cellule_ie_regionale_a_contacter(self):
        n = L.noter(ext(famille_offre="etude_strategique", dimension_internationale=True))
        self.assertEqual(n["perimetre"], "affiche")
        self.assertEqual(n["action"], "contacter")

    def test_afa_accord_cadre_multi_barrieres_fortes(self):
        n = L.noter(ext(famille_offre="conformite_anticorruption",
                        type_acheteur="etat_ministere", format="accord_cadre_multi",
                        barrieres_entree="forte"))
        self.assertGreaterEqual(n["score"], L.SEUIL_SURVEILLER)

    def test_achat_d_outil_jamais_contacter(self):
        # IE-1 : KB Crawl, Coexel, Infolegale... ne sont pas des prestations ARISE.
        n = L.noter(ext(famille_offre="veille_strategique", achat_outil=True,
                        type_acheteur="region_agence_dev", format="accord_cadre_multi",
                        barrieres_entree="faible", sensibilite=True))
        self.assertNotEqual(n["action"], "contacter")

    def test_hors_perimetre_ignore_meme_si_acheteur_ideal(self):
        for fam in ("etude_geopolitique", "sondage_statistique", "audit_cyber", "hors_ie"):
            n = L.noter(ext(famille_offre=fam, format="accord_cadre_multi",
                            barrieres_entree="faible"))
            self.assertEqual(n["action"], "ignorer", fam)
            self.assertEqual(n["perimetre"], "hors", fam)

    def test_ombre_journalise_sans_affichage(self):
        n = L.noter(ext(famille_offre="investigation_contentieux"))
        self.assertEqual(n["perimetre"], "ombre")
        self.assertEqual(n["action"], "ombre")

    def test_amarante_sortant_augmente_le_commercial(self):
        a = L.noter(ext())
        b = L.noter(ext(), contexte={"amarante_sortant": True})
        self.assertAlmostEqual(b["commercial"] - a["commercial"], 1.5)

    def test_montant_sans_effet_sur_le_score(self):
        # Regle du projet : pas de potentiel en euros.
        e = ext()
        self.assertEqual(L.noter(e)["score"],
                         L.noter(e, contexte={"montant": 3800000})["score"])

    def test_extraction_echouee(self):
        n = L.noter(None)
        self.assertEqual((n["action"], n["perimetre"]), ("ignorer", "hors"))

    def test_motifs_expliquent_la_note(self):
        n = L.noter(ext(dimension_internationale=True, achat_outil=True))
        texte = " ".join(n["motifs"])
        self.assertIn("internationale", texte)
        self.assertIn("outil", texte)

    def test_bornes(self):
        n = L.noter(ext(famille_offre="cartographie_acteurs", dimension_internationale=True,
                        sensibilite=True, type_acheteur="region_agence_dev",
                        format="accord_cadre_multi", barrieres_entree="faible"),
                    contexte={"amarante_sortant": True})
        self.assertLessEqual(n["adequation"], 10.0)
        self.assertLessEqual(n["commercial"], 10.0)


class TestChaineAvecAppelInjecte(unittest.TestCase):

    AVIS = {"titre": "Cellule d'Intelligence Stratégique", "acheteur": "AURA Entreprises",
            "description": "Prestations en IE pour l'internationalisation des entreprises",
            "source": "BOAMP"}

    def _rep(self, **kw):
        base = {"famille_offre": "etude_strategique", "type_acheteur": "region_agence_dev",
                "format": "accord_cadre_mono", "barrieres_entree": "moyenne",
                "achat_outil": False, "dimension_internationale": True,
                "sensibilite": False, "resume": "Cellule IE", "confiance": 0.9}
        base.update(kw)
        return json.dumps(base)

    def test_lead_prometteur_escalade_vers_le_modele_fort(self):
        modeles = []

        def appel(prompt, modele):
            modeles.append(modele)
            return self._rep()

        r = L.analyser(self.AVIS, appel=appel)
        self.assertEqual(modeles, [L.MODELE, L.MODELE_RAFFINEMENT])
        self.assertTrue(r["escalade"])
        self.assertEqual(r["note"]["action"], "contacter")

    def test_escalade_ratee_garde_le_premier_resultat(self):
        appels = []

        def appel(prompt, modele):
            appels.append(modele)
            if modele == L.MODELE_RAFFINEMENT:
                raise RuntimeError("529 overloaded")
            return self._rep()

        r = L.analyser(self.AVIS, appel=appel)
        self.assertFalse(r["escalade"])
        self.assertEqual(r["note"]["action"], "contacter")

    def test_hors_perimetre_confiant_sans_escalade(self):
        modeles = []

        def appel(prompt, modele):
            modeles.append(modele)
            return self._rep(famille_offre="sondage_statistique", confiance=0.95)

        r = L.analyser(self.AVIS, appel=appel)
        self.assertEqual(modeles, [L.MODELE])
        self.assertEqual(r["note"]["action"], "ignorer")

    def test_echec_total_ne_leve_pas(self):
        def appel(prompt, modele):
            raise RuntimeError("cle revoquee")

        r = L.analyser(self.AVIS, appel=appel)
        self.assertIsNone(r["extraction"])
        self.assertEqual(r["note"]["action"], "ignorer")

    def test_import_sans_effet_de_bord(self):
        self.assertIn(L.ACTIF, (True, False))
        self.assertTrue(L.MODELE and L.MODELE_RAFFINEMENT)


if __name__ == "__main__":
    unittest.main()
