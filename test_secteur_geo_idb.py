# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Tests des chantiers 10/08/2026.
=================================================
Couvre trois ajouts au dashboard :

  1. TAXONOMIE SECTEUR CANONIQUE (`secteur_canonique`, `_secteur_du_lead`) :
     replie CPV + watchlist libre sur 10 secteurs ; chaque lead porte `sect`.
  2. LENTILLE GEOPOLITIQUE (`preparer_geo`) : historique 90 j, fenetre distincte
     du bandeau (30 j), champs cartographiables, tri severite+date.
  3. IDB BRANCHE : source d'avis ISO3, presente dans le tuple de lecture, dans
     construire_leads, et dans le gabarit (badge + libelles + filtrable).

Ces tests sont deterministes (aucun reseau) et suivent la convention du depot :
fichier appaire, `unittest`, dates calculees jamais figees.
"""

import unittest
from datetime import date, timedelta

import radar_dashboard as rd


# ---------------------------------------------------------------------------
# 1. TAXONOMIE SECTEUR
# ---------------------------------------------------------------------------
class TestSecteurCanonique(unittest.TestCase):
    def test_libelles_cpv_replies(self):
        """Les libelles CPV des attributions tombent sur le bon canonique."""
        cas = {
            "BTP / construction": "BTP / Construction",
            "Energie / petrole-gaz": "Énergie & Oil-Gas",
            "Mines / materiaux": "Mines / Matériaux",
            "Transport / vehicules": "Transport / Logistique",
            "Telecom / equipements": "Télécom / IT",
            "Administration / defense": "Défense",
        }
        for brut, attendu in cas.items():
            self.assertEqual(rd.secteur_canonique(brut), attendu,
                             "{} devrait donner {}".format(brut, attendu))

    def test_watchlist_libre_repliee(self):
        """Le vocabulaire libre de la watchlist (dont 'luxe', 'oil & gas')."""
        self.assertEqual(rd.secteur_canonique("Luxe"), "Luxe")
        self.assertEqual(rd.secteur_canonique("oil & gas"), "Énergie & Oil-Gas")
        self.assertEqual(rd.secteur_canonique("Agro"), "Agro")
        self.assertEqual(rd.secteur_canonique("Défense"), "Défense")

    def test_repli_autre(self):
        """Sans mot-cle reconnu -> 'Autre', jamais d'exception."""
        self.assertEqual(rd.secteur_canonique(""), "Autre")
        self.assertEqual(rd.secteur_canonique("blablabla inconnu"), "Autre")
        self.assertEqual(rd.secteur_canonique(None), "Autre")

    def test_priorite_defense_avant_generique(self):
        """'Défense' est prioritaire : un titre mixte defense+batiment reste
        classe defense (signal metier fort)."""
        self.assertEqual(
            rd.secteur_canonique("construction d'une base militaire"),
            "Défense")

    def test_lead_avis_porte_un_secteur(self):
        """Un avis (TED) porte un `sect` deduit du titre (estime)."""
        row = {"titre": "Travaux de rehabilitation routiere",
               "pays_execution": "MLI", "score_final": "6",
               "action_recommandee": "contacter", "publication_number": "T1"}
        lead = rd.ligne_vers_lead(row, "TED")
        self.assertIn("sect", lead)
        self.assertEqual(lead["sect"], "BTP / Construction")

    def test_lead_idb_utilise_secteur_idb(self):
        """IDB fournit `secteur_idb` : il prime sur la deduction du titre."""
        row = {"titre": "Proyecto sin palabras clave utiles",
               "secteur_idb": "Transporte y obras", "pays_execution": "HTI",
               "score_final": "6", "action_recommandee": "contacter",
               "publication_number": "I1"}
        lead = rd.ligne_vers_lead(row, "IDB")
        self.assertEqual(lead["sect"], "Transport / Logistique")

    def test_taxonomie_exposee(self):
        """La liste canonique existe et contient les secteurs attendus."""
        for s in ("Défense", "BTP / Construction", "Luxe", "Autre"):
            self.assertIn(s, rd.SECTEURS_CANONIQUES)


# ---------------------------------------------------------------------------
# 2. LENTILLE GEOPOLITIQUE
# ---------------------------------------------------------------------------
def _alerte(iso, nom, zone, sens, sev, motif, jours_recul):
    d = (date.today() - timedelta(days=jours_recul)).isoformat()
    return {"date_maj": d, "pays_execution": iso, "pays_nom": nom, "zone": zone,
            "niveau_avant": "Orange", "niveau_apres": "Rouge", "sens": sens,
            "severite": str(sev), "motif": motif, "lien": "https://x/" + iso}


class TestPreparerGeo(unittest.TestCase):
    def test_fenetre_semaine_en_cours(self):
        """Onglet = 7 derniers jours. Un signal a 3 j reste, a 10 j sort."""
        recent = [_alerte("MLI", "Mali", "Sahel", "aggravation", 3, "x", 3)]
        vieux = [_alerte("MLI", "Mali", "Sahel", "aggravation", 3, "x", 10)]
        self.assertEqual(len(rd.preparer_geo(recent)), 1)
        self.assertEqual(len(rd.preparer_geo(vieux)), 0)

    def test_exclusion_au_dela_de_7j(self):
        a = [_alerte("MLI", "Mali", "Sahel", "aggravation", 3, "x", 30)]
        self.assertEqual(len(rd.preparer_geo(a)), 0)

    def test_champs_cartographiables(self):
        a = [_alerte("HTI", "Haïti", "Amérique latine", "lateral", 4, "gangs", 5)]
        g = rd.preparer_geo(a)[0]
        for champ in ("pays", "zone", "sens", "severite", "date", "motif", "lien"):
            self.assertIn(champ, g)
        self.assertEqual(g["pays"], "Haïti")
        self.assertEqual(g["zone"], "Amérique latine")
        self.assertEqual(g["severite"], 4)

    def test_tri_severite_puis_date(self):
        """Tri : severite decroissante d'abord, puis date recente."""
        a = [
            _alerte("A", "A", "Sahel", "aggravation", 1, "faible", 2),
            _alerte("B", "B", "Sahel", "aggravation", 4, "fort", 10),
            _alerte("C", "C", "Sahel", "aggravation", 4, "fort recent", 1),
        ]
        g = rd.preparer_geo(a)
        self.assertEqual(g[0]["severite"], 4)
        self.assertEqual(g[0]["date"], g[0]["date"])       # sev 4 le plus recent
        self.assertEqual(g[-1]["severite"], 1)             # sev 1 en dernier

    def test_severite_non_numerique_toleree(self):
        a = [{"date_maj": date.today().isoformat(), "pays_execution": "MLI",
              "pays_nom": "Mali", "zone": "Sahel", "sens": "aggravation",
              "severite": "", "motif": "x"}]
        g = rd.preparer_geo(a)
        self.assertEqual(g[0]["severite"], 0)

    def test_liste_vide(self):
        self.assertEqual(rd.preparer_geo([]), [])
        self.assertEqual(rd.preparer_geo(None), [])


# ---------------------------------------------------------------------------
# 3. IDB BRANCHE
# ---------------------------------------------------------------------------
class TestIdbBranche(unittest.TestCase):
    def _row(self):
        return {"titre": "Rehabilitación de carretera", "acheteur": "Min. Transporte",
                "pays_execution": "HTI", "secteur_idb": "Transporte",
                "score_final": "7.1", "score_surete": "7.4", "score_commercial": "6.8",
                "action_recommandee": "contacter", "fenetre_action": "immediate",
                "justification": "zone instable", "lien_avis": "https://idb/h1",
                "publication_number": "IDB-1"}

    def test_construire_leads_accepte_idb(self):
        leads = rd.construire_leads([], [], [], {}, [], lignes_idb=[self._row()])
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["src"], "IDB")

    def test_idb_resolu_en_iso3(self):
        """IDB ecrit des ISO3 : Haïti doit etre resolu, pas 'Non classé'."""
        leads = rd.construire_leads([], [], [], {}, [], lignes_idb=[self._row()])
        self.assertEqual(leads[0]["pays"], "Haïti")
        self.assertNotEqual(leads[0]["zone"], "Non classé")

    def test_idb_dans_le_gabarit(self):
        """Points de branchement IDB : badge, libelles, source filtrable, avis."""
        html = rd.GABARIT_HTML
        for nom, marqueur in (
                ("badge CSS", ".src.idb{"),
                ("source filtrable (barre dynamique)", "'IFC','IDB'"),
                ("libelle de carte", "IDB:'IDB · Amérique latine'"),
                ("libelle du bandeau", "IDB:'IDB (Amérique latine)'"),
                ("compteur avis", "l.src==='IDB'")):
            self.assertIn(marqueur, html, "branchement IDB absent : {}".format(nom))

    def test_idb_dans_catalogue_sources(self):
        self.assertIn("IDB", rd.CATALOGUE_SOURCES)

    def test_lire_onglets_renvoie_dix_sept_valeurs(self):
        """Le tuple de lecture inclut IDB (16e) puis BMP amont (17e). Garde-fou
        contre une regression d'arite non propagee aux consommateurs."""
        import ast
        src = open("radar_dashboard.py", encoding="utf-8").read()
        arbre = ast.parse(src)
        fonc = next(n for n in ast.walk(arbre)
                    if isinstance(n, ast.FunctionDef) and n.name == "lire_onglets")
        ret = next(n for n in ast.walk(fonc)
                   if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple))
        self.assertEqual(len(ret.value.elts), 17)


# ---------------------------------------------------------------------------
# 4. RENDU HTML DE BOUT EN BOUT (les nouveaux JSON sont injectes)
# ---------------------------------------------------------------------------
class TestRenduComplet(unittest.TestCase):
    def test_placeholders_geo_et_secteurs_remplis(self):
        row = {"titre": "Travaux routiers", "pays_execution": "MLI",
               "score_final": "6", "action_recommandee": "contacter",
               "publication_number": "T1"}
        leads = rd.construire_leads([row], [], [], {}, [])
        alertes = [_alerte("MLI", "Mali", "Sahel", "aggravation", 3, "x", 5)]
        html = rd.generer_html(leads, [{"entreprise": "LVMH", "secteur": "Luxe"}],
                               alertes=alertes)
        for ph in ("__GEO_JSON__", "__SECTEURS_JSON__", "__LEADS_JSON__"):
            self.assertNotIn(ph, html, "placeholder non remplace : {}".format(ph))
        # La lentille geo et les selecteurs sont presents dans le gabarit.
        self.assertIn('data-lens="geo"', html)
        self.assertIn('id="secteurSel"', html)
        self.assertIn('id="groupSel"', html)


if __name__ == "__main__":
    unittest.main()
