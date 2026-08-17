# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE MONTANT v3 (jetable) : l'API search donne-t-elle un
montant exploitable au niveau notice d'attribution ?
===============================================================================

CONTEXTE
--------
Sonde v2 : l'API expose `winner-name` + `winner-selection-status`, mais le test
des champs montant a fauté (52 POST sequentiels -> 429 Too Many Requests des la
9e requete). On refait proprement.

CORRECTIF vs v2
---------------
UNE SEULE requete : tous les champs montant candidats sont demandes ensemble
dans `fields` (le catalogue de l'etape 1 garantit qu'ils sont valides, donc
aucun 400 a isoler). Zero rafale, zero rate limit. On dumpe ensuite, pour
chaque notice de l'echantillon, les champs montant RENSEIGNES (non nuls).

BUT
---
Savoir si un champ type `total-value` / `result-value-notice` / `BT-720-Tender`
porte un montant au niveau notice. Si oui -> le scoring peut l'utiliser et
SPARQL devient optionnel meme pour le montant global. Si seuls des null
reviennent (montant differe, cf attribut privacy eForms) -> garder SPARQL.

USAGE
-----
    python sonde_montant_attributions.py                       # echantillon auto
    SONDE_PUB="10759-2026" python sonde_montant_attributions.py # cibler
    SONDE_WINNER_DRYRUN=1 python sonde_montant_attributions.py  # affiche requete

AUCUNE ECRITURE. Aucun secret. Aucun LLM. Sortie toujours en code 0.
"""

import json
import os
import re
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
PUB = os.environ.get("SONDE_PUB", "").strip()
DRYRUN = os.environ.get("SONDE_WINNER_DRYRUN") == "1"

CHAMPS_BASE = ["publication-number", "notice-title", "notice-type"]

# Champs montant candidats (valides via le catalogue de la sonde v2). Groupes
# du plus agrege (notice) au plus fin (lot / offre du titulaire).
CHAMPS_MONTANT = [
    "total-value", "total-value-cur",
    "result-value-notice", "result-value-cur-notice",
    "result-value-lot", "result-value-cur-lot",
    "tender-value", "tender-value-cur",
    "BT-720-Tender", "BT-720-Tender-Currency",
    "BT-711-LotResult", "BT-711-LotResult-Currency",
    "estimated-value-lot", "estimated-value-cur-lot",
]

NOTICE_TYPES_ATTRIB = ["can-standard", "can-social"]  # can-tport invalide (rejete par l API, absent du SDK)


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
    if PUB:
        return "publication-number IN ({})".format(PUB)
    return "notice-type IN ({}) SORT BY publication-date DESC".format(
        " ".join(NOTICE_TYPES_ATTRIB))


def _non_vide(v):
    """Une valeur d'API est-elle reellement renseignee (pas null / [] / '')."""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return any(_non_vide(x) for x in (v.values() if isinstance(v, dict) else v))
    return True


def main():
    print("SONDE MONTANT v3 -- montant au niveau notice d'attribution ?")
    print("Cible :", PUB or "attributions recentes (auto)")
    print("Dry-run :", DRYRUN)
    print()
    corps = {
        "query": _query(),
        "fields": CHAMPS_BASE + CHAMPS_MONTANT,
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
    print("HTTP {}".format(rep.status_code))
    if rep.status_code != 200:
        print("Corps :", rep.text[:400])
        sys.exit(0)

    notices = _notices(rep.json())
    print("Attributions dans l'echantillon :", len(notices))
    print()

    # Combien de fois chaque champ montant est renseigne (sur l'echantillon).
    compte = {c: 0 for c in CHAMPS_MONTANT}
    for n in notices:
        for c in CHAMPS_MONTANT:
            if _non_vide(n.get(c)):
                compte[c] += 1

    print("=" * 70)
    print("TAUX DE REMPLISSAGE PAR CHAMP (sur {} notices)".format(len(notices)))
    print("=" * 70)
    for c in CHAMPS_MONTANT:
        barre = "#" * compte[c]
        print("  {:<28} {:>2}/{:<2} {}".format(c, compte[c], len(notices), barre))

    print()
    print("=" * 70)
    print("EXEMPLES BRUTS (2 premieres notices avec au moins un montant)")
    print("=" * 70)
    montres = 0
    for n in notices:
        renseignes = {c: n.get(c) for c in CHAMPS_MONTANT if _non_vide(n.get(c))}
        if not renseignes:
            continue
        print("\n  {} | {}".format(
            n.get("publication-number"),
            (n.get("notice-title") or "")[:60] if isinstance(n.get("notice-title"), str)
            else str(n.get("notice-title"))[:60]))
        for c, v in renseignes.items():
            print("      {:<28} = {}".format(c, json.dumps(v, ensure_ascii=False)[:90]))
        montres += 1
        if montres >= 2:
            break
    if montres == 0:
        print("\n  Aucun champ montant renseigne sur l'echantillon.")
        print("  -> montant probablement differe (attribut privacy eForms) :")
        print("     garder SPARQL pour le montant. Reessayer avec un SONDE_PUB")
        print("     d'attribution dont le montant est publie.")

    print()
    print("=" * 70)
    print("LECTURE")
    print("=" * 70)
    print("  Champ le plus rempli = candidat pour le montant niveau notice.")
    print("  Un champ *-cur donne la devise (a apparier au *-value du meme")
    print("  prefixe). Si un montant fiable existe -> l'ajouter aux `fields` du")
    print("  collecteur et au scoring ; sinon conserver SPARQL pour le detail.")
    sys.exit(0)


if __name__ == "__main__":
    main()
