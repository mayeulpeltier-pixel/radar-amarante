# -*- coding: utf-8 -*-
"""Sonde DECP IE (IE-1) : lexique corrige, agregation par marche,
echeances, portefeuilles, rappel et verdict testes offline sur des lignes
reprises des marches reels identifies par IE-0. Le chargement DuckDB est
exerce sur un petit Parquet genere a la volee, et se saute proprement si
DuckDB n'est pas installe (cas de la CI principale : DuckDB n'est installe
que par le workflow de la sonde)."""

import datetime
import os
import shutil
import tempfile
import unittest

import sonde_decp_ie as s

AUJ = datetime.date(2026, 10, 30)


def ligne(uid, objet, titulaire="", tid="", acheteur="Acheteur", notif="2025-05-01",
          duree=48, montant=100000.0, modif=0, rationalise=None):
    return {"uid": uid, "objet": objet, "acheteur_nom": acheteur,
            "acheteur_id": "1" * 14, "titulaire_nom": titulaire, "titulaire_id": tid,
            "montant": montant, "montant_rationalise": rationalise,
            "dateNotification": notif, "dureeMois": duree, "modification_id": modif,
            "offresRecues": 3, "codeCPV": "79311000", "procedure": "Appel d'offres ouvert"}


class TestLexiqueCorrige(unittest.TestCase):

    def test_termes_ajoutes_apres_ie0(self):
        self.assertEqual(s.classer("Cartographie des risques d'atteintes à la probité"), "ie")
        self.assertEqual(s.classer("Evaluation de l'intégrité des tiers : solution digitale"), "ie")
        self.assertEqual(s.classer("Veille Economique Locale et Veille Sectorielle"), "ie")

    def test_due_diligence_immobiliere_est_du_bruit(self):
        self.assertEqual(s.classer('vérifications dites de "due diligence" immobilières'),
                         "bruit")

    def test_revue_de_presse_seule_est_ambigue(self):
        self.assertEqual(s.classer("Revue de presse quotidienne"), "a_mesurer")
        self.assertEqual(s.classer("Veille média, revue de presse et veille stratégique"), "ie")

    def test_observatoire_hors_perimetre_sauf_noyau_ie(self):
        self.assertEqual(s.classer("Observatoire du multilatéralisme en Indopacifique"),
                         "hors_analyste")
        self.assertEqual(s.classer("Observatoire de la sécurité économique"), "ie")

    def test_titre_opaque(self):
        self.assertEqual(s.classer("2025_000314_SGA_SDPAMG_BPI"), "sans_terme")

    def test_regex_sql_echappe_et_borne(self):
        rx = s.regex_sql(["anti-corruption", "sapin ii"])
        self.assertTrue(rx.startswith(r"\b(") and rx.endswith(r")\b"))
        self.assertIn(r"anti\-corruption", rx)


class TestAgregation(unittest.TestCase):

    def test_cotitulaires_un_seul_marche_montant_non_somme(self):
        ls = [ligne("U1", "Feuille de route en matière d'intelligence économique",
                    "AMARANTE INTERNATIONAL", "11111111100011", montant=300000.0),
              ligne("U1", "Feuille de route en matière d'intelligence économique",
                    "ADIT", "22222222200022", montant=300000.0)]
        m = s.agreger(ls, aujourd=AUJ)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["montant"], 300000.0)
        self.assertEqual(m[0]["surveilles"], ["adit", "amarante"])
        self.assertEqual(len(m[0]["titulaires"]), 2)

    def test_montant_rationalise_prioritaire(self):
        m = s.agreger([ligne("U2", "Due diligence", montant=9e12, rationalise=50000.0)],
                      aujourd=AUJ)
        self.assertEqual(m[0]["montant"], 50000.0)

    def test_derniere_modification_retenue(self):
        ls = [ligne("U3", "Veille stratégique", duree=12, modif=0),
              ligne("U3", "Veille stratégique", duree=24, modif=1)]
        self.assertEqual(s.agreger(ls, aujourd=AUJ)[0]["duree_mois"], 24)

    def test_fin_estimee_et_mois_restants(self):
        m = s.agreger([ligne("U4", "Intelligence économique", notif="2025-05-15",
                             duree=48)], aujourd=AUJ)[0]
        self.assertEqual(m["fin"], datetime.date(2029, 5, 15))
        self.assertEqual(m["mois_restants"], 31)

    def test_duree_absente_pas_de_fin(self):
        m = s.agreger([ligne("U5", "Intelligence économique", duree=None)], aujourd=AUJ)[0]
        self.assertIsNone(m["fin"])

    def test_ajouter_mois_fin_de_mois_et_negatif(self):
        self.assertEqual(s.ajouter_mois(datetime.date(2024, 1, 31), 1),
                         datetime.date(2024, 2, 29))
        self.assertEqual(s.ajouter_mois(datetime.date(2026, 10, 30), -6),
                         datetime.date(2026, 4, 30))

    def test_mot_entier_pour_les_titulaires(self):
        m = s.agreger([ligne("U6", "Veille stratégique", "EXPEDITION ADITIVE SA")],
                      aujourd=AUJ)[0]
        self.assertEqual(m["surveilles"], [])


class TestAnalyseEtVerdict(unittest.TestCase):

    def _marches(self):
        ls = [
            # Echeance dans 8 mois : doit sortir en renouvellement.
            ligne("A", "Cellule d'Intelligence Stratégique", "AMARANTE RISK INTELLIGENCE",
                  "50987922700023", acheteur="AUVERGNE-RHONE-ALPES ENTREPRISES",
                  notif="2024-07-04", duree=36),
            # Echeance en 2029 : hors fenetre.
            ligne("B", "24N0423 Prestations intelligence économique", "ADIT", "3",
                  acheteur="METROPOLE NICE COTE D'AZUR", notif="2025-05-15", duree=48),
            # Titre opaque gagne par Amarante.
            ligne("C", "2025_000314_SGA_SDPAMG_BPI", "AMARANTE INTERNATIONAL", "4",
                  acheteur="MINARM", notif="2026-10-01", duree=36),
            # Expire il y a 3 mois : remise en concurrence probable en cours.
            ligne("D", "Mise en place d'un dispositif conformité loi Sapin II",
                  "CABINET X", "5", notif="2023-07-30", duree=36),
        ]
        return s.agreger(ls, aujourd=AUJ)

    def test_fenetre_des_echeances(self):
        rap = s.analyser(self._marches(), aujourd=AUJ)
        fins = [m["cle"] for m in rap["renouvellements"]]
        self.assertEqual(fins, ["D", "A"])  # trie par date de fin

    def test_portefeuille_amarante_et_opaques(self):
        rap = s.analyser(self._marches(), aujourd=AUJ)
        p = rap["portefeuilles"]["amarante"]
        self.assertEqual(len(p["marches"]), 2)
        self.assertEqual(p["ie"], 1)
        self.assertEqual(p["opaques"], 1)
        self.assertEqual(p["ids"], ["4", "50987922700023"])

    def test_rappel(self):
        r = s.evaluer_rappel(self._marches())
        trouves = {x["libelle"][:4]: x["trouve"] for x in r}
        self.assertTrue(trouves["AURA"])
        self.assertTrue(trouves["Metr"])
        self.assertFalse(trouves["Regi"])

    def test_verdicts(self):
        marches = self._marches()
        rap = s.analyser(marches, aujourd=AUJ)
        self.assertEqual(s.verdict(rap, s.evaluer_rappel(marches))[0], "EXPLOITABLE")
        vide = s.analyser([], aujourd=AUJ)
        self.assertEqual(s.verdict(vide, [], charge_ok=False)[0], "NON CONCLU")
        self.assertEqual(s.verdict(vide, [])[0], "NON CONCLU")
        rap_rien = s.analyser([m for m in marches if m["classe"] != "ie"], aujourd=AUJ)
        self.assertEqual(s.verdict(rap_rien, [])[0], "INFAISABLE")
        rappel_vide = [{"libelle": "x", "trouve": False, "marches": []}]
        self.assertEqual(s.verdict(rap, rappel_vide)[0], "COUVERTURE A VERIFIER")

    def test_limite_si_durees_inconnues(self):
        ls = [ligne(str(i), "Intelligence économique", acheteur="REGION HAUTS DE FRANCE",
                    duree=None) for i in range(3)]
        marches = s.agreger(ls, aujourd=AUJ)
        rap = s.analyser(marches, aujourd=AUJ)
        self.assertEqual(s.verdict(rap, s.evaluer_rappel(marches))[0], "LIMITE")

    def test_rapport_et_csv(self):
        marches = self._marches()
        rap = s.analyser(marches, aujourd=AUJ)
        rappel = s.evaluer_rappel(marches)
        v = s.verdict(rap, rappel)
        md = s.rapport_markdown(rap, rappel, v, 1000, ["uid"], AUJ)
        self.assertIn("## Verdict : EXPLOITABLE", md)
        self.assertIn("## Portefeuille AMARANTE", md)
        d = tempfile.mkdtemp()
        try:
            chemin = os.path.join(d, "m.csv")
            s.ecrire_csv(marches, chemin)
            with open(chemin, encoding="utf-8") as f:
                self.assertEqual(len(f.read().splitlines()), 5)
        finally:
            shutil.rmtree(d)


try:
    import duckdb  # noqa: F401
    DUCKDB = True
except ImportError:
    DUCKDB = False


@unittest.skipUnless(DUCKDB, "DuckDB absent : installe seulement par le workflow de la sonde")
class TestChargementParquet(unittest.TestCase):

    def test_prefiltre_lexique_titulaires_et_versions(self):
        import duckdb
        d = tempfile.mkdtemp()
        try:
            chemin = os.path.join(d, "decp.parquet")
            con = duckdb.connect()
            con.execute("""
                CREATE TABLE t AS SELECT * FROM (VALUES
                  ('U1', 'Prestations d''intelligence ÉCONOMIQUE', 'ADIT', true),
                  ('U2', 'Fourniture de papier', 'PAPETERIE', true),
                  ('U3', '2025_000314_SGA', 'AMARANTE INTERNATIONAL', true),
                  ('U4', 'Veille stratégique', 'CABINET', false),
                  ('U5', 'Travaux de voirie', 'EXPEDITION ADITIVE', true)
                ) AS v(uid, objet, titulaire_nom, donneesActuelles)""")
            con.execute("COPY t TO '{}' (FORMAT PARQUET)".format(chemin))
            lignes, colonnes, total = s.charger(chemin)
            self.assertEqual(total, 5)
            self.assertEqual(sorted(l["uid"] for l in lignes), ["U1", "U3"])
            self.assertIn("donneesActuelles", colonnes)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
