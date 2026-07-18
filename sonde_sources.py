# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v2 (jetable) : questions de CONCEPTION.
================================================================

La sonde v1 a repondu aux questions d'ACCESSIBILITE. Trois questions de
CONCEPTION restent ouvertes, et chacune change la forme du collecteur a
ecrire. Les trancher maintenant evite de coder deux fois.

  A. BM / procnotices : le champ `notice_text` d'une attribution contient-il
     le NOM DU GAGNANT ? Si oui -> chemin le moins cher : on etend le
     collecteur BM existant (fraicheur du jour), pas de nouvelle source.

  B. BM / Finances One (DS00005/RS00005, champ `supplier` confirme) : peut-on
     FILTRER ou TRIER cote serveur ? Le jeu contient 233 000+ lignes depuis
     2001 ; or seules les attributions RECENTES ont une valeur commerciale
     (une entreprise qui vient de gagner est en mobilisation). Sans filtre
     serveur, il faudrait paginer tout le jeu a chaque run.

  C. UNGM / attributions : l'endpoint existe (il repond 500, pas 404) mais
     refuse notre charge utile. On teste plusieurs formes pour trouver la
     bonne. NB : la sonde v1 a conclu a tort a l'echec pour les AVIS UNGM ;
     l'endpoint marche (voir son journal), c'est la detection qui etait
     fausse (UNGM rend des <div role="row">, pas des <tr>).

Aucune ecriture. Sortie toujours en code 0. Supprimable apres usage.
LANCEMENT : workflow "Sonde sources" (manuel), apres remplacement du fichier.
"""

import json
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible : pip install requests")
    sys.exit(0)


TIMEOUT = 60
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}
RESULTATS = []


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OUI" if ok else "NON", detail))


def _titre(txt):
    print("\n" + "=" * 70)
    print(txt)
    print("=" * 70)


def _apercu(txt, n=400):
    t = " ".join(str(txt or "").split())
    return t[:n] + ("..." if len(t) > n else "")


def _lignes(data):
    """Extrait la liste d'enregistrements quelle que soit l'enveloppe."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for cle in ("data", "value", "items", "records", "result"):
            if isinstance(data.get(cle), list):
                return data[cle]
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


# ===========================================================================
# A -- Le notice_text d'une attribution BM nomme-t-il le gagnant ?
# ===========================================================================

def sonde_a_notice_text(s):
    _titre("A -- BM / procnotices : le notice_text d'une attribution nomme-t-il le gagnant ?")
    url = "https://search.worldbank.org/api/v2/procnotices"
    try:
        r = s.get(url, params={"format": "json", "rows": 8,
                               "notice_type_exact": "Contract Award"}, timeout=TIMEOUT)
        print("HTTP {}".format(r.status_code))
        if r.status_code >= 400:
            _verdict("A notice_text", False, "HTTP {}".format(r.status_code))
            return
        recs = _lignes(r.json())
        print("{} attribution(s) recuperee(s).\n".format(len(recs)))
        avec_texte = 0
        for i, rec in enumerate(recs[:4], 1):
            txt = rec.get("notice_text") or ""
            print("--- Attribution {} | {} | groupe {} ---".format(
                i, rec.get("project_ctry_name"), rec.get("procurement_group")))
            print("    description : {}".format(_apercu(rec.get("bid_description"), 150)))
            print("    notice_text ({} caracteres) :".format(len(txt)))
            print("    {}".format(_apercu(txt, 900) if txt else "(VIDE)"))
            print("")
            if txt:
                avec_texte += 1
        _verdict("A notice_text", avec_texte > 0,
                 "{}/{} attributions ont un notice_text non vide. "
                 "Lire ci-dessus : contient-il un nom d'entreprise ?".format(
                     avec_texte, len(recs[:4])))
    except Exception as e:
        _verdict("A notice_text", False, "exception : {}".format(e))


# ===========================================================================
# B -- Finances One : filtrage / tri / pagination cote serveur ?
# ===========================================================================

def sonde_b_finances_one(s):
    _titre("B -- BM / Finances One : peut-on filtrer ou trier cote serveur ?")
    url = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice"
    base = {"datasetId": "DS00005", "resourceId": "RS00005", "type": "json"}

    # B1. Taille de page maximale.
    taille_ok = 0
    for top in (100, 1000, 5000):
        try:
            p = dict(base); p["top"] = top
            r = s.get(url, params=p, timeout=TIMEOUT)
            n = len(_lignes(r.json())) if r.status_code < 400 else 0
            print("B1. top={:<5} -> HTTP {} ; {} ligne(s)".format(top, r.status_code, n))
            if n:
                taille_ok = max(taille_ok, n)
        except Exception as e:
            print("B1. top={} exception : {}".format(top, e))
    print("    -> taille de page utile constatee : {}".format(taille_ok))

    # B2. Pagination (skip) : la 2e page differe-t-elle de la 1re ?
    try:
        p1 = dict(base); p1["top"] = 3
        p2 = dict(base); p2["top"] = 3; p2["skip"] = 3
        r1 = s.get(url, params=p1, timeout=TIMEOUT)
        r2 = s.get(url, params=p2, timeout=TIMEOUT)
        l1, l2 = _lignes(r1.json()), _lignes(r2.json())
        id1 = [x.get("wb_contract_number") for x in l1]
        id2 = [x.get("wb_contract_number") for x in l2]
        print("B2. page1={} | page2={}".format(id1, id2))
        print("    -> skip fonctionne : {}".format(bool(id1 and id2 and id1 != id2)))
    except Exception as e:
        print("B2. exception : {}".format(e))

    # B3. Tri : peut-on obtenir les contrats les plus RECENTS en premier ?
    #     Question decisive : sinon il faut paginer tout le jeu a chaque run.
    essais_tri = [
        {"sort": "contract_signing_date desc"},
        {"orderby": "contract_signing_date desc"},
        {"$orderby": "contract_signing_date desc"},
        {"sortBy": "contract_signing_date", "sortOrder": "desc"},
        {"order": "contract_signing_date:desc"},
    ]
    tri_trouve = ""
    for extra in essais_tri:
        try:
            p = dict(base); p["top"] = 3; p.update(extra)
            r = s.get(url, params=p, timeout=TIMEOUT)
            lg = _lignes(r.json()) if r.status_code < 400 else []
            dates = [x.get("contract_signing_date") for x in lg]
            print("B3. {} -> HTTP {} ; dates {}".format(extra, r.status_code, dates))
            recents = [d for d in dates if d and ("2026" in str(d) or "2025" in str(d))]
            if lg and recents:
                tri_trouve = str(extra)
                print("    *** TRI RECENT OBTENU avec {} ***".format(extra))
                break
        except Exception as e:
            print("B3. {} exception : {}".format(extra, e))

    # B4. Filtrage par pays : evite de rapatrier le monde entier.
    filtre_trouve = ""
    essais_filtre = [
        {"filter": "borrower_country eq 'Mali'"},
        {"$filter": "borrower_country eq 'Mali'"},
        {"borrower_country": "Mali"},
        {"filter": "borrower_country:Mali"},
        {"where": "borrower_country='Mali'"},
    ]
    for extra in essais_filtre:
        try:
            p = dict(base); p["top"] = 3; p.update(extra)
            r = s.get(url, params=p, timeout=TIMEOUT)
            lg = _lignes(r.json()) if r.status_code < 400 else []
            pays = [x.get("borrower_country") for x in lg]
            print("B4. {} -> HTTP {} ; pays {}".format(extra, r.status_code, pays))
            if lg and pays and all(p2 == "Mali" for p2 in pays if p2):
                filtre_trouve = str(extra)
                print("    *** FILTRE PAYS OBTENU avec {} ***".format(extra))
                break
        except Exception as e:
            print("B4. {} exception : {}".format(extra, e))

    # B5. L'enveloppe renvoie-t-elle un compte total ?
    try:
        p = dict(base); p["top"] = 1
        r = s.get(url, params=p, timeout=TIMEOUT)
        d = r.json()
        if isinstance(d, dict):
            meta = {k: v for k, v in d.items() if not isinstance(v, (list, dict))}
            print("B5. cles hors donnees (compte total ?) : {}".format(meta))
        else:
            print("B5. reponse = liste nue, pas d'enveloppe.")
    except Exception as e:
        print("B5. exception : {}".format(e))

    _verdict("B Finances One", bool(tri_trouve or filtre_trouve),
             "tri={} ; filtre={} ; page utile={}".format(
                 tri_trouve or "AUCUN", filtre_trouve or "AUCUN", taille_ok))


# ===========================================================================
# C -- UNGM attributions : trouver la bonne charge utile
# ===========================================================================

def sonde_c_ungm_awards(s):
    _titre("C -- UNGM / attributions : quelle charge utile accepte l'endpoint ?")
    url = "https://www.ungm.org/Public/ContractAward/Search"
    entetes = {"X-Requested-With": "XMLHttpRequest",
               "Referer": "https://www.ungm.org/Public/ContractAward"}

    charges = [
        ("minimal", {"PageIndex": 0, "PageSize": 15}),
        ("awards1", {"PageIndex": 0, "PageSize": 15, "Description": "", "Supplier": "",
                     "Reference": "", "AwardDateFrom": "", "AwardDateTo": "",
                     "Agencies": [], "Countries": [], "UNSPSCs": [],
                     "SortField": "AwardDate", "SortAscending": False}),
        ("awards2", {"PageIndex": 0, "PageSize": 15, "Title": "", "Description": "",
                     "Reference": "", "NoticeTypes": [], "SortField": "",
                     "SortAscending": False}),
        ("vide", {}),
    ]
    for nom, charge in charges:
        for ctype, corps, lib in (
            ("application/json", json.dumps(charge), "JSON"),
            ("application/x-www-form-urlencoded", charge, "form"),
        ):
            try:
                h = dict(entetes); h["Content-Type"] = ctype
                r = s.post(url, data=corps, headers=h, timeout=TIMEOUT)
                txt = r.text or ""
                # UNGM rend ses lignes en <div role="row">, pas en <tr>.
                nb = txt.count('role="row"') + txt.count("dataRow")
                print("C. {:<8} [{:<4}] -> HTTP {} ({} octets) ; marqueurs de ligne : {}".format(
                    nom, lib, r.status_code, len(r.content), nb))
                if r.status_code < 400 and nb > 2:
                    print("   extrait : {}".format(_apercu(txt, 600)))
                    _verdict("C UNGM attributions", True,
                             "charge {!r} en {} acceptee ({} marqueurs).".format(nom, lib, nb))
                    return
            except Exception as e:
                print("C. {} [{}] exception : {}".format(nom, lib, e))
    _verdict("C UNGM attributions", False,
             "aucune charge testee n'a produit de lignes. A creuser via F12 > Reseau.")


def main():
    print("SONDE v2 -- questions de conception (aucune ecriture)")
    s = requests.Session()
    s.headers.update(ENTETES)
    for f in (sonde_a_notice_text, sonde_b_finances_one, sonde_c_ungm_awards):
        try:
            f(s)
        except Exception as e:
            _verdict(f.__name__, False, "exception non rattrapee : {}".format(e))

    _titre("VERDICT v2")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {} -- {}".format("OUI" if ok else "NON", nom, detail))


if __name__ == "__main__":
    main()
