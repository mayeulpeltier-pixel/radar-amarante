"""Tests du filtre date COTE SERVEUR de la requete TED (RADAR_TED_FILTRE_DATE).

Contexte : historiquement on tirait tout le stock ACTIVE (jusqu'a MAX_PAGES)
puis on coupait cote client sur NB_JOURS_FENETRE. Le nouveau filtre demande
directement a TED les avis publies dans la fenetre, ce qui ferme le trou
MAX_PAGES (des avis recents pouvaient etre rates) et allege la charge.

Tout est hors-ligne : aucune requete reseau (session TED simulee).
"""

import os
import unittest
from datetime import date, timedelta

import ted_complet_v14 as ted


# --- Fausse session TED (aucun reseau) ------------------------------------

class _FauxRepJSON:
    """Reponse minimale imitant requests.Response cote TED.

    status_code est requis depuis l'ajout de poster_ted (failover), qui
    l'inspecte pour decider d'une bascule d'endpoint. 200 = pas de bascule.
    """
    def __init__(self, notices, status=200):
        self._notices = notices
        self.status_code = status

    def raise_for_status(self):
        return None

    def json(self):
        return {"notices": self._notices}


class _FauxSessionTED:
    """Renvoie une page de notices par appel et MEMORISE chaque payload envoye,
    pour verifier que la query (filtre date inclus) est preservee page apres
    page."""
    def __init__(self, pages):
        self.pages = pages           # liste de listes de notices, une par page
        self.payloads = []           # payloads json captures, dans l'ordre
        self._idx = 0

    def post(self, url, json=None, timeout=None, **kw):
        self.payloads.append(json)
        notices = self.pages[self._idx] if self._idx < len(self.pages) else []
        self._idx += 1
        return _FauxRepJSON(notices)


class _BaseFiltreDate(unittest.TestCase):
    """Isole l'etat global entre tests : le flag d'env et le niveau
    d'enrichissement verrouille par interroger_ted."""
    def setUp(self):
        self._flag_avant = os.environ.get("RADAR_TED_FILTRE_DATE")
        self._niveau_avant = ted._NIVEAU_ENRICHISSEMENT
        ted._NIVEAU_ENRICHISSEMENT = None

    def tearDown(self):
        if self._flag_avant is None:
            os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        else:
            os.environ["RADAR_TED_FILTRE_DATE"] = self._flag_avant
        ted._NIVEAU_ENRICHISSEMENT = self._niveau_avant


# --- construire_requete ----------------------------------------------------

class TestConstruireRequeteFiltreDate(_BaseFiltreDate):

    def test_flag_off_par_defaut_comportement_inchange(self):
        """Sans le flag, la query ne doit contenir NI filtre date NI tri :
        retrocompatibilite stricte avec l'ancien comportement."""
        os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        corps = ted.construire_requete()
        self.assertNotIn("publication-date>=", corps["query"])
        self.assertNotIn("SORT BY", corps["query"])
        # Le socle CPV + pays reste intact.
        self.assertIn("classification-cpv IN (", corps["query"])
        self.assertIn("place-of-performance IN (", corps["query"])

    def test_flag_zero_explicite_reste_off(self):
        os.environ["RADAR_TED_FILTRE_DATE"] = "0"
        corps = ted.construire_requete()
        self.assertNotIn("publication-date>=", corps["query"])

    def test_flag_on_injecte_filtre_et_tri_desc(self):
        os.environ["RADAR_TED_FILTRE_DATE"] = "1"
        corps = ted.construire_requete()
        self.assertIn("publication-date>=", corps["query"])
        # Radar = priorite au frais : tri DESCENDANT, jamais ascendant.
        self.assertIn("SORT BY publication-date DESC", corps["query"])
        self.assertNotIn("ASC", corps["query"])

    def test_flag_on_date_par_defaut_est_fenetre_glissante(self):
        """La date par defaut doit valoir today - NB_JOURS_FENETRE, au format
        compact AAAAMMJJ attendu par l'API v3."""
        os.environ["RADAR_TED_FILTRE_DATE"] = "1"
        attendu = (date.today() - timedelta(days=ted.NB_JOURS_FENETRE)).strftime("%Y%m%d")
        corps = ted.construire_requete()
        self.assertIn("publication-date>={}".format(attendu), corps["query"])

    def test_override_depuis_date_respecte(self):
        os.environ["RADAR_TED_FILTRE_DATE"] = "1"
        corps = ted.construire_requete(depuis_date="20260101")
        self.assertIn("publication-date>=20260101", corps["query"])

    def test_override_ignore_si_flag_off(self):
        """depuis_date ne doit RIEN injecter tant que le flag est off :
        le garde-fou d'activation progressive prime."""
        os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        corps = ted.construire_requete(depuis_date="20260101")
        self.assertNotIn("20260101", corps["query"])

    def test_structure_corps_inchangee_quel_que_soit_le_flag(self):
        """fields / scope / limit / paginationMode ne bougent pas : seul le
        champ query evolue."""
        os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        off = ted.construire_requete()
        os.environ["RADAR_TED_FILTRE_DATE"] = "1"
        on = ted.construire_requete()
        for cle in ("fields", "scope", "limit", "paginationMode", "checkQuerySyntax"):
            self.assertEqual(off[cle], on[cle], "cle {} modifiee a tort".format(cle))
        self.assertEqual(on["scope"], "ACTIVE")
        self.assertEqual(on["limit"], ted.LIMITE_RESULTATS)


# --- interroger_ted : preservation du filtre a travers la pagination -------

class TestPropagationFiltreParPage(_BaseFiltreDate):

    def test_filtre_date_present_sur_chaque_page(self):
        """Le vrai risque : que la pagination reconstruise le corps et PERDE la
        clause date sur les pages > 1. On force 2 pages et on verifie que les
        DEUX payloads portent le filtre."""
        os.environ["RADAR_TED_FILTRE_DATE"] = "1"
        corps = ted.construire_requete(depuis_date="20260101")
        corps["limit"] = 2  # petite page pour declencher l'arret des la page 2

        # page 1 = 2 notices (== limite -> on continue) ; page 2 = 1 (< limite -> stop)
        faux = _FauxSessionTED(pages=[
            [{"publication-number": "1-2026"}, {"publication-number": "2-2026"}],
            [{"publication-number": "3-2026"}],
        ])
        session_avant = ted.session_robuste
        ted.session_robuste = lambda: faux
        try:
            resultats = ted.interroger_ted(corps_requete=corps, max_pages=5)
        finally:
            ted.session_robuste = session_avant

        # 2 pages interrogees, 3 notices ramenees.
        self.assertEqual(len(faux.payloads), 2)
        self.assertEqual(len(resultats), 3)
        for i, payload in enumerate(faux.payloads, start=1):
            self.assertIn("publication-date>=20260101", payload["query"],
                          "filtre date perdu sur la page {}".format(i))
            self.assertEqual(payload["page"], i, "numero de page incoherent")

    def test_sans_flag_aucun_filtre_dans_les_payloads(self):
        """Symetrie : flag off -> aucune page ne doit porter de filtre date."""
        os.environ.pop("RADAR_TED_FILTRE_DATE", None)
        corps = ted.construire_requete()
        corps["limit"] = 2
        faux = _FauxSessionTED(pages=[
            [{"publication-number": "1-2026"}, {"publication-number": "2-2026"}],
            [{"publication-number": "3-2026"}],
        ])
        session_avant = ted.session_robuste
        ted.session_robuste = lambda: faux
        try:
            ted.interroger_ted(corps_requete=corps, max_pages=5)
        finally:
            ted.session_robuste = session_avant
        for payload in faux.payloads:
            self.assertNotIn("publication-date>=", payload["query"])


if __name__ == "__main__":
    unittest.main()
