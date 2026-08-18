# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE PAYS TITULAIRE (jetable) : le pays (et l'UBO) du
titulaire d'attribution sont-ils recuperables en SPARQL ?
===============================================================================

CONTEXTE
--------
Le repo ted-rdf-mapping-eforms (mapping officiel eForms -> ePO) montre que le
pays du titulaire suit la chaine :
    Organization -> cccev:registeredAddress -> Address -> epo:hasCountryCode
et que le code pays est un IRI se terminant par l'ISO3 (ex .../country/GRC).
Le bonus `epo:hasBeneficialOwner` expose le beneficiaire effectif (UBO).

MAIS le mapping decrit la PRODUCTION du RDF, pas ce que l'endpoint LIVE renvoie
(version ePO possiblement differente). On CONFIRME donc avant tout cablage, sans
deviner : la sonde teste PLUSIEURS chemins et montre lequel repond.

BUT
---
  1. Le pays du titulaire est-il renseigne, et par quel chemin :
        A. org -> cccev:registeredAddress -> hasCountryCode   (attendu)
        B. org -> epo:hasCountryCode                          (direct)
        C. org -> epo:hasNationality
     ...et sous quelle forme (IRI -> ISO3) ? Bonus : NUTS via l'adresse.
  2. (bonus, pour cadrer un futur chantier) L'UBO est-il expose
     (epo:hasBeneficialOwner -> nom + pays) ?

Si un chemin repond -> on cable `pays_titulaire` en DETERMINISTE (sans LLM).

USAGE
-----
    SONDE_PN="10759-2026" python sonde_pays_titulaire.py   # cibler une attrib
    python sonde_pays_titulaire.py                          # defaut 10759-2026
    SONDE_PAYS_DRYRUN=1 python sonde_pays_titulaire.py      # affiche les requetes

AUCUNE ECRITURE. Aucun secret. Aucun LLM. Sortie toujours en code 0.
"""

import os
import re
import sys

try:
    import requests
except Exception:
    requests = None


ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
ACCEPT_JSON = "application/sparql-results+json"
TIMEOUT = 45
PN = os.environ.get("SONDE_PN", "10759-2026").strip()
DRYRUN = os.environ.get("SONDE_PAYS_DRYRUN") == "1"

PREFIXES = (
    "PREFIX epo: <http://data.europa.eu/a4g/ontology#>\n"
    "PREFIX cccev: <http://data.europa.eu/m8g/>\n"
)


def _variantes_pn(pn):
    """Formats plausibles (repris de sparql_titulaires) : tel quel, sans zeros
    de tete, zero-padde a 8 chiffres (format du triplestore)."""
    pn = str(pn or "").strip()
    variantes = {pn}
    m = re.match(r"0*(\d+)-(\d{4})$", pn)
    if m:
        num, an = m.group(1), m.group(2)
        variantes.add("{}-{}".format(num, an))
        variantes.add("{:08d}-{}".format(int(num), an))
    return sorted(v for v in variantes if v)


def _iso3(uri):
    """'.../authority/country/GRC' -> 'GRC'. Tel quel si non-URI."""
    s = str(uri or "").strip()
    return s.rsplit("/", 1)[-1] if "/" in s else s


def _filtre_pn():
    return ", ".join('"%s"' % v for v in _variantes_pn(PN))


def _interroger(query):
    if DRYRUN:
        print("      [DRYRUN] query =\n" + "\n".join(
            "        " + l for l in query.splitlines()))
        return None
    if requests is None:
        print("      requests indisponible")
        return None
    try:
        rep = requests.post(
            ENDPOINT,
            data={"query": query, "format": ACCEPT_JSON},
            headers={"Accept": ACCEPT_JSON},
            timeout=TIMEOUT,
        )
        rep.raise_for_status()
        return rep.json()
    except Exception as e:
        print("      (reseau/HTTP) echec :", type(e).__name__, str(e)[:120])
        return None


def _bindings(data):
    if not isinstance(data, dict):
        return []
    return data.get("results", {}).get("bindings", [])


def _v(binding, cle):
    return binding.get(cle, {}).get("value", "")


# ===========================================================================
# ETAPE 0 -- CONNECTIVITE : le PN a-t-il un titulaire dans le triplestore ?
# ===========================================================================
def etape_0():
    print("=" * 74)
    print("ETAPE 0 -- CONNECTIVITE (le PN existe-t-il avec un titulaire ?)")
    print("=" * 74)
    q = PREFIXES + (
        "SELECT ?nom WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ; epo:hasNoticePublicationNumber ?pn .\n"
        "    FILTER(STR(?pn) IN (%s))\n"
        "    ?tender a epo:Tender ; epo:isSubmitedBy ?tenderer .\n"
        "    ?tenderer epo:playedBy / epo:hasLegalName ?nom .\n"
        "  }\n"
        "} LIMIT 25" % _filtre_pn()
    )
    data = _interroger(q)
    if data is None:
        return None
    noms = sorted({_v(b, "nom") for b in _bindings(data) if _v(b, "nom")})
    print("  Titulaire(s) trouve(s) :", len(noms))
    for n in noms[:8]:
        print("    -", n)
    if not noms:
        print("  Aucun titulaire pour ce PN dans le triplestore.")
        print("  -> fournir un autre SONDE_PN (attribution recente avec titulaire).")
    return noms


# ===========================================================================
# ETAPE A -- PAYS DU TITULAIRE : quel chemin repond ?
# ===========================================================================
def etape_A():
    print()
    print("=" * 74)
    print("ETAPE A -- PAYS DU TITULAIRE (3 chemins testes ensemble)")
    print("=" * 74)
    q = PREFIXES + (
        "SELECT ?nom ?paysAdr ?nuts ?paysDirect ?nat WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ; epo:hasNoticePublicationNumber ?pn .\n"
        "    FILTER(STR(?pn) IN (%s))\n"
        "    ?tender a epo:Tender ; epo:isSubmitedBy ?tenderer .\n"
        "    ?tenderer epo:playedBy ?org .\n"
        "    ?org epo:hasLegalName ?nom .\n"
        "    OPTIONAL {\n"
        "      ?org cccev:registeredAddress ?adr .\n"
        "      ?adr epo:hasCountryCode ?paysAdr .\n"
        "      OPTIONAL { ?adr epo:hasNutsCode ?nuts . }\n"
        "    }\n"
        "    OPTIONAL { ?org epo:hasCountryCode ?paysDirect . }\n"
        "    OPTIONAL { ?org epo:hasNationality ?nat . }\n"
        "  }\n"
        "} LIMIT 50" % _filtre_pn()
    )
    data = _interroger(q)
    if data is None:
        return
    bs = _bindings(data)
    print("  Lignes renvoyees :", len(bs))
    compte = {"paysAdr (registeredAddress)": 0, "paysDirect (org)": 0,
              "nat (hasNationality)": 0, "nuts": 0}
    for b in bs:
        if _v(b, "paysAdr"):
            compte["paysAdr (registeredAddress)"] += 1
        if _v(b, "paysDirect"):
            compte["paysDirect (org)"] += 1
        if _v(b, "nat"):
            compte["nat (hasNationality)"] += 1
        if _v(b, "nuts"):
            compte["nuts"] += 1
    print("\n  Taux de remplissage par chemin :")
    for k, n in compte.items():
        print("    {:<32} {}/{}".format(k, n, len(bs)))
    print("\n  Exemples (nom -> pays ISO3 selon chemin) :")
    for b in bs[:8]:
        print("    {:<34} | adr={} direct={} nat={} nuts={}".format(
            _v(b, "nom")[:34],
            _iso3(_v(b, "paysAdr")) or "-",
            _iso3(_v(b, "paysDirect")) or "-",
            _iso3(_v(b, "nat")) or "-",
            _iso3(_v(b, "nuts")) or "-"))


# ===========================================================================
# ETAPE B -- BONUS UBO (beneficiaire effectif) pour cadrer un futur chantier
# ===========================================================================
def etape_B():
    print()
    print("=" * 74)
    print("ETAPE B -- BONUS : UBO (beneficiaire effectif) expose ?")
    print("=" * 74)
    q = PREFIXES + (
        "SELECT ?nomOrg ?ubo ?paysUbo WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ; epo:hasNoticePublicationNumber ?pn .\n"
        "    FILTER(STR(?pn) IN (%s))\n"
        "    ?tender a epo:Tender ; epo:isSubmitedBy ?tenderer .\n"
        "    ?tenderer epo:playedBy ?org .\n"
        "    ?org epo:hasLegalName ?nomOrg .\n"
        "    OPTIONAL {\n"
        "      ?org epo:hasBeneficialOwner ?person .\n"
        "      OPTIONAL { ?person epo:hasLegalName ?ubo . }\n"
        "      OPTIONAL { ?person epo:hasCountryCode ?paysUbo . }\n"
        "    }\n"
        "  }\n"
        "} LIMIT 25" % _filtre_pn()
    )
    data = _interroger(q)
    if data is None:
        return
    bs = _bindings(data)
    avec_ubo = [b for b in bs if _v(b, "ubo") or _v(b, "paysUbo")]
    print("  Lignes :", len(bs), "| avec UBO renseigne :", len(avec_ubo))
    for b in avec_ubo[:6]:
        print("    org={} | ubo={} | pays_ubo={}".format(
            _v(b, "nomOrg")[:30], _v(b, "ubo")[:30] or "-",
            _iso3(_v(b, "paysUbo")) or "-"))
    if not avec_ubo:
        print("  UBO non renseigne sur cette attribution (souvent absent hors")
        print("  seuils reglementaires) : a re-tester sur d'autres PN avant de")
        print("  conclure sur la disponibilite generale.")


def main():
    print("SONDE PAYS TITULAIRE -- endpoint SPARQL ePO")
    print("PN cible :", PN, "| Dry-run :", DRYRUN)
    print()
    noms = etape_0()
    if noms is None and not DRYRUN:
        print("\nArret : endpoint injoignable.")
        sys.exit(0)
    etape_A()
    etape_B()
    print()
    print("=" * 74)
    print("LECTURE")
    print("=" * 74)
    print("  Le chemin le plus rempli = celui a cabler dans sparql_titulaires")
    print("  pour remplir `pays_titulaire` en deterministe (sans LLM). Le pays")
    print("  est un IRI .../country/XXX -> ISO3 par rsplit('/'), deja aligne")
    print("  avec pays_reference. Si aucun chemin ne repond : re-tester d'autres")
    print("  PN avant de conclure.")
    sys.exit(0)


if __name__ == "__main__":
    main()
