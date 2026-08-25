# -*- coding: utf-8 -*-
"""Rapatriement des orphelins de surface dans le cockpit (25/08/2026).

CE QUE CE FICHIER GARDE
-----------------------
Trois mecanismes existaient, etaient testes, et n'etaient visibles que sur des
surfaces que personne ne regarde :

  1. `sante_run` (etat du dernier run par source) : cable uniquement dans
     `radar_dashboard.generer_html`, donc absent de l'application. Le detecteur
     de source muette etait lui-meme muet ;
  2. `appliquer_boost_geo` (rehausse des avis d'un pays en aggravation) : idem.
     L'onglet Geopolitique affichait l'alerte pendant que le score l'ignorait ;
  3. `deadline` : collectee, serialisee dans le lead, affichee nulle part sur le
     cockpit. Pour un onglet « a contacter », c'est le champ qui dit si le lead
     est encore actionnable.

Le piege principal est en 2 : `appliquer_boost_geo` N'EST PAS idempotente. Un
double appel doublerait le boost. Un test le verrouille explicitement.

Tests OFFLINE : generation HTML pure, aucune base, aucun reseau.
"""

import json
import re
import unittest

import radar_cockpit as ck
import radar_dashboard as dash


def _lead(**kw):
    base = {
        "src": "TED", "pays": "Mali", "zone": "Sahel", "titre": "Escorte convois",
        "agence": "PNUD", "final": 8.0, "surete": 8.0, "comm": 7.0,
        "action": "contacter", "win": "court_terme", "nom": "n.c.",
        "email": "n.c.", "tel": "n.c.", "cible": "c", "justif": "j",
        "grp": "AT", "lien": "http://a", "ecart": False, "secu": False,
        "mois": "2026-08", "mois_label": "août 2026", "date_det": "2026-08-24",
        "statut": "nouveau", "motif_ecart": "", "deadline": "2026-09-15",
        "conf": "", "modele": "", "pub": "P1", "projet_id": "",
        "valeur": "2000000 EUR", "enveloppe": "", "entreprise": "",
        "sect": "BTP / Construction"}
    base.update(kw)
    return base


ALERTE_MALI = [{"pays_execution": "MLI", "pays_nom": "Mali", "severite": "4",
                "sens": "aggravation", "motif": "Dégradation nord",
                "date_maj": "2026-08-24"}]


def _leads_du_html(html):
    return json.loads(re.search(r"^const RAW=(\[.*?\]), COORDS=",
                                html, re.S | re.M).group(1))


def _sante_du_html(html):
    return json.loads(re.search(r"^const SANTE=(\{.*?\});$",
                                html, re.S | re.M).group(1))


class TestEtatSante(unittest.TestCase):

    def setUp(self):
        ck.SANTE_ON = True

    def test_derive_du_dashboard_sans_duplication(self):
        """Une seule definition des seuils et des etats : celle du dashboard."""
        leads = [_lead(), _lead(src="ATTRIB", date_det="2025-12-01")]
        self.assertEqual(ck.etat_sante(leads), dash.sante_run(leads))

    def test_injecte_dans_la_page(self):
        html = ck.generer_cockpit([_lead()])
        sante = _sante_du_html(html)
        self.assertIn("sources", sante)
        self.assertTrue(any(s["src"] == "TED" and s["n"] == 1
                            for s in sante["sources"]))

    def test_source_muette_marquee_a_verifier(self):
        """LA raison d'etre du bandeau : voir qu'une source s'est tue."""
        sante = ck.etat_sante([_lead(date_det="2026-01-05")])
        etats = {s["src"]: s["etat"] for s in sante["sources"]}
        self.assertEqual(etats["TED"], "ancien")
        self.assertEqual(etats["EBRD"], "absent")   # jamais vue = 0 lead
        self.assertGreater(sante["a_verifier"], 0)

    def test_flag_off_masque_le_bandeau_sans_casser_la_page(self):
        ck.SANTE_ON = False
        html = ck.generer_cockpit([_lead()])
        self.assertEqual(_sante_du_html(html), {})
        self.assertIn("santeRun", html)             # page complete malgre tout

    def test_sante_explicite_prime_sur_le_calcul(self):
        html = ck.generer_cockpit([_lead()], sante={"sources": [], "date": "X",
                                                    "actives": 0, "a_verifier": 0})
        self.assertEqual(_sante_du_html(html)["date"], "X")


class TestBoostGeo(unittest.TestCase):

    def setUp(self):
        ck.GEO_BOOST_ON = True

    def test_avis_rehausse_et_score_origine_conserve(self):
        [l] = ck.appliquer_geo([_lead()], ALERTE_MALI)
        self.assertGreater(l["final"], 8.0)
        self.assertEqual(l["final_base"], 8.0)
        self.assertGreater(l["geo_boost"], 0)

    def test_attributions_intactes(self):
        """Un titulaire deja designe ne devient pas plus chaud parce que le
        pays se degrade : ce n'est plus une opportunite a saisir."""
        [l] = ck.appliquer_geo([_lead(src="ATTRIB")], ALERTE_MALI)
        self.assertEqual(l["final"], 8.0)
        self.assertNotIn("geo_boost", l)

    def test_double_appel_ne_double_pas_le_boost(self):
        """GARDE CENTRALE. appliquer_boost_geo n'est pas idempotente : elle ne
        doit etre appelee qu'une fois par rendu. Ce test documente le piege et
        echouera si quelqu'un ajoute un second appel dans la chaine."""
        [une] = ck.appliquer_geo([_lead()], ALERTE_MALI)
        [deux] = ck.appliquer_geo(ck.appliquer_geo([_lead()], ALERTE_MALI),
                                  ALERTE_MALI)
        self.assertNotEqual(une["final"], deux["final"],
                            "si ces deux valeurs sont egales, la fonction est "
                            "devenue idempotente : ce test peut etre retire")

    def test_sans_alerte_aucun_effet(self):
        [l] = ck.appliquer_geo([_lead()], [])
        self.assertEqual(l["final"], 8.0)

    def test_flag_off_restitue_les_scores_bruts(self):
        ck.GEO_BOOST_ON = False
        [l] = ck.appliquer_geo([_lead()], ALERTE_MALI)
        self.assertEqual(l["final"], 8.0)

    def test_alertes_malformees_ne_cassent_pas_le_rendu(self):
        leads = ck.appliquer_geo([_lead()], [{"n_importe": "quoi"}])
        self.assertEqual(leads[0]["final"], 8.0)

    def test_rehausse_exposee_au_front(self):
        """Le payload serialise porte les champs bruts du dashboard
        (`geo_boost`, `final_base`) ; le mapping JS les renomme en geoboost /
        finalbase. C'est le contrat entre les deux qu'on verrouille ici."""
        leads = ck.appliquer_geo([_lead()], ALERTE_MALI)
        html = ck.generer_cockpit(leads)
        [l] = _leads_du_html(html)
        self.assertGreater(l["geo_boost"], 0)
        self.assertEqual(l["final_base"], 8.0)
        self.assertTrue(l["geo_motif"])
        self.assertIn("geoboost:+l.geo_boost", html)
        self.assertIn("finalbase:(l.final_base==null", html)


class TestEcheance(unittest.TestCase):
    """Le champ `deadline` doit atteindre l'ecran, pas seulement le JSON."""

    def test_deadline_serialisee(self):
        [l] = _leads_du_html(ck.generer_cockpit([_lead()]))
        self.assertEqual(l["deadline"], "2026-09-15")

    def test_fonction_de_rendu_presente(self):
        html = ck.generer_cockpit([_lead()])
        for attendu in ("function badgeDeadline", "function joursRestants",
                        "clôturé", "clôt. aujourd", "J-${jr}"):
            self.assertIn(attendu, html)

    def test_libelles_denum_traduits(self):
        """`court_terme` et `fort` ne doivent plus fuir en langage machine."""
        html = ck.generer_cockpit([_lead()])
        self.assertIn("const WIN_LBL=", html)
        self.assertIn("const BESOIN_LBL=", html)
        self.assertIn("Court terme", html)


class TestRetrocompatibilite(unittest.TestCase):

    def setUp(self):
        ck.SANTE_ON = True
        ck.GEO_BOOST_ON = True

    def test_aucun_placeholder_non_remplace(self):
        html = ck.generer_cockpit([_lead()])
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])

    def test_page_generee_sans_aucun_lead(self):
        html = ck.generer_cockpit([])
        self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])
        self.assertEqual(_leads_du_html(html), [])

    def test_signature_reste_appelable_sans_sante(self):
        """L'ancien appel (sans le parametre) doit continuer de marcher."""
        self.assertIn("santeRun", ck.generer_cockpit([_lead()], geo=[]))


if __name__ == "__main__":
    unittest.main()
