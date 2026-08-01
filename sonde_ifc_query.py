# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC QUERY (jetable) : params en query, corps filtres.
=========================================================================

Acquis : POST sur la base IFC, mais 'page' n'est PAS valide dans le CORPS
("not a valid parameter for the operation 'search'"). Donc pagination/tri/search
vont en QUERY STRING, le corps ne porte que les filtres (facettes). On teste ces
placements avec des corps vides/candidats.

Aucune ecriture, aucun LLM. Sortie code 0. Jetable.
"""

import json
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible"); sys.exit(0)

URL = "https://webapi.worldbank.org/aemsite/ifc-disclosure-search"
TIMEOUT = 45
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://disclosures.ifc.org/",
    "Origin": "https://disclosures.ifc.org",
}

Q1 = "?search=&page=1&sortBy=Disclosed_Date&sortOrder=desc"
Q2 = "?page=1&sortBy=Disclosed_Date&sortOrder=desc"
Q3 = "?search=&pageNumber=1&pageSize=10&sortBy=Disclosed_Date&sortOrder=desc"

ESSAIS = [
    ("query complet, corps {}", "POST", URL + Q1, {}),
    ("query complet, sans corps", "POST", URL + Q1, "__none__"),
    ("query complet, corps {filters:[]}", "POST", URL + Q1, {"filters": []}),
    ("query complet, corps {selectedFilters:{}}", "POST", URL + Q1, {"selectedFilters": {}}),
    ("query sans search, corps {search:''}", "POST", URL + Q2, {"search": ""}),
    ("query pageNumber/pageSize, corps {}", "POST", URL + Q3, {}),
    ("GET query complet", "GET", URL + Q1, "__none__"),
]


def _trouver_records(obj, prof=0):
    if prof > 4:
        return None, None
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj, "(racine)"
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v, k
        for k, v in obj.items():
            r, kk = _trouver_records(v, prof + 1)
            if r:
                return r, "{}.{}".format(k, kk)
    return None, None


def main():
    print("SONDE IFC QUERY -- params en query. Aucune ecriture.")
    s = requests.Session(); s.headers.update(ENTETES)
    gagnant = None
    for libelle, methode, url, corps in ESSAIS:
        print("\n" + "=" * 72)
        print("[{}] {} {}".format(libelle, methode, url))
        print("=" * 72)
        try:
            if corps == "__none__":
                r = s.request(methode, url, timeout=TIMEOUT)
            else:
                r = s.request(methode, url, json=corps, timeout=TIMEOUT)
        except Exception as e:
            print("  echec :", e); continue
        print("  HTTP {} | {} octets".format(r.status_code, len(r.text)))
        print("  reponse (500) :", r.text[:500].replace("\n", " "))
        if r.status_code == 200:
            try:
                rec, cle = _trouver_records(r.json())
                if rec:
                    print("  <<< JSON RECORDS : {} sous '{}'".format(len(rec), cle))
                    gagnant = (libelle, methode, url, corps, r.json(), rec, cle)
                    break
            except Exception:
                pass

    print("\n" + "=" * 72)
    if gagnant:
        libelle, methode, url, corps, charge, rec, cle = gagnant
        print("GAGNANT : {} {}  | corps={}".format(methode, url, corps))
        print("Records sous '{}' : {}".format(cle, len(rec)))
        if isinstance(charge, dict):
            for k in charge:
                if not isinstance(charge[k], (list, dict)):
                    print("  meta {} = {}".format(k, charge[k]))
        r0 = rec[0]
        print("\nChamps :", sorted(r0.keys()))
        print("\n1er record (brut) :")
        print(json.dumps(r0, ensure_ascii=False, indent=1)[:1200])
        cp = next((c for c in ("Country_Description", "Country", "country") if c in r0), None)
        if cp:
            d = {}
            for x in rec:
                v = str(x.get(cp, "")).strip() or "(vide)"
                d[v] = d.get(v, 0) + 1
            print("\nPays :", dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:12]))
    else:
        print("Toujours pas de records. Lis les messages : ils guident le placement")
        print("exact des params. On ajuste encore d'un cran.")
    print("\nSonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
