# -*- coding: utf-8 -*-
"""Collecteur presse M&A IE : RSS parsing, dedup, pre-filtre lexical, lentille
(LLM simule), action deterministe, alignement du stockage. Offline, aucun
reseau ni LLM. Mutation : voir les asserts de pre-filtre et d'action."""

import datetime
import unittest

import collecteur_presse_ma_ie as P

AUJ = datetime.date(2026, 11, 3)
MOIS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
        "Oct", "Nov", "Dec")


def _date_rss(d):
    return "Mon, {:02d} {} {} 10:00:00 GMT".format(d.day, MOIS[d.month], d.year)


def _rss(items):
    """Construit un flux RSS Google News minimal."""
    corps = ""
    for it in items:
        corps += ("<item><title>{t}</title><link>{l}</link>"
                  "<pubDate>{d}</pubDate><source url=\"x\">{s}</source></item>").format(
            t=it["t"], l=it.get("l", "http://x"), d=it.get("d", _date_rss(AUJ)),
            s=it.get("s", "Media"))
    return ("<?xml version='1.0'?><rss><channel>" + corps + "</channel></rss>").encode("utf-8")


class TestParsingRSS(unittest.TestCase):
    def test_parse_items(self):
        xml = _rss([{"t": "VINCI acquiert All for One", "l": "http://a", "s": "Les Echos"}])
        out = P.parser_rss(xml)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["titre"], "VINCI acquiert All for One")
        self.assertEqual(out[0]["source"], "Les Echos")
        self.assertEqual(out[0]["lien"], "http://a")

    def test_xml_casse_ne_leve_pas(self):
        self.assertEqual(P.parser_rss(b"<rss><bad"), [])

    def test_jours_independant_locale(self):
        self.assertEqual(P._jours("Wed, 29 Oct 2026 10:00:00 GMT", AUJ), 5)
        self.assertIsNone(P._jours("", AUJ))
        self.assertIsNone(P._jours("pas une date", AUJ))


class TestCle(unittest.TestCase):
    def test_dedup_meme_evenement(self):
        a = P.cle_item({"titre": "JPMorgan s'implante au Nigeria - Forbes"})
        b = P.cle_item({"titre": "JPMorgan s'implante au Nigeria - RFI"})
        self.assertEqual(a, b)              # meme evenement, sources differentes

    def test_cle_non_vide(self):
        self.assertTrue(P.cle_item({"titre": "Dangote s'implante au Cameroun"}))


class TestPrefiltre(unittest.TestCase):
    def test_candidat_sur_signal(self):
        cl, sig = P.prefiltre_presse("VINCI acquiert une societe allemande")
        self.assertEqual(cl, "candidat")
        self.assertIn("acquiert", sig)

    def test_implantation_candidat(self):
        self.assertEqual(P.prefiltre_presse("Dangote s'implante au Cameroun")[0], "candidat")

    def test_bruit_diplomatie(self):
        self.assertEqual(P.prefiltre_presse(
            "Vietnam-UE : un partenariat strategique global")[0], "bruit")

    def test_bruit_prime_sur_signal(self):
        # Un titre avec diplomatie d'Etat ET un mot signal reste du bruit.
        self.assertEqual(P.prefiltre_presse(
            "Sommet : acquisition d'armes pour l'Ukraine via l'OTAN")[0], "bruit")

    def test_sans_signal(self):
        self.assertEqual(P.prefiltre_presse("La bourse de Paris ouvre en hausse")[0],
                         "sans_signal")


class TestValiderEtAction(unittest.TestCase):
    def test_valider_ramene_aux_valeurs_prudentes(self):
        v = P.valider({"operation": "n'importe", "besoin": "bidon",
                       "zone_risque": "oui", "pertinent_arise": "true",
                       "confiance": 2.0, "entreprise": "X"})
        self.assertEqual(v["operation"], "autre")
        self.assertEqual(v["besoin"], "aucun")
        self.assertTrue(v["zone_risque"])
        self.assertEqual(v["confiance"], 1.0)

    def test_valider_none_sur_non_dict(self):
        self.assertIsNone(P.valider("pas un dict"))

    def test_action_contacter_zone_risque_securite(self):
        ext = {"pertinent_arise": True, "operation": "implantation",
               "zone_risque": True, "besoin": "les_deux"}
        self.assertEqual(P.action(ext), "contacter")

    def test_action_surveiller_dd_seul(self):
        ext = {"pertinent_arise": True, "operation": "acquisition",
               "zone_risque": False, "besoin": "due_diligence"}
        self.assertEqual(P.action(ext), "surveiller")

    def test_action_ignorer_non_pertinent(self):
        ext = {"pertinent_arise": False, "operation": "acquisition",
               "zone_risque": True, "besoin": "les_deux"}
        self.assertEqual(P.action(ext), "ignorer")

    def test_action_ignorer_operation_autre(self):
        ext = {"pertinent_arise": True, "operation": "autre",
               "zone_risque": True, "besoin": "securite"}
        self.assertEqual(P.action(ext), "ignorer")


class TestCollecterItems(unittest.TestCase):
    def _get(self, _q):
        return _rss([
            {"t": "JPMorgan s'implante au Nigeria - Forbes", "l": "http://1"},
            {"t": "JPMorgan s'implante au Nigeria - RFI", "l": "http://2"},   # doublon
            {"t": "Vieux rachat", "d": _date_rss(AUJ - datetime.timedelta(days=60))},  # perime
        ])

    def test_dedup_et_fraicheur(self):
        items = P.collecter_items(AUJ, get=self._get, qs=["q1", "q2"])
        titres = [i["titre"] for i in items]
        # Le doublon JPMorgan est fusionne ; le vieux rachat (60 j) est ecarte.
        self.assertEqual(sum("JPMorgan" in t for t in titres), 1)
        self.assertNotIn("Vieux rachat", titres)


class TestExecuter(unittest.TestCase):
    def _items(self):
        return [
            {"titre": "Dangote s'implante au Cameroun", "cle": "dangote", "source": "RFI"},
            {"titre": "Partenariat strategique global Vietnam-UE", "cle": "diplo", "source": "VOV"},
            {"titre": "Hilton acquiert un groupe hotelier en Afrique", "cle": "hilton", "source": "Ecofin"},
        ]

    def _appel(self, prompt, modele):
        # LLM simule : repond selon l'entreprise citee dans le prompt.
        if "Dangote" in prompt:
            return ('{"entreprise":"Dangote","operation":"implantation","pays":"Cameroun",'
                    '"zone_risque":true,"besoin":"les_deux","pertinent_arise":true,'
                    '"resume":"Dangote entre dans le petrole camerounais","confiance":0.8}')
        return ('{"entreprise":"Hilton","operation":"acquisition","pays":"Afrique",'
                '"zone_risque":true,"besoin":"due_diligence","pertinent_arise":true,'
                '"resume":"x","confiance":0.7}')

    def test_prefiltre_puis_llm_puis_action(self):
        retenus, info = P.executer(self._items(), appel=self._appel, aujourd=AUJ, pause=0)
        # La diplomatie est ecartee au pre-filtre (jamais envoyee au LLM).
        self.assertEqual(info["candidats"], 2)
        cles = {it["cle"]: it["action"] for it in retenus}
        self.assertEqual(cles["dangote"], "contacter")    # zone risque + les_deux
        self.assertEqual(cles["hilton"], "surveiller")    # DD seul

    def test_memoire_avant_plafond(self):
        # Un item deja vu n'est pas reanalyse.
        retenus, info = P.executer(self._items(), deja_vus={"dangote"},
                                   appel=self._appel, aujourd=AUJ, pause=0)
        self.assertEqual(info["candidats"], 1)
        self.assertEqual([it["cle"] for it in retenus], ["hilton"])


class TestStockage(unittest.TestCase):
    def _retenu(self):
        return {"cle": "k", "detecte_le": "2026-11-03", "action": "contacter",
                "source": "RFI", "lien": "http://x", "date_article": "",
                "extraction": {"entreprise": "Dangote", "operation": "implantation",
                               "pays": "Cameroun", "zone_risque": True,
                               "besoin": "les_deux", "resume": "r", "confiance": 0.8}}

    def test_alignement_sheet(self):
        ligne = P.ligne_sheet(self._retenu())
        self.assertEqual(len(ligne), len(P.COLONNES_SHEET))
        self.assertEqual(ligne[P.COLONNES_SHEET.index("entreprise")], "Dangote")
        self.assertEqual(ligne[P.COLONNES_SHEET.index("zone_risque")], "oui")

    def test_alignement_pg(self):
        v = P.valeurs_pg(self._retenu())
        self.assertEqual(len(v), len(P.COLONNES_PG))
        self.assertEqual(v[P.COLONNES_PG.index("entreprise")], "Dangote")


if __name__ == "__main__":
    unittest.main()
