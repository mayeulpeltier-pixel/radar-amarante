# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC JS (jetable) : comment le SPA appelle l'API.
=========================================================================

L'endpoint https://webapi.worldbank.org/aemsite/ifc-disclosure-search existe
(JSON structure) mais repond 404 en GET simple : il manque le sous-chemin, la
methode (POST ?) ou les bons parametres. On lit le client de donnees du SPA
(clientlib-ifc-disclosures-data.js) pour voir la CONSTRUCTION exacte de l'appel.

On dumpe le code brut autour des jetons distinctifs (endpoint, methode, params,
sortBy). Aucune ecriture, aucun LLM. Sortie code 0. Jetable.
"""

import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible"); sys.exit(0)

BUNDLES = [
    "https://disclosures.ifc.org/etc.clientlibs/datasites-spa/clientlibs/clientlib-ifc-disclosures-data.js",
    "https://disclosures.ifc.org/etc.clientlibs/ifc/clientlibs/clientlib-disclosure-site-author.js",
]
TIMEOUT = 45
ENTETES = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}

# Jetons distinctifs a localiser, avec fenetre de dump.
JETONS = ["ifc-disclosure-search", "aemsite", "apiEndpoint", "endpointLabel",
          "Disclosed_Date", "sortOrder", "sortBy", "Type_Description",
          "disclosure-search", "/search", "pageSize", "pageNumber"]


def _dumps(js, jeton, avant=120, apres=320, maxi=3):
    sorties = []
    start = 0
    for _ in range(maxi):
        i = js.find(jeton, start)
        if i == -1:
            break
        a = max(0, i - avant)
        sorties.append(js[a:i + apres])
        start = i + len(jeton)
    return sorties


def main():
    print("SONDE IFC JS -- lecture du client de donnees. Aucune ecriture.")
    s = requests.Session(); s.headers.update(ENTETES)
    for url in BUNDLES:
        print("\n" + "#" * 72)
        print("BUNDLE :", url.rsplit("/", 1)[-1])
        print("#" * 72)
        try:
            js = s.get(url, timeout=TIMEOUT).text
        except Exception as e:
            print("  injoignable :", e); continue
        print("  {} octets".format(len(js)))
        for jeton in JETONS:
            n = js.count(jeton)
            if not n:
                continue
            print("\n  === '{}' ({} occurrence(s)) ===".format(jeton, n))
            for frag in _dumps(js, jeton):
                # nettoyage leger pour lisibilite (garde la structure)
                print("   ...", frag.replace("\n", " ")[:440], "...")
        # Cherche aussi les methodes/ajax/fetch autour du mot 'search'
        for mot in ("method:", "type:\"POST\"", "type:'POST'", ".ajax(", "fetch(", "axios."):
            if mot in js:
                idx = js.find(mot)
                print("\n  [{}] ...{}...".format(mot, js[max(0, idx-80):idx+200].replace(chr(10), ' ')[:300]))

    print("\n" + "=" * 72)
    print("SYNTHESE : repere dans les dumps le sous-chemin ajoute a l'endpoint,")
    print("la methode (GET/POST), et les noms de parametres (pageSize, sortBy...).")
    print("Colle-moi les blocs autour de 'ifc-disclosure-search' et 'sortBy'.")
    print("Sonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
