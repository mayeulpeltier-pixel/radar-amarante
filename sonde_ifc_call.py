# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC CALL (jetable) : trouver la route exacte.
=========================================================================

Acquis : endpoint https://webapi.worldbank.org/aemsite/ifc-disclosure-search
(404 en GET nu). Le JS donne les params (search, page, sortBy=Disclosed_Date,
sortOrder=desc, startDate, endDate ; pageSize=10) mais pas le sous-chemin ni la
methode. On les trouve EMPIRIQUEMENT : GET et POST, plusieurs sous-chemins, avec
les params connus. On s'arrete des qu'un appel rend du JSON de projets.

Aucune ecriture, aucun LLM. Sortie code 0. Jetable.
"""

import json
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible"); sys.exit(0)

BASE = "https://webapi.worldbank.org/aemsite/ifc-disclosure-search"
TIMEOUT = 45
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://disclosures.ifc.org/",
    "Origin": "https://disclosures.ifc.org",
}
SOUS_CHEMINS = ["", "/", "/projects", "/search", "/list", "/getProjects",
                "/GetProjects", "/disclosures", "/results", "/data", "/getDisclosures"]
PARAMS = {"search": "", "page": 1, "pageSize": 10,
          "sortBy": "Disclosed_Date", "sortOrder": "desc"}
CORPS = dict(PARAMS)


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


def _essayer(s, methode, url, **kw):
    try:
        r = s.request(methode, url, timeout=TIMEOUT, **kw)
    except Exception as e:
        return None, "  [{}] {} -> echec ({})".format(methode, url[:80], e)
    tag = "  [{} {}] {}".format(methode, r.status_code, url[:80])
    if r.status_code == 200:
        try:
            charge = r.json()
            rec, cle = _trouver_records(charge)
            if rec:
                return (charge, rec, cle), tag + "  <<< JSON RECORDS ({} sous '{}')".format(len(rec), cle)
            return None, tag + "  (JSON mais pas de records ; cles: {})".format(
                list(charge.keys())[:8] if isinstance(charge, dict) else type(charge))
        except Exception:
            return None, tag + "  (200 non-JSON)"
    return None, tag + "  {}".format(r.text[:80].replace(chr(10), " "))


def main():
    print("SONDE IFC CALL -- route exacte (GET/POST x sous-chemins). Aucune ecriture.")
    s = requests.Session(); s.headers.update(ENTETES)
    trouve = None

    print("\n--- GET (params en query) ---")
    for sc in SOUS_CHEMINS:
        res, ligne = _essayer(s, "GET", BASE + sc, params=PARAMS)
        print(ligne)
        if res:
            trouve = ("GET", BASE + sc, res); break

    if not trouve:
        print("\n--- POST (corps JSON) ---")
        for sc in SOUS_CHEMINS:
            res, ligne = _essayer(s, "POST", BASE + sc,
                                  json=CORPS, headers={"Content-Type": "application/json"})
            print(ligne)
            if res:
                trouve = ("POST", BASE + sc, res); break

    print("\n" + "=" * 72)
    if trouve:
        methode, url, (charge, rec, cle) = trouve
        print("ROUTE TROUVEE : {} {}".format(methode, url))
        print("Records sous '{}' : {}".format(cle, len(rec)))
        r0 = rec[0]
        print("Champs :", sorted(r0.keys()))
        print("\n1er record (brut) :")
        print(json.dumps(r0, ensure_ascii=False, indent=1)[:1100])
        # distribution pays (global ?)
        champ_pays = next((c for c in ("Country_Description", "country", "Country") if c in r0), None)
        if champ_pays:
            d = {}
            for x in rec:
                v = str(x.get(champ_pays, "")).strip() or "(vide)"
                d[v] = d.get(v, 0) + 1
            print("\nPays ({}) :".format(champ_pays), dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:12]))
    else:
        print("Aucune route concluante. Il faudra relire le JS (chunk lazy) ou")
        print("capturer l'appel reseau. Repli : option A (tir par pays).")
    print("\nSonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
