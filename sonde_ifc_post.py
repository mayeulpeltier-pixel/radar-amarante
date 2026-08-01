# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC POST (jetable) : le bon corps de requete.
=========================================================================

Acquis decisif : POST sur https://webapi.worldbank.org/aemsite/ifc-disclosure-search
(SANS sous-chemin) renvoie 400 "parameter 'p...' invalid" -> bonne route,
mauvais corps. Le JS liste les params valides : search, page, sortBy, sortOrder,
startDate, endDate, isAIContentIncluded (PAS de pageSize).

On envoie plusieurs corps candidats et on AFFICHE LA REPONSE COMPLETE, pour lire
le message d'erreur exact puis obtenir les records.

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

CORPS = [
    ("ancien (avec pageSize) -> lire l'erreur complete",
     {"search": "", "page": 1, "pageSize": 10, "sortBy": "Disclosed_Date", "sortOrder": "desc"}),
    ("params valides du JS",
     {"search": "", "page": 1, "sortBy": "Disclosed_Date", "sortOrder": "desc",
      "startDate": "", "endDate": "", "isAIContentIncluded": False}),
    ("minimal",
     {"page": 1, "sortBy": "Disclosed_Date", "sortOrder": "desc"}),
    ("search + page seuls",
     {"search": "", "page": 1}),
    ("page en chaine",
     {"search": "", "page": "1", "sortBy": "Disclosed_Date", "sortOrder": "desc"}),
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
    print("SONDE IFC POST -- bon corps de requete. Aucune ecriture.")
    s = requests.Session(); s.headers.update(ENTETES)
    gagnant = None
    for libelle, corps in CORPS:
        print("\n" + "=" * 72)
        print("CORPS [{}] : {}".format(libelle, json.dumps(corps)))
        print("=" * 72)
        try:
            r = s.post(URL, json=corps, timeout=TIMEOUT)
        except Exception as e:
            print("  echec :", e); continue
        print("  HTTP {} | {} octets".format(r.status_code, len(r.text)))
        print("  reponse (600) :", r.text[:600].replace("\n", " "))
        if r.status_code == 200:
            try:
                rec, cle = _trouver_records(r.json())
                if rec:
                    print("  <<< JSON RECORDS : {} sous '{}'".format(len(rec), cle))
                    gagnant = (corps, r.json(), rec, cle)
                    break
            except Exception:
                pass

    print("\n" + "=" * 72)
    if gagnant:
        corps, charge, rec, cle = gagnant
        print("CORPS GAGNANT :", json.dumps(corps))
        print("Records sous '{}' : {}".format(cle, len(rec)))
        # metadonnees de pagination (total)
        if isinstance(charge, dict):
            for k in charge:
                if not isinstance(charge[k], (list, dict)):
                    print("  meta {} = {}".format(k, charge[k]))
        r0 = rec[0]
        print("\nChamps d'un record :", sorted(r0.keys()))
        print("\n1er record (brut) :")
        print(json.dumps(r0, ensure_ascii=False, indent=1)[:1200])
        champ_pays = next((c for c in ("Country_Description", "Country", "country") if c in r0), None)
        if champ_pays:
            d = {}
            for x in rec:
                v = str(x.get(champ_pays, "")).strip() or "(vide)"
                d[v] = d.get(v, 0) + 1
            print("\nPays :", dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:12]))
    else:
        print("Pas encore de records. Lis le message d'erreur complet ci-dessus")
        print("(il nomme le parametre attendu) et on ajuste le corps.")
    print("\nSonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
