# -*- coding: utf-8 -*-
"""
SONDE DE DIAGNOSTIC -- ALERTES VOYAGEURS FCDO (UK).
====================================================

POURQUOI UNE SONDE AVANT LE COLLECTEUR (principe du projet)
-----------------------------------------------------------
On n'ecrit jamais un collecteur avant d'avoir VU la structure reelle des
donnees. Cette sonde interroge l'API GOV.UK Content (FCDO), affiche la
structure brute, et ne prend AUCUNE decision de collecte. Elle repond a trois
questions avant qu'on code quoi que ce soit :

  1. l'index des pays est-il bien sous `links.children[]` ? combien de pays ?
  2. dans une fiche pays, ou vit le NIVEAU d'alerte, et sous quelle forme ?
  3. l'API expose-t-elle une date de derniere mise a jour exploitable pour
     detecter un CHANGEMENT (le vrai signal) d'un run a l'autre ?

CE QU'ON SAIT DEJA (verifie le 23/07/2026, docs officielles)
------------------------------------------------------------
  - API publique, SANS authentification, SANS scraping. Base :
    https://www.gov.uk/api/content
  - Index : /foreign-travel-advice -> `links.children[]`, ~226 pays.
  - Par pays : /foreign-travel-advice/<slug> -> ContentItem avec `details`
    (contient alert_status et change_description), `updated_at`,
    `public_updated_at`, et un `withdrawn_notice` eventuel.
  - Contenu sous Open Government Licence v3.0 : reutilisation LEGALE explicite.
    C'est ce qui rend cette source sure pour un projet a visee commerciale,
    la ou LinkedIn/Bloomberg/scraping ne le seraient pas.
  - Corpus complet ~4 min a 1 req/sec ; pas de rate limit au-dela du fair use.

CONTRAINTE D'ENVIRONNEMENT
--------------------------
Le bac a sable de developpement bloque gov.uk au pare-feu (seuls quelques
domaines sont autorises). Cette sonde est donc faite pour tourner LA OU le
reseau est ouvert : en local chez toi, ou dans un job GitHub Actions dedie.
Elle degrade proprement si l'acces est refuse (message clair, pas de trace).

USAGE
-----
    python sonde_alertes.py                 # 3 pays de la zone Amarante
    python sonde_alertes.py mali niger      # slugs precis
    RADAR_SONDE_INDEX=1 python sonde_alertes.py   # + dump de l'index complet
"""

import json
import os
import sys
import urllib.request
import urllib.error


BASE = "https://www.gov.uk/api/content/foreign-travel-advice"
UA = "radar-amarante-sonde/1.0 (business development; contact via Amarante)"

# OPTION A : la liste surveillee est DERIVEE du perimetre commercial du radar
# (ted.CODES_PAYS_SUIVIS), pas maintenue a part. Une seule source de verite :
# ajouter un pays au perimetre l'ajoute a la veille d'alertes. Le perimetre
# couvre deja Afrique, Amerique latine, MENA et Asie centrale (123 pays).
#
# Le point delicat est la correspondance ISO3 -> slug FCDO. Le FCDO nomme ses
# URL en anglais, sans regle mecanique fiable (COD -> democratic-republic-of-
# the-congo, pas congo-...). Plutot que de DEVINER ces slugs, la sonde les
# DECOUVRE : elle lit l'index, en extrait les slugs reels, et n'a plus qu'a les
# associer aux pays du perimetre par leur nom. Aucune table a maintenir a la
# main, aucune supposition qui casserait le jour ou le FCDO renomme une URL.


def _perimetre_iso3():
    """Les ISO3 suivis par le radar. Import tardif : la sonde reste utilisable
    meme si le coeur n'est pas importable (elle sonde alors une zone par defaut)."""
    try:
        import ted_complet_v14 as ted
        return list(ted.CODES_PAYS_SUIVIS)
    except Exception:
        return []


def _noms_perimetre():
    """ISO3 -> nom (FR) depuis le dashboard, pour rapprocher des slugs FCDO."""
    try:
        import radar_dashboard as dash
        return {iso3: nom for iso3, (nom, _zone) in dash.ZONE_PAR_ISO3.items()}
    except Exception:
        return {}


def _get(url):
    """GET JSON. Leve pour que l'appelant distingue les cas (403 pare-feu,
    404 slug inconnu, reseau). La sonde a le droit de lever : elle diagnostique."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def sonder_index():
    """Structure de l'index : ou sont les pays, combien, quels slugs."""
    print("=" * 66)
    print("1. INDEX DES PAYS  ({})".format(BASE))
    print("=" * 66)
    try:
        data = _get(BASE)
    except urllib.error.HTTPError as e:
        print("  HTTP {} -- probablement le pare-feu du bac a sable.".format(e.code))
        print("  Relance cette sonde la ou gov.uk est joignable (local / CI).")
        return None
    except Exception as e:
        print("  Reseau indisponible : {}".format(e))
        return None

    print("  cles racine :", ", ".join(sorted(data.keys())))
    children = (data.get("links") or {}).get("children", [])
    print("  pays trouves sous links.children[] :", len(children))
    if children:
        c = children[0]
        print("  exemple, cles d'un enfant :", ", ".join(sorted(c.keys())))
        for champ in ("title", "base_path", "api_path", "public_updated_at"):
            print("    {:18} {}".format(champ, c.get(champ)))

    # Croisement avec le perimetre radar : combien de nos pays ont un slug FCDO,
    # et lesquels manquent (a mapper a la main, ou sans fiche FCDO du tout).
    slugs = {}
    for c in children:
        bp = c.get("base_path", "") or ""
        slug = bp.rsplit("/", 1)[-1] if bp else ""
        titre = (c.get("title") or "").strip()
        if slug:
            slugs[slug] = titre
    perimetre = _perimetre_iso3()
    noms = _noms_perimetre()
    if perimetre:
        print("\n  -- croisement avec le perimetre radar ({} pays) --".format(
            len(perimetre)))
        print("    slugs FCDO disponibles au total :", len(slugs))
        # Rapprochement simple par nom normalise (indicatif : le collecteur
        # figera une table ISO3->slug a partir de ce que la sonde revele).
        def _norm(s):
            return "".join(ch for ch in s.lower() if ch.isalnum())
        index_par_titre = {_norm(t): sl for sl, t in slugs.items()}
        trouves, absents = [], []
        for iso3 in sorted(perimetre):
            nom = noms.get(iso3, "")
            sl = index_par_titre.get(_norm(nom))
            (trouves if sl else absents).append(
                "{}->{}".format(iso3, sl) if sl else iso3)
        print("    rapproches par nom FR direct :", len(trouves))
        print("    a mapper (nom FR != titre FCDO EN) :", len(absents))
        if absents:
            print("      ", ", ".join(absents[:40]))
            print("      (normal : noms FR vs EN. Le collecteur figera la table.)")

    if os.environ.get("RADAR_SONDE_INDEX") == "1":
        print("\n  --- tous les slugs FCDO ---")
        for sl in sorted(slugs):
            print("    {:42} {}".format(sl, slugs[sl]))
    return children


def sonder_pays(slug):
    """Structure d'une fiche pays : ou vit le niveau d'alerte, la date de maj,
    la description du changement. C'est LA question qui conditionne le parsing."""
    url = "{}/{}".format(BASE, slug)
    print("\n" + "=" * 66)
    print("2. FICHE PAYS  ({})".format(slug))
    print("=" * 66)
    try:
        data = _get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("  404 : slug inconnu. Verifie l'orthographe (ex. 'chad' pas 'tchad').")
        else:
            print("  HTTP {} -- pare-feu probable si en bac a sable.".format(e.code))
        return None
    except Exception as e:
        print("  Reseau indisponible : {}".format(e))
        return None

    # Metadonnees de fraicheur : candidates pour detecter un CHANGEMENT.
    print("  -- fraicheur (pour detecter un changement d'un run a l'autre) --")
    for champ in ("public_updated_at", "updated_at", "first_published_at"):
        print("    {:20} {}".format(champ, data.get(champ)))
    wn = data.get("withdrawn_notice")
    if wn:
        print("    withdrawn_notice     ", wn)

    # Le coeur : le champ details, ou vit le niveau d'alerte.
    details = data.get("details") or {}
    print("\n  -- details : cles disponibles --")
    print("    ", ", ".join(sorted(details.keys())) or "(vide)")

    # alert_status : documente comme une CHAINE JSON a re-parser.
    brut = details.get("alert_status")
    print("\n  -- alert_status (brut) --")
    print("    type :", type(brut).__name__)
    print("    valeur :", repr(brut)[:300])
    if isinstance(brut, str) and brut.strip().startswith(("[", "{")):
        try:
            print("    -> re-parse JSON :", json.loads(brut))
        except json.JSONDecodeError:
            print("    -> pas du JSON valide, a traiter comme texte")

    # change_description : le libelle du dernier changement, utile pour le lead.
    for champ in ("change_description",):
        if champ in details:
            print("\n  -- {} --".format(champ))
            print("    ", repr(details[champ])[:300])

    # Historique : certains schemas exposent une liste de changements datee.
    for champ in ("change_history", "parts"):
        if champ in details:
            v = details[champ]
            print("\n  -- {} ({} element(s)) --".format(champ, len(v) if isinstance(v, list) else "?"))
            if isinstance(v, list) and v:
                print("    premier element, cles :",
                      ", ".join(sorted(v[0].keys())) if isinstance(v[0], dict) else type(v[0]).__name__)
                print("    ", repr(v[0])[:300])
    return data


def main():
    slugs = [a.strip().lower() for a in sys.argv[1:] if a.strip()]

    print("SONDE ALERTES VOYAGEURS FCDO -- diagnostic seul, aucune ecriture.")
    print("Liste surveillee DERIVEE du perimetre radar (option A).\n")
    enfants = sonder_index()

    # Sans slugs explicites en argument, on sonde quelques pays de la zone pour
    # reveler la structure d'une fiche. Le collecteur, lui, parcourra tout le
    # perimetre ; ici on veut juste voir la FORME des donnees.
    if not slugs:
        slugs = ["mali", "niger", "colombia"]   # Sahel + Amerique latine

    for slug in slugs:
        sonder_pays(slug)

    print("\n" + "=" * 66)
    print("FIN DE SONDE. Prochaine etape : ecrire alertes_voyageurs.py + tests,")
    print("en figeant la table ISO3->slug d'apres le croisement ci-dessus et en")
    print("parsant alert_status selon ce que la sonde a REELLEMENT montre.")
    print("=" * 66)


if __name__ == "__main__":
    main()
