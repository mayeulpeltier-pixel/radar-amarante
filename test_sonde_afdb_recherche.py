# -*- coding: utf-8 -*-
"""Sonde AfDB via moteur de recherche : confirmation locale des URL, extraction
de date, exclusion des attributions, pagination, verdicts. Offline : les
reponses Brave sont injectees (aucun appel reseau, aucune cle requise)."""

import datetime
import unittest

import sonde_afdb_recherche as S

S.PAUSE = 0.0
AUJ = datetime.date(2026, 10, 31)


def res(url, titre="Notice", page_age="2026-10-20T10:00:00"):
    r = {"url": url, "title": titre}
    if page_age:
        r["page_age"] = page_age
    return r


def rep(resultats):
    return 200, {"web": {"results": resultats}}


URL_GPN = ("https://www.afdb.org/en/documents/project-related-procurement/"
           "procurement-notices/general-procurement-notices/gpn-mali-2026")
URL_EOI = ("https://www.afdb.org/en/documents/project-related-procurement/"
           "procurement-notices/request-for-expression-of-interest/eoi-chad")
URL_AWARD = ("https://www.afdb.org/en/documents/project-related-procurement/"
             "contract-award-niger")
URL_HORS = "https://www.afdb.org/en/news-and-events/press-releases/annual-meeting"


class TestConfirmation(unittest.TestCase):

    def test_url_avis_reconnue(self):
        self.assertTrue(S.est_avis_afdb(URL_GPN))
        self.assertTrue(S.est_avis_afdb(URL_EOI))

    def test_hors_chemin_rejete(self):
        self.assertFalse(S.est_avis_afdb(URL_HORS))          # page actualites
        self.assertFalse(S.est_avis_afdb("https://ungm.org/procurement-notices/x"))

    def test_attribution_detectee(self):
        self.assertTrue(S.est_attribution("Contract Award Niger", URL_AWARD))
        self.assertFalse(S.est_attribution("GPN Mali", URL_GPN))

    def test_date_depuis_page_age(self):
        self.assertEqual(S.date_publication({"page_age": "2026-10-20T10:00:00"}),
                         datetime.date(2026, 10, 20))
        self.assertIsNone(S.date_publication({"age": "2 days ago"}))
        self.assertIsNone(S.date_publication({}))


class TestResultatsWeb(unittest.TestCase):

    def test_forme_normale(self):
        self.assertEqual(len(S.resultats_web({"web": {"results": [1, 2]}})), 2)

    def test_formes_degradees(self):
        self.assertEqual(S.resultats_web({}), [])
        self.assertEqual(S.resultats_web({"web": {}}), [])
        self.assertEqual(S.resultats_web("boom"), [])


class TestSonder(unittest.TestCase):

    def test_confirmation_dedoublonnage_et_erreurs(self):
        reqs = [('site:afdb.org "general procurement notice"', "GPN", "amont"),
                ('site:afdb.org "invitation for bids"', "IFB", "tender")]

        def fetch(requete, offset):
            if "invitation" in requete:
                return 0, {"_erreur": "reseau: timeout"}
            # Une page : avis + doublon + hors-chemin + attribution.
            return rep([res(URL_GPN, "GPN Mali"), res(URL_GPN, "GPN Mali (copie)"),
                        res(URL_HORS, "Actualite"), res(URL_AWARD, "Award")])

        avis, j = S.sonder(fetch=fetch, aujourd=AUJ, requetes=reqs)
        urls = sorted(a["url"] for a in avis)
        self.assertEqual(urls, sorted([URL_GPN, URL_AWARD]))   # hors-chemin exclu, doublon fondu
        garde = {a["url"]: a["titre"] for a in avis}
        self.assertEqual(garde[URL_GPN], "GPN Mali")           # 1re occurrence gardee, pas la copie
        self.assertEqual(j["par_requete"][reqs[0][0]], (4, 3))  # 4 bruts ; GPN x2 + award confirmes, hors-chemin exclu
        self.assertEqual(len(j["erreurs"]), 1)
        self.assertIn("IFB", j["erreurs"][0])

    def test_pagination_s_arrete_sous_le_plafond(self):
        appels = []

        def fetch(requete, offset):
            appels.append(offset)
            return rep([res(URL_GPN + "#" + str(offset))])   # 1 < COUNT -> stop

        S.sonder(fetch=fetch, aujourd=AUJ,
                 requetes=[('q', "GPN", "amont")])
        self.assertEqual(appels, [0])        # une seule page, pas de pagination inutile


class TestVerdict(unittest.TestCase):

    def _avis(self, n, date="2026-10-20", attribution=False, type_="GPN", phase="amont"):
        d = datetime.date.fromisoformat(date) if date else None
        return [{"url": "u{}".format(i), "titre": "t", "type": type_, "phase": phase,
                 "attribution": attribution, "date": d} for i in range(n)]

    def test_exploitable_si_frais(self):
        st = S.stats(self._avis(10, "2026-10-25"), AUJ)
        self.assertEqual(S.verdict(st, [])[0], "EXPLOITABLE")
        self.assertEqual(st["frais"], 10)

    def test_amont_seulement_si_index_en_retard(self):
        st = S.stats(self._avis(10, "2026-06-01"), AUJ)      # tout vieux de 5 mois
        self.assertEqual(S.verdict(st, [])[0], "AMONT SEULEMENT")
        self.assertGreater(st["retard_jours"], 120)

    def test_insuffisant_sous_le_seuil(self):
        st = S.stats(self._avis(3, "2026-10-25"), AUJ)
        self.assertEqual(S.verdict(st, [])[0], "INSUFFISANT")

    def test_non_conclu_si_rien_et_erreurs(self):
        st = S.stats([], AUJ)
        self.assertEqual(S.verdict(st, ["HTTP 422"])[0], "NON CONCLU")

    def test_attributions_hors_opportunites(self):
        avis = self._avis(8, "2026-10-25") + self._avis(5, "2026-10-25", attribution=True)
        st = S.stats(avis, AUJ)
        self.assertEqual(st["opportunites"], 8)
        self.assertEqual(st["attributions"], 5)

    def test_rapport_complet(self):
        avis = self._avis(8, "2026-10-25")
        md, v = S.rapport(avis, {"requetes": 16, "brut": 40, "erreurs": []}, AUJ)
        self.assertIn("## Verdict : EXPLOITABLE", md)
        self.assertIn("[avis](u0)", md)
        self.assertIn("Cloudflare", md)


if __name__ == "__main__":
    unittest.main()
