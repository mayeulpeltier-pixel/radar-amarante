# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE SPARQL v2 (jetable) : titulaires via TED Open Data.
===============================================================================

BUT
---
Verifier si l'endpoint SPARQL public de l'Office des Publications
(https://publications.europa.eu/webapi/rdf/sparql) permet de recuperer, de
maniere STRUCTUREE, le(s) titulaire(s) d'une attribution TED : nom, montant,
devise. Si oui, on remplace le parsing PDF par regex (fragile) de
`ted_complet_attributions` par une requete propre.

v2 : la CONNECTIVITE etant confirmee (HTTP 200), on ajoute une etape
AUTO-SUFFISANTE (A) qui trouve elle-meme des attributions ayant un titulaire,
SANS qu'on fournisse de numero -> prouve la chaine ontologique de bout en bout
et REVELE le format reel des publication-number (a comparer a ceux de Radar).

Ontologie (confirmee depuis les requetes curees de data.ted.europa.eu) :
    ?tender epo:isSubmitedBy ?tenderer ; epo:hasFinancialOfferValue ?ov .
    ?ov epo:hasAmountValue ?montant ; epo:hasCurrency ?curUri .
    ?tenderer epo:playedBy / epo:hasLegalName ?nom .

ETAPES
------
  0. CONNECTIVITE (deja OK, garde comme garde-fou).
  A. ECHANTILLON : des attributions AVEC titulaire, sans filtre -> prouve la
     faisabilite et montre le format des publication-number.
  1-3. CIBLE (seulement si SONDE_PUB fourni) : notice, predicats, titulaires
     pour UN publication-number precis (utile pour caler le collecteur sur un
     avis reel de l'onglet attributions_radar).

USAGE
-----
    python sonde_sparql.py                      # etapes 0 + A (auto)
    SONDE_PUB="00123456-2024" python sonde_sparql.py   # + etapes 1-3 ciblees
    SONDE_SPARQL_DRYRUN=1 python sonde_sparql.py        # affiche les requetes

AUCUNE ECRITURE. Aucun secret. Sortie toujours en code 0.
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


ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
ACCEPT_JSON = "application/sparql-results+json"
TIMEOUT = 50
PUB = os.environ.get("SONDE_PUB", "").strip()
DRYRUN = os.environ.get("SONDE_SPARQL_DRYRUN") == "1"

PREFIXES = (
    "PREFIX adms: <http://www.w3.org/ns/adms#>\n"
    "PREFIX epo: <http://data.europa.eu/a4g/ontology#>\n"
    "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
)


def interroger(query, accept=ACCEPT_JSON):
    """POST la requete. Renvoie (statut, objet_json_ou_texte). Ne leve jamais."""
    try:
        rep = requests.post(
            ENDPOINT,
            data={"query": query, "format": accept},
            headers={"Accept": accept},
            timeout=TIMEOUT,
        )
    except Exception as e:
        return None, "ERREUR RESEAU / TIMEOUT : {}".format(e)
    corps = rep.text
    if "json" in accept:
        try:
            corps = rep.json()
        except Exception:
            corps = rep.text[:2000]
    return rep.status_code, corps


def lignes(data):
    if isinstance(data, dict):
        return data.get("results", {}).get("bindings", [])
    return []


def val(binding, cle):
    return (binding.get(cle) or {}).get("value", "")


# --------------------------------------------------------------------------
# REQUETES
# --------------------------------------------------------------------------
def q0_connectivite():
    return "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"


def qA_echantillon():
    # Sans filtre sur le publication-number : trouve DES attributions ayant un
    # titulaire. Prouve la chaine et montre le format des ?pn. LIMIT bas pour
    # borner le cout.
    return PREFIXES + (
        "SELECT ?pn ?tendererLegalName ?offerAmountValue ?currencyUri WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        "    ?tender a epo:Tender ;\n"
        "            epo:isSubmitedBy ?tenderer ;\n"
        "            epo:hasFinancialOfferValue ?ov .\n"
        "    ?ov epo:hasAmountValue ?offerAmountValue ;\n"
        "        epo:hasCurrency ?currencyUri .\n"
        "    ?tenderer epo:playedBy / epo:hasLegalName ?tendererLegalName .\n"
        "  }\n"
        "} LIMIT 5"
    )


def _variantes_pn(pub):
    """Formats plausibles d'un publication-number, pour absorber le decalage
    entre ce que Radar stocke (souvent 6 chiffres, ex "302871-2026") et le
    triplestore (8 chiffres zero-paddes, ex "00302871-2026")."""
    pub = str(pub or "").strip()
    variantes = {pub}
    m = re.match(r"0*(\d+)-(\d{4})$", pub)
    if m:
        num, an = m.group(1), m.group(2)
        variantes.add("{}-{}".format(num, an))            # sans zeros de tete
        variantes.add("{:08d}-{}".format(int(num), an))   # 8 chiffres zero-paddes
    return sorted(variantes)


def _filtre_pn(pub):
    vs = ", ".join('"%s"' % v for v in _variantes_pn(pub))
    return "    FILTER(STR(?pn) IN (%s))\n" % vs


def q1_notice(pub):
    return PREFIXES + (
        "SELECT ?g ?notice ?pn WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        + _filtre_pn(pub) +
        "  }\n"
        "} LIMIT 5"
    )


def q2_predicats(pub):
    return PREFIXES + (
        "SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        + _filtre_pn(pub) +
        "    ?s ?p ?o .\n"
        "  }\n"
        "} GROUP BY ?p ORDER BY DESC(?n) LIMIT 200"
    )


def q3_titulaires(pub):
    # Montant et devise OPTIONNELS : beaucoup d'attributions ne publient pas le
    # montant -> le titulaire doit sortir meme sans offre financiere.
    return PREFIXES + (
        "SELECT ?tendererLegalName ?offerAmountValue ?currencyUri WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        + _filtre_pn(pub) +
        "    ?tender a epo:Tender ;\n"
        "            epo:isSubmitedBy ?tenderer .\n"
        "    ?tenderer epo:playedBy / epo:hasLegalName ?tendererLegalName .\n"
        "    OPTIONAL {\n"
        "      ?tender epo:hasFinancialOfferValue ?offerValue .\n"
        "      ?offerValue epo:hasAmountValue ?offerAmountValue ;\n"
        "                  epo:hasCurrency ?currencyUri .\n"
        "    }\n"
        "  }\n"
        "} LIMIT 50"
    )


# --------------------------------------------------------------------------
# ORCHESTRATION
# --------------------------------------------------------------------------
def _titre(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70)


def _rapporter(statut, corps, apercu=120):
    print("HTTP:", statut)
    if isinstance(corps, str):
        print(corps[:1500])
        return
    bs = lignes(corps)
    print("resultats:", len(bs))
    for b in bs[:apercu]:
        print("  " + json.dumps({k: val(b, k) for k in b}, ensure_ascii=False))


def main():
    print("SONDE SPARQL v2 -- titulaires TED via", ENDPOINT)

    requetes = [
        ("0. CONNECTIVITE", q0_connectivite()),
        ("A. ECHANTILLON d'attributions AVEC titulaire (auto, sans numero)",
         qA_echantillon()),
    ]
    if PUB:
        requetes += [
            ("1. NOTICE par publication-number (" + PUB + ")", q1_notice(PUB)),
            ("2. PREDICATS du graphe de la notice (tous)", q2_predicats(PUB)),
            ("3. TITULAIRES (nom + montant + devise)", q3_titulaires(PUB)),
        ]

    if DRYRUN:
        print("\n[DRYRUN] requetes construites (non envoyees) :")
        for titre, q in requetes:
            _titre(titre)
            print(q)
        print("\n[DRYRUN] fin.")
        return 0

    for titre, q in requetes:
        _titre(titre)
        statut, corps = interroger(q)
        _rapporter(statut, corps)

    if not PUB:
        _titre("ETAPES CIBLEES 1-3 IGNOREES (optionnel)")
        print("Pour valider sur un avis PRECIS de l'onglet attributions_radar,")
        print('relancer avec SONDE_PUB="<publication-number>". Comparer d\'abord')
        print("le format attendu aux ?pn affiches en etape A ci-dessus.")

    print("\nFin de sonde.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
