"""Affichage des fiches Analyste et des candidats prouves dans le cockpit."""

import json
import re
import unittest
from unittest import mock

import radar_cockpit as ck


def _avis(**k):
    base = {"src": "AFDB", "pays": "RDC", "zone": "Afrique centrale",
            "titre": "PPM - RDC - Projet de Réponse à la Crise PRECAPE", "agence": "Fonds social de la RDC",
            "final": 7.0, "surete": 7, "comm": 7, "action": "contacter", "win": "", "nom": "n.c.",
            "email": "n.c.", "tel": "n.c.", "cible": "", "justif": "", "grp": "AT", "lien": "",
            "ecart": False, "secu": False, "mois": "2026-10", "mois_label": "oct.", "date_det": "2026-10-01",
            "statut": "nouveau", "motif_ecart": "", "deadline": "", "conf": "", "modele": "",
            "pub": "PPM-1", "projet_id": "", "valeur": "", "enveloppe": "", "entreprise": "",
            "sect": "Génie civil / BTP"}
    base.update(k)
    return base


def _attrib(ent, **k):
    base = dict(_avis(), src="ATTRIB", entreprise=ent, pub="ATT-" + ent, action="surveiller",
                marche="Travaux de réhabilitation d'écoles", lien="https://ted.europa.eu/x",
                date_det="2026-05-01", etranger_titulaire=True)
    base.update(k)
    return base


def _fiche_pour(lead):
    import analyste
    return {"publication_number": analyste.cle_fiche(lead), "date": "2026-11-06",
            "cout_usd": 0.2, "modele": "claude-sonnet-5-5",
            "sources": ["https://afdb.org/precape", "javascript:alert(1)"],
            "candidats": [], "fiche": {"en_une_phrase": "PRECAPE à Uvira <script>", "verdict": "surveiller",
                                       "qui_deploiera": [{"acteur": "FSRDC", "source": "https://afdb.org/precape"}],
                                       "prochaine_action": {"action": "Lire le PPM", "cible": "FSRDC",
                                                            "avant_le": "2026-12-01"}}}


class TestPreparerFiches(unittest.TestCase):
    def test_fiche_visible_sur_tous_les_documents_du_projet(self):
        ppm = _avis()
        agpm = _avis(pub="AGPM-1", titre="AGPM - RDC - Projet de Réponse à la Crise PRECAPE")
        autre = _avis(pub="X-1", titre="AON - RDC - Route nationale")
        par_cle, par_pub = ck.preparer_fiches([ppm, agpm, autre], [_fiche_pour(ppm)])
        self.assertEqual(set(par_pub), {"PPM-1", "AGPM-1"})
        self.assertEqual(len(par_cle), 1)
        self.assertNotIn("cout_usd", list(par_cle.values())[0])

    def test_sans_fiche_rien(self):
        self.assertEqual(ck.preparer_fiches([_avis()], None), ({}, {}))
        self.assertEqual(ck.preparer_fiches([_avis()], [{"publication_number": "x"}]), ({}, {}))


class TestPage(unittest.TestCase):
    def setUp(self):
        self.leads = [_avis(), _attrib("SOGEA SATOM"),
                      _attrib("Monega Enterprises", marche="Supply of medicines", pays="Ouganda")]
        self.html = ck.generer_cockpit(self.leads, suivi={"api": True},
                                       fiches=[_fiche_pour(self.leads[0])])

    def test_page_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])
        self.assertIn("function blocFiche", self.html)
        self.assertIn("${blocFiche(l)}", self.html)

    def test_donnees_fiche_embarquees(self):
        m = re.search(r"const FICHES=(\{.*?\}), FICHE_PUB=(\{.*?\});", self.html)
        self.assertIsNotNone(m)
        self.assertEqual(json.loads(m.group(2)), {"PPM-1": list(json.loads(m.group(1)))[0]})

    def test_candidats_prouves_seulement(self):
        m = re.search(r"const SOUM=(\{.*?\});\n", self.html)
        soum = json.loads(m.group(1))
        self.assertEqual([c["entreprise"] for c in soum["PPM-1"]], ["SOGEA SATOM"])
        self.assertEqual(soum["PPM-1"][0]["preuve"], "meme_acheteur")

    def test_l_ancienne_cascade_secteur_theatre_a_quitte_le_panneau(self):
        self.assertNotIn("Inféré depuis l'historique des attributions du même secteur", self.html)

    def test_liens_assainis_cote_js(self):
        self.assertIn("function lienSur", self.html)
        self.assertIn("/^https?:", self.html)


class TestApplication(unittest.TestCase):
    def test_lire_donnees_lit_les_fiches_dans_la_phase_de_lecture(self):
        import inspect
        import radar_app
        self.assertIn('_onglet(conn, "fiches_analyste")', inspect.getsource(radar_app.lire_donnees))

    def test_rendre_page_tolere_l_ancien_format(self):
        import radar_app
        vide = tuple([[] for _ in range(4)] + [{}] + [[] for _ in range(16)])
        with mock.patch("radar_cockpit.generer_cockpit", return_value="ok") as g:
            radar_app.rendre_page(vide)                       # 21 elements
            self.assertEqual(g.call_args.kwargs["fiches"], [])
            radar_app.rendre_page(vide + ([{"publication_number": "k"}],))
            self.assertEqual(g.call_args.kwargs["fiches"], [{"publication_number": "k"}])


if __name__ == "__main__":
    unittest.main()
