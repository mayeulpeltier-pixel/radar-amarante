# -*- coding: utf-8 -*-
"""Sonde demande privée IE : Adzuna (offres par intitulé) et presse (familles
Google News), avec heuristique de précision sans LLM, et verdicts. Offline :
réponses injectées."""

import datetime
import unittest

import sonde_demande_privee_ie as S

S.PAUSE = 0.0
S.JOURS = 45
AUJ = datetime.date.today()


def offre(company, created=None):
    created = created or AUJ.isoformat()
    return {"company": {"display_name": company}, "created": created + "T10:00:00Z"}


class TestAdzuna(unittest.TestCase):

    def test_inactif_sans_cle(self):
        S.ADZUNA_ID, S.ADZUNA_KEY = "", ""
        j = S.sonder_adzuna(fetch=None)
        # fetch=None force le mode reel ; sans cle -> actif False
        self.assertFalse(j["actif"])

    def test_comptage_fraicheur_et_entreprises(self):
        vieux = (AUJ - datetime.timedelta(days=100)).isoformat()
        def fetch(pays, params):
            return {"count": 3, "results": [offre("ACME SA"), offre("ACME SA"),
                                            offre("OLD CORP", vieux)]}
        j = S.sonder_adzuna(fetch=fetch, postes={"fr": ["due diligence"]})
        self.assertTrue(j["actif"])
        annoncees, lues, frais = j["par_requete"]["fr:due diligence"]
        self.assertEqual((annoncees, lues, frais), (3, 3, 2))   # OLD CORP hors fenetre
        self.assertEqual(j["entreprises"]["ACME SA"], 2)

    def test_http_erreur_journalisee(self):
        j = S.sonder_adzuna(fetch=lambda p, q: {"_http": 429},
                            postes={"fr": ["kyc"]})
        self.assertEqual(j["par_requete"], {})
        self.assertEqual(len(j["erreurs"]), 1)

    def test_verdict_exploitable(self):
        def fetch(pays, params):
            return {"count": 10, "results": [offre("E%d" % i) for i in range(10)]}
        j = S.sonder_adzuna(fetch=fetch, postes={"fr": ["due diligence", "conformité"]})
        self.assertEqual(S.verdict_adzuna(j)[0], "EXPLOITABLE")

    def test_verdict_marginal(self):
        # 2 offres fraiches sur 45 j -> ~1,3/mois, sous le seuil de 3.
        def fetch(pays, params):
            return {"count": 2, "results": [offre("A"), offre("B")]}
        j = S.sonder_adzuna(fetch=fetch, postes={"fr": ["due diligence"]})
        self.assertEqual(S.verdict_adzuna(j)[0], "MARGINAL")


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>{t1}</title><link>http://a/1</link><pubDate>{d}</pubDate></item>
<item><title>{t2}</title><link>http://a/2</link><pubDate>{d}</pubDate></item>
<item><title>{t3}</title><link>http://a/3</link><pubDate>Mon, 01 Jan 2020 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def rss(t1, t2, t3="Vieux sujet"):
    d = AUJ.strftime("%a, %d %b %Y %H:%M:%S GMT")
    return RSS.format(t1=t1, t2=t2, t3=t3, d=d)


class TestPresse(unittest.TestCase):

    def test_precision_titre(self):
        nomme, bruit = S.precision_titre("SFR rachète Altice pour 2 milliards")
        self.assertTrue(nomme)
        self.assertFalse(bruit)
        _, bruit2 = S.precision_titre("Colloque pour les 10 ans de la loi Sapin II")
        self.assertTrue(bruit2)
        # Org nommée mais AUCUN verbe de besoin -> pas exploitable.
        n2, _ = S.precision_titre("VEOLIA, leader de l'intelligence économique")
        self.assertFalse(n2)
        # Verbe de besoin mais AUCUNE org nommée -> pas exploitable.
        n3, _ = S.precision_titre("vague de rachats en cours dans le secteur")
        self.assertFalse(n3)

    def test_comptage_par_famille(self):
        html = rss("VEOLIA rachète une PME de traitement",        # exploitable, frais
                   "Qu'est-ce que la due diligence : définition",  # bruit
                   "OLDCORP rachète une filiale")                  # nomme MAIS vieux (2020)
        j = S.sonder_presse(get=lambda u: html, familles={"ma": "q"})
        st = j["par_famille"]["ma"]
        self.assertEqual(st["items"], 3)
        self.assertEqual(st["recents"], 2)          # le 2020 hors fenetre
        self.assertEqual(st["exploitables"], 1)     # vieux non compte malgre nomme
        self.assertEqual(st["bruit"], 1)

    def test_dedoublonnage_par_lien(self):
        d = AUJ.strftime("%a, %d %b %Y %H:%M:%S GMT")
        html = ('<?xml version="1.0"?><rss><channel>'
                '<item><title>VEOLIA rachète une PME</title><link>http://a/1</link>'
                '<pubDate>{d}</pubDate></item>'
                '<item><title>VEOLIA rachète une PME (repris)</title><link>http://a/1</link>'
                '<pubDate>{d}</pubDate></item></channel></rss>').format(d=d)
        j = S.sonder_presse(get=lambda u: html, familles={"ma": "q"})
        st = j["par_famille"]["ma"]
        self.assertEqual(st["items"], 1)
        self.assertEqual(st["recents"], 1)         # compte une fois, pas deux
        self.assertEqual(st["exploitables"], 1)

    def test_erreur_famille_isolee(self):
        def get(u):
            raise RuntimeError("503")
        j = S.sonder_presse(get=get, familles={"x": "q"})
        self.assertEqual(j["par_famille"], {})
        self.assertEqual(len(j["erreurs"]), 1)

    def test_verdict_a_qualifier_si_bruite(self):
        # 4 récents, 0 exploitable -> presse à qualifier (LLM requis)
        html = rss("Tribune sur l'intelligence économique",
                   "Podcast conformité : replay du webinaire")
        j = S.sonder_presse(get=lambda u: html, familles={"ie": "q"})
        self.assertEqual(S.verdict_presse(j)[0], "A QUALIFIER")

    def test_verdict_pas_exploitable_si_moins_de_trois_recents(self):
        # 2 récents, tous exploitables (ratio 1.0) : insuffisant en volume.
        html = rss("ALSTOM mis en examen pour corruption", "BYGUES perquisitionné pour fraude")
        j = S.sonder_presse(get=lambda u: html, familles={"enq": "q"})
        self.assertEqual(j["par_famille"]["enq"]["recents"], 2)
        self.assertNotEqual(S.verdict_presse(j)[0], "EXPLOITABLE (avec qualification)")

    def test_verdict_exploitable_si_precis(self):
        d = AUJ.strftime("%a, %d %b %Y %H:%M:%S GMT")
        it = ('<item><title>{t}</title><link>http://a/{i}</link>'
              '<pubDate>{d}</pubDate></item>')
        titres = ["ALSTOM mis en examen pour corruption",
                  "BOUYGUES perquisitionné pour fraude",
                  "THALES condamné pour corruption",
                  "ORANO rachète une filiale minière"]
        html = ('<?xml version="1.0"?><rss><channel>'
                + "".join(it.format(t=t, i=i, d=d) for i, t in enumerate(titres))
                + "</channel></rss>")
        j = S.sonder_presse(get=lambda u: html, familles={"enq": "q"})
        self.assertEqual(j["par_famille"]["enq"]["recents"], 4)
        self.assertEqual(S.verdict_presse(j)[0], "EXPLOITABLE (avec qualification)")


class TestRapport(unittest.TestCase):

    def test_rapport_complet(self):
        adz = S.sonder_adzuna(fetch=lambda p, q: {"count": 5, "results": [offre("ACME")]},
                              postes={"fr": ["due diligence"]})
        presse = S.sonder_presse(get=lambda u: rss("X SA rachète Y", "colloque Sapin II"),
                                 familles={"ma": "q"})
        md, (va, vp) = S.rapport(adz, presse, AUJ)
        self.assertIn("Sonde demande privée IE", md)
        self.assertIn("Adzuna", md)
        self.assertIn("ACME", md)


if __name__ == "__main__":
    unittest.main()
