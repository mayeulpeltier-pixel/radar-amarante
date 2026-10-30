# -*- coding: utf-8 -*-
"""Collecteur UNGM, panne du 30/10/2026 : le portail exige le jeton
anti-falsification de la page publique et refuse PageSize > 15. Avant le
correctif, chaque pays recevait HTTP 400 et etait saute EN SILENCE
("0 ligne | 60 pays interroges dont 0 avec des avis"). Offline : la session
est simulee et reproduit le comportement reel du portail."""

import json
import unittest
from datetime import date, timedelta

import ungm_radar as U

PAGE = ('<form><input name="__RequestVerificationToken" type="hidden" '
        'value="JETON-1" /></form><select><option value="12">Mali</option></select>'
        '<input name="__RequestVerificationToken" type="hidden" value="JETON-2" />')


def ligne(ident, publie):
    return ('<div role="row" data-noticeid="{0}">'
            '<div role="cell">Construction of a health centre in Gao region</div>'
            '<div role="cell">{1}</div><div role="cell">UNOPS</div>'
            '<div role="cell">Invitation to bid</div></div>').format(ident, publie)


class Reponse:
    def __init__(self, statut, texte):
        self.status_code, self.text = statut, texte


class PortailSimule:
    """Comme le vrai portail : 400 sans jeton ou si PageSize > 15."""

    def __init__(self, pages):
        self.pages, self.posts, self.gets = pages, [], 0

    def get(self, url, headers=None, timeout=None):
        self.gets += 1
        return Reponse(200, PAGE)

    def post(self, url, data=None, headers=None, timeout=None):
        charge = json.loads(data)
        self.posts.append((charge, headers))
        if headers.get("RequestVerificationToken") != "JETON-1" or charge["PageSize"] > 15:
            return Reponse(400, "Bad Request")
        i = charge["PageIndex"]
        return Reponse(200, self.pages[i] if i < len(self.pages) else "")


def page_de(n, debut, publie):
    return "".join(ligne(debut + k, publie) for k in range(n))


class TestJeton(unittest.TestCase):

    def setUp(self):
        U._JETON["valeur"] = ""

    def test_lire_le_premier_jeton(self):
        self.assertEqual(U.lire_jeton(PAGE), "JETON-1")
        self.assertEqual(U.lire_jeton("<html>rien</html>"), "")

    def test_page_sans_jeton_n_efface_pas_le_bon(self):
        U.retenir_jeton(PAGE)
        U.retenir_jeton("<html>degradee</html>")
        self.assertEqual(U.entetes_recherche()["RequestVerificationToken"], "JETON-1")

    def test_charge_plafonnee_a_15(self):
        self.assertEqual(U.charge_recherche(0)["PageSize"], 15)
        self.assertEqual(U.charge_recherche(0, taille=50)["PageSize"], 15)

    def test_collecte_par_pays_avec_jeton(self):
        recent = (date.today() - timedelta(days=3)).strftime("%d-%b-%Y")
        portail = PortailSimule([page_de(15, 1000, recent), page_de(4, 2000, recent)])
        table = U.charger_pays_ungm(session=portail)
        self.assertEqual(table, {"MLI": "12"})
        lignes, stats = U.collecte_par_pays(session=portail, table_pays=table)
        self.assertEqual(len(lignes), 19)
        self.assertEqual(stats["erreurs_http"], {})
        self.assertTrue(all(h["RequestVerificationToken"] == "JETON-1" for _, h in portail.posts))
        self.assertTrue(all(l["pays_iso3"] == "MLI" for l in lignes))
        self.assertEqual(portail.gets, 1)     # le jeton vient de la page des pays

    def test_erreurs_http_comptees_et_non_silencieuses(self):
        portail = PortailSimule([])
        portail.get = lambda *a, **k: Reponse(200, "<html>sans jeton</html>")
        lignes, stats = U.collecte_par_pays(session=portail, table_pays={"MLI": "12"})
        self.assertEqual(lignes, [])
        self.assertEqual(stats["erreurs_http"], {"400": 1})

    def test_pagination_arretee_hors_fenetre(self):
        ancien = (date.today() - timedelta(days=U.JOURS_FENETRE + 10)).strftime("%d-%b-%Y")
        portail = PortailSimule([page_de(15, 1000, ancien)] * 6)
        U.retenir_jeton(PAGE)
        _, stats = U.collecte_par_pays(session=portail, table_pays={"MLI": "12"})
        self.assertEqual(len(portail.posts), 1)
        self.assertEqual(stats["arrets_fenetre"], 1)

    def test_collecte_globale_obtient_le_jeton(self):
        recent = (date.today() - timedelta(days=1)).strftime("%d-%b-%Y")
        portail = PortailSimule([page_de(3, 1000, recent)])
        lignes, stats = U.collecte(session=portail)
        self.assertEqual(len(lignes), 3)
        self.assertEqual(stats["arret"], "fin des donnees")


class TestAttributions(unittest.TestCase):
    """Meme panne sur ungm_attributions.py (HTTP 400 sur PublicSearch)."""

    def test_recherche_des_attributions_porte_le_jeton(self):
        import ungm_attributions as A
        U._JETON["valeur"] = ""
        portail = PortailSimule(["<div>ok</div>"])
        statut, texte = A.interroger(portail, A.ENDPOINT_AWARDS,
                                     {"PageIndex": 0, "PageSize": 15})
        self.assertEqual(statut, 200)
        self.assertEqual(portail.posts[0][1]["RequestVerificationToken"], "JETON-1")


if __name__ == "__main__":
    unittest.main()
