# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE IFC (jetable) : quel viewId est GLOBAL ?
=========================================================================

Acquis : l'endpoint Finances One `view?viewId=<DS>&top=N&type=json` repond
depuis le CI, en JSON structure et nominatif (company_name, country,
environmental_category [FI = crible], montants, projected_board_date,
document_type). Mais DS01162 est le jeu BRESIL uniquement (les projets IFC sont
publies par pays).

QUESTION UNIQUE : existe-t-il un viewId GLOBAL (tous pays) ? On teste plusieurs
candidats depuis le CI et on regarde la distribution des pays :
  - DS01162 : IFC Investment Services in Brazil (temoin, doit etre 100% Brazil)
  - DS01670 : "IFC Active Investment Projects by Country" (candidat GLOBAL,
              meme motif de nom que MIGA DS01671 qui est global)
  - DS01671 : MIGA Issued Projects by Country (reference : global connu)

Si un candidat renvoie plusieurs dizaines de pays -> c'est notre source globale,
le collecteur IFC sera trivial. Sinon -> on tirera par pays (carte a batir).

Aucune ecriture, aucun LLM. Sortie code 0. Jetable.
"""

import json
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

BASE = "https://datacatalogapi.worldbank.org/dexapps/fone/api/view"
TIMEOUT = 45
ENTETES = {"User-Agent": "amarante-radar/1.0", "Accept": "application/json"}

CANDIDATS = [
    ("DS01162", "IFC Investment Services in Brazil (temoin)"),
    ("DS01670", "IFC Active Investment Projects by Country (candidat GLOBAL)"),
    ("DS01671", "MIGA Issued Projects by Country (reference globale)"),
]

RESULTATS = []


def _distribution(records, champ, n=15):
    d = {}
    for r in records:
        v = str(r.get(champ, "")).strip() or "(vide)"
        d[v] = d.get(v, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n])


def _tester(s, view_id, libelle):
    print("\n" + "=" * 72)
    print("{} -- {}".format(view_id, libelle))
    print("=" * 72)
    url = "{}?viewId={}&top=300&type=json".format(BASE, view_id)
    try:
        r = s.get(url, timeout=TIMEOUT)
    except Exception as e:
        print("  injoignable : {}".format(e))
        RESULTATS.append((view_id, "injoignable"))
        return
    if r.status_code != 200:
        print("  HTTP {} : {}".format(r.status_code, r.text[:200]))
        RESULTATS.append((view_id, "HTTP {}".format(r.status_code)))
        return
    try:
        charge = r.json()
    except Exception as e:
        print("  non-JSON : {}".format(e))
        RESULTATS.append((view_id, "non-JSON"))
        return
    records = (charge.get("data") if isinstance(charge, dict) else charge) or []
    count = charge.get("count") if isinstance(charge, dict) else len(records)
    pays = _distribution(records, "country")
    nb_pays = len({str(x.get("country", "")).strip() for x in records if x.get("country")})
    print("  count total :", count, "| records lus :", len(records), "| pays distincts :", nb_pays)
    print("  distribution pays :", pays)
    if records:
        print("  champs :", sorted(records[0].keys())[:20])
        print("  document_type :", _distribution(records, "document_type", 6))
    verdict = "GLOBAL ({} pays)".format(nb_pays) if nb_pays > 5 else "mono/limite ({} pays)".format(nb_pays)
    RESULTATS.append((view_id, verdict))


def main():
    print("SONDE IFC -- quel viewId est global ? Aucune ecriture, aucun LLM.")
    s = requests.Session()
    s.headers.update(ENTETES)
    for view_id, libelle in CANDIDATS:
        _tester(s, view_id, libelle)

    print("\n" + "=" * 72)
    print("SYNTHESE")
    print("=" * 72)
    for view_id, verdict in RESULTATS:
        print("  {} : {}".format(view_id, verdict))
    print("\n  LECTURE : si un candidat est GLOBAL avec les champs de divulgation")
    print("  (company_name, country, environmental_category, document_type), c'est")
    print("  la source unique du collecteur IFC. Sinon on tire par pays.")
    print("\nSonde jetable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
