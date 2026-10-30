# -*- coding: utf-8 -*-
"""Echeances IE (IE-6) : regroupement des lots, SIREN contre homonymes,
fenetre d'echeance, exclusion des outils, montants douteux, lignes Sheet et
Postgres. Donnees reprises des cas REELS de la sonde IE-1 (AFA, PACA, AFD,
France Televisions, homonymes). Le chargement DuckDB est teste sur un petit
Parquet genere, et se saute si DuckDB est absent (CI principale)."""

import datetime
import os
import shutil
import tempfile
import unittest

import echeances_ie as E

AUJ = datetime.date(2026, 10, 30)
ACH_AFA = "13000000000011"


def l(uid, objet, titulaire="", tid="", acheteur="ACHETEUR", aid="1" * 14,
      notif="2023-02-06", duree=48, montant=100000.0, rationalise=None, modif=0):
    return {"uid": uid, "objet": objet, "acheteur_nom": acheteur, "acheteur_id": aid,
            "titulaire_nom": titulaire, "titulaire_id": tid, "montant": montant,
            "montant_rationalise": rationalise, "dateNotification": notif,
            "dureeMois": duree, "modification_id": modif, "offresRecues": 4,
            "acheteur_region_nom": "Ile-de-France"}


OBJ_AFA = "Prestations d’expertises pour le compte de l’agence française anti-corruption"


def lignes_afa():
    # 3 lots, 7 titulaires : UNE procedure.
    return [l("A1", OBJ_AFA, "ADIT", "44874736000015", aid=ACH_AFA, montant=3.8e6),
            l("A1", OBJ_AFA, "SIA PARTNERS", "11111111100011", aid=ACH_AFA, montant=3.8e6),
            l("A2", OBJ_AFA, "ADIT", "44874736000015", aid=ACH_AFA, montant=299000.0),
            l("A3", OBJ_AFA, "GRANT THORNTON CONSEIL", "22222222200022", aid=ACH_AFA,
              montant=330000.0)]


class TestRegroupementEtSiren(unittest.TestCase):

    def test_lots_regroupes_en_une_procedure(self):
        p = E.regrouper(lignes_afa(), AUJ)
        self.assertEqual(len(p), 1)
        p = p[0]
        self.assertEqual(p["nb_lots"], 3)
        self.assertEqual(p["plafond"], 3.8e6)
        self.assertEqual(len(p["titulaires"]), 3)
        self.assertEqual(p["fin"], datetime.date(2027, 2, 6))
        self.assertEqual(p["position"], "concurrent_suivi")
        self.assertEqual(p["sortants_suivis"], ["ADIT"])
        self.assertEqual(p["expertise"], "ie")
        self.assertEqual(p["lien"], "https://colibre.fr/marches/A1")

    def test_homonymes_ecartes_par_siren(self):
        ls = [l("H1", "Serrurerie menuiserie", "BONHEUR D'AMARANTE", "79180685400018"),
              l("H2", "Entretien des toilettes sèches", "ADIT", "82754416400011")]
        for p in E.regrouper(ls, AUJ):
            self.assertEqual(p["position"], "autre", p["objet"])

    def test_vrai_siret_amarante(self):
        p = E.regrouper([l("P1", "Parcours d'accompagnement en intelligence et sécurité "
                           "économique auprès de TPE PME", "AMARANTE RISK INTELLIGENCE",
                           "50987922700023", notif="2025-05-07", duree=24)], AUJ)[0]
        self.assertEqual(p["position"], "amarante_sortant")
        self.assertEqual(p["fin"], datetime.date(2027, 5, 7))

    def test_siren(self):
        self.assertEqual(E.siren("509 879 227 00023"), "509879227")
        self.assertEqual(E.siren("509879227"), "509879227")
        self.assertEqual(E.siren("FR12345"), "")

    def test_montants_douteux_masques(self):
        bas = E.regrouper([l("M1", "Enquêtes de notoriété et due diligence", montant=340.0)], AUJ)[0]
        haut = E.regrouper([l("M2", "Prestation intelligence économique", montant=36e6)], AUJ)[0]
        for p in (bas, haut):
            self.assertIsNone(p["plafond"])
            self.assertTrue(p["montant_douteux"])

    def test_montant_rationalise_prioritaire(self):
        p = E.regrouper([l("M3", "Due diligence", montant=9e12, rationalise=50000.0)], AUJ)[0]
        self.assertEqual(p["plafond"], 50000.0)

    def test_derniere_modification_fait_foi(self):
        ls = [l("D1", "Veille stratégique", duree=12, modif=0),
              l("D1", "Veille stratégique", duree=36, modif=1)]
        self.assertEqual(E.regrouper(ls, AUJ)[0]["duree_mois"], 36)


class TestFenetreEtVues(unittest.TestCase):

    def _procs(self):
        ls = lignes_afa() + [
            # Outil : en base, jamais dans la vue ARISE.
            l("O1", "Acquisition d’un outil de veille stratégique", "KB CRAWL", "3" * 14,
              notif="2022-06-16", duree=48),
            # Observatoire gagne par Amarante : base (analyste), pas ARISE.
            l("OB", "Observatoire sur l’analyse d’auditoires d’intérêt pour la France",
              "AMARANTE INTERNATIONAL", "49965596700044", notif="2023-10-30", duree=36),
            # Securite AFD (titre sans terme) : base car portefeuille, pas ARISE.
            l("AFD", "Appui sécurité mutualisé auprès des 15 directions régionales",
              "AMARANTE INTERNATIONAL", "49965596700044", notif="2023-06-14", duree=48,
              montant=4.5e6),
            # Hors fenetre (fin 2029) : nulle part.
            l("NICE", "DA - PRESTATION INTELLIGENCE ECONOMIQUE", "ADIT", "44874736000015",
              notif="2025-09-25", duree=48),
            # Expire il y a 3 mois : remise en concurrence en cours, retenu.
            l("TIS", "Prévention des risques d’atteinte à la probité", "ADIT",
              "44874736000015", notif="2024-07-12", duree=24),
        ]
        return E.regrouper(ls, AUJ)

    def test_base_et_vue_arise(self):
        base, arise = E.retenir(self._procs(), AUJ)
        en_base = {p["uids"][0] for p in base}
        self.assertEqual(en_base, {"A1", "O1", "OB", "AFD", "TIS"})
        self.assertEqual([p["uids"][0] for p in arise], ["TIS", "A1"])

    def test_outil_detecte(self):
        self.assertTrue(E.est_outil("Accès à une solution de veille stratégique en mode SaaS"))
        self.assertTrue(E.est_outil("Acquisition d’un outil de veille stratégique"))
        self.assertFalse(E.est_outil("Prestations d'intelligence économique"))

    def test_ligne_sheet_et_zone_arise_vide(self):
        _, arise = E.retenir(self._procs(), AUJ)
        ligne = E.ligne_sheet(arise[1], AUJ)
        self.assertEqual(len(ligne), len(E.COLONNES_SHEET))
        self.assertEqual(ligne[E.COLONNES_SHEET.index("fin_estimee")], "2027-02-06")
        self.assertEqual(ligne[E.COLONNES_SHEET.index("plafond_eur")], 3800000)
        self.assertEqual(ligne[-2:], ["", ""])

    def test_seules_les_nouvelles_procedures_sont_ajoutees(self):
        _, arise = E.retenir(self._procs(), AUJ)
        deja = {arise[0]["cle"]}
        nouvelles = E.nouvelles_lignes(deja, arise, AUJ)
        self.assertEqual(len(nouvelles), 1)
        self.assertEqual(nouvelles[0][E.COL_CLE], arise[1]["cle"])

    def test_valeurs_pg_alignees(self):
        base, _ = E.retenir(self._procs(), AUJ)
        self.assertEqual(len(E.valeurs_pg(base[0])), len(E.COLONNES_PG))

    def test_rapport(self):
        procs = self._procs()
        base, arise = E.retenir(procs, AUJ)
        md = E.rapport_markdown(1000, procs, base, arise, ["aucune (mode debug)"], True)
        self.assertIn("Portefeuille Amarante", md)
        self.assertIn("https://colibre.fr/marches/A1", md)

    def test_ecritures_sans_configuration(self):
        self.assertIn("absent", E.ecrire_pg(None, [{}]))
        self.assertIn("non configure", E.ecrire_sheet(None, [["x"]]))


try:
    import duckdb  # noqa: F401
    DUCKDB = True
except ImportError:
    DUCKDB = False


@unittest.skipUnless(DUCKDB, "DuckDB absent : installe seulement par le workflow IE")
class TestChargement(unittest.TestCase):

    def test_prefiltre_lexique_et_siren(self):
        import duckdb
        d = tempfile.mkdtemp()
        try:
            chemin = os.path.join(d, "decp.parquet")
            con = duckdb.connect()
            con.execute("""CREATE TABLE t AS SELECT * FROM (VALUES
              ('U1', 'Prestations d''intelligence ÉCONOMIQUE', 'X', '11111111100011', true),
              ('U2', 'Fourniture de papier', 'Y', '22222222200022', true),
              ('U3', 'Appui sécurité AFD', 'AMARANTE INTERNATIONAL', '49965596700044', true),
              ('U4', 'Serrurerie', 'BONHEUR D''AMARANTE', '79180685400018', true),
              ('U5', 'Veille stratégique', 'Z', '33333333300033', false)
            ) AS v(uid, objet, titulaire_nom, titulaire_id, donneesActuelles)""")
            con.execute("COPY t TO '{}' (FORMAT PARQUET)".format(chemin))
            lignes, total = E.charger(chemin)
            self.assertEqual(total, 5)
            self.assertEqual(sorted(x["uid"] for x in lignes), ["U1", "U3"])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
