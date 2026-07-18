# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE DE DIAGNOSTIC DES SOURCES CANDIDATES (jetable).
=======================================================================

POURQUOI CE FICHIER EXISTE
--------------------------
Lecon ADB : une source peut etre parfaitement publique depuis un navigateur et
totalement inaccessible depuis l'infra GitHub Actions (403, rendu JavaScript,
pare-feu). Construire un collecteur complet AVANT de le savoir, c'est produire
du code mort.

Cette sonde teste les 4 incertitudes du chantier "attributions + UNGM" en UN
SEUL run, depuis l'infra qui compte (GitHub Actions). Elle n'ecrit RIEN :
ni Google Sheet, ni fichier, ni etat. Elle imprime un verdict dans le journal.

CE QU'ELLE TESTE
----------------
  1. BM / procnotices  : l'API que ton collecteur BM utilise DEJA accepte-t-elle
     le type d'avis "Contract Award" ? Si oui -> chemin le moins cher :
     quelques lignes dans ted_complet_bm.py, pas de nouvelle source.
  2. BM / Finances One : jeu de donnees dedie aux attributions (champ
     "Supplier" = nom du gagnant). Repli structure si le point 1 echoue.
  3. UNGM / avis       : les opportunites sont-elles lisibles par une machine,
     ou le tableau est-il rendu en JavaScript (piege ADB) ?
  4. UNGM / attributions : la page Contract Awards expose-t-elle les gagnants ?

APRES USAGE : une fois les verdicts connus, ce fichier peut etre supprime du
depot. Il n'est importe par aucun autre module et n'a aucun effet de bord.

LANCEMENT : workflow "Sonde sources" (declenchement manuel), ou en local :
    python sonde_sources.py
"""

import json
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible : pip install requests")
    sys.exit(0)


TIMEOUT = 45
# User-Agent de navigateur : certains portails renvoient 403 a un client qui
# n'en presente pas (c'etait une des pistes sur ADB).
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}

# Verdicts accumules puis resumes en fin de run.
RESULTATS = []


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("ACCESSIBLE" if ok else "ECHEC", detail))


def _titre(txt):
    print("\n" + "=" * 70)
    print(txt)
    print("=" * 70)


def _apercu(txt, n=400):
    """Extrait lisible d'une reponse, sans polluer le journal."""
    t = " ".join(str(txt or "").split())
    return t[:n] + ("..." if len(t) > n else "")


def _session():
    s = requests.Session()
    s.headers.update(ENTETES)
    return s


# ===========================================================================
# SONDE 1 -- Banque Mondiale : l'API procnotices sert-elle les attributions ?
# ===========================================================================

def sonde_bm_procnotices(s):
    _titre("SONDE 1 -- BM / procnotices : les attributions sont-elles servies ?")
    url = "https://search.worldbank.org/api/v2/procnotices"

    # 1a. L'endpoint repond-il tout court, et quels types d'avis existent ?
    try:
        r = s.get(url, params={"format": "json", "rows": 50}, timeout=TIMEOUT)
        print("1a. GET simple -> HTTP {} ({} octets)".format(r.status_code, len(r.content)))
        if r.status_code >= 400:
            _verdict("BM procnotices", False,
                     "HTTP {} sur un GET simple. {}".format(r.status_code, _apercu(r.text, 200)))
            return
        data = r.json()
        # La reponse encapsule les enregistrements sous une cle variable.
        cle = next((k for k in data if isinstance(data.get(k), list)), None)
        records = data.get(cle) or []
        print("    cle des enregistrements : {!r} ; {} enregistrement(s)".format(cle, len(records)))
        types = sorted({(x.get("notice_type") or "?") for x in records if isinstance(x, dict)})
        print("    types d'avis vus dans l'echantillon : {}".format(types))
        if records and isinstance(records[0], dict):
            print("    champs disponibles : {}".format(sorted(records[0].keys())))
    except Exception as e:
        _verdict("BM procnotices", False, "exception sur GET simple : {}".format(e))
        return

    # 1b. Le filtre "Contract Award" renvoie-t-il des lignes, et avec quels
    #     champs ? C'est LA question : y a-t-il un nom de gagnant ?
    trouve = False
    for libelle in ("Contract Award", "Contract Awards", "Award"):
        try:
            r = s.get(url, params={"format": "json", "rows": 5,
                                   "notice_type_exact": libelle}, timeout=TIMEOUT)
            print("1b. notice_type_exact={!r} -> HTTP {}".format(libelle, r.status_code))
            if r.status_code >= 400:
                continue
            data = r.json()
            cle = next((k for k in data if isinstance(data.get(k), list)), None)
            recs = data.get(cle) or []
            print("    {} enregistrement(s)".format(len(recs)))
            if recs and isinstance(recs[0], dict):
                trouve = True
                champs = sorted(recs[0].keys())
                print("    champs : {}".format(champs))
                # Cherche un champ qui ressemble a un nom de gagnant.
                pistes = [c for c in champs if any(m in c.lower() for m in
                          ("supplier", "contractor", "award", "vendor", "winner", "bidder"))]
                print("    CHAMPS GAGNANT POSSIBLES : {}".format(pistes or "AUCUN"))
                print("    exemple : {}".format(_apercu(json.dumps(recs[0], ensure_ascii=False), 600)))
                _verdict("BM procnotices", True,
                         "type {!r} servi. Champs gagnant : {}".format(libelle, pistes or "aucun"))
                return
        except Exception as e:
            print("    exception : {}".format(e))
    if not trouve:
        _verdict("BM procnotices", False,
                 "aucun type d'attribution servi par cette API -> passer par la sonde 2.")


# ===========================================================================
# SONDE 2 -- Banque Mondiale : jeu de donnees Finances One (champ Supplier)
# ===========================================================================

def sonde_bm_financesone(s):
    _titre("SONDE 2 -- BM / Finances One : jeu de donnees attributions")
    base = "https://datacatalogapi.worldbank.org/dexapps/fone/api"

    # 2a. Metadonnees : le jeu existe-t-il et quelle est sa fraicheur ?
    for asset in ("DS01666", "DS01702"):
        try:
            r = s.get(base + "/metadata", params={"assetId": asset}, timeout=TIMEOUT)
            print("2a. metadata {} -> HTTP {}".format(asset, r.status_code))
            if r.status_code < 400:
                m = r.json()
                print("    titre      : {}".format(m.get("title")))
                print("    lignes     : {} ; colonnes : {}".format(
                    m.get("row_count"), m.get("column_count")))
                print("    donnees au : {} (maj {})".format(
                    m.get("data_as_of"), m.get("data_last_updated")))
        except Exception as e:
            print("    exception metadata {} : {}".format(asset, e))

    # 2b. Donnees : il faut le couple datasetId + resourceId. On essaie les
    #     combinaisons plausibles ; le journal dira laquelle repond.
    couples = [("DS01666", "RS01666"), ("DS01666", "RS01669"),
               ("DS01702", "RS01702"), ("DS00005", "RS00005")]
    for ds, rs in couples:
        try:
            r = s.get(base + "/apiservice",
                      params={"datasetId": ds, "resourceId": rs, "type": "json", "top": 3},
                      timeout=TIMEOUT)
            print("2b. apiservice {}/{} -> HTTP {} ({} octets)".format(
                ds, rs, r.status_code, len(r.content)))
            if r.status_code >= 400:
                continue
            data = r.json()
            lignes = data if isinstance(data, list) else (
                data.get("data") or data.get("value") or [])
            if lignes and isinstance(lignes[0], dict):
                champs = sorted(lignes[0].keys())
                print("    champs : {}".format(champs))
                pistes = [c for c in champs if any(m in c.lower() for m in
                          ("supplier", "contractor", "vendor", "awarded"))]
                print("    CHAMPS GAGNANT POSSIBLES : {}".format(pistes or "AUCUN"))
                print("    exemple : {}".format(_apercu(json.dumps(lignes[0], ensure_ascii=False), 600)))
                _verdict("BM Finances One", True,
                         "{}/{} sert des donnees. Champs gagnant : {}".format(ds, rs, pistes or "aucun"))
                return
            print("    reponse sans lignes exploitables : {}".format(_apercu(r.text, 300)))
        except Exception as e:
            print("    exception {}/{} : {}".format(ds, rs, e))
    _verdict("BM Finances One", False,
             "aucun couple datasetId/resourceId teste n'a servi de donnees.")


# ===========================================================================
# SONDE 3 & 4 -- UNGM : avis et attributions, lisibles par une machine ?
# ===========================================================================

def _ungm_page(s, nom, url_page, chemins_recherche):
    """Teste une page UNGM : d'abord le GET (la liste est-elle DANS le HTML ?),
    puis les endpoints de recherche AJAX candidats (POST)."""
    # a. GET : la page est-elle atteignable, et contient-elle deja des lignes ?
    try:
        r = s.get(url_page, timeout=TIMEOUT)
        html = r.text or ""
        print("a. GET {} -> HTTP {} ({} octets)".format(url_page, r.status_code, len(r.content)))
        if r.status_code >= 400:
            _verdict(nom, False, "HTTP {} sur la page publique.".format(r.status_code))
            return
        # Indice de rendu JS : le gabarit est la, mais pas de lignes de tableau.
        lignes_tableau = html.count("<tr")
        vide = "No procurement opportunity was found" in html or "no results" in html.lower()
        print("    balises <tr> dans le HTML : {} ; message 'aucun resultat' : {}".format(
            lignes_tableau, vide))
        if lignes_tableau > 5 and not vide:
            _verdict(nom, True, "les lignes sont DANS le HTML : scraping direct possible.")
            return
        print("    -> tableau probablement rendu en JavaScript, on teste les endpoints AJAX.")
    except Exception as e:
        _verdict(nom, False, "exception sur GET : {}".format(e))
        return

    # b. POST : endpoints de recherche candidats.
    charge = {"PageIndex": 0, "PageSize": 15, "Title": "", "Description": "",
              "Reference": "", "NoticeTypes": [], "SortField": "DatePublished",
              "SortAscending": False}
    for chemin in chemins_recherche:
        for entetes, corps, libelle in (
            ({"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
             json.dumps(charge), "JSON"),
            ({"Content-Type": "application/x-www-form-urlencoded",
              "X-Requested-With": "XMLHttpRequest"}, charge, "form"),
        ):
            try:
                r = s.post(chemin, data=corps, headers=entetes, timeout=TIMEOUT)
                taille = len(r.content)
                print("b. POST {} [{}] -> HTTP {} ({} octets)".format(
                    chemin, libelle, r.status_code, taille))
                if r.status_code >= 400 or taille < 200:
                    continue
                txt = r.text or ""
                nb_tr = txt.count("<tr")
                print("    balises <tr> : {} ; debut : {}".format(nb_tr, _apercu(txt, 250)))
                if nb_tr > 3 or txt.strip().startswith("{") or txt.strip().startswith("["):
                    _verdict(nom, True,
                             "endpoint exploitable : POST {} [{}]".format(chemin, libelle))
                    return
            except Exception as e:
                print("    exception POST {} [{}] : {}".format(chemin, libelle, e))
    _verdict(nom, False, "aucun endpoint de recherche candidat n'a repondu de facon exploitable.")


def sonde_ungm_avis(s):
    _titre("SONDE 3 -- UNGM / avis (Procurement opportunities)")
    _ungm_page(s, "UNGM avis", "https://www.ungm.org/Public/Notice",
               ["https://www.ungm.org/Public/Notice/Search",
                "https://www.ungm.org/Public/Notice/SearchNotices"])


def sonde_ungm_attributions(s):
    _titre("SONDE 4 -- UNGM / attributions (Contract Awards) : NOMS DE GAGNANTS")
    _ungm_page(s, "UNGM attributions", "https://www.ungm.org/Public/ContractAward",
               ["https://www.ungm.org/Public/ContractAward/Search",
                "https://www.ungm.org/Public/ContractAward/SearchAwards"])


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("SONDE DES SOURCES CANDIDATES -- Radar Amarante")
    print("Aucune ecriture : ni Sheet, ni fichier, ni etat. Diagnostic seul.")
    s = _session()
    for sonde in (sonde_bm_procnotices, sonde_bm_financesone,
                  sonde_ungm_avis, sonde_ungm_attributions):
        try:
            sonde(s)
        except Exception as e:                       # ceinture et bretelles
            _verdict(sonde.__name__, False, "exception non rattrapee : {}".format(e))

    _titre("VERDICT")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {} -- {}".format("OK " if ok else "NON", nom, detail))
    print("\nRappel : ces verdicts valent pour l'infra GitHub Actions, la seule")
    print("qui compte pour le radar. Un resultat different dans ton navigateur")
    print("ne change rien : c'est ce journal qui decide.")
    # Sortie 0 dans tous les cas : une sonde ne casse jamais un workflow.


if __name__ == "__main__":
    main()
