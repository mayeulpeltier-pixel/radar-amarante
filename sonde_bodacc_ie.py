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


from datetime import date, timedelta

# Familles d'avis BODACC visees, mappees a une ligne de service ARISE.
CIBLES = [
    ("Procédures collectives", "forensic / recouvrement"),
    ("Ventes et cessions", "due diligence (acquisition fonds/société)"),
    ("Modifications", "due diligence (changement de contrôle / dirigeant)"),
    ("Créations", "(volume de référence : trop générique = bruit)"),
]


def main():
    print("=" * 64)
    print("SONDE BODACC pour ARISE -- volume par famille sur 30 j + exemples")
    print("=" * 64)

    # 1) Schema + volume total.
    d = get({"limit": 1, "order_by": "dateparution desc"})
    recs = d.get("results") or []
    if not recs:
        print("Aucun enregistrement renvoye."); return
    fam_key = champ_famille(recs[0]) or "familleavis_lib"
    print("total_count (toutes dates) :", d.get("total_count"))
    print("champ famille              :", fam_key)
    depuis = (date.today() - timedelta(days=30)).isoformat()
    print("fenetre                    : annonces depuis", depuis)

    # 2) Pour chaque famille cible : volume sur 30 j + exemples nommes.
    for fam, service in CIBLES:
        where = '{k}="{v}" and dateparution>="{d}"'.format(k=fam_key, v=fam, d=depuis)
        try:
            r = get({"where": where, "limit": 4, "order_by": "dateparution desc"})
        except Exception as e:  # noqa: BLE001
            print("\n### {} : requete en echec ({})".format(fam, str(e)[:80])); continue
        n = r.get("total_count")
        print("\n### {}  ->  {}".format(fam, service))
        print("    volume 30 j : {}".format(n))
        for rec in (r.get("results") or [])[:4]:
            jug = premier(rec.get("jugement"))
            if isinstance(jug, dict):
                jug = jug.get("famille") or jug.get("nature") or ""
            print("      {} | {} | SIREN {} | {}".format(
                premier(rec.get("dateparution")),
                (nom_entreprise(rec) or "?")[:42],
                (premier(rec.get("registre")) or "?")[:20],
                (str(jug) or premier(rec.get("ville")) or "")[:40]))

    print("\n(sonde terminee ; aucune ecriture)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("SONDE EN ECHEC :", repr(e))
    sys.exit(0)
