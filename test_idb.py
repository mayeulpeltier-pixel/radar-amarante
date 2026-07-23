# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- TESTS DU COLLECTEUR IDB.
===========================================

Aucun reseau, aucun LLM, aucun Sheet : le CSV est injecte par une doublure.
Les pieges verrouilles ici viennent tous des DONNEES REELLES relevees par la
sonde du 22/07/2026 :
  - BOM UTF-8 sur la premiere colonne ("\ufeffnoticeid") ;
  - valeurs manquantes ecrites "null" / "NULL" en toutes lettres ;
  - `countryname` en majuscules, et parfois "REGIONAL" (pas un pays) ;
  - deux formats de date melanges dans le meme fichier ;
  - avis dont l'echeance est passee (aucune valeur commerciale).
"""

import unittest
from datetime import date, timedelta

import idb_radar as idb


EN_TETE = ("\ufeffnoticeid,type,countryname,projectnumber,proyecturl,loannumber,"
           "noticetitle,ezshareid,documenturl,projectname,publicationyear,"
           "publicationdate,deadline,sector,sectorenglnm,projectstatus,"
           "procurement_id,process_id,category_nm,prcrmnt_mthd_engl_nm,"
           "process_nm,process_desc")


def _ligne(noticeid="29553", pays="COLOMBIA", titre="Rehabilitacion vial",
           date_pub=None, deadline=None, categorie="Bidding Notice",
           secteur="TRANSPORT"):
    """Construit une ligne CSV realiste (22 colonnes, memes positions)."""
    if date_pub is None:
        date_pub = (date.today() - timedelta(days=5)).isoformat() + "T00:00"
    if deadline is None:
        deadline = (date.today() + timedelta(days=30)).strftime("%m/%d/%Y")
    return ",".join([
        noticeid, "SPECIFIC", pays, "CO-L1014",
        "https://www.iadb.org/en/project/CO-L1014", "null",
        titre, "EZSHARE-1", "https://idbdocs.iadb.org/doc?n=1",
        "Programa de Transporte", "2026", date_pub, deadline,
        "NULL", secteur, "Implementation", "PR1", "PS1", categorie,
        "International Competitive Bidding", "Proceso", "Descripcion du marche",
    ])


def _csv(*lignes):
    return "\n".join([EN_TETE] + list(lignes))


class TestLectureDuFichier(unittest.TestCase):
    """Les pieges de forme du CSV reel."""

    def test_bom_retire_de_la_premiere_colonne(self):
        """Sans retrait du BOM, la colonne s'appellerait '\\ufeffnoticeid' et
        l'identifiant serait introuvable : chaque run reecrirait tout."""
        lignes = list(idb.lignes_csv("x", fetch=lambda _u: _csv(_ligne())))
        self.assertIn("noticeid", lignes[0])
        self.assertEqual(lignes[0]["noticeid"], "29553")

    def test_chaines_null_traitees_comme_vides(self):
        """'null' et 'NULL' sont des valeurs litterales dans ce fichier."""
        ligne = {"a": "null", "b": "NULL", "c": "  ", "d": "reel"}
        self.assertEqual(idb._val(ligne, "a"), "")
        self.assertEqual(idb._val(ligne, "b"), "")
        self.assertEqual(idb._val(ligne, "c"), "")
        self.assertEqual(idb._val(ligne, "d"), "reel")

    def test_deux_formats_de_date(self):
        self.assertEqual(idb.lire_date("2019-04-05T00:00"), "2019-04-05")
        self.assertEqual(idb.lire_date("4/5/2019"), "2019-04-05")
        for brut in ("", "null", "NULL", "pas une date"):
            self.assertEqual(idb.lire_date(brut), "")


class TestNormalisation(unittest.TestCase):

    def _un(self, **kw):
        lignes = list(idb.lignes_csv("x", fetch=lambda _u: _csv(_ligne(**kw))))
        return idb.normaliser(lignes[0])

    def test_avis_complet(self):
        a = self._un()
        self.assertEqual(a["pays_execution"], "COL")
        self.assertEqual(a["publication_number"], "IDB-29553")
        self.assertEqual(a["acheteur"], "Inter-American Development Bank")
        self.assertIn("Rehabilitacion", a["titre"])
        self.assertTrue(a["lien_avis"].startswith("http"))

    def test_pays_du_perimetre_acceptes(self):
        for nom, iso in (("MEXICO", "MEX"), ("ARGENTINA", "ARG"),
                         ("BRAZIL", "BRA"), ("HONDURAS", "HND"),
                         ("GUATEMALA", "GTM"), ("CHILE", "CHL")):
            a = self._un(pays=nom)
            self.assertIsNotNone(a, "{} devrait etre retenu".format(nom))
            self.assertEqual(a["pays_execution"], iso)

    def test_regional_ecarte(self):
        """'REGIONAL' n'est pas un pays : aucun pays d'execution ne peut en
        etre deduit, donc on ecarte plutot que d'inventer."""
        self.assertIsNone(self._un(pays="REGIONAL"))

    def test_pays_hors_perimetre_ecarte(self):
        for nom in ("BAHAMAS", "URUGUAY", "BARBADOS"):
            self.assertIsNone(self._un(pays=nom),
                              "{} ne devrait pas etre retenu".format(nom))

    def test_echeance_passee_ecartee(self):
        """Un avis dont la date limite est passee n'a aucune valeur."""
        hier = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")
        self.assertIsNone(self._un(deadline=hier))

    def test_echeance_illisible_conservee(self):
        """Prudence assumee : on prefere analyser en trop qu'ecarter a tort."""
        self.assertIsNotNone(self._un(deadline="null"))

    def test_sans_titre_ecarte(self):
        self.assertIsNone(self._un(titre=""))

    def test_echeance_depassee_bornes(self):
        aujourd = date(2026, 7, 22)
        self.assertTrue(idb.echeance_depassee("2026-07-21", aujourd))
        self.assertFalse(idb.echeance_depassee("2026-07-22", aujourd))
        self.assertFalse(idb.echeance_depassee("", aujourd))


class TestPrioriteEtFenetre(unittest.TestCase):
    """Meme doctrine que ReliefWeb : risque dominant, fraicheur en depart."""

    AUJOURD = date(2026, 7, 22)

    def test_risque_domine(self):
        mex = {"pays_execution": "MEX", "date_publication": "2026-06-01"}
        chl = {"pays_execution": "CHL", "date_publication": "2026-07-22"}
        self.assertGreater(idb.priorite_analyse(mex, self.AUJOURD),
                           idb.priorite_analyse(chl, self.AUJOURD))

    def test_a_risque_egal_le_plus_recent_gagne(self):
        vieux = {"pays_execution": "COL", "date_publication": "2026-06-01"}
        neuf = {"pays_execution": "COL", "date_publication": "2026-07-21"}
        self.assertGreater(idb.priorite_analyse(neuf, self.AUJOURD),
                           idb.priorite_analyse(vieux, self.AUJOURD))

    def test_date_absente_ni_favorisee_ni_condamnee(self):
        import ted_complet_v14 as ted
        sans = {"pays_execution": "COL", "date_publication": ""}
        attendu = ted.MULTIPLICATEUR_ZONE["COL"] * 0.6
        self.assertAlmostEqual(idb.priorite_analyse(sans, self.AUJOURD),
                               attendu, places=6)

    def test_fenetre_de_fraicheur(self):
        seuil = self.AUJOURD - timedelta(days=60)
        self.assertTrue(idb.dans_la_fenetre(
            {"date_publication": "2026-07-01"}, seuil))
        self.assertFalse(idb.dans_la_fenetre(
            {"date_publication": "2026-01-01"}, seuil))
        self.assertTrue(idb.dans_la_fenetre({"date_publication": ""}, seuil))



class TestChaineComplete(unittest.TestCase):
    """De bout en bout, sans reseau : CSV -> avis filtres + statistiques."""

    def test_entonnoir(self):
        vieux = (date.today() - timedelta(days=200)).isoformat() + "T00:00"
        hier = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")
        contenu = _csv(
            _ligne(noticeid="1", pays="COLOMBIA"),
            _ligne(noticeid="2", pays="REGIONAL"),            # pas un pays
            _ligne(noticeid="3", pays="BAHAMAS"),             # hors perimetre
            _ligne(noticeid="4", pays="PERU", deadline=hier),  # echeance passee
            _ligne(noticeid="5", pays="MEXICO", date_pub=vieux),  # hors fenetre
            _ligne(noticeid="6", pays="ECUADOR"),
        )
        avis, stats = idb.collecter_et_normaliser(
            fetch_url=lambda: "url-factice",
            fetch_csv=lambda _u: contenu)
        self.assertEqual(stats["lignes"], 6)
        self.assertEqual(stats["retenus"], 2)
        self.assertEqual(sorted(a["pays_execution"] for a in avis),
                         ["COL", "ECU"])
        self.assertEqual(stats["hors_fenetre"], 1)
        # Motifs attribues dans l'ORDRE REEL des filtres (le compteur initial
        # testait l'echeance avant le pays, ce qui faussait le diagnostic).
        self.assertEqual(stats["motifs"].get("echeance_passee"), 1)   # PERU
        # REGIONAL et BAHAMAS n'ont pas de correspondance ISO3 : meme motif.
        self.assertEqual(stats["motifs"].get("pays_non_reconnu"), 2)

    def test_doublons_ecartes(self):
        contenu = _csv(_ligne(noticeid="7"), _ligne(noticeid="7"))
        avis, _s = idb.collecter_et_normaliser(
            fetch_url=lambda: "u", fetch_csv=lambda _u: contenu)
        self.assertEqual(len(avis), 1)


class TestEntetesReseau(unittest.TestCase):
    """Le bug du premier run reel (22/07/2026) : Cloudflare a renvoye 403 sur
    le telechargement parce que la session s'annoncait 'python-requests/2.33.1'."""

    def test_entete_navigateur(self):
        ua = idb.ENTETES.get("User-Agent", "")
        self.assertIn("Mozilla", ua)
        self.assertNotIn("python-requests", ua)

    def test_les_appels_reseau_portent_les_entetes(self):
        """Structurel : les deux appels (CKAN et telechargement) doivent
        passer ENTETES, sinon le 403 revient."""
        import inspect
        for fonction in (idb.url_du_fichier, idb.lignes_csv):
            src = inspect.getsource(fonction)
            self.assertIn("headers=ENTETES", src,
                          "{} n'envoie pas les en-tetes".format(fonction.__name__))

    def test_session_globale_non_mutee(self):
        """PIEGE MAJEUR : ted.session_robuste() est un SINGLETON partage avec
        TED et Anthropic. Modifier ses en-tetes contaminerait tout le pipeline.
        Les en-tetes doivent etre passes par requete, jamais poses dessus."""
        import inspect
        src = inspect.getsource(idb)
        self.assertNotIn("session.headers.update", src)
        self.assertNotIn("session_robuste().headers", src)



class TestMotifsDeRejet(unittest.TestCase):
    """Un compteur faux oriente le diagnostic dans la mauvaise direction :
    la premiere version testait l'echeance AVANT le pays, et attribuait donc
    a "echeance passee" des lignes qui etaient d'abord hors perimetre."""

    def _motif(self, **kw):
        ligne = list(idb.lignes_csv("x", fetch=lambda _u: _csv(_ligne(**kw))))[0]
        return idb.motif_rejet(ligne)

    def test_les_quatre_motifs(self):
        hier = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")
        self.assertEqual(self._motif(titre=""), "sans_titre")
        # "REGIONAL" et "BAHAMAS" n'ont aucune correspondance ISO3.
        self.assertEqual(self._motif(pays="REGIONAL"), "pays_non_reconnu")
        self.assertEqual(self._motif(pays="BAHAMAS"), "pays_non_reconnu")
        # "PHILIPPINES" se traduit (PHL) mais reste hors perimetre commercial.
        self.assertEqual(self._motif(pays="PHILIPPINES"), "hors_perimetre")
        self.assertEqual(self._motif(pays="PERU", deadline=hier), "echeance_passee")

    def test_le_pays_prime_sur_l_echeance(self):
        """Une ligne hors perimetre ET a echeance passee compte comme HORS
        PERIMETRE : c'est ce filtre-la qui s'applique en premier."""
        hier = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")
        self.assertEqual(self._motif(pays="PHILIPPINES", deadline=hier),
                         "hors_perimetre")

    def test_ligne_valide_sans_motif(self):
        self.assertEqual(self._motif(), "")


class TestInspectionSchema(unittest.TestCase):
    """Le jeu ATTRIBUTIONS (70 Mo) n'a jamais ete inspecte. On lit son schema
    AVANT d'ecrire la moindre regle : coder a l'aveugle est exactement ce qui
    a range des numeros de telephone sous `publication_number`."""

    FAUX = ("award_id,contract_date,supplier_name,supplier_country,"
            "country_name,amount_usd\n"
            "A1,2026-06-15T00:00,Odebrecht SA,Brazil,Colombia,12500000\n"
            "A2,2025-11-02T00:00,Sacyr,Spain,Peru,8200000\n")

    def _info(self):
        return idb.inspecter_schema(fetch_url=lambda: "u",
                                    fetch_csv=lambda _u: self.FAUX)

    def test_colonnes_relevees(self):
        self.assertEqual(self._info()["colonnes"][0], "award_id")
        self.assertIn("supplier_name", self._info()["colonnes"])

    def test_fraicheur_par_colonne_de_date(self):
        """La mesure decisive : le jeu d'avis s'est revele fige a 2025-10."""
        annees = self._info()["annees"]
        self.assertIn("contract_date", annees)
        self.assertEqual(annees["contract_date"]["2026"], 1)

    def test_echantillon_brut_conserve(self):
        ligne = self._info()["echantillon"][0]
        self.assertEqual(ligne["supplier_name"], "Odebrecht SA")

    def test_choix_du_jeu(self):
        """RADAR_IDB_JEU bascule sur le paquet des attributions."""
        self.assertNotEqual(idb.PAQUET_AVIS, idb.PAQUET_ATTRIB)
        self.assertIn(idb.PAQUET, (idb.PAQUET_AVIS, idb.PAQUET_ATTRIB))


class TestDiagnosticRessources(unittest.TestCase):
    """Le jeu des attributions n'expose AUCUNE URL de ressource. J'ai alors
    fabrique '/file/download/<id>' -> 403 applicatif. Une URL ne se devine
    pas : elle se diagnostique."""

    PAQUET = {"resources": [
        {"id": "dd09c605", "name": "Awards CSV", "format": "CSV",
         "size": 70076202, "url": "", "datastore_active": True}]}

    def test_tous_les_champs_sont_rendus(self):
        rap = idb.diagnostiquer_paquet(fetch=lambda: self.PAQUET)
        champs = rap["ressources"][0]
        self.assertIn("datastore_active", champs)
        self.assertIn("size", champs)

    def test_aucune_url_fabriquee(self):
        """Garde anti-regression : le code ne doit plus construire d'URL de
        telechargement a partir d'un identifiant de ressource."""
        import inspect
        src = inspect.getsource(idb.url_du_fichier)
        self.assertNotIn('"https://data.iadb.org/file/download/{}".format', src)

    def test_paquet_sans_ressource(self):
        rap = idb.diagnostiquer_paquet(fetch=lambda: {"resources": []})
        self.assertEqual(rap["ressources"], [])
        self.assertEqual(rap["essais"], [])


class TestDatastore(unittest.TestCase):
    """Le jeu des attributions pese 70 Mo et n'expose AUCUNE URL. Le datastore
    CKAN, lui, est actif : donnees en JSON, page par page, avec filtrage COTE
    SERVEUR. On ne rapatrie que le perimetre au lieu du fichier entier."""

    CHAMPS = ["_id", "contract_id", "contract_type", "project_name",
              "operation_country_code", "operation_country_name",
              "economic_sector_name", "idb_amount", "awarded_firm_name",
              "awarded_firm_country_name", "contract_date"]

    def _faux(self, rid, filtres, limite, decalage):
        rec = {c: "" for c in self.CHAMPS}
        rec.update({"contract_id": "CO-L1174-C01",
                    "operation_country_code": "CO",
                    "awarded_firm_name": "Constructora Odebrecht SA",
                    "awarded_firm_country_name": "Brazil",
                    "idb_amount": "12500000"})
        total = 42 if not filtres else (
            7 if filtres.get("operation_country_code") == "CO" else 0)
        return {"records": [rec][:limite],
                "fields": [{"id": c} for c in self.CHAMPS], "total": total}

    def test_lecture(self):
        rec, champs, total = idb.lire_datastore("r", fetch=self._faux, limite=5)
        self.assertEqual(len(champs), len(self.CHAMPS))
        self.assertEqual(total, 42)
        self.assertEqual(rec[0]["awarded_firm_name"], "Constructora Odebrecht SA")

    def test_filtrage_serveur(self):
        _r, _c, n = idb.lire_datastore(
            "r", filtres={"operation_country_code": "CO"}, fetch=self._faux)
        self.assertEqual(n, 7)
        _r2, _c2, n2 = idb.lire_datastore(
            "r", filtres={"operation_country_code": "FR"}, fetch=self._faux)
        self.assertEqual(n2, 0)

    def test_ressource_datastore_prioritaire(self):
        """On choisit la ressource dont le datastore est ACTIF, pas la
        premiere venue."""
        paquet = {"resources": [
            {"id": "sans-datastore", "datastore_active": False},
            {"id": "avec-datastore", "datastore_active": True}]}
        self.assertEqual(idb.id_ressource(fetch=lambda: paquet),
                         "avec-datastore")

    def test_perimetre_sous_les_deux_formes(self):
        """Le datastore expose le pays en ISO2 et en clair : on doit pouvoir
        filtrer avec l'un ou l'autre."""
        self.assertEqual(len(idb.PAYS_ISO2), len(idb.PAYS_NOMS))
        self.assertIn("CO", idb.PAYS_ISO2)
        self.assertIn("COLOMBIA", idb.PAYS_NOMS)  # majuscules : forme du datastore


class TestAttributions(unittest.TestCase):
    """Alimentation de l'onglet PARTAGE `attributions_radar` (lentille
    Titulaires). Le titulaire est le prospect : une entreprise qui a gagne un
    marche IDB le mobilise sur le terrain."""

    AUJOURD = date(2026, 7, 22)
    BASE = {
        "awarded_firm_name": "Constructora Conconcreto SA",
        "operation_country_name": "COLOMBIA",
        "awarded_firm_country_name": "COLOMBIA",
        "contract_id": "CO-L1174-C01", "project_number": "CO-L1174",
        "project_name": "Corredor vial Catatumbo", "contract_type": "Works",
        "procurement_type": "ICB", "economic_sector_name": "TRANSPORT",
        "total_amount": "12500000.0", "idb_amount": "9000000",
        "executing_agency": "INVIAS",
        "signature_date": "2026-05-14 09:00:00.000000000",
    }

    def _n(self, **kw):
        return idb.normaliser_attribution(dict(self.BASE, **kw), self.AUJOURD)

    def test_ligne_complete(self):
        a = self._n()
        self.assertEqual(a["pays_execution"], "COL")
        self.assertEqual(a["gagnant"], "Constructora Conconcreto SA")
        self.assertEqual(a["valeur_attribuee"], "USD 12 500 000")
        self.assertEqual(a["publication_number"], "IDB-C-CO-L1174-C01")
        self.assertEqual(a["a_demarcher"], "oui")

    def test_titulaire_etranger_detecte(self):
        """Le signal le plus fort : une entreprise etrangere qui mobilise."""
        self.assertTrue(self._n(awarded_firm_name="Odebrecht SA",
                                awarded_firm_country_name="BRAZIL")["_etranger"])
        self.assertFalse(self._n()["_etranger"])

    def test_titulaire_non_nomme_ecarte(self):
        """'Not Available' est frequent : sans nom, pas de prospect."""
        for brut in ("Not Available", "N/A", "", "  ", "null"):
            self.assertIsNone(self._n(awarded_firm_name=brut))

    def test_contrat_trop_ancien_ecarte(self):
        self.assertIsNone(self._n(signature_date="2021-10-25 09:00:00"))

    def test_pays_hors_perimetre_ecarte(self):
        """L'Uruguay est finance par l'IDB mais hors perimetre commercial."""
        self.assertIsNone(self._n(operation_country_name="URUGUAY"))

    def test_pays_a_code_idb_non_iso(self):
        """Le piege du 22/07/2026 : filtrer sur operation_country_code renvoyait
        ZERO contrat pour le Mexique, le Honduras, le Guatemala et le Chili,
        car l'IDB utilise ses propres codes (UR pour l'Uruguay, ISO2 = UY).
        On passe par les NOMS, donc ces pays remontent."""
        for nom, iso in (("MEXICO", "MEX"), ("HONDURAS", "HND"),
                         ("GUATEMALA", "GTM"), ("CHILE", "CHL")):
            a = self._n(operation_country_name=nom)
            self.assertIsNotNone(a, "{} devrait remonter".format(nom))
            self.assertEqual(a["pays_execution"], iso)

    def test_montant_absent_ne_bloque_pas(self):
        """Les contrats de consultants individuels sont souvent a 0."""
        a = self._n(total_amount="0.0", idb_amount="0")
        self.assertIsNotNone(a)
        self.assertEqual(a["valeur_attribuee"], "")

    def test_horodatage(self):
        self.assertEqual(
            idb._lire_horodatage("2021-10-25 09:00:00.000000000"), "2021-10-25")
        for brut in ("", "Not Available", "pas une date", None):
            self.assertEqual(idb._lire_horodatage(brut), "")

    def test_schema_compatible_onglet_partage(self):
        """L'onglet est partage avec TED, BM, UNGM et IsDB : chaque colonne
        officielle doit etre presente, sinon la lentille lit de travers."""
        import bm_attributions
        a = self._n()
        for colonne in bm_attributions.COLONNES:
            self.assertIn(colonne, a,
                          "colonne '{}' manquante".format(colonne))


class TestCollecteAttributions(unittest.TestCase):
    """Les trois filtres, mesures sur donnees reelles le 22/07/2026 : 82 % de
    titulaires anonymes, une masse de micro-contrats (741 USD), et un jeu qui
    accuse 7 mois de retard."""

    AUJOURD = date(2026, 7, 22)
    BASE = {"awarded_firm_name": "IZAMAK S.A. DE C.V.",
            "operation_country_name": "GUATEMALA",
            "awarded_firm_country_name": "MEXICO", "contract_id": "GU-L1-C01",
            "project_number": "GU-L1", "project_name": "Carretera CA-9",
            "contract_type": "Works", "economic_sector_name": "TRANSPORT",
            "total_amount": "1121569", "executing_agency": "CIV",
            "signature_date": "2025-11-14 09:00:00"}

    def _collecte(self, records):
        def faux(rid, filtres, limite, decalage):
            if (decalage or filtres.get("operation_country_name") != "GUATEMALA"
                    or filtres.get("contract_type") != "Works"):
                return {"records": [], "fields": [], "total": 0}
            return {"records": records, "fields": [], "total": len(records)}
        return idb.collecter_attributions(rid="r", fetch=faux,
                                          aujourd_hui=self.AUJOURD)

    def test_les_trois_filtres(self):
        recs = [self.BASE,
                dict(self.BASE, contract_id="X2", total_amount="741"),
                dict(self.BASE, contract_id="X3",
                     awarded_firm_name="Not Available"),
                dict(self.BASE, contract_id="X4",
                     signature_date="2020-01-01 09:00:00")]
        a, s = self._collecte(recs)
        self.assertEqual(s["retenus"], 1)
        self.assertEqual(s["sans_nom"], 1)
        self.assertEqual(s["trop_petit"], 1)
        self.assertEqual(s["hors_fenetre"], 1)
        self.assertEqual(a[0]["gagnant"], "IZAMAK S.A. DE C.V.")

    def test_titulaire_etranger_compte(self):
        _a, s = self._collecte([self.BASE])
        self.assertEqual(s["etrangers"], 1)

    def test_tri_par_montant_decroissant(self):
        gros = dict(self.BASE, contract_id="G", total_amount="17547637")
        a, _s = self._collecte([self.BASE, gros])
        self.assertEqual(a[0]["publication_number"], "IDB-C-G")

    def test_doublons_ecartes(self):
        a, _s = self._collecte([self.BASE, dict(self.BASE)])
        self.assertEqual(len(a), 1)

    def test_seuil_pilotable(self):
        self.assertIsInstance(idb.MONTANT_MIN, float)
        self.assertIn("Works", idb.TYPES_RETENUS)


class TestEcritureAttributions(unittest.TestCase):
    """La garde partagee par les cinq sources d'attributions : une ligne
    existante n'est JAMAIS reecrite (statut_prospection = saisie humaine)."""

    class _Feuille:
        def __init__(self, existants=()):
            self._rec = [{"publication_number": p} for p in existants]
            self.ajouts = []

        def get_all_values(self):
            """Grille BRUTE (en-tete + lignes), comme gspread.

            Depuis le 23/07/2026 le chemin d'ecriture construit son index en
            LECTURE POSITIONNELLE selon le schema partage des attributions
            (regle 4), et non plus via `get_all_records`, qui numerisait les
            identifiants et levait sur un en-tete duplique."""
            import bm_attributions
            if not self._rec:
                return []
            colonnes = list(bm_attributions.COLONNES)
            return ([colonnes] +
                    [[str(r.get(c, "")) for c in colonnes] for r in self._rec])

        def get_all_records(self):
            return list(self._rec)

        def append_rows(self, lignes, value_input_option=None):
            self.ajouts.append(list(lignes))

    def _attrib(self, pub="IDB-C-1"):
        import bm_attributions
        a = {c: "" for c in bm_attributions.COLONNES}
        a.update({"publication_number": pub, "gagnant": "IZAMAK",
                  "pays_execution": "GTM", "a_demarcher": "oui"})
        return a

    def test_ligne_existante_jamais_reecrite(self):
        f = self._Feuille(["IDB-C-1"])
        nb, ignorees = idb.ecrire_attributions(f, [self._attrib()])
        self.assertEqual((nb, ignorees), (0, 1))
        self.assertEqual(f.ajouts, [])

    def test_nouvelle_ligne_statut_vierge(self):
        import bm_attributions
        f = self._Feuille()
        nb, _i = idb.ecrire_attributions(f, [self._attrib("IDB-C-9")])
        self.assertEqual(nb, 1)
        ligne = f.ajouts[0][0]
        self.assertEqual(len(ligne), len(bm_attributions.TOUTES_COLONNES))
        self.assertEqual(ligne[-2], "")          # statut a remplir par l'humain

    def test_doublon_dans_le_meme_lot(self):
        f = self._Feuille()
        nb, ignorees = idb.ecrire_attributions(
            f, [self._attrib("IDB-C-5"), self._attrib("IDB-C-5")])
        self.assertEqual((nb, ignorees), (1, 1))


class TestRoutageDuMain(unittest.TestCase):
    """Le bug du 22/07/2026 : un garde-fou pose quand le schema etait inconnu
    bloquait encore l'ecriture apres sa validation, et le bloc 'mode reel'
    s'executait meme pour le jeu 'avis'. Trois combinaisons, trois branches."""

    def _router(self, jeu, debug):
        import importlib
        import io
        import os
        import sys
        from contextlib import redirect_stdout
        avant = (os.environ.get("RADAR_IDB_JEU"),
                 os.environ.get("RADAR_IDB_DEBUG"))
        os.environ["RADAR_IDB_JEU"] = jeu
        os.environ["RADAR_IDB_DEBUG"] = debug
        for m in [x for x in list(sys.modules) if x.startswith("idb_radar")]:
            del sys.modules[m]
        module = importlib.import_module("idb_radar")
        appels = []
        module.collecter_attributions = (
            lambda **k: appels.append("attributions") or ([], {}))
        module.collecter_et_normaliser = (
            lambda **k: appels.append("avis") or ([], {}))
        module.diagnostiquer_paquet = (
            lambda **k: appels.append("diagnostic") or {"ressources": [],
                                                        "essais": []})
        try:
            with redirect_stdout(io.StringIO()):
                try:
                    module.main()
                except Exception:
                    pass                    # doublures incompletes : sans objet
        finally:
            for cle, val in zip(("RADAR_IDB_JEU", "RADAR_IDB_DEBUG"), avant):
                if val is None:
                    os.environ.pop(cle, None)
                else:
                    os.environ[cle] = val
            for m in [x for x in list(sys.modules) if x.startswith("idb_radar")]:
                del sys.modules[m]
        return appels

    def test_attributions_en_reel_collecte_et_ecrit(self):
        self.assertEqual(self._router("attributions", "0")[0], "attributions")

    def test_attributions_en_verification_diagnostique(self):
        appels = self._router("attributions", "1")
        self.assertEqual(appels[0], "diagnostic")
        self.assertNotIn("attributions", appels)

    def test_avis_ne_declenche_pas_les_attributions(self):
        appels = self._router("avis", "1")
        self.assertIn("avis", appels)
        self.assertNotIn("attributions", appels)
        self.assertNotIn("diagnostic", appels)


class TestSortieSheet(unittest.TestCase):

    def test_schema_coherent(self):
        """L'onglet partage ses conventions avec les autres collecteurs."""
        for attendu in ("publication_number", "pays_execution", "score_final",
                        "deadline", "lien_avis"):
            self.assertIn(attendu, idb.COLONNES_IDB)
        self.assertEqual(idb.TOUTES_COLONNES_IDB[-2:],
                         ["statut_suivi", "date_detection"])

    def test_ligne_respecte_l_ordre_des_colonnes(self):
        avis = {"titre": "Route", "pays_execution": "COL",
                "publication_number": "IDB-1", "acheteur": "IDB",
                "deadline": "2026-09-01", "date_publication": "2026-07-01",
                "lien_avis": "https://x", "type_notice": "Bidding Notice",
                "methode_passation": "ICB", "secteur_idb": "TRANSPORT",
                "projet": "P", "numero_projet": "CO-L1", "pays_acheteur": ""}
        r = {"avis": avis, "extraction": {}, "score": 6.1, "surete": 5.0,
             "commercial": 7.2, "raffine": False, "divergence": False}
        ligne = idb.ligne_depuis_resultat(r)
        self.assertEqual(len(ligne), len(idb.COLONNES_IDB))
        idx = idb.COLONNES_IDB.index
        self.assertEqual(ligne[idx("publication_number")], "IDB-1")
        self.assertEqual(ligne[idx("pays_execution")], "COL")
        self.assertEqual(ligne[idx("titre")], "Route")


if __name__ == "__main__":
    unittest.main(verbosity=2)
