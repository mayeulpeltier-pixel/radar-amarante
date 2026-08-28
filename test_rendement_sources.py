# -*- coding: utf-8 -*-
"""P3.2 — Rendement par source (26/08/2026).

CE QUE MESURER LE VOLUME NE DIT PAS
-----------------------------------
Une source qui produit 900 leads dont 14 % mènent à un contact vaut moins
qu'une source qui en produit 110 dont 87 %. Compter les leads récompense le
bruit. C'était l'exemple central de l'audit externe, et il avait raison.

CE QU'ON NE POUVAIT PAS FAIRE, ET CE QU'ON A FAIT
-------------------------------------------------
Le vrai rendement (« combien de marchés gagnés par 100 leads ») exige des
issues gagné/perdu. Elles sont enregistrables depuis P1.1 mais n'existeront en
volume que dans plusieurs semaines. Attendre aurait été une perte sèche.

On livre donc les deux proxys DISPONIBLES, nommés pour ce qu'ils sont :
    taux d'ÉCART   (non_pertinent / traités) -> proxy de BRUIT
    taux de CONTACT (contacte / traités)     -> proxy de VALEUR
et le taux de SUCCÈS apparaît de lui-même dès que les issues arrivent.

LE PIÈGE PRINCIPAL
------------------
Un taux calculé sur trois leads traités inviterait à couper une source sur un
échantillon de trois. En dessous du seuil, les taux valent None : « — » se lit
« on ne sait pas », un « 0 % » se lirait « source inutile », et ce n'est pas
la même chose.

Tests OFFLINE : fonction pure et contrats du gabarit, aucun réseau.
"""

import re
import unittest

import radar_cockpit as ck
import radar_dashboard as dash


def _l(src, statut):
    return {"src": src, "statut": statut}


# Le cas de l'audit : gros volume bruyant contre petit volume utile.
CORPUS = (
    [_l("GNEWS", "non_pertinent")] * 38
    + [_l("GNEWS", "contacte")] * 6
    + [_l("GNEWS", "nouveau")] * 856
    + [_l("EBRD", "non_pertinent")] * 4
    + [_l("EBRD", "contacte")] * 18
    + [_l("EBRD", "gagne")] * 3
    + [_l("EBRD", "perdu")] * 5
    + [_l("EBRD", "nouveau")] * 80
    + [_l("IDB", "contacte")] * 2
    + [_l("IDB", "nouveau")] * 40
)


def _par_src(leads, **kw):
    return {d["src"]: d for d in dash.rendement_sources(leads, **kw)}


class TestLeVolumeNeMesureRien(unittest.TestCase):
    """La démonstration centrale du chantier."""

    def setUp(self):
        self.r = _par_src(CORPUS)

    def test_la_source_la_plus_volumineuse_est_la_plus_bruyante(self):
        self.assertGreater(self.r["GNEWS"]["n"], self.r["EBRD"]["n"])
        self.assertGreater(self.r["GNEWS"]["taux_ecart"],
                           self.r["EBRD"]["taux_ecart"])

    def test_la_source_la_moins_volumineuse_convertit_le_mieux(self):
        self.assertGreater(self.r["EBRD"]["taux_contact"],
                           self.r["GNEWS"]["taux_contact"])

    def test_le_tri_reste_par_volume(self):
        """On n'impose pas un classement par rendement : le lecteur compare
        lui-même. Un tri par taux mettrait en tête une source à 100 % sur
        deux leads."""
        ordre = [d["src"] for d in dash.rendement_sources(CORPUS)]
        self.assertEqual(ordre, ["GNEWS", "EBRD", "IDB"])


class TestSeuilDeSignificativite(unittest.TestCase):
    """Le piège : conclure sur trois leads."""

    def test_echantillon_maigre_ne_produit_aucun_taux(self):
        idb = _par_src(CORPUS)["IDB"]
        self.assertFalse(idb["assez"])
        self.assertIsNone(idb["taux_ecart"])
        self.assertIsNone(idb["taux_contact"])

    def test_none_et_zero_ne_veulent_pas_dire_la_meme_chose(self):
        """« — » = on ne sait pas. « 0 % » = source inutile."""
        maigre = _par_src([_l("X", "contacte")] * 2)["X"]
        fourni = _par_src([_l("Y", "contacte")] * 12)["Y"]
        self.assertIsNone(maigre["taux_ecart"])
        self.assertEqual(fourni["taux_ecart"], 0)

    def test_seuil_configurable(self):
        r = _par_src([_l("X", "contacte")] * 5, traites_min=3)["X"]
        self.assertTrue(r["assez"])

    def test_le_succes_a_son_propre_seuil(self):
        """Il se calcule sur les ISSUES, pas sur les leads traités : 40 leads
        traités et 2 issues ne disent rien du taux de succès."""
        r = _par_src([_l("X", "contacte")] * 40 + [_l("X", "gagne")])["X"]
        self.assertTrue(r["assez"])
        self.assertIsNone(r["taux_succes"])

    def test_succes_calcule_quand_les_issues_suffisent(self):
        ebrd = _par_src(CORPUS)["EBRD"]
        self.assertEqual(ebrd["issues"], 8)
        self.assertEqual(ebrd["taux_succes"], 38)


class TestComptage(unittest.TestCase):

    def test_les_nouveaux_ne_comptent_pas_comme_traites(self):
        """Un lead jamais regardé n'est ni du bruit ni de la valeur."""
        r = _par_src([_l("X", "nouveau")] * 50 + [_l("X", "contacte")] * 10)["X"]
        self.assertEqual(r["n"], 60)
        self.assertEqual(r["traites"], 10)

    def test_un_marche_gagne_compte_aussi_comme_contact(self):
        """Sinon gagner ferait BAISSER le taux de contact de la source."""
        r = _par_src([_l("X", "gagne")] * 12)["X"]
        self.assertEqual(r["taux_contact"], 100)

    def test_un_marche_perdu_aussi(self):
        r = _par_src([_l("X", "perdu")] * 12)["X"]
        self.assertEqual(r["taux_contact"], 100)
        self.assertEqual(r["taux_succes"], 0)

    def test_corpus_vide(self):
        self.assertEqual(dash.rendement_sources([]), [])
        self.assertEqual(dash.rendement_sources(None), [])

    def test_source_absente_devient_point_interrogation(self):
        r = _par_src([{"statut": "contacte"}] * 12)
        self.assertIn("?", r)


class TestAffichage(unittest.TestCase):

    def setUp(self):
        leads = [dict(l, pays="", zone="", titre="", agence="", final=0,
                      surete=0, comm=0, action="contacter", win="", nom="",
                      email="", tel="", cible="", justif="", grp="", lien="",
                      ecart=False, secu=False, mois="", mois_label="",
                      date_det="", motif_ecart="", deadline="", conf="",
                      modele="", pub=str(i), projet_id="", valeur="",
                      enveloppe="", entreprise="", sect="")
                 for i, l in enumerate(CORPUS)]
        self.html = ck.generer_cockpit(leads, suivi={"api": True})

    def test_tableau_rendu(self):
        self.assertIn("Rendement par source", self.html)
        self.assertIn('<table class="rd">', self.html)

    def test_les_proxys_sont_nommes_comme_tels(self):
        """Présenter « Bruit » comme une mesure exacte tromperait."""
        self.assertIn("proxy de BRUIT", self.html)
        self.assertIn("proxy de VALEUR", self.html)
        self.assertIn("sont des <b>proxys</b>", self.html)

    def test_l_avertissement_sur_le_volume_est_a_l_ecran(self):
        self.assertIn("volume ne mesure rien", self.html)

    def test_l_absence_de_taux_est_expliquee(self):
        self.assertIn("on ne sait pas</b>, pas « zéro »", self.html)

    def test_le_succes_est_annonce_comme_a_venir(self):
        self.assertIn("il arrive avec l'usage", self.html)

    def test_ligne_maigre_estompee(self):
        self.assertIn("tr class=\"${x.assez?\"\":\"maigre\"}\"", self.html)
        self.assertIn(".rd tr.maigre{opacity:.5}", self.html)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])

    def test_chantiers_precedents_intacts(self):
        for attendu in ("Sources en régression", "renderAujourdhui",
                        "function blocCompte", "const ONGLET_SRC="):
            self.assertIn(attendu, self.html)


if __name__ == "__main__":
    unittest.main()
