# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE MIGA v2 (jetable) : trouver une porte d'acces CI.
=========================================================================

Contexte : la v1 a montre que le portail www.miga.org renvoie 403 depuis
GitHub Actions (WAF Akamai bloque les IP de datacenter -- lecon ADB). La donnee
MIGA existe et est nominative (verifie par reconnaissance web), reste a trouver
un CHEMIN accessible depuis le CI. On ne lache pas une source avant d'avoir
epuise les pistes (lecon UNGM).

Cette sonde teste PLUSIEURS portes en un seul run et dumpe le brut de chacune :
  1. Portail HTML avec en-tetes navigateur complets (au cas ou le 403 serait
     un simple filtre d'en-tetes et non un blocage d'IP).
  2. API World Bank Finances One -- METADATA (hote datacatalogapi, concu pour
     l'acces programmatique) : confirme que le CI atteint cet hote.
  3. API Finances One -- DATA (apiservice) : le vrai jeu MIGA Issued Projects
     (DS01671, ~592 lignes, nominatif : Guarantee Holder, pays, secteur,
     montant). On tente datasetId seul, puis avec resourceId decouvert.
  4. Variante CSV de l'apiservice.

Pour chaque porte : statut HTTP, type de contenu, et debut BRUT de la reponse.
Verdict : quelle(s) porte(s) renvoie(nt) de la vraie donnee MIGA depuis le CI.

Aucune ecriture, aucun LLM. Ne depend que de `requests`. Sortie code 0.
"""

import json
import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

TIMEOUT = 45
DS = "DS01671"                                        # MIGA Issued Projects
CATALOG = "https://datacatalogapi.worldbank.org/dexapps/fone/api"
PORTAIL = "https://www.miga.org/projects"

ENTETES_NAVIGATEUR = {
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
ENTETES_API = {
    "User-Agent": "amarante-radar/1.0 (+veille)",
    "Accept": "application/json, text/csv, */*",
}

RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OK" if ok else "a creuser", detail))


def _apercu(txt, n=280):
    return re.sub(r"\s+", " ", (txt or "")[:n])


def _ressemble_donnee_miga(txt):
    """Heuristique : la reponse contient-elle des champs MIGA reconnaissables ?"""
    motifs = ("Guarantee", "Host Country", "HostCountry", "Investor",
              "Gross Exposure", "Fiscal Year", "FiscalYear", "Project Name")
    return any(m.lower() in (txt or "").lower() for m in motifs)


def _get(s, url, entetes, label):
    print("\n[{}] GET {}".format(label, url))
    try:
        r = s.get(url, headers=entetes, timeout=TIMEOUT)
    except Exception as e:
        print("   echec reseau : {}".format(e))
        return None
    ct = r.headers.get("Content-Type", "")
    print("   HTTP {} | {} | {} octets".format(r.status_code, ct, len(r.text)))
    print("   debut : {}".format(_apercu(r.text)))
    return r


def _resource_ids(txt):
    """Extrait d'eventuels resourceId (RS#####) d'une reponse JSON/texte."""
    return list(dict.fromkeys(re.findall(r"RS\d{4,}", txt or "")))


def main():
    print("SONDE MIGA v2 -- test multi-chemins depuis le CI. Aucune ecriture.")
    s = requests.Session()

    # ---- Porte 1 : portail HTML avec en-tetes navigateur complets ----------
    _titre("1 -- Portail HTML (en-tetes navigateur complets)")
    r1 = _get(s, PORTAIL, ENTETES_NAVIGATEUR, "portail")
    ok1 = bool(r1 and r1.status_code == 200 and "/project/" in r1.text)
    _verdict("1 portail", ok1,
             "portail accessible depuis CI (le 403 n'etait qu'un filtre d'en-tetes)"
             if ok1 else "portail toujours bloque cote CI (blocage IP datacenter probable)")

    # ---- Porte 2 : API Finances One -- metadata ----------------------------
    _titre("2 -- API Finances One : metadata (l'hote datacatalogapi repond-il ?)")
    r2 = _get(s, CATALOG + "/metadata?assetId=" + DS, ENTETES_API, "metadata")
    ok2 = bool(r2 and r2.status_code == 200 and "MIGA" in r2.text)
    _verdict("2 metadata", ok2,
             "hote API atteint depuis CI (metadata MIGA servie)"
             if ok2 else "hote API injoignable/filtre depuis CI")
    resources = _resource_ids(r2.text if r2 else "")
    if resources:
        print("   resourceId reperes dans la metadata :", resources)

    # ---- Porte 3 : API Finances One -- data (apiservice) -------------------
    _titre("3 -- API Finances One : data apiservice (le vrai jeu MIGA)")
    essais = [CATALOG + "/apiservice?datasetId={}&top=3".format(DS)]
    for rs in resources[:2]:
        essais.append(CATALOG + "/apiservice?datasetId={}&resourceId={}&top=3".format(DS, rs))
    ok3 = False
    for url in essais:
        r = _get(s, url, ENTETES_API, "data")
        if r and r.status_code == 200 and _ressemble_donnee_miga(r.text):
            ok3 = True
            # Dump la 1re ligne pour voir les colonnes reelles.
            try:
                donnees = r.json()
                echantillon = donnees.get("data") if isinstance(donnees, dict) else donnees
                if isinstance(echantillon, list) and echantillon:
                    print("\n   COLONNES reelles :", list(echantillon[0].keys()))
                    print("   1re ligne (brut) :",
                          json.dumps(echantillon[0], ensure_ascii=False)[:600])
            except Exception:
                print("   (reponse non-JSON, a inspecter dans le debut ci-dessus)")
            break
    _verdict("3 data", ok3,
             "DATA MIGA servie depuis CI (chemin API exploitable, nominatif)"
             if ok3 else "apiservice n'a pas rendu de donnee reconnaissable (voir debut brut)")

    # ---- Porte 4 : variante CSV -------------------------------------------
    _titre("4 -- API Finances One : variante CSV")
    r4 = _get(s, CATALOG + "/apiservice?datasetId={}&top=3&type=csv".format(DS),
              ENTETES_API, "csv")
    ok4 = bool(r4 and r4.status_code == 200 and "," in (r4.text[:200] or "")
               and "<" not in (r4.text[:40] or ""))
    _verdict("4 csv", ok4,
             "CSV structure servi depuis CI" if ok4
             else "pas de CSV exploitable par ce parametre")

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {:12} {}".format("OK " if ok else "?? ", nom, detail))
    print("\n  LECTURE :")
    print("   - une porte API OK (2+3) -> collecteur MIGA sur l'API Finances One")
    print("     (projets EMIS, nominatifs). Signal precoce SPG a voir ensuite.")
    print("   - portail OK (1)         -> scrape possible, y compris SPG precoce.")
    print("   - TOUT bloque            -> MIGA hostile au CI (comme ADB). On")
    print("     tranche : proxy/fetch alternatif, ou on passe a IFC/IDB.")
    print("\nSonde jetable : a supprimer une fois la decision prise.")
    sys.exit(0)


if __name__ == "__main__":
    main()
