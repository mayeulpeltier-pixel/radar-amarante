# -*- coding: utf-8 -*-
"""Collecteur IE international (IE-5) : collecte TED (title-proc, hors France,
repli OR) et UNGM (jeton, parser HTML reel), selection (ouverture, hors France,
pre-filtre, memoire avant plafond), et reutilisation du schema/ecriture du
collecteur BOAMP. Offline : HTTP et LLM injectes, aucun reseau."""

import datetime
import unittest

import collecteur_international_ie as I
import collecteur_ie as C

I.PAUSE = 0.0
AUJ = datetime.date(2026, 10, 31)
DEBUT = AUJ - datetime.timedelta(days=I.JOURS)


# --- TED --------------------------------------------------------------------
def notice_ted(pub, titre, pays="BEL", type_n="cn-standard", acheteur="EUAA",
               date="2026-10-20+02:00", deadline="2026-12-01+02:00", langue="eng"):
    return {"publication-number": pub, "title-proc": {langue: titre},
            "buyer-name": {"eng": [acheteur]}, "buyer-country": [pays],
            "notice-type": type_n, "publication-date": date, "deadline": deadline}


class FauxTED:
    def __init__(self, notices, refuse_or=False):
        self.notices, self.refuse_or, self.corps = notices, refuse_or, []

    def __call__(self, corps):
        self.corps.append(corps)
        if self.refuse_or and " OR " in corps["query"]:
            return 400, {"message": "syntax"}
        return 200, {"notices": self.notices if corps["page"] == 1 else []}


class TestTED(unittest.TestCase):

    def test_requete_sur_title_proc(self):
        q = I.requete_ted(["due diligence", "osint"], DEBUT, AUJ)
        self.assertIn('title-proc ~ "due diligence"', q)
        self.assertIn(" OR ", q)
        self.assertIn("description-proc", I.CHAMPS_TED)
        self.assertNotIn("notice-title", I.CHAMPS_TED)   # libelle CPV traduit, trompeur

    def test_normalisation_et_france(self):
        f = FauxTED([notice_ted("1-2026", "Provision of OSINT reports"),
                     notice_ted("2-2026", "Due diligence des tiers", pays="FRA",
                                langue="fra")])
        avis, _ = I.collecter_ted(DEBUT, AUJ, fetch=f, requetes=["osint"])
        par = {a["id_avis"]: a for a in avis}
        self.assertEqual(par["1-2026"]["source"], "TED")
        self.assertEqual(par["1-2026"]["date_publication"], "2026-10-20")
        self.assertFalse(par["1-2026"]["francais"])
        self.assertTrue(par["2-2026"]["francais"])

    def test_repli_terme_par_terme(self):
        f = FauxTED([notice_ted("1-2026", "OSINT")], refuse_or=True)
        avis, j = I.collecter_ted(DEBUT, AUJ, fetch=f, requetes=["osint", "kyc"])
        self.assertEqual(j["mode"], "terme")
        self.assertEqual(j["erreurs"], [])
        self.assertEqual(len(avis), 1)

    def test_nature_attribution(self):
        self.assertEqual(I.nature_ted("can-standard"), "attribution")
        self.assertEqual(I.nature_ted("cn-standard"), "avis")


# --- UNGM -------------------------------------------------------------------
def ligne_ungm(ident, titre, ref="RFQ/2026/1", restant="30.0", publie="24-Oct-2026",
               agence="UNOPS", type_av="Request for proposal"):
    return ('<div role="row" data-noticeid="{id}">'
            '<div role="cell" class="tableCell resultTitle">'
            '<span class="ungm-title ungm-title--small">{ref} {titre}</span></div>'
            '<div role="cell" class="tableCell resultInfo1 deadline" '
            'data-description="Deadline"><span>02-Dec-2026 14:00</span>'
            '<span class="remainingDaysToDeadline">{restant}</span></div>'
            '<div role="cell" class="tableCell"><span>{publie}</span></div>'
            '<div role="cell" class="tableCell resultAgency"><span>{agence}</span></div>'
            '<div role="cell" class="tableCell"><span>{type_av}</span></div>'
            '<div role="cell" class="tableCell resultInfo1" '
            'data-description="Reference"><span>{ref}</span></div>'
            '<div role="cell" class="tableCell"><span>Ukraine</span></div></div>'
            '<script>x</script>').format(id=ident, titre=titre, ref=ref,
                                         restant=restant, publie=publie,
                                         agence=agence, type_av=type_av)


class TestUNGM(unittest.TestCase):

    def test_parser_html(self):
        l = I.parser_ungm(ligne_ungm("314921", "Integrity due diligence of vendors"))[0]
        self.assertEqual(l["id"], "314921")
        self.assertEqual(l["titre"], "Integrity due diligence of vendors")
        self.assertEqual(l["agence"], "UNOPS")
        self.assertEqual(l["pays"], "Ukraine")
        self.assertEqual(l["date"], "2026-10-24")
        self.assertEqual(l["deadline"], "2026-12-02")
        self.assertTrue(l["ouvert"])
        self.assertFalse(I.parser_ungm(ligne_ungm("1", "x", restant="-5"))[0]["ouvert"])

    def test_collecte_dedoublonnage_et_arret_page_incomplete(self):
        html = (ligne_ungm("1", "Due diligence of partners")
                + ligne_ungm("2", "Background screening"))
        appels = []

        def fetch(charge):
            appels.append(charge["PageIndex"])
            return (200, html) if charge["PageIndex"] == 0 else (200, "")
        avis, j = I.collecter_ungm(DEBUT, fetch=fetch, requetes=["due diligence"])
        self.assertEqual(sorted(a["id_avis"] for a in avis), ["1", "2"])
        self.assertTrue(all(a["source"] == "UNGM" for a in avis))
        # Page incomplete (2 < 15) -> on s'arrete, pas de page 1 inutile.
        self.assertEqual(appels, [0])

    def test_charge_respecte_le_plafond(self):
        self.assertLessEqual(I._charge_ungm("osint", 0)["PageSize"], 15)

    def test_http_400_journalise(self):
        avis, j = I.collecter_ungm(DEBUT, fetch=lambda c: (400, "Bad Request"),
                                   requetes=["osint"])
        self.assertEqual(avis, [])
        self.assertEqual(len(j["erreurs"]), 1)


# --- Selection --------------------------------------------------------------
def avis(source="TED", id="x", titre="OSINT et due diligence", nature="avis",
         pub="2026-10-20", limite="2026-12-01", francais=False, ouvert=True):
    return {"source": source, "id_avis": id, "titre": titre, "acheteur": "EUAA",
            "nature": nature, "date_publication": pub, "date_limite": limite,
            "description": titre, "corps": "", "francais": francais,
            "ouvert": ouvert, "lien": "http://x/" + id}


class TestSelection(unittest.TestCase):

    def test_est_ouvert(self):
        self.assertTrue(I.est_ouvert(avis(), AUJ))
        self.assertFalse(I.est_ouvert(avis(nature="attribution"), AUJ))
        self.assertFalse(I.est_ouvert(avis(limite="2026-01-01"), AUJ))   # passee
        self.assertFalse(I.est_ouvert(
            avis(source="UNGM", ouvert=False, limite=""), AUJ))

    def test_france_et_attribution_ecartees(self):
        file_, ent = I.file_d_analyse(
            [avis(id="a"), avis(id="b", francais=True),
             avis(id="c", nature="attribution"),
             avis(id="d", titre="Marche de nettoyage")],   # sans terme IE
            set(), AUJ)
        self.assertEqual([a["id_avis"] for a in file_], ["a"])
        self.assertEqual(ent["francais"], 1)
        self.assertEqual(ent["fermes"], 1)

    def test_memoire_avant_plafond(self):
        # 3 avis IE-titre, budget 2, 1 deja vu -> la memoire retire AVANT le
        # plafond : 2 analyses, le deja-vu n'occupe pas une place (regle 9).
        lot = [avis(id=str(i), pub="2026-10-%02d" % (10 + i)) for i in range(3)]
        deja = {C.cle_avis(dict(lot[0], source="TED"))}
        file_, ent = I.file_d_analyse(lot, deja, AUJ, budget=2)
        self.assertEqual(ent["deja_vus"], 1)
        self.assertEqual(len(file_), 2)
        self.assertNotIn("0", [a["id_avis"] for a in file_])

    def test_plafond_budget(self):
        lot = [avis(id=str(i), pub="2026-10-%02d" % (10 + i)) for i in range(3)]
        file_, ent = I.file_d_analyse(lot, set(), AUJ, budget=1)
        self.assertEqual(len(file_), 1)
        self.assertEqual(ent["hors_budget"], 2)

    def test_priorite_titre_puis_recent(self):
        lot = [avis(id="corps", titre="marche divers",
                    pub="2026-10-28"),                        # pas IE au titre
               avis(id="vieux", pub="2026-10-01"),
               avis(id="neuf", pub="2026-10-25")]
        # injecter un terme IE dans le corps du premier pour qu'il passe en ie_corps
        lot[0]["corps"] = "due diligence des fournisseurs"
        file_, _ = I.file_d_analyse(lot, set(), AUJ, budget=10)
        self.assertEqual(file_[0]["id_avis"], "neuf")         # titre IE, plus recent
        self.assertEqual(file_[-1]["id_avis"], "corps")       # ie_corps en dernier


# --- Orchestration (reutilise ligne_pg / ligne_sheet / visible_arise) -------
class TestExecuter(unittest.TestCase):

    def _appel(self, prompt, modele):
        import json as _json
        return _json.dumps({
            "famille_offre": "due_diligence", "type_acheteur": "institution",
            "format": "accord_cadre", "barrieres_entree": "faibles",
            "achat_outil": False, "dimension_internationale": True,
            "sensibilite": "haute", "resume": "DD tiers", "confiance": 0.9})

    def test_bout_en_bout_ecrit_lignes(self):
        fted = FauxTED([notice_ted("1-2026", "Integrity due diligence services")])
        lignes_pg, lignes_sheet, rap = I.executer(
            aujourd=AUJ, fetch_ted=fted, fetch_ungm=lambda c: (200, ""),
            appel=self._appel, deja_vus=set(), debug=False)
        self.assertEqual(len(lignes_pg), 1)
        self.assertEqual(lignes_pg[0]["source"], "TED")
        self.assertIn("action", lignes_pg[0])
        # La ligne Sheet respecte le schema partage du collecteur BOAMP.
        if lignes_sheet:
            self.assertEqual(len(lignes_sheet[0]), len(C.COLONNES_SHEET))

    def test_debug_sans_llm_ni_ecriture(self):
        fted = FauxTED([notice_ted("1-2026", "OSINT reports")])
        lignes_pg, lignes_sheet, rap = I.executer(
            aujourd=AUJ, fetch_ted=fted, fetch_ungm=lambda c: (200, ""),
            deja_vus=set(), debug=True)
        self.assertEqual((lignes_pg, lignes_sheet), ([], []))
        self.assertTrue(rap["debug"])
        self.assertEqual(rap["collecte"]["TED"], 1)

    def test_rapport_markdown(self):
        fted = FauxTED([notice_ted("1-2026", "Due diligence", pays="FRA", langue="fra")])
        _, _, rap = I.executer(aujourd=AUJ, fetch_ted=fted,
                               fetch_ungm=lambda c: (200, ""), deja_vus=set(), debug=True)
        md = I.rapport_markdown(rap, ["aucune (mode debug)"])
        self.assertIn("TED + UNGM", md)
        self.assertIn("francais ecartes", md)


if __name__ == "__main__":
    unittest.main()
