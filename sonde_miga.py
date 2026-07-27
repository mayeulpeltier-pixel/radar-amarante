# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE MIGA v3 (jetable) : balisage brut avant collecteur.
=========================================================================

Acquis (v1/v2) : le portail www.miga.org/projects repond 200 au CI AVEC des
en-tetes navigateur complets (le 403 n'etait qu'un filtre d'en-tetes). La
donnee est nominative et etrangere par statut. L'API Finances One repond aussi
mais exige un resourceId (bonus, projets emis seulement).

Cette derniere sonde ne VALIDE plus l'acces (fait), elle DUMPE LE BALISAGE BRUT
pour ecrire le scraper sur du reel, pas sur des suppositions (lecon UNGM). Elle
tranche :
  A. LISTE : structure d'une carte projet (lien fiche, tag type de document
     SPG/Brief, pays, secteur, date), noms des filtres (Document Type, Project
     Status...) pour construire des requetes ciblees, et presence d'un blob
     JSON embarque (Drupal) plus propre a parser que le HTML.
  B. FICHE : ou vivent Guarantee Holder, Investor Country, Host Country,
     secteur, montant, categorie E&S, statut, dans le DOM reel.
  C. BONUS : recuperer le resourceId du dataset MIGA via le catalogue, pour
     debloquer l'API Finances One (backfill des projets emis).

Aucune ecriture, aucun LLM. `requests` seul (pas de parseur : on dumpe le brut
et on compte par motif). Sortie code 0. Jetable.
"""

import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

TIMEOUT = 45
BASE = "https://www.miga.org"
LISTE = BASE + "/projects"
DS = "DS01671"
CATALOG = "https://datacatalogapi.worldbank.org/dexapps/fone/api"

ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Referer": "https://www.miga.org/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Connection": "keep-alive",
}
ENTETES_API = {"User-Agent": "amarante-radar/1.0", "Accept": "application/json,*/*"}


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _brut(txt, motif, avant=220, apres=360, etiquette=None):
    """Dump une tranche BRUTE (HTML non nettoye) autour d'un motif."""
    i = txt.find(motif)
    lab = etiquette or motif
    if i == -1:
        print("  [absent] '{}'".format(lab))
        return
    debut = max(0, i - avant)
    print("  --- brut autour de '{}' ---".format(lab))
    print("  " + txt[debut:i + apres])
    print("  ---")


def main():
    print("SONDE MIGA v3 -- balisage brut. Aucune ecriture, aucun LLM.")
    s = requests.Session()

    # ===================================================================== A
    _titre("A -- LISTE : balisage d'une carte, filtres, blob JSON")
    try:
        r = s.get(LISTE, headers=ENTETES, timeout=TIMEOUT)
    except Exception as e:
        print("liste injoignable : {}".format(e))
        sys.exit(0)
    print("HTTP {} | {} octets".format(r.status_code, len(r.text)))
    html = r.text

    liens = list(dict.fromkeys(re.findall(r'href="(/project/[^"#?]+)"', html)))
    print("Liens fiche /project/ uniques : {}".format(len(liens)))
    for l in liens[:6]:
        print("   ", l)

    if liens:
        _brut(html, liens[0], avant=420, apres=260, etiquette="carte projet (1er lien)")
    for tag in ("SPG", "Project Brief", "ESRS"):
        _brut(html, tag, avant=160, apres=120, etiquette="tag " + tag)

    # Noms des filtres (pour requetes ciblees) : on cherche les <select>/<label>.
    for f in ("Document Type", "Project Status", "Environmental Category",
              "Board Approval", "host_country", "project_status", "document_type"):
        _brut(html, f, avant=60, apres=220, etiquette="filtre " + f)

    # Blob JSON embarque (Drupal) : souvent plus propre que le HTML.
    for motif in ('type="application/json"', "drupal-settings-json",
                  '"@type"', "data-drupal-selector"):
        _brut(html, motif, avant=40, apres=260, etiquette="blob " + motif)

    # Pagination.
    pages = re.findall(r"[?&]page=(\d+)", html)
    print("\n  Pagination : pages vues =", sorted(set(pages)) or "aucune")

    # ===================================================================== B
    _titre("B -- FICHE : Guarantee Holder, Investor Country, montant, secteur")
    if liens:
        url = BASE + liens[0]
        try:
            rd = s.get(url, headers=ENTETES, timeout=TIMEOUT)
            print("GET {} -> HTTP {} | {} octets".format(url, rd.status_code, len(rd.text)))
            d = rd.text
            for champ in ("Guarantee Holder", "Investor Country", "Host Country",
                          "Sector", "Project Type", "Fiscal Year", "Board Date",
                          "Environmental Category", "Status"):
                _brut(d, champ, avant=30, apres=200, etiquette=champ)
            _brut(d, "Project Description", avant=10, apres=400, etiquette="Project Description")
            # Montant : 1re occurrence d'un motif monetaire.
            m = re.search(r"(US\$|USD|EUR|€|CHF)\s?[\d,\.]+\s*(million|m\b)?", d)
            if m:
                s0 = max(0, m.start() - 80)
                print("  --- brut autour du montant ---")
                print("  " + re.sub(r"\s+", " ", d[s0:m.end() + 40]))
                print("  ---")
            else:
                print("  [montant] aucun motif US$/EUR/CHF trouve en clair")
        except Exception as e:
            print("fiche injoignable : {}".format(e))

    # ===================================================================== C
    _titre("C -- BONUS : resourceId du dataset MIGA (debloquer l'API)")
    candidats = [
        CATALOG + "/datasets?datasetId=" + DS,
        CATALOG + "/dataset?datasetId=" + DS,
        CATALOG + "/resources?datasetId=" + DS,
        CATALOG + "/metadata?assetId=" + DS,
    ]
    trouve = []
    for u in candidats:
        try:
            rr = s.get(u, headers=ENTETES_API, timeout=TIMEOUT)
            rs = list(dict.fromkeys(re.findall(r"RS\d{4,}", rr.text)))
            print("  {} -> HTTP {} | resourceId: {}".format(u, rr.status_code, rs or "aucun"))
            trouve += rs
        except Exception as e:
            print("  {} -> echec ({})".format(u, e))
    trouve = list(dict.fromkeys(trouve))
    if trouve:
        print("\n  resourceId(s) a tester : {}".format(trouve))
        print("  -> apiservice?datasetId={}&resourceId={}&top=3".format(DS, trouve[0]))

    _titre("SYNTHESE")
    print("  Colle-moi surtout : le brut de la CARTE projet (A), les champs de")
    print("  la FICHE (B), et le resourceId (C). Avec ca j'ecris le collecteur")
    print("  portail (SPG + Brief) sur du reel, et l'API en backfill si resourceId.")
    print("\nSonde jetable : a supprimer une fois le collecteur ecrit.")
    sys.exit(0)


if __name__ == "__main__":
    main()
