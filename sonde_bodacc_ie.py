# -*- coding: utf-8 -*-
"""
SONDE JETABLE -- BODACC pour ARISE (01/11/2026). A SUPPRIMER apres decision.

Question : le BODACC (Opendatasoft, gratuit, meme famille que le BOAMP deja
utilise) fournit-il des DECLENCHEURS exploitables pour les lignes de service
ARISE au-dela de la conformite ?
  - "Ventes et cessions" / changements de controle -> due diligence
  - "Procedures collectives" (redressement/liquidation) -> forensic / recouvrement

Ne mesure pas de pertinence fine (c'est le LLM qui trancherait) : mesure la
STRUCTURE et le VOLUME (quelles familles d'avis, a quelle frequence, sur des
entreprises NOMMEES), pour decider s'il vaut la peine d'un collecteur.

Aucune ecriture. Lecture seule de l'API publique. Tourne en GitHub Actions
(l'egress conteneur de dev ne joint pas Opendatasoft ce soir ; Actions oui,
comme Proparco/opendata.afd.fr en prod). Code 0 toujours.
"""

import json
import sys
import urllib.request
import urllib.parse
from collections import Counter

BASE = ("https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/"
        "catalog/datasets/annonces-commerciales/records")


def get(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "radar-amarante-sonde",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def premier(v):
    if isinstance(v, (list, tuple)):
        return premier(v[0]) if v else ""
    return v if v is not None else ""


def champ_famille(rec):
    for k in ("familleavis_lib", "familleavis", "typeavis_lib", "typeavis"):
        if rec.get(k):
            return k
    return None


def nom_entreprise(rec):
    # BODACC loge le nom tantot dans `commercant`, tantot dans listepersonnes.
    for k in ("commercant", "denomination", "nom"):
        if rec.get(k):
            return premier(rec[k])
    lp = rec.get("listepersonnes")
    if isinstance(lp, str) and lp:
        try:
            lp = json.loads(lp)
        except Exception:
            return lp[:80]
    try:
        p = (lp or {}).get("personne")
        p = p[0] if isinstance(p, list) else p
        return (p or {}).get("denomination") or (p or {}).get("nom") or ""
    except Exception:
        return ""


def main():
    print("=" * 64)
    print("SONDE BODACC pour ARISE -- structure, volume, exemples")
    print("=" * 64)

    # 1) Un enregistrement : schema + volume total.
    d = get({"limit": 1, "order_by": "dateparution desc"})
    total = d.get("total_count")
    recs = d.get("results") or []
    if not recs:
        print("Aucun enregistrement renvoye."); return
    champs = sorted(recs[0].keys())
    fam_key = champ_famille(recs[0])
    print("total_count (toutes dates) :", total)
    print("champ famille d'avis       :", fam_key)
    print("champs disponibles         :", ", ".join(champs))
    print()

    # 2) Echantillon des annonces les plus recentes -> distribution par famille.
    echantillon = []
    for page in range(5):                       # 5 x 100 = 500 plus recentes
        d = get({"limit": 100, "offset": page * 100, "order_by": "dateparution desc"})
        echantillon += d.get("results") or []
    if not fam_key:
        fam_key = champ_famille(echantillon[0]) if echantillon else None
    dist = Counter(premier(r.get(fam_key)) for r in echantillon if fam_key)
    if echantillon:
        dates = sorted(premier(r.get("dateparution")) for r in echantillon if r.get("dateparution"))
        print("Echantillon : {} annonces, du {} au {}".format(
            len(echantillon), dates[0] if dates else "?", dates[-1] if dates else "?"))
    print("\nDistribution par famille d'avis (echantillon recent) :")
    for fam, n in dist.most_common():
        print("  {:5d}  {}".format(n, fam))

    # 3) Exemples pour les deux declencheurs ARISE visés.
    def exemples(motcle, titre):
        print("\n--- {} ---".format(titre))
        vus = [r for r in echantillon
               if motcle in premier(r.get(fam_key)).lower()] if fam_key else []
        if not vus:
            print("  (aucun dans l'echantillon recent)")
        for r in vus[:4]:
            print("  {} | {} | {}".format(
                premier(r.get("dateparution")),
                (nom_entreprise(r) or "?")[:48],
                (premier(r.get("ville")) or premier(r.get("tribunal")) or "")[:30]))

    exemples("proc", "Procedures collectives -> forensic / recouvrement")
    exemples("vente", "Ventes et cessions -> due diligence")
    exemples("cession", "Cessions -> due diligence")

    print("\n(sonde terminee ; aucune ecriture)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("SONDE EN ECHEC :", repr(e))
    sys.exit(0)
