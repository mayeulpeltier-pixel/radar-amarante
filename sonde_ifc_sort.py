# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC SORT (jetable, DERNIERE) : tri/filtre/pagination.
=========================================================================

Acces craque : POST base + query + corps {} -> 50 records globaux sous 'value'.
Backend = Azure Cognitive Search. Reste LE dernier point : obtenir le RECENT
d'abord (le tri sortBy/sortOrder est ignore par Azure). On teste la syntaxe
OData d'Azure ($orderby, $filter par date) et la pagination (skip), pour fixer
la strategie de collecte. Puis on ecrit le collecteur.

On imprime, pour chaque essai, les Disclosed_Date des 1ers records + le total.
Aucune ecriture, aucun LLM. Sortie code 0. Jetable.
"""

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

ESSAIS = [
    ("$orderby desc", "?search=&$orderby=Disclosed_Date desc"),
    ("sortBy/sortOrder (temoin, cense ne pas trier)", "?search=&sortBy=Disclosed_Date&sortOrder=desc"),
    ("$orderby + $filter >= 2026", "?search=&$orderby=Disclosed_Date desc&$filter=Disclosed_Date ge 2026-01-01T00:00:00Z"),
    ("$orderby desc + $skip=50 (page 2)", "?search=&$orderby=Disclosed_Date desc&$skip=50"),
    ("$orderby desc + &skip=50 (proxy)", "?search=&$orderby=Disclosed_Date desc&skip=50"),
    ("$orderby desc + &page=2 (proxy)", "?search=&$orderby=Disclosed_Date desc&page=2"),
]


def _dates(charge, n=4):
    recs = charge.get("value") if isinstance(charge, dict) else None
    if not recs:
        return "(pas de records)", None
    apercu = ["{} | {} | {}".format(
        str(r.get("Disclosed_Date", ""))[:10], r.get("Project_Number", ""),
        str(r.get("Country_Description", ""))[:18]) for r in recs[:n]]
    return apercu, recs


def main():
    print("SONDE IFC SORT -- tri/filtre/pagination. Aucune ecriture.")
    s = requests.Session(); s.headers.update(ENTETES)
    for libelle, q in ESSAIS:
        print("\n" + "=" * 72)
        print("[{}]".format(libelle))
        print("  " + URL + q)
        print("=" * 72)
        try:
            r = s.post(URL + q, json={}, timeout=TIMEOUT)
        except Exception as e:
            print("  echec :", e); continue
        print("  HTTP {} | {} octets".format(r.status_code, len(r.text)))
        if r.status_code != 200:
            print("  reponse :", r.text[:250].replace("\n", " ")); continue
        try:
            charge = r.json()
        except Exception:
            print("  non-JSON"); continue
        total = charge.get("@odata.count") or charge.get("@search.count") or "?"
        nextp = charge.get("@search.nextPageParameters")
        apercu, recs = _dates(charge)
        print("  total annonce :", total, "| nextPage :", nextp,
              "| records :", len(recs) if recs else 0)
        print("  1ers records (date | numero | pays) :")
        for a in (apercu if isinstance(apercu, list) else [apercu]):
            print("     ", a)

    print("\n" + "=" * 72)
    print("LECTURE : l'essai qui liste des dates 2026 en tete = tri OK. Celui avec")
    print("$filter = fenetre par date. La pagination qui CHANGE les records entre")
    print("page 1 et 'page 2' = le bon parametre de saut. Avec ca, je fixe la")
    print("strategie et j'ecris le collecteur IFC.")
    print("Sonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
