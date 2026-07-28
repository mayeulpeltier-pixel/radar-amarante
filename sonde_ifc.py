# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC (jetable) : valider l'API avant collecteur.
=========================================================================

Decouverte : les divulgations IFC sont exposees en JSON STRUCTURE via World
Bank Finances One, sur un endpoint qui ne demande PAS de resourceId (contrairement
a l'apiservice qui nous avait bloques sur MIGA) :

    https://datacatalogapi.worldbank.org/dexapps/fone/api/view?viewId=DS01162&top=N&type=json

Hote deja prouve joignable depuis le CI (MIGA metadata). Reponse : {count, data:[...]}.
Champs : date_disclosed, company_name, project_name, country, industry,
environmental_category, status, projected_board_date, montants IFC, document_type,
project_number. Nominatif, structure = source ideale (aucun scraping).

CETTE SONDE TRANCHE (aucune supposition, on dumpe le reel) :
  A. L'endpoint repond-il depuis le CI ? Forme de l'enveloppe, nb de records.
  B. Champs reels d'un record (dump brut) + valeurs cle.
  C. GLOBAL ou "Brazil" ? -> distribution des pays sur un echantillon.
  D. Ordre par defaut : les dates_disclosed sont-elles recent-d'abord ou
     origine-d'abord ? (decide la strategie de collecte : tri/skip/filtre date)
  E. Distribution document_type / environmental_category / status : de quoi
     calibrer les cribles (FI a ecarter, ESRS/SPI a garder, pre-board = precoce).

Aucune ecriture, aucun LLM. `requests` seul. Sortie code 0. Jetable.
"""

import json
import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

BASE = "https://datacatalogapi.worldbank.org/dexapps/fone/api/view"
VIEW_ID = "DS01162"
TIMEOUT = 45
ENTETES = {"User-Agent": "amarante-radar/1.0", "Accept": "application/json"}

RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OK" if ok else "a creuser", detail))


def _distribution(records, champ, n=12):
    d = {}
    for r in records:
        v = str(r.get(champ, "")).strip() or "(vide)"
        d[v] = d.get(v, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n])


def main():
    print("SONDE IFC -- API Finances One view. Aucune ecriture, aucun LLM.")
    s = requests.Session()
    s.headers.update(ENTETES)
    url = "{}?viewId={}&top=200&type=json".format(BASE, VIEW_ID)

    _titre("A -- Endpoint IFC (view) : acces CI + enveloppe")
    print("GET", url)
    try:
        r = s.get(url, timeout=TIMEOUT)
    except Exception as e:
        _verdict("A acces", False, "injoignable depuis CI : {}".format(e))
        _fin()
        return
    print("HTTP {} | {} octets".format(r.status_code, len(r.text)))
    if r.status_code != 200:
        print("Debut reponse :", r.text[:300])
        _verdict("A acces", False, "statut != 200")
        _fin()
        return
    try:
        charge = r.json()
    except Exception as e:
        _verdict("A acces", False, "reponse non-JSON : {}".format(e))
        _fin()
        return
    cles = list(charge.keys()) if isinstance(charge, dict) else ["(liste)"]
    records = charge.get("data") if isinstance(charge, dict) else charge
    records = records or []
    print("Cles enveloppe :", cles, "| count annonce :", charge.get("count") if isinstance(charge, dict) else "n/a")
    print("Records recus :", len(records))
    _verdict("A acces", len(records) > 0, "{} records, enveloppe {}".format(
        len(records), "conforme" if records else "vide"))
    if not records:
        _fin()
        return

    _titre("B -- Champs reels d'un record (dump brut)")
    r0 = records[0]
    print("Champs :", sorted(r0.keys()))
    print("\n1er record (brut) :")
    print(json.dumps(r0, ensure_ascii=False, indent=1)[:1200])

    _titre("C -- GLOBAL ou Brazil ? Distribution des pays")
    pays = _distribution(records, "country")
    print("Pays (top) :", pays)
    nb_pays = len({str(x.get("country", "")).strip() for x in records if x.get("country")})
    global_ok = nb_pays > 3
    _verdict("C portee", global_ok,
             "{} pays distincts -> dataset GLOBAL".format(nb_pays) if global_ok
             else "{} pays -> possiblement limite, chercher le viewId global".format(nb_pays))

    _titre("D -- Ordre par defaut (recent d'abord ?)")
    dates = [str(x.get("date_disclosed", "")) for x in records if x.get("date_disclosed")]
    if dates:
        print("date_disclosed 1er record  :", dates[0])
        print("date_disclosed dernier      :", dates[-1])
        print("min :", min(dates), "| max :", max(dates))
        recent_dabord = dates[0] >= dates[-1]
        _verdict("D ordre", True,
                 "recent-d'abord (ideal, on lit la tete)" if recent_dabord
                 else "origine-d'abord (il faudra trier ou filtrer par date)")
    else:
        _verdict("D ordre", False, "date_disclosed absente/vide")

    _titre("E -- Cribles : document_type / environmental_category / status")
    print("document_type          :", _distribution(records, "document_type"))
    print("environmental_category :", _distribution(records, "environmental_category"))
    print("status                 :", _distribution(records, "status"))
    print("projected_board_date presents :",
          sum(1 for x in records if str(x.get("projected_board_date", "")).strip()),
          "/", len(records), "(pre-board = signal precoce)")
    _verdict("E cribles", True, "distributions dumpees ci-dessus")

    _fin()


def _fin():
    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {:10} {}".format("OK " if ok else "?? ", nom, detail))
    print("\n  LECTURE : si global + nominatif + FI present, on ecrit le collecteur")
    print("  IFC (JSON structure) en reutilisant le scoring/LLM/Sheet de MIGA.")
    print("  On fetchera la fiche riche (project_number) seulement pour les tops.")
    print("\nSonde jetable : a supprimer une fois le collecteur ecrit.")
    sys.exit(0)


if __name__ == "__main__":
    main()
