# -*- coding: utf-8 -*-
"""
SONDE JETABLE -- BODACC x taille d'entreprise (01/11/2026). A SUPPRIMER apres.

Tranche le choix d'architecture du futur collecteur BODACC : les procedures
collectives et cessions sont nombreuses mais dominees par des TPE (sonde v2).
Le signal ARISE = l'intersection avec la TAILLE. Cette sonde MESURE cette
intersection : sur un echantillon recent de procedures collectives et de
cessions, combien concernent des ETI (>= 250 salaries) et des grands groupes
(>= 5000) ? Valide aussi l'extraction du SIREN depuis le champ `registre`.

Lecture seule (BODACC + Recherche d'entreprises, publics, sans cle). Tourne en
GitHub Actions (egress Opendatasoft OK la-bas). Budget de lookups borne. Code 0.
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

BODACC = ("https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/"
          "catalog/datasets/annonces-commerciales/records")
GOUV = "https://recherche-entreprises.api.gouv.fr/search"
UA = {"User-Agent": "radar-amarante-sonde", "Accept": "application/json"}

PAR_FAMILLE = 200          # annonces recentes echantillonnees par famille
BUDGET_LOOKUP = 220        # nb max d'appels effectif (borne la duree)
# Tranches INSEE : code -> plancher d'effectif salarie.
PLANCHER = {"00": 0, "01": 1, "02": 3, "03": 6, "11": 10, "12": 20, "21": 50,
            "22": 100, "31": 200, "32": 250, "41": 500, "42": 1000, "51": 2000,
            "52": 5000, "53": 10000}


def get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(full, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def premier(v):
    if isinstance(v, (list, tuple)):
        return premier(v[0]) if v else ""
    return v if v is not None else ""


def siren_depuis(rec):
    """BODACC loge le SIREN dans `registre` ('843 322 371 (RCS...)') ou dans
    des champs voisins. On prend la premiere sequence de 9 chiffres."""
    for champ in ("registre", "numeroidentification", "numeroimmatriculation"):
        v = rec.get(champ)
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v)
        m = re.search(r"\b(\d[\d\s]{8,}\d)\b", str(v or ""))
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) >= 9:
                return digits[:9]
    return ""


def nom(rec):
    for k in ("commercant", "denomination"):
        if rec.get(k):
            return premier(rec[k])
    return ""


def effectif_tranche(siren, cache):
    if siren in cache:
        return cache[siren]
    try:
        d = get(GOUV, {"q": siren, "page": 1, "per_page": 1})
        res = (d.get("results") or [{}])[0]
        tr = res.get("tranche_effectif_salarie") or ""
    except Exception:
        tr = None
    cache[siren] = tr
    time.sleep(0.25)
    return tr


def main():
    print("=" * 64)
    print("SONDE BODACC x TAILLE -- quelle part d'ETI / grands groupes ?")
    print("=" * 64)
    cache = {}
    lookups = 0
    for famille in ("Procédures collectives", "Ventes et cessions"):
        print("\n########## {} ##########".format(famille))
        recs = []
        for page in range((PAR_FAMILLE + 99) // 100):
            try:
                d = get(BODACC, {"where": 'familleavis_lib="{}"'.format(famille),
                                 "order_by": "dateparution desc",
                                 "limit": 100, "offset": page * 100})
            except Exception as e:
                print("  requete BODACC en echec :", str(e)[:100]); break
            recs += d.get("results") or []
        avec_siren = [r for r in recs if siren_depuis(r)]
        print("  annonces echantillon : {} | avec SIREN exploitable : {}".format(
            len(recs), len(avec_siren)))
        if recs:
            print("  exemple registre brut :", repr(premier(recs[0].get("registre")))[:80],
                  "-> SIREN", siren_depuis(recs[0]))

        buckets = Counter()
        gros = []
        for r in avec_siren:
            if lookups >= BUDGET_LOOKUP:
                break
            tr = effectif_tranche(siren_depuis(r), cache)
            lookups += 1
            if tr is None:
                buckets["lookup_echec"] += 1
                continue
            p = PLANCHER.get(str(tr), -1)
            if p < 0:
                buckets["effectif_inconnu"] += 1
            elif p >= 5000:
                buckets[">=5000"] += 1
                gros.append((nom(r), tr, ">=5000"))
            elif p >= 250:
                buckets["250-4999"] += 1
                gros.append((nom(r), tr, "250-4999"))
            else:
                buckets["<250"] += 1
        echantillonnes = sum(buckets.values())
        print("  effectif mesure sur {} societes :".format(echantillonnes))
        for k in (">=5000", "250-4999", "<250", "effectif_inconnu", "lookup_echec"):
            if buckets.get(k):
                print("     {:5d}  {}".format(buckets[k], k))
        eti_plus = buckets.get(">=5000", 0) + buckets.get("250-4999", 0)
        if echantillonnes:
            print("  >>> part ETI+ (>=250) : {:.0%}".format(eti_plus / echantillonnes))
        for n, tr, cat in gros[:6]:
            print("     [{}] {} (tranche {})".format(cat, (n or "?")[:46], tr))

    print("\n(sonde terminee ; aucune ecriture ; lookups effectif = {})".format(lookups))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("SONDE EN ECHEC :", repr(e))
    sys.exit(0)
