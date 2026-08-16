# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE WINNER-STATUS v2 (jetable) : catalogue des champs,
structure multi-lots, montant par titulaire.
===============================================================================

CONTEXTE (verdict sonde v1)
---------------------------
L'API search TED expose bien `winner-selection-status` (valeur `selec-w`
confirmee) ET `winner-name` en clair (ex "Yandalux Solar GmbH"), sans PDF ni
SPARQL. Restent 3 inconnues a lever avant de cabler `ted_complet_attributions`.

BUT (3 inconnues, une seule sonde)
----------------------------------
  1. CATALOGUE : capturer la LISTE EXHAUSTIVE des champs supportes par l'API
     search. Le message d'erreur 400 la contient en entier (v1 la tronquait a
     200 car.). On provoque le 400 avec un champ bidon, on parse la liste, on
     grep les familles utiles (winner / tender / value / amount / size /
     country / lot / result / organisation).
  2. STRUCTURE MULTI-LOTS : dumper le BRUT (non aplati) de winner-selection-
     status + winner-name sur une attribution multi-lots, pour verrouiller
     l'appariement lot <-> statut <-> titulaire (risque : lot1 selec-w /
     lot2 clos-nw).
  3. MONTANT PAR TITULAIRE : tester AUTOMATIQUEMENT les champs valeur/montant
     du catalogue (etape 1) et dumper leurs valeurs -> voir si l'API donne un
     montant PAR titulaire (rendrait SPARQL quasi inutile) ou seulement un
     total agrege.

METHODE : l'etape 1 alimente l'etape 3 (les noms testes viennent du catalogue
reel, pas d'une liste devinee).

USAGE
-----
    python sonde_winner_status_v2.py                        # decouverte auto
    SONDE_PUB="10759-2026" python sonde_winner_status_v2.py  # cibler une attrib
    SONDE_WINNER_DRYRUN=1 python sonde_winner_status_v2.py   # affiche requetes

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

CHAMPS_BASE = ["publication-number", "notice-title", "notice-type",
               "publication-date"]
NOTICE_TYPES_ATTRIB = ["can-standard", "can-social", "can-tport"]

# Familles de champs a mettre en avant dans le catalogue (etape 1).
FAMILLES = ["winner", "tender", "value", "amount", "price", "size", "country",
            "lot-result", "result", "organisation", "contract", "sme"]

# Champs deja confirmes utiles par la v1 (dumpes en brut a l'etape 2).
CHAMPS_CONFIRMES = ["winner-selection-status", "winner-name"]

# Regex pour reperer, dans le catalogue, les champs candidats "montant".
CANDIDAT_MONTANT = re.compile(r"(value|amount|price)", re.IGNORECASE)


def poster(corps):
    """POST avec failover secondaire (calque poster_ted). 4xx : pas de bascule
    (on VEUT voir le 400). Renvoie la reponse, ou None."""
    if DRYRUN:
        print("      [DRYRUN] corps =", json.dumps(corps, ensure_ascii=False)[:300])
        return None
    rep = None
    for i, url in enumerate(ENDPOINTS):
        dernier = (i == len(ENDPOINTS) - 1)
        try:
            rep = requests.post(url, json=corps, timeout=TIMEOUT)
        except requests.exceptions.RequestException as e:
            if dernier:
                print("      (reseau) tous endpoints injoignables :",
                      type(e).__name__)
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


def _query(pub=None):
    if pub:
        return "publication-number IN ({})".format(pub)
    return "notice-type IN ({}) SORT BY publication-date DESC".format(
        " ".join(NOTICE_TYPES_ATTRIB))


def _corps(fields, pub=None, limit=10):
    return {
        "query": _query(pub),
        "fields": fields,
        "page": 1,
        "limit": limit,
        "scope": "ALL",
        "checkQuerySyntax": False,
        "paginationMode": "PAGE_NUMBER",
    }


def _val(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        for x in v.values():
            s = _val(x)
            if s:
                return s
        return ""
    if isinstance(v, list):
        return ", ".join(s for s in (_val(x) for x in v) if s)
    return str(v)


# ===========================================================================
# ETAPE 1 -- CATALOGUE EXHAUSTIF DES CHAMPS
# ===========================================================================
def etape_1_catalogue():
    print("=" * 74)
    print("ETAPE 1 -- CATALOGUE : liste exhaustive des champs supportes")
    print("=" * 74)
    # On provoque un 400 avec un champ bidon : le message liste tous les champs.
    rep = poster(_corps(fields=["__sonde_invalide__"], pub=PUB or None, limit=1))
    if rep is None:
        print("  Pas de reponse (dry-run/reseau).")
        return []
    if rep.status_code != 400:
        print("  HTTP {} inattendu (on visait un 400). Corps :".format(
            rep.status_code), rep.text[:300])
        return []
    texte = rep.text
    m = re.search(r"supported values are:\s*(.+)", texte, re.DOTALL)
    if not m:
        print("  Message 400 sans liste de champs. Brut :", texte[:300])
        return []
    brut = m.group(1)
    # La liste se termine par ')' (fin de la parenthese du message).
    fin = brut.rfind(")")
    if fin != -1:
        brut = brut[:fin]
    champs = [c.strip() for c in brut.split(",") if c.strip()]
    print("  Nombre de champs supportes :", len(champs))
    print()
    for fam in FAMILLES:
        matchs = sorted(c for c in champs if fam in c.lower())
        if matchs:
            print("  [{}] ({}) :".format(fam, len(matchs)))
            print("      " + ", ".join(matchs))
    return champs


# ===========================================================================
# ETAPE 2 -- STRUCTURE BRUTE MULTI-LOTS
# ===========================================================================
def etape_2_structure():
    print()
    print("=" * 74)
    print("ETAPE 2 -- STRUCTURE BRUTE : appariement lot / statut / titulaire")
    print("=" * 74)
    fields = CHAMPS_BASE + CHAMPS_CONFIRMES
    rep = poster(_corps(fields=fields, pub=PUB or None, limit=10))
    if rep is None:
        return None
    if rep.status_code != 200:
        print("  HTTP {} : {}".format(rep.status_code, rep.text[:200]))
        return None
    notices = _notices(rep.json())
    if not notices:
        print("  Aucune attribution.")
        return None

    def _compte(n, champ):
        v = n.get(champ)
        return len(v) if isinstance(v, list) else (1 if v else 0)

    # On cherche en priorite une attribution avec plusieurs titulaires/statuts.
    multi = sorted(notices,
                   key=lambda n: max(_compte(n, "winner-name"),
                                     _compte(n, "winner-selection-status")),
                   reverse=True)
    cible = multi[0]
    print("  Attribution inspectee : {} | lots(winner-name)={} | "
          "lots(statut)={}".format(
              _val(cible.get("publication-number")),
              _compte(cible, "winner-name"),
              _compte(cible, "winner-selection-status")))
    print()
    for champ in CHAMPS_CONFIRMES:
        print("  --- {} (BRUT) ---".format(champ))
        print(json.dumps(cible.get(champ), indent=2, ensure_ascii=False))
    print()
    print("  A verifier a l'oeil : les listes winner-name et")
    print("  winner-selection-status ont-elles la MEME longueur et le MEME")
    print("  ordre (index i = meme lot) ? Si oui -> appariement par index.")
    print("  Si les longueurs different -> il faut un champ lot-* pour lier.")
    return notices


# ===========================================================================
# ETAPE 3 -- MONTANT PAR TITULAIRE (auto-alimentee par l'etape 1)
# ===========================================================================
def etape_3_montant(catalogue, notices):
    print()
    print("=" * 74)
    print("ETAPE 3 -- MONTANT : l'API donne-t-elle un montant par titulaire ?")
    print("=" * 74)
    candidats = sorted(c for c in catalogue if CANDIDAT_MONTANT.search(c))
    if not candidats:
        print("  Aucun champ valeur/montant dans le catalogue (etape 1 vide ?).")
        return
    print("  Champs 'montant' candidats issus du catalogue :", len(candidats))
    print("      " + ", ".join(candidats))
    print()
    pub_cible = None
    if notices:
        pub_cible = _val(notices[0].get("publication-number"))
    for nom in candidats:
        corps = _corps(fields=CHAMPS_BASE + [nom],
                       pub=pub_cible or (PUB or None), limit=3)
        rep = poster(corps)
        if rep is None:
            continue
        if rep.status_code == 200:
            ns = _notices(rep.json())
            v0 = ns[0].get(nom) if ns else None
            brut = json.dumps(v0, ensure_ascii=False)
            print("  [OK 200]  {:<34} exemple brut = {}".format(nom, brut[:90]))
        elif rep.status_code == 400:
            # Champ liste mais non requetable seul, ou incompatible : on note.
            print("  [400]     {:<34} rejete en requete isolee".format(nom))
        else:
            print("  [{}]     {:<34} {}".format(
                rep.status_code, nom, rep.text[:60]))
    print()
    print("  Lecture : si un champ 'winner/tender-value' renvoie une LISTE")
    print("  alignee sur winner-name -> montant PAR titulaire dispo (SPARQL")
    print("  devient optionnel). Si seul un total agrege existe -> garder")
    print("  SPARQL pour le detail par titulaire.")


def main():
    print("SONDE WINNER-STATUS v2 -- catalogue + structure + montant")
    print("Cible :", PUB or "attributions recentes (auto)")
    print("Dry-run :", DRYRUN)
    print()
    catalogue = etape_1_catalogue()
    notices = etape_2_structure()
    etape_3_montant(catalogue, notices)
    print()
    print("=" * 74)
    print("PROCHAINE ETAPE")
    print("=" * 74)
    print("  1. Ajouter winner-selection-status + winner-name aux `fields` du")
    print("     collecteur attributions (+ le champ montant retenu si dispo).")
    print("  2. Filtrer clos-nw hors des leads -> alerte 're-tender a surveiller'.")
    print("  3. Apparier par index si les listes ont meme longueur (etape 2).")
    sys.exit(0)


if __name__ == "__main__":
    main()
