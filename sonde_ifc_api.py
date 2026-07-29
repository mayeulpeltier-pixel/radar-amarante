# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC API (jetable) : la source globale trouvee.
=========================================================================

Le crack du bundle JS a livre l'endpoint de recherche des divulgations IFC :

    https://webapi.worldbank.org/aemsite/ifc-disclosure-search

C'est la source GLOBALE, recente, tous types (ESRS/SPI), que le portail appelle.
Cette sonde la valide DEPUIS LE CI : parametres acceptes, forme du JSON, champs,
distribution pays (global ?), tri par date, pagination, filtre pays.

Aucune ecriture, aucun LLM. Sortie code 0. Jetable.
"""

import json
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible"); sys.exit(0)

E = "https://webapi.worldbank.org/aemsite/ifc-disclosure-search"
TIMEOUT = 45
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://disclosures.ifc.org/",
    "Origin": "https://disclosures.ifc.org",
}

CANDIDATS = [
    E + "?sortBy=Disclosed_Date&sortOrder=desc&page=1&size=10",
    E + "?sortBy=Disclosed_Date&sortOrder=desc&rows=10",
    E + "?sortBy=Disclosed_Date&sortOrder=desc",
    E + "?Type_Description=Investment&sortBy=Disclosed_Date&sortOrder=desc&page=1&size=10",
    E,
]


def _trouver_records(obj, prof=0):
    """Cherche recursivement la 1re liste de dicts (les records) et sa cle."""
    if prof > 4:
        return None, None
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj, "(racine liste)"
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v, k
        for k, v in obj.items():
            r, kk = _trouver_records(v, prof + 1)
            if r:
                return r, "{}.{}".format(k, kk)
    return None, None


def _distribution(records, champ, n=15):
    d = {}
    for r in records:
        v = str(r.get(champ, "")).strip() or "(vide)"
        d[v] = d.get(v, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n])


def _analyser(charge):
    records, cle = _trouver_records(charge)
    if not records:
        print("  (pas de liste de records reconnue ; cles racine :",
              list(charge.keys()) if isinstance(charge, dict) else type(charge), ")")
        return
    print("  Liste de records sous cle :", cle, "| n =", len(records))
    r0 = records[0]
    champs = sorted(r0.keys())
    print("  Champs d'un record :", champs)
    print("\n  1er record (brut) :")
    print(json.dumps(r0, ensure_ascii=False, indent=1)[:1000])
    # champs pays / date / type / entreprise (noms probables, on tente plusieurs)
    for candidats_champ in (["country", "Country", "country_name"],
                            ["date_disclosed", "Disclosed_Date", "disclosedDate"],
                            ["document_type", "Document_Type", "type"],
                            ["company_name", "Company_Name", "companyName"],
                            ["environmental_category", "Environmental_Category"]):
        champ = next((c for c in candidats_champ if c in r0), None)
        if champ:
            print("\n  '{}' -> {}".format(champ, _distribution(records, champ, 12)))


def main():
    print("SONDE IFC API -- endpoint global. Aucune ecriture, aucun LLM.")
    s = requests.Session(); s.headers.update(ENTETES)
    for url in CANDIDATS:
        print("\n" + "=" * 72)
        print("GET", url)
        print("=" * 72)
        try:
            r = s.get(url, timeout=TIMEOUT)
        except Exception as e:
            print("  injoignable :", e); continue
        ct = r.headers.get("Content-Type", "")
        print("  HTTP {} | {} | {} octets".format(r.status_code, ct, len(r.text)))
        if r.status_code != 200:
            print("  debut :", r.text[:200]); continue
        try:
            charge = r.json()
        except Exception:
            print("  non-JSON. debut :", r.text[:200]); continue
        _analyser(charge)
        # Si on a trouve des records, on teste un filtre pays et on s'arrete au dump detaille.
        records, _ = _trouver_records(charge)
        if records:
            print("\n  --- test filtre pays (Country=Nigeria) ---")
            try:
                rr = s.get(E + "?sortBy=Disclosed_Date&sortOrder=desc&Country=Nigeria&page=1&size=5",
                           timeout=TIMEOUT)
                rec2, _ = _trouver_records(rr.json())
                print("  filtre pays -> {} records, pays :".format(len(rec2 or [])),
                      _distribution(rec2 or [], next((c for c in ("country","Country") if rec2 and c in rec2[0]), "country"), 5))
            except Exception as e:
                print("  (filtre pays non concluant : {})".format(e))
            break

    print("\n" + "=" * 72)
    print("SYNTHESE : si un GET rend du JSON avec company/country/date/type sur")
    print("plusieurs pays, c'est la source globale du collecteur IFC. Colle-moi")
    print("les champs et le 1er record.")
    print("Sonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
