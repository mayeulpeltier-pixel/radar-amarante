# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE ENRICHISSEMENT TITULAIRE (jetable) : quels champs
`winner-*` sont exploitables pour qualifier un lead ?
===============================================================================

CONTEXTE
--------
Le catalogue de l'API search (sonde v2, 1830 champs) expose 27 champs winner-*.
On mesure ici le REMPLISSAGE REEL et la FORME de ceux qui qualifient un lead
Amarante, avant tout cablage :
  - winner-size              taille (PME / grand groupe) -> calibre du prospect
  - winner-owner-nationality origine reelle -> affine le signal "etranger"
  - winner-city              ville (observation)
  - winner-country           pays cote API (peut-il rendre le SPARQL optionnel ?)

METHODE (corrige du 429 : UNE requete groupee)
----------------------------------------------
Tous les champs demandes ensemble dans `fields` (le catalogue garantit qu'ils
sont valides). Zero rafale. On dumpe le taux de remplissage + des exemples
bruts, en filtrant les valeurs vides et la sentinelle -1.

USAGE
-----
    python sonde_enrich_titulaire.py                       # echantillon auto
    SONDE_PN="10759-2026" python sonde_enrich_titulaire.py  # cibler
    SONDE_ENRICH_DRYRUN=1 python sonde_enrich_titulaire.py  # affiche la requete

AUCUNE ECRITURE. Aucun secret. Aucun LLM. Sortie toujours en code 0.
"""

import json
import os
import sys

try:
    import requests
except ImportError:
    print("requests indisponible : pip install requests")
    sys.exit(0)


ENDPOINTS = [
    "https://api.ted.europa.eu/v3/notices/search",
    "https://tedweb.api.ted.europa.eu/v3/notices/search",
]
TIMEOUT = 45
PN = os.environ.get("SONDE_PN", "").strip()
DRYRUN = os.environ.get("SONDE_ENRICH_DRYRUN") == "1"

CHAMPS_BASE = ["publication-number", "notice-title", "winner-name"]
CHAMPS_ENRICH = [
    "winner-size",
    "winner-owner-nationality",
    "winner-city",
    "winner-country",
]
# can-tport retire (invalide, cf chantier attributions).
NOTICE_TYPES_ATTRIB = ["can-standard", "can-social"]


def poster(corps):
    if DRYRUN:
        print("      [DRYRUN] corps =", json.dumps(corps, ensure_ascii=False)[:400])
        return None
    rep = None
    for i, url in enumerate(ENDPOINTS):
        dernier = (i == len(ENDPOINTS) - 1)
        try:
            rep = requests.post(url, json=corps, timeout=TIMEOUT)
        except requests.exceptions.RequestException as e:
            if dernier:
                print("      (reseau) endpoints injoignables :", type(e).__name__)
                return None
            continue
        if rep.status_code >= 500 and not dernier:
            continue
        return rep
    return rep


def _notices(data):
    if not isinstance(data, dict):
        return []
    return data.get("notices") or data.get("results") or data.get("items") or []


def _query():
    if PN:
        return "publication-number IN ({})".format(PN)
    return "notice-type IN ({}) SORT BY publication-date DESC".format(
        " ".join(NOTICE_TYPES_ATTRIB))


def _non_vide(v):
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip()) and v.strip() != "-1"
    if isinstance(v, (list, dict)):
        vals = v.values() if isinstance(v, dict) else v
        return any(_non_vide(x) for x in vals)
    if isinstance(v, (int, float)):
        return v != -1
    return True


def main():
    print("SONDE ENRICHISSEMENT TITULAIRE -- winner-* exploitables ?")
    print("Cible :", PN or "attributions recentes (auto)", "| Dry-run :", DRYRUN)
    print()
    corps = {
        "query": _query(),
        "fields": CHAMPS_BASE + CHAMPS_ENRICH,
        "page": 1,
        "limit": 15,
        "scope": "ALL",
        "checkQuerySyntax": False,
        "paginationMode": "PAGE_NUMBER",
    }
    rep = poster(corps)
    if rep is None:
        print("Pas de reponse (dry-run/reseau).")
        sys.exit(0)
    print("HTTP", rep.status_code)
    if rep.status_code != 200:
        print("Corps :", rep.text[:400])
        sys.exit(0)

    notices = _notices(rep.json())
    print("Attributions dans l'echantillon :", len(notices))
    print()
    compte = {c: 0 for c in CHAMPS_ENRICH}
    for n in notices:
        for c in CHAMPS_ENRICH:
            if _non_vide(n.get(c)):
                compte[c] += 1

    print("=" * 68)
    print("TAUX DE REMPLISSAGE (sur {} notices)".format(len(notices)))
    print("=" * 68)
    for c in CHAMPS_ENRICH:
        print("  {:<26} {:>2}/{:<2} {}".format(
            c, compte[c], len(notices), "#" * compte[c]))

    print()
    print("=" * 68)
    print("EXEMPLES BRUTS (3 premieres notices avec un champ renseigne)")
    print("=" * 68)
    montres = 0
    for n in notices:
        renseignes = {c: n.get(c) for c in CHAMPS_ENRICH if _non_vide(n.get(c))}
        if not renseignes:
            continue
        titre = n.get("notice-title")
        titre = titre if isinstance(titre, str) else json.dumps(titre, ensure_ascii=False)
        print("\n  {} | {}".format(n.get("publication-number"), titre[:50]))
        for c, v in renseignes.items():
            print("      {:<26} = {}".format(c, json.dumps(v, ensure_ascii=False)[:80]))
        montres += 1
        if montres >= 3:
            break
    if montres == 0:
        print("\n  Aucun champ enrichissement renseigne sur l'echantillon.")

    print()
    print("=" * 68)
    print("LECTURE")
    print("=" * 68)
    print("  winner-size fort -> calibre du prospect (a injecter au prompt LLM).")
    print("  winner-owner-nationality -> affine 'etranger' (souvent rare).")
    print("  winner-country bien rempli -> pourrait rendre le SPARQL pays")
    print("  optionnel. Attention aux listes non appariees (agreger au niveau")
    print("  notice, comme winner-name) et aux sentinelles -1.")
    sys.exit(0)


if __name__ == "__main__":
    main()
