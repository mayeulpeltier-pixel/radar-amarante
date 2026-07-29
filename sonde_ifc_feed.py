# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC FEED (jetable) : cracker le flux global.
=========================================================================

Cible : disclosures.ifc.org publie quotidiennement les ESRS/SPI IFC (global,
recent, tous types). La page /search est un SPA AEM : les resultats sont
charges en AJAX vers un endpoint JSON. On veut CET endpoint (source globale
unique, l'ideal radar), au lieu de tirer par pays.

Methode UNGM : lire le bundle JavaScript du site pour trouver le chemin d'API
que la recherche appelle. Ne pouvant pas voir le JS depuis un simple fetch, on
le fait DEPUIS LE CI :
  A. Recuperer le HTML brut de /search, lister les <script src> (clientlibs
     AEM) et reperer tout indice d'endpoint dans le HTML (data-*, config JSON).
  B. Telecharger chaque bundle JS et grep les chemins d'API plausibles
     (search / disclosure / project / api / servlet / solr / querybuilder) et
     les appels fetch/xhr/url.
  C. Tester les endpoints candidats (retournent-ils du JSON de projets ?).

Aucune ecriture, aucun LLM. `requests` seul. Sortie code 0. Jetable.
"""

import re
import sys
from urllib.parse import urljoin

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

BASE = "https://disclosures.ifc.org"
PAGE = BASE + "/search?sortBy=Disclosed_Date&sortOrder=desc"
TIMEOUT = 45
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Motifs de chemins d'API a debusquer dans le HTML et le JS.
MOTIFS = re.compile(
    r'["\'`](/[^"\'`?\s]{0,80}?(?:search|disclosure|project|api|servlet|solr|'
    r'querybuilder|content/dam|graphql)[^"\'`?\s]{0,80}?)["\'`]', re.I)
APPELS = re.compile(r'(?:fetch|axios\.\w+|\.open|url\s*[:=])\s*\(?\s*["\'`]([^"\'`]+)["\'`]')


def _titre(t):
    print("\n" + "=" * 72); print(t); print("=" * 72)


def main():
    print("SONDE IFC FEED -- crack du flux global. Aucune ecriture, aucun LLM.")
    s = requests.Session(); s.headers.update(ENTETES)

    # ---- A : HTML brut + scripts + indices ----
    _titre("A -- HTML /search : scripts et indices d'endpoint")
    try:
        r = s.get(PAGE, timeout=TIMEOUT)
    except Exception as e:
        print("injoignable :", e); sys.exit(0)
    html = r.text
    print("HTTP {} | {} octets".format(r.status_code, len(html)))

    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    scripts = [urljoin(BASE, u) for u in dict.fromkeys(scripts)]
    print("Scripts trouves :", len(scripts))
    for u in scripts[:20]:
        print("   ", u)

    indices_html = sorted(set(m.group(1) for m in MOTIFS.finditer(html)))
    if indices_html:
        print("\nIndices d'endpoint dans le HTML :")
        for i in indices_html[:25]:
            print("   ", i)

    # Config inline (AEM met souvent un JSON de conf avec l'URL du servlet).
    for cle in ('searchUrl', 'apiUrl', 'serviceUrl', 'endpoint', 'searchEndpoint',
                'data-search', 'solrUrl', 'resultUrl'):
        i = html.find(cle)
        if i != -1:
            print("\n  conf '{}' -> {}".format(cle, re.sub(r"\s+", " ", html[i:i + 160])))

    # ---- B : grep des bundles JS ----
    _titre("B -- Bundles JS : chemins d'API et appels reseau")
    candidats = set()
    for u in scripts:
        if not u.endswith(".js"):
            continue
        try:
            js = s.get(u, timeout=TIMEOUT).text
        except Exception:
            continue
        trouve = set(m.group(1) for m in MOTIFS.finditer(js))
        appels = set(m.group(1) for m in APPELS.finditer(js) if "/" in m.group(1))
        pertinents = {x for x in (trouve | appels)
                      if re.search(r'search|disclos|project|api|servlet|solr|querybuilder|graphql', x, re.I)}
        if pertinents:
            print("\n  {} ({} octets) :".format(u.rsplit("/", 1)[-1], len(js)))
            for p in sorted(pertinents)[:15]:
                print("     ", p)
            candidats |= pertinents

    # ---- C : test des candidats + variantes directes ----
    _titre("C -- Test des endpoints candidats (JSON de projets ?)")
    a_tester = set()
    for c in candidats:
        a_tester.add(urljoin(BASE, c))
    # variantes directes de la recherche
    a_tester |= {
        PAGE + "&format=json",
        BASE + "/search.json?sortBy=Disclosed_Date&sortOrder=desc",
        BASE + "/bin/querybuilder.json?path=/content&type=cq:Page&p.limit=3",
    }
    for u in sorted(a_tester)[:25]:
        try:
            rr = s.get(u, headers={"Accept": "application/json"}, timeout=TIMEOUT)
            ct = rr.headers.get("Content-Type", "")
            tete = re.sub(r"\s+", " ", rr.text[:120])
            est_json = "json" in ct.lower() or tete.strip().startswith(("{", "["))
            marque = "<< JSON" if est_json and rr.status_code == 200 else ""
            print("  [{}] {} | {} {}".format(rr.status_code, u[:90], ct[:30], marque))
        except Exception as e:
            print("  [x] {} ({})".format(u[:90], e))

    _titre("SYNTHESE")
    print("  Colle-moi : la liste des SCRIPTS (A), les chemins pertinents des")
    print("  BUNDLES (B), et surtout toute ligne '<< JSON' en (C). Un endpoint")
    print("  qui rend du JSON de projets = source globale unique pour IFC.")
    print("  Sinon, on retombe sur le tir par pays (option A).")
    print("\nSonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
