# -*- coding: utf-8 -*-
"""Tableau de santé des sources : analyse pure (fraîcheur, classe, état, tri) et
construction des stats depuis un curseur injecté. Offline, aucun réseau.

Verrou anti-faux-positif (01/11/2026) : une source LENTE sans nouveauté depuis
des mois n'est PAS « muette », une sonde / référence / source inactive n'est
jamais une alarme. Seule une vraie panne doit remonter en tête."""

import datetime
import unittest

import sante_sources as S

AUJ = datetime.date(2026, 11, 1)


class TestAnalyse(unittest.TestCase):

    def test_jours_depuis(self):
        self.assertEqual(S.jours_depuis("2026-10-22", AUJ), 10)
        self.assertEqual(S.jours_depuis("2026-10-22T09:00:00Z", AUJ), 10)
        self.assertIsNone(S.jours_depuis("", AUJ))
        self.assertIsNone(S.jours_depuis("pas une date", AUJ))

    def test_etat(self):
        self.assertEqual(S.etat(2, 100), "OK")
        self.assertEqual(S.etat(15, 100), "VIEILLIT")      # > 10
        self.assertEqual(S.etat(40, 100), "MUET")          # > 30
        self.assertEqual(S.etat(10, 100), "OK")            # pile seuil vieillit -> encore OK
        self.assertEqual(S.etat(30, 100), "VIEILLIT")      # pile seuil muet -> pas encore muet
        self.assertEqual(S.etat(None, 0), "VIDE")
        self.assertEqual(S.etat(None, None), "INDISPONIBLE")
        self.assertEqual(S.etat(None, 5), "SANS DATE")

    def test_etat_seuils_elargis(self):
        # Avec des seuils de source lente, 150 j n'est pas encore muet.
        self.assertEqual(S.etat(150, 100, S.SEUIL_VIEILLIT_LENT, S.SEUIL_MUET_LENT),
                         "VIEILLIT")
        self.assertEqual(S.etat(250, 100, S.SEUIL_VIEILLIT_LENT, S.SEUIL_MUET_LENT),
                         "MUET")


class TestClasse(unittest.TestCase):

    def test_classe_defaut_live(self):
        self.assertEqual(S.classe("Operations / ted_radar"), "live")
        self.assertEqual(S.classe("IE leads / BOAMP"), "live")

    def test_classe_lente(self):
        self.assertEqual(S.classe("Operations / dfc_radar"), "lent")
        self.assertEqual(S.classe("Operations / proparco_radar"), "lent")
        self.assertEqual(S.classe("IE vigilance"), "lent")

    def test_classe_sonde_reference_inactif(self):
        self.assertEqual(S.classe("Operations / prozorro_radar"), "sonde")
        self.assertEqual(S.classe("Operations / comptes_cibles_bitd"), "reference")
        self.assertEqual(S.classe("Operations / boamp_radar"), "inactif")
        self.assertEqual(S.classe("Operations / afdb_radar"), "inactif")

    def test_seuils_selon_classe(self):
        self.assertEqual(S.seuils("live"), (S.SEUIL_VIEILLIT, S.SEUIL_MUET))
        self.assertEqual(S.seuils("lent"), (S.SEUIL_VIEILLIT_LENT, S.SEUIL_MUET_LENT))


class TestEtatSource(unittest.TestCase):
    """Le coeur de la correction : la classe change le verdict à fraîcheur égale."""

    def test_source_lente_pas_muette(self):
        # DFC sans nouveauté depuis 150 j : VIEILLIT (surveiller), PAS MUET (panne).
        cl, et = S.etat_source("Operations / dfc_radar", 150, 75)
        self.assertEqual(cl, "lent")
        self.assertEqual(et, "VIEILLIT")

    def test_source_vive_muette_au_meme_age(self):
        # Même fraîcheur, source vive : là c'est bien une panne.
        cl, et = S.etat_source("Operations / bm_radar", 150, 75)
        self.assertEqual(cl, "live")
        self.assertEqual(et, "MUET")

    def test_sonde_jamais_muette(self):
        cl, et = S.etat_source("Operations / prozorro_radar", 97, 12)
        self.assertEqual(cl, "sonde")
        self.assertEqual(et, "SONDE")

    def test_inactif_jamais_muet(self):
        cl, et = S.etat_source("Operations / boamp_radar", 300, 5)
        self.assertEqual(et, "INACTIF")

    def test_reference_jamais_muette(self):
        cl, et = S.etat_source("Operations / watchlist_prives", 90, 408)
        self.assertEqual(et, "RÉFÉRENCE")

    def test_sans_alarme_vide_reste_mesure(self):
        # Une classe sans alarme mais SANS donnée retombe sur la mesure (vide/absent) :
        # on ne masque pas une table inexistante derrière une étiquette rassurante.
        _, et = S.etat_source("Operations / prozorro_radar", None, 0)
        self.assertEqual(et, "VIDE")
        _, et = S.etat_source("Operations / prozorro_radar", None, None)
        self.assertEqual(et, "INDISPONIBLE")


class TestEnrichirEtTri(unittest.TestCase):

    def test_enrichir_et_tri(self):
        stats = [{"source": "a", "lignes": 100, "dernier": "2026-10-31"},   # OK (live)
                 {"source": "b", "lignes": 5, "dernier": "2026-08-01"},     # MUET (live, 92 j)
                 {"source": "c", "lignes": None, "dernier": None}]          # INDISPONIBLE
        out = S.enrichir(stats, AUJ)
        self.assertEqual([x["source"] for x in out], ["c", "b", "a"])
        self.assertEqual(out[-1]["etat"], "OK")
        self.assertEqual(out[1]["etat"], "MUET")

    def test_lente_et_sonde_descendent_sous_les_pannes(self):
        stats = [{"source": "Operations / dfc_radar", "lignes": 75, "dernier": "2026-06-01"},  # lent, VIEILLIT
                 {"source": "Operations / prozorro_radar", "lignes": 12, "dernier": "2026-07-01"},  # SONDE
                 {"source": "Operations / bm_radar", "lignes": 5, "dernier": "2026-06-01"}]    # live, MUET
        out = S.enrichir(stats, AUJ)
        # La vraie panne (bm_radar MUET) remonte avant la lente et la sonde.
        self.assertEqual(out[0]["source"], "Operations / bm_radar")
        self.assertEqual(out[0]["etat"], "MUET")
        etats = {x["source"]: x["etat"] for x in out}
        self.assertEqual(etats["Operations / dfc_radar"], "VIEILLIT")
        self.assertEqual(etats["Operations / prozorro_radar"], "SONDE")

    def test_resume(self):
        c = S.resume([{"etat": "OK"}, {"etat": "OK"}, {"etat": "MUET"}])
        self.assertEqual(c, {"OK": 2, "MUET": 1})


class FauxCurseur:
    """Simule psycopg : renvoie des réponses programmées, ou lève pour une table."""
    def __init__(self, grouped=None, scalaire=None, casse=()):
        self.grouped = grouped or {}      # table -> [(grp, n, dernier), ...]
        self.scalaire = scalaire or {}    # table -> (n, dernier)
        self.casse = set(casse)           # tables qui lèvent
        self._res = None

    def execute(self, sql):
        table = next((t for t in list(self.grouped) + list(self.scalaire) + list(self.casse)
                      if (" " + t + " ") in sql or sql.endswith(t)), None)
        if table in self.casse:
            raise RuntimeError("relation absente")
        if "GROUP BY" in sql:
            self._res = ("g", self.grouped.get(table, []))
        else:
            self._res = ("s", self.scalaire.get(table, (0, None)))

    def fetchall(self):
        return self._res[1]

    def fetchone(self):
        return self._res[1]


class TestStatsTable(unittest.TestCase):

    def test_groupe(self):
        cur = FauxCurseur(grouped={"radar_lignes": [("ted", 120, "2026-10-30"),
                                                    ("bm", 40, "2026-09-01")]})
        out = S._stats_table(cur, "Operations", "radar_lignes", "date_detection", "onglet")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["source"], "Operations / ted")
        self.assertEqual(out[0]["lignes"], 120)
        self.assertEqual(out[0]["dernier"], "2026-10-30")

    def test_scalaire(self):
        cur = FauxCurseur(scalaire={"radar_ie_vigilance": (138, "2026-11-01")})
        out = S._stats_table(cur, "IE vigilance", "radar_ie_vigilance", "detecte_le", None)
        self.assertEqual(out, [{"source": "IE vigilance", "lignes": 138, "dernier": "2026-11-01"}])

    def test_table_absente(self):
        cur = FauxCurseur(casse=("radar_ie_echeances",))
        out = S._stats_table(cur, "IE echeances", "radar_ie_echeances", "maj_le", None)
        self.assertEqual(out, [{"source": "IE echeances", "lignes": None, "dernier": None}])
        self.assertEqual(S.etat(None, out[0]["lignes"]), "INDISPONIBLE")


class TestSources(unittest.TestCase):

    def test_echeances_lit_maj_le(self):
        # Verrou de regression : la colonne `cree_le` n'existe pas sur
        # radar_ie_echeances ; la seule date de collecte est `maj_le`.
        ech = [s for s in S.SOURCES if s[0] == "IE echeances"]
        self.assertEqual(ech[0][2], "maj_le")


class TestCollecter(unittest.TestCase):

    def test_sans_url(self):
        self.assertEqual(S.collecter(None), [])


if __name__ == "__main__":
    unittest.main()
