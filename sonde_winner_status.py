# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE WINNER-STATUS v1 (jetable) : statut de selection du
lauréat (BT-142) exposé par l'API search TED ?
===============================================================================

BUT
---
Determiner si l'API search TED v3 renvoie le STATUT DE SELECTION DU LAUREAT
d'une attribution (eForms BT-142-LotResult, codelist `winner-selection-status`
du SDK OP-TED), et sous QUEL nom de champ. Trois valeurs officielles attendues :
    selec-w  = au moins un laureat choisi        (lead concurrent reel)
    clos-nw  = clos SANS laureat (infructueux)    (signal de re-publication)
    open-nw  = en cours, laureat pas encore choisi

Si l'API le fournit -> on peut sortir les infructueux des attributions et
transformer `clos-nw` en alerte "re-tender a surveiller", SANS parsing PDF ni
appel SPARQL supplementaire. Sinon -> on sait qu'il faut passer par la notice
XML complete ou par SPARQL (deja en place via sparql_titulaires).

METHODE (on ne PARIE pas sur le nom du champ, on le DECOUVRE)
------------------------------------------------------------
  0. CONNECTIVITE : une requete minimale connue-bonne (garde-fou).
  A. REVELATION : sur une attribution reelle, on tente plusieurs tactiques
     (sans `fields`, `fields=['*']`, base+candidats) et on DUMPE les cles
     reellement presentes -> revele les noms exposes + un eventuel lien XML.
  B. TEST CIBLE : chaque nom candidat est demande isolement dans `fields`.
     200 => champ accepte (on dumpe sa valeur brute) ; 400 => n'existe pas
     sous ce nom (meme logique de degradation de champ que le collecteur).
  C. REPLI NOTICE XML : si l'API search ne donne pas le statut mais fournit une
     URL XML (bloc `links`), on la recupere et on grep les marqueurs BT-142.
     On ne DEVINE aucune URL : si l'API n'en fournit pas, on s'arrete et on
     recommande SPARQL.

USAGE
-----
    python sonde_winner_status.py                       # decouverte auto
    SONDE_PUB="10759-2026" python sonde_winner_status.py # cibler une attribution
    SONDE_WINNER_DRYRUN=1 python sonde_winner_status.py  # affiche les requetes

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


# --- Endpoints (memes que ted_complet_v14.poster_ted, avec failover) ---------
ENDPOINTS = [
    "https://api.ted.europa.eu/v3/notices/search",
    "https://tedweb.api.ted.europa.eu/v3/notices/search",
]
TIMEOUT = 45
PUB = os.environ.get("SONDE_PUB", "").strip()
DRYRUN = os.environ.get("SONDE_WINNER_DRYRUN") == "1"

# Champs connus-bons (le collecteur d'attributions les demande deja).
CHAMPS_BASE = ["publication-number", "notice-title", "notice-type",
               "publication-date"]

# Types "avis d'attribution" (repris de ted_complet_attributions).
NOTICE_TYPES_ATTRIB = ["can-standard", "can-social", "can-tport"]

# Noms candidats pour le statut de selection du laureat. L'etape A (dump reel)
# reste la source de verite ; cette liste ne sert qu'a forcer des tests cibles
# si le dump ne suffit pas. Conventions kebab-case de l'API search TED.
CANDIDATS_CHAMP = [
    "winner-selection-status",
    "winner-chosen",
    "tender-result",
    "lot-result",
    "result-code",
    "award-status",
    "winner",
    "winner-name",
]

# Regex de reperage des cles interessantes dans une notice brute.
CLE_INTERESSANTE = re.compile(
    r"(winner|result|status|selec|award|tender|laureat)", re.IGNORECASE)

# Marqueurs BT-142 dans une notice XML eForms complete.
MARQUEURS_XML = [
    "winner-selection-status", "TenderResultCode", "WinnerChoosen",
    "WinnerChosen", "BT-142", "selec-w", "clos-nw", "open-nw",
]


def poster(corps):
    """POST vers TED avec bascule sur l'endpoint secondaire (calque de
    poster_ted). Un 4xx ne declenche PAS de bascule : c'est la requete qui est
    en cause, et on VEUT voir le 400 pour la logique de test de champ.
    Renvoie l'objet reponse, ou None si tous les endpoints echouent."""
    if DRYRUN:
        print("      [DRYRUN] corps =", json.dumps(corps, ensure_ascii=False))
        return None
    rep = None
    for i, url in enumerate(ENDPOINTS):
        dernier = (i == len(ENDPOINTS) - 1)
        try:
            rep = requests.post(url, json=corps, timeout=TIMEOUT)
        except requests.exceptions.RequestException as e:
            if dernier:
                print("      (reseau) tous endpoints injoignables : {}".format(
                    type(e).__name__))
                return None
            continue
        if rep.status_code >= 500 and not dernier:
            continue
        return rep
    return rep


def _notices(data):
    """Extrait la liste de notices quel que soit le nom de cle renvoye."""
    if not isinstance(data, dict):
        return []
    return data.get("notices") or data.get("results") or data.get("items") or []


def _query(pub=None):
    """Query language TED. Cible un publication-number si fourni, sinon toutes
    les attributions recentes (tri par date desc pour tomber sur du frais)."""
    if pub:
        return "publication-number IN ({})".format(pub)
    return "notice-type IN ({}) SORT BY publication-date DESC".format(
        " ".join(NOTICE_TYPES_ATTRIB))


def _corps(fields=None, pub=None, limit=5, omit_fields=False):
    c = {
        "query": _query(pub),
        "page": 1,
        "limit": limit,
        "scope": "ALL",            # les attributions ne sont pas "ACTIVE"
        "checkQuerySyntax": False,
        "paginationMode": "PAGE_NUMBER",
    }
    if not omit_fields:
        c["fields"] = fields if fields is not None else CHAMPS_BASE
    return c


def _val(v):
    """Aplati une valeur de champ TED (souvent list/dict multilingue) en texte
    court lisible pour l'affichage de sonde."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        # dict multilingue ou {value: ...} : on prend la 1re valeur scalaire.
        for x in v.values():
            s = _val(x)
            if s:
                return s
        return ""
    if isinstance(v, list):
        return ", ".join(s for s in (_val(x) for x in v) if s)
    return str(v)


def _liens_xml(notice):
    """Cherche recursivement une URL XML dans la notice (bloc 'links' ou autre).
    Ne DEVINE rien : renvoie seulement des URL reellement presentes."""
    trouves = []

    def _rec(o):
        if isinstance(o, str):
            if o.startswith("http") and ("xml" in o.lower()):
                trouves.append(o)
        elif isinstance(o, dict):
            for x in o.values():
                _rec(x)
        elif isinstance(o, list):
            for x in o:
                _rec(x)

    _rec(notice.get("links", notice))
    # dedup en gardant l'ordre
    vus, out = set(), []
    for u in trouves:
        if u not in vus:
            vus.add(u)
            out.append(u)
    return out


# ===========================================================================
# ETAPE 0 -- CONNECTIVITE
# ===========================================================================
def etape_0():
    print("=" * 74)
    print("ETAPE 0 -- CONNECTIVITE (garde-fou)")
    print("=" * 74)
    rep = poster(_corps(pub=PUB or None, limit=5))
    if rep is None:
        print("  Pas de reponse (dry-run ou reseau). Etapes suivantes ignorees.")
        return None
    print("  HTTP {} sur {}".format(rep.status_code, ENDPOINTS[0]))
    if rep.status_code != 200:
        print("  Corps :", rep.text[:400])
        return None
    notices = _notices(rep.json())
    print("  Attributions recuperees :", len(notices))
    if not notices:
        print("  Aucune attribution sur ce filtre : impossible de sonder la "
              "structure. Elargir la fenetre ou fournir SONDE_PUB.")
        return None
    for n in notices[:3]:
        print("    - {} | {} | {}".format(
            _val(n.get("publication-number")),
            _val(n.get("notice-type")),
            _val(n.get("notice-title"))[:60]))
    return notices


# ===========================================================================
# ETAPE A -- REVELATION DE LA STRUCTURE
# ===========================================================================
def etape_A():
    print()
    print("=" * 74)
    print("ETAPE A -- REVELATION : quels champs l'API renvoie-t-elle vraiment ?")
    print("=" * 74)
    tactiques = [
        ("sans clef 'fields'", _corps(pub=PUB or None, limit=3, omit_fields=True)),
        ("fields=['*']",       _corps(fields=["*"], pub=PUB or None, limit=3)),
        ("base + candidats",   _corps(fields=CHAMPS_BASE + CANDIDATS_CHAMP,
                                       pub=PUB or None, limit=3)),
    ]
    cles_vues = set()
    liens_xml = []
    for libelle, corps in tactiques:
        print("\n  Tactique : {}".format(libelle))
        rep = poster(corps)
        if rep is None:
            continue
        if rep.status_code != 200:
            print("    HTTP {} -> tactique rejetee. {}".format(
                rep.status_code, rep.text[:200]))
            continue
        notices = _notices(rep.json())
        if not notices:
            print("    200 mais 0 notice.")
            continue
        n0 = notices[0]
        cles = sorted(n0.keys())
        cles_vues.update(cles)
        print("    200. Cles de la 1re notice ({}) :".format(len(cles)))
        print("      ", ", ".join(cles))
        interessantes = [k for k in cles if CLE_INTERESSANTE.search(k)]
        if interessantes:
            print("    >>> Cles a inspecter :", interessantes)
            for k in interessantes:
                print("          {} = {}".format(k, _val(n0.get(k))[:80]))
        xml = _liens_xml(n0)
        if xml:
            liens_xml.extend(xml)
            print("    Lien(s) XML detecte(s) :", xml[:2])
    print("\n  Bilan etape A :")
    print("    Total cles distinctes vues :", len(cles_vues))
    candidates = sorted(k for k in cles_vues if CLE_INTERESSANTE.search(k))
    print("    Cles candidates (statut/laureat) :", candidates or "AUCUNE")
    return candidates, liens_xml


# ===========================================================================
# ETAPE B -- TEST CIBLE DES NOMS CANDIDATS
# ===========================================================================
def etape_B(candidates_decouvertes):
    print()
    print("=" * 74)
    print("ETAPE B -- TEST CIBLE : chaque nom candidat, accepte ou rejete ?")
    print("=" * 74)
    # On teste l'union des candidats predefinis et de ceux decouverts en A.
    a_tester = list(dict.fromkeys(CANDIDATS_CHAMP + list(candidates_decouvertes)))
    acceptes = {}
    for nom in a_tester:
        corps = _corps(fields=CHAMPS_BASE + [nom], pub=PUB or None, limit=3)
        rep = poster(corps)
        if rep is None:
            continue
        if rep.status_code == 200:
            notices = _notices(rep.json())
            valeurs = [_val(n.get(nom)) for n in notices]
            renseigne = any(v for v in valeurs)
            acceptes[nom] = valeurs
            print("  [OK 200]  {:<26} renseigne={} valeurs={}".format(
                nom, renseigne, [v[:30] for v in valeurs] or "[]"))
        elif rep.status_code == 400:
            print("  [400]     {:<26} n'existe pas sous ce nom".format(nom))
        else:
            print("  [{}]     {:<26} {}".format(
                rep.status_code, nom, rep.text[:80]))
    return acceptes


# ===========================================================================
# ETAPE C -- REPLI NOTICE XML COMPLETE
# ===========================================================================
def etape_C(liens_xml):
    print()
    print("=" * 74)
    print("ETAPE C -- REPLI : le statut est-il dans la notice XML complete ?")
    print("=" * 74)
    if not liens_xml:
        print("  Aucune URL XML fournie par l'API search (on ne DEVINE pas).")
        print("  => Si l'etape B n'a rien donne : passer par SPARQL (deja en")
        print("     place, sparql_titulaires) ou la doc developpeur de l'API TED.")
        return
    url = liens_xml[0]
    print("  Recuperation :", url)
    if DRYRUN:
        print("  [DRYRUN] fetch ignore.")
        return
    try:
        rep = requests.get(url, timeout=TIMEOUT)
    except requests.exceptions.RequestException as e:
        print("  (reseau) XML injoignable :", type(e).__name__)
        return
    print("  HTTP {} | {} octets".format(rep.status_code, len(rep.content)))
    if rep.status_code != 200:
        return
    txt = rep.text
    for m in MARQUEURS_XML:
        idx = txt.find(m)
        if idx != -1:
            extrait = txt[max(0, idx - 40): idx + 80].replace("\n", " ")
            print("  >>> marqueur '{}' trouve : ...{}...".format(m, extrait))
    if not any(m in txt for m in MARQUEURS_XML):
        print("  Aucun marqueur BT-142 dans ce XML (structure a re-examiner).")


def main():
    print("SONDE WINNER-STATUS (BT-142) -- API search TED")
    print("Cible :", PUB or "attributions recentes (auto)")
    print("Dry-run :", DRYRUN)
    print()
    notices = etape_0()
    if notices is None and not DRYRUN:
        # Sans echantillon, A/B/C ne peuvent pas conclure ; on s'arrete proprement.
        print("\nArret : pas d'echantillon exploitable.")
        sys.exit(0)
    candidates, liens_xml = etape_A()
    acceptes = etape_B(candidates)
    # Repli XML seulement si aucun champ search renseigne n'a donne le statut.
    statut_trouve = any(vs and any(v for v in vs) for vs in acceptes.values())
    etape_C(liens_xml if not statut_trouve else [])
    print()
    print("=" * 74)
    print("VERDICT")
    print("=" * 74)
    if statut_trouve:
        print("  L'API search EXPOSE un champ de statut/laureat renseigne.")
        print("  Champs utiles :", [k for k, v in acceptes.items()
                                     if v and any(x for x in v)])
        print("  Prochaine etape : caler le collecteur attributions sur ce champ")
        print("  (sortir clos-nw des leads, transformer en alerte re-tender).")
    else:
        print("  L'API search NE fournit pas de statut laureat exploitable ici.")
        print("  Chemin recommande : notice XML (etape C) ou SPARQL (en place).")
    sys.exit(0)


if __name__ == "__main__":
    main()
