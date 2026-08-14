# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE SPARQL (jetable) : titulaires via TED Open Data.
===============================================================================

BUT
---
Verifier si l'endpoint SPARQL public de l'Office des Publications
(https://publications.europa.eu/webapi/rdf/sparql) permet de recuperer, de
maniere STRUCTUREE, le(s) titulaire(s) d'une attribution TED : nom, montant,
devise. Si oui, on pourra remplacer le parsing PDF par regex (fragile) de
`ted_complet_attributions` par une requete propre.

L'ontologie utilisee (confirmee depuis les requetes curees de data.ted.europa.eu) :
    ?notice  a epo:Notice ; epo:hasNoticePublicationNumber ?pn .
    ?tender  a epo:Tender ;
             epo:isSubmitedBy ?tenderer ;              # (orthographe ePO exacte)
             epo:hasFinancialOfferValue ?offerValue .
    ?offerValue epo:hasAmountValue ?amount ; epo:hasCurrency ?curUri .
    ?tenderer epo:playedBy / epo:hasLegalName ?nom .

CE QUE CETTE SONDE ETABLIT, ET RIEN D'AUTRE
-------------------------------------------
  0. CONNECTIVITE : l'endpoint repond-il, et dans quel format.
  1. NOTICE : trouve-t-on la notice par son publication-number (le champ que
     Radar stocke deja pour chaque attribution).
  2. STRUCTURE : quels predicats existent dans le graphe de la notice -> revele
     la forme reelle (au cas ou l'award/winner differe du modele ci-dessus).
  3. TITULAIRES : la requete ciblee renvoie-t-elle nom + montant + devise.

USAGE
-----
Fournir un VRAI numero d'attribution TED via la variable SONDE_PUB (en prendre
un dans l'onglet attributions_radar, colonne publication-number). Exemple :
    SONDE_PUB="00123456-2024" python sonde_sparql.py

Mode hors-ligne (affiche les requetes sans les envoyer, pour relecture) :
    SONDE_SPARQL_DRYRUN=1 python sonde_sparql.py

AUCUNE ECRITURE. Aucun secret. Sortie toujours en code 0 (c'est une sonde).
"""

import json
import os
import sys

try:
    import requests
except ImportError:
    print("requests indisponible : pip install requests")
    sys.exit(0)


ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
ACCEPT_JSON = "application/sparql-results+json"
TIMEOUT = 45
PUB = os.environ.get("SONDE_PUB", "").strip()
DRYRUN = os.environ.get("SONDE_SPARQL_DRYRUN") == "1"

PREFIXES = (
    "PREFIX adms: <http://www.w3.org/ns/adms#>\n"
    "PREFIX epo: <http://data.europa.eu/a4g/ontology#>\n"
    "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
)


def interroger(query, accept=ACCEPT_JSON):
    """POST la requete a l'endpoint. Renvoie (statut, objet_json_ou_texte).
    Ne leve jamais : une sonde rapporte, elle ne casse pas."""
    try:
        rep = requests.post(
            ENDPOINT,
            data={"query": query, "format": accept},
            headers={"Accept": accept},
            timeout=TIMEOUT,
        )
    except Exception as e:
        return None, "ERREUR RESEAU : {}".format(e)
    corps = rep.text
    if "json" in accept:
        try:
            corps = rep.json()
        except Exception:
            corps = rep.text[:2000]
    return rep.status_code, corps


def lignes(data):
    """bindings d'un resultat SELECT."""
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


def q1_notice(pub):
    return PREFIXES + (
        "SELECT ?g ?notice WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        '    FILTER(STR(?pn) = "%s")\n'
        "  }\n"
        "} LIMIT 5" % pub
    )


def q2_predicats(pub):
    return PREFIXES + (
        "SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        '    FILTER(STR(?pn) = "%s")\n'
        "    ?s ?p ?o .\n"
        "  }\n"
        "} GROUP BY ?p ORDER BY DESC(?n) LIMIT 200" % pub
    )


def q3_titulaires(pub):
    return PREFIXES + (
        "SELECT ?tendererLegalName ?offerAmountValue ?currency WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        '    FILTER(STR(?pn) = "%s")\n'
        "    ?tender a epo:Tender ;\n"
        "            epo:isSubmitedBy ?tenderer ;\n"
        "            epo:hasFinancialOfferValue ?offerValue .\n"
        "    ?offerValue epo:hasAmountValue ?offerAmountValue ;\n"
        "                epo:hasCurrency ?currencyUri .\n"
        "    ?tenderer epo:playedBy / epo:hasLegalName ?tendererLegalName .\n"
        "  }\n"
        '  OPTIONAL { ?currencyUri skos:prefLabel ?currency . FILTER(lang(?currency) = "en") }\n'
        "} LIMIT 50" % pub
    )


# --------------------------------------------------------------------------
# ORCHESTRATION
# --------------------------------------------------------------------------
def _titre(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70)


def _rapporter(statut, corps, apercu_lignes=8):
    print("HTTP:", statut)
    if isinstance(corps, str):
        print(corps[:1500])
        return
    bs = lignes(corps)
    print("resultats:", len(bs))
    for b in bs[:apercu_lignes]:
        print("  " + json.dumps({k: val(b, k) for k in b}, ensure_ascii=False))


def main():
    print("SONDE SPARQL -- titulaires TED via", ENDPOINT)

    requetes = [
        ("0. CONNECTIVITE", q0_connectivite()),
    ]
    if PUB:
        requetes += [
            ("1. NOTICE par publication-number (" + PUB + ")", q1_notice(PUB)),
            ("2. PREDICATS du graphe de la notice", q2_predicats(PUB)),
            ("3. TITULAIRES (nom + montant + devise)", q3_titulaires(PUB)),
        ]

    if DRYRUN:
        print("\n[DRYRUN] requetes construites (non envoyees) :")
        for titre, q in requetes:
            _titre(titre)
            print(q)
        print("\n[DRYRUN] fin. Aucun appel reseau.")
        return 0

    for titre, q in requetes:
        _titre(titre)
        statut, corps = interroger(q)
        _rapporter(statut, corps)

    if not PUB:
        _titre("ETAPES 1-3 IGNOREES")
        print("Fournir SONDE_PUB=<publication-number d'une attribution> pour")
        print("tester la recuperation des titulaires. Ex : SONDE_PUB=\"00123456-2024\"")

    print("\nFin de sonde.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
