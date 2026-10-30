# -*- coding: utf-8 -*-
"""Collecteur IE (IE-4) : collecte BOAMP, repli de syntaxe, filtre des avis
ouverts, ordre memoire/plafond, lignes Postgres et Sheet ARISE, mode debug.
Tout est offline : BOAMP et le LLM sont injectes, aucune base, aucun Sheet.
Les avis reprennent les intitules reels vus par les sondes IE-0 et IE-1."""

import datetime
import json
import os
import unittest

import collecteur_ie as C
import lentille_ie as L

AUJ = datetime.date(2026, 10, 30)


def rec(idweb, objet, nature="Avis de marché", limite="2026-11-27",
        parution="2026-10-28", acheteur="Acheteur", donnees=None):
    r = {"idweb": idweb, "objet": objet, "nature_libelle": nature,
         "datelimitereponse": limite, "dateparution": parution,
         "nomacheteur": acheteur}
    if donnees is not None:
        r["donnees"] = donnees
    return r


AVIS_REELS = [
    rec("26-104139", "Prestations d’expertises pour le compte de l’Agence française anticorruption (AFA)",
        acheteur="MINISTERES ECONOMIQUES ET FINANCIERS"),
    rec("26-98422", "Fourniture de solutions de veille média, de revue de presse et de veille stratégique",
        acheteur="CCI REGION NORMANDIE", parution="2026-10-13"),
    rec("26-101404", "Accompagnement à la stratégie d'attractivité économique du territoire",
        acheteur="GrandSoissons", parution="2026-10-20",
        donnees={"Description": "Mission incluant un volet d'intelligence économique territoriale et sectorielle"}),
    rec("26-80413", "ACCOMPAGNEMENT DE LA REGION ... FEUILLE DE ROUTE EN MATIERE D'INTELLIGENCE ECONOMIQUE",
        nature="Résultat de marché"),
    rec("26-91966", "Fourniture de denrées alimentaires", acheteur="Région Réunion",
        donnees={"clause": "respect de la loi Sapin II et clause anticorruption"}),
    rec("26-00001", "Prestations d'intelligence économique", limite="2026-10-01"),
]


def fetch_fixe(enregistrements):
    def fetch(url, params):
        return {"total_count": len(enregistrements),
                "results": enregistrements if params["offset"] == 0 else []}
    return fetch


def appel_llm(famille="etude_strategique", **kw):
    base = {"famille_offre": famille, "type_acheteur": "etat_ministere",
            "format": "accord_cadre_multi", "barrieres_entree": "moyenne",
            "achat_outil": False, "dimension_internationale": False,
            "sensibilite": False, "resume": "r", "confiance": 0.9}
    base.update(kw)
    return lambda prompt, modele: json.dumps(base)


class TestCollecte(unittest.TestCase):

    def setUp(self):
        C.PAUSE = 0.0

    def test_requete_limitee_au_titre(self):
        w = C.where_titre("intelligence économique", datetime.date(2026, 10, 1))
        self.assertEqual(w, "search(objet, \"intelligence économique\") "
                            "AND dateparution >= date'2026-10-01'")

    def test_repli_plein_texte_si_syntaxe_refusee(self):
        vus = []

        def fetch(url, params):
            vus.append(params["where"])
            if params["where"].startswith("search("):
                raise C.SyntaxeRefusee("ODSQL error")
            return {"results": [rec("26-1", "Due diligence")]}

        avis, journal = C.collecter(AUJ, fetch=fetch, requetes=["due diligence", "OSINT"])
        self.assertEqual(journal["mode"], "plein_texte")
        self.assertEqual(len(avis), 1)
        self.assertTrue(vus[1].startswith('"due diligence"'))
        self.assertTrue(vus[-1].startswith('"OSINT"'))   # le repli est definitif

    def test_requete_en_erreur_n_arrete_pas(self):
        def fetch(url, params):
            if "OSINT" in params["where"]:
                raise RuntimeError("503")
            return {"results": [rec("26-2", "Veille stratégique")]}

        avis, journal = C.collecter(AUJ, fetch=fetch, requetes=["OSINT", "veille stratégique"])
        self.assertEqual(len(avis), 1)
        self.assertEqual(len(journal["erreurs"]), 1)

    def test_description_tiree_des_donnees(self):
        brut = json.dumps({"ProcurementProject": {"Description": {
            "#text": "Prestations de veille stratégique et de cartographie des acteurs du territoire"}}},
            ensure_ascii=False)
        self.assertIn("cartographie des acteurs", C.description_depuis_donnees(brut))
        self.assertEqual(C.description_depuis_donnees('{"Description": "court"}'), "")


class TestFileDAnalyse(unittest.TestCase):

    def _avis(self):
        return [C.normaliser(r) for r in AVIS_REELS]

    def test_entonnoir_sur_les_avis_reels(self):
        file_, ent = C.file_d_analyse(self._avis(), set(), AUJ, budget=10)
        ids = [a["id_avis"] for a in file_]
        # Resultat et date limite depassee : fermes.
        self.assertEqual(ent["fermes"], 2)
        # Clause anticorruption du corps (Reunion) : jamais au LLM.
        self.assertNotIn("26-91966", ids)
        # Titre IE d'abord, du plus recent au plus ancien, puis corps.
        self.assertEqual(ids, ["26-104139", "26-98422", "26-101404"])

    def test_memoire_avant_plafond(self):
        # Regle 9 : le budget sert a DECOUVRIR. Avec budget 1 et l'avis AFA deja
        # vu, c'est la CCI Normandie qui doit passer, pas zero avis.
        file_, ent = C.file_d_analyse(self._avis(), {"boamp:26-104139"}, AUJ, budget=1)
        self.assertEqual([a["id_avis"] for a in file_], ["26-98422"])
        self.assertEqual(ent["deja_vus"], 1)
        self.assertEqual(ent["hors_budget"], 1)

    def test_avis_sans_date_limite_reste_ouvert(self):
        a = C.normaliser(rec("26-3", "Due diligence", limite=""))
        self.assertTrue(C.est_ouvert(a, AUJ))


class TestExecutionEtSorties(unittest.TestCase):

    def setUp(self):
        C.PAUSE = 0.0

    def test_debug_sans_llm_ni_ligne(self):
        def appel(prompt, modele):
            raise AssertionError("aucun appel LLM en debug")

        pg, sh, rap = C.executer(aujourd=AUJ, fetch=fetch_fixe(AVIS_REELS),
                                 appel=appel, debug=True, budget=10)
        self.assertEqual((pg, sh), ([], []))
        self.assertEqual(len(rap["file"]), 3)
        self.assertIn("DEBUG", C.rapport_markdown(rap, ["aucune"]))

    def test_production_lignes_pg_et_sheet(self):
        pg, sh, rap = C.executer(aujourd=AUJ, fetch=fetch_fixe(AVIS_REELS),
                                 appel=appel_llm(), debug=False, budget=10)
        self.assertEqual(len(pg), 3)
        self.assertEqual(len(sh), 3)
        self.assertEqual(len(sh[0]), len(C.COLONNES_SHEET))
        self.assertEqual(sh[0][C.COL_ID_AVIS], "26-104139")
        # Zone de saisie ARISE toujours vide a l'ecriture.
        self.assertEqual(sh[0][-2:], ["", ""])
        self.assertEqual(pg[0]["cle"], "boamp:26-104139")

    def test_ombre_et_hors_en_base_mais_pas_dans_le_sheet(self):
        for fam in ("investigation_contentieux", "sondage_statistique"):
            pg, sh, _ = C.executer(aujourd=AUJ, fetch=fetch_fixe(AVIS_REELS),
                                   appel=appel_llm(fam), debug=False, budget=10)
            self.assertEqual(len(pg), 3, fam)   # memoire + revue D1
            self.assertEqual(sh, [], fam)

    def test_extraction_ratee_non_ecrite_donc_rejouee(self):
        def appel(prompt, modele):
            return "pas de json"

        pg, sh, rap = C.executer(aujourd=AUJ, fetch=fetch_fixe(AVIS_REELS),
                                 appel=appel, debug=False, budget=10)
        self.assertEqual(pg, [])
        self.assertEqual(rap["actions"]["echec_extraction"], 3)

    def test_ecritures_sans_configuration_ne_levent_pas(self):
        self.assertIn("absent", C.ecrire_pg(None, [{"cle": "x"}]))
        self.assertIn("non configure", C.ecrire_sheet(None, [["x"]]))
        self.assertIsNone(C.memoire_pg(None))
        self.assertIn("ECHEC", C.ecrire_pg("postgresql://invalide:1/x", [
            {c: None for c in C.COLONNES_PG}]))

    @unittest.skipIf(os.environ.get("RADAR_IE", "0") not in ("", "0"),
                     "RADAR_IE force dans l'environnement")
    def test_desactive_par_defaut(self):
        self.assertFalse(L.ACTIF)


if __name__ == "__main__":
    unittest.main()
