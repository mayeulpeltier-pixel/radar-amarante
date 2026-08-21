# -*- coding: utf-8 -*-
"""Resolveur de dossiers (regroupement des phases d'un projet BM par proj_id).

On verifie l'extraction du proj_id (depuis projet_id ou 'BMP-P######'), le
classement des phases, la timeline chronologique, la phase courante, le tri par
richesse et le filtre multi-phases.
"""

import unittest

import dossiers as d


def amont(pid, **kw):
    base = {"src": "BMP", "pub": "BMP-" + pid, "titre": "Projet " + pid,
            "pays": "Mali", "sect": "BTP", "date_pub": "2026-01-01"}
    base.update(kw)
    return base


def avis(pid, **kw):
    base = {"src": "BM", "projet_id": pid, "pub": "OP1", "titre": "Avis " + pid,
            "pays": "Mali", "sect": "BTP", "date_pub": "2026-06-01"}
    base.update(kw)
    return base


def attrib(pid, **kw):
    base = {"src": "ATTRIB", "projet_id": pid, "pub": "OP2",
            "titre": "Titulaire · Works", "pays": "Mali", "sect": "BTP",
            "date_pub": "2026-11-01"}
    base.update(kw)
    return base


class TestExtractionProjId(unittest.TestCase):

    def test_depuis_projet_id(self):
        self.assertEqual(d.proj_id_du_lead({"projet_id": "P172945"}), "P172945")

    def test_depuis_pub_amont(self):
        self.assertEqual(d.proj_id_du_lead({"pub": "BMP-P172945"}), "P172945")

    def test_aucun(self):
        self.assertEqual(d.proj_id_du_lead({"pub": "123-2026"}), "")

    def test_phase(self):
        self.assertEqual(d.phase_du_lead({"src": "BMP"}), "amont")
        self.assertEqual(d.phase_du_lead({"src": "BM"}), "avis")
        self.assertEqual(d.phase_du_lead({"src": "ATTRIB"}), "attribution")


class TestDossiers(unittest.TestCase):

    def test_projet_complet_trois_phases(self):
        doss = d.construire_dossiers([amont("P000001"), avis("P000001"), attrib("P000001")])
        self.assertEqual(len(doss), 1)
        self.assertEqual(doss[0]["n_phases"], 3)
        self.assertEqual(doss[0]["phase_courante"], "attribution")

    def test_timeline_chronologique(self):
        doss = d.construire_dossiers([attrib("P000001"), amont("P000001"), avis("P000001")])
        self.assertEqual([l["src"] for l in doss[0]["timeline"]],
                         ["BMP", "BM", "ATTRIB"])

    def test_phase_courante_avis_si_pas_attribution(self):
        doss = d.construire_dossiers([amont("P000001"), avis("P000001")])
        self.assertEqual(doss[0]["phase_courante"], "avis")

    def test_lead_sans_proj_id_ignore(self):
        doss = d.construire_dossiers([{"src": "TED", "pub": "123-2026"}])
        self.assertEqual(doss, [])

    def test_tri_par_richesse(self):
        doss = d.construire_dossiers(
            [amont("P000002"), amont("P000001"), avis("P000001"), attrib("P000001")])
        self.assertEqual(doss[0]["proj_id"], "P000001")   # 3 phases avant 1 phase

    def test_multi_phases_seulement(self):
        doss = d.construire_dossiers(
            [amont("P000001"), avis("P000001"), amont("P000002")],
            multi_phases_seulement=True)
        self.assertEqual([x["proj_id"] for x in doss], ["P000001"])

    def test_index_par_proj_id(self):
        doss = d.construire_dossiers([amont("P000001")])
        idx = d.index_par_proj_id(doss)
        self.assertIn("P000001", idx)
        self.assertEqual(idx["P000001"]["proj_id"], "P000001")


class TestSerialisation(unittest.TestCase):

    def test_compact_metadata_et_timeline(self):
        doss = d.construire_dossiers([amont("P000001"), avis("P000001"),
                                      attrib("P000001")])
        out = d.serialiser(doss)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(s["proj_id"], "P000001")
        self.assertEqual(s["n_phases"], 3)
        self.assertEqual([t["phase"] for t in s["timeline"]],
                         ["amont", "avis", "attribution"])

    def test_json_serialisable(self):
        import json
        doss = d.construire_dossiers([amont("P000001"), avis("P000001")])
        json.dumps(d.serialiser(doss))  # ne doit pas lever

    def test_limite(self):
        leads = []
        for i in range(5):
            leads.append(amont("P%06d" % i))
        self.assertEqual(len(d.serialiser(d.construire_dossiers(leads), limite=2)), 2)


if __name__ == "__main__":
    unittest.main()
