# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Titulaires d'attribution via SPARQL (TED Open Data).
===============================================================================

Recupere le(s) titulaire(s) d'une attribution TED (nom, montant, devise) de
maniere STRUCTUREE, en interrogeant l'endpoint SPARQL public de l'Office des
Publications. Remplace, quand il repond, le parsing PDF par regex de
`ted_complet_attributions` (fragile sur les noms/montants).

Role : SPARQL PRIORITAIRE, PDF EN SECOURS. Enrichissement A LA COLLECTE.
Active par le flag RADAR_SPARQL_TITULAIRES=1 (defaut OFF le temps de valider
en debug ; tant qu'il est a 0, le collecteur garde son comportement actuel).

Ontologie confirmee par sonde (etape A, HTTP 200, donnees reelles) :
    ?tender epo:isSubmitedBy ?tenderer ; epo:hasFinancialOfferValue ?ov .
    ?ov epo:hasAmountValue ?montant ; epo:hasCurrency ?devise .
    ?tenderer epo:playedBy / epo:hasLegalName ?nom .

Garde-fous : le format du publication-number est normalise (le triplestore
utilise 8 chiffres zero-paddes, Radar en stocke souvent 6) ; un disjoncteur
coupe SPARQL pour le reste du run apres trop d'echecs reseau consecutifs, si
bien qu'un endpoint lent/mort fait retomber proprement sur le PDF.
"""

import os
import re

try:
    import requests
except Exception:                    # pragma: no cover
    requests = None


ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
ACCEPT_JSON = "application/sparql-results+json"
TIMEOUT = 45
ACTIF = os.environ.get("RADAR_SPARQL_TITULAIRES", "0") == "1"

PREFIXES = "PREFIX epo: <http://data.europa.eu/a4g/ontology#>\n"

# Disjoncteur : au-dela de MAX_ECHECS erreurs reseau d'affilee, on cesse
# d'interroger SPARQL pour ce run (retombee sur le PDF).
MAX_ECHECS = 5
_ETAT = {"echecs": 0, "coupe": False}


def _variantes_pn(pn):
    """Formats plausibles d'un publication-number : tel quel, sans zeros de
    tete, et zero-padde a 8 chiffres (format du triplestore)."""
    pn = str(pn or "").strip()
    variantes = {pn}
    m = re.match(r"0*(\d+)-(\d{4})$", pn)
    if m:
        num, an = m.group(1), m.group(2)
        variantes.add("{}-{}".format(num, an))
        variantes.add("{:08d}-{}".format(int(num), an))
    return sorted(v for v in variantes if v)


def _code_devise(uri):
    """'.../authority/currency/RON' -> 'RON'. Renvoie tel quel si non-URI."""
    s = str(uri or "").strip()
    if "/" in s:
        return s.rsplit("/", 1)[-1]
    return s


def requete(pn):
    vs = ", ".join('"%s"' % v for v in _variantes_pn(pn))
    return PREFIXES + (
        "SELECT ?nom ?montant ?devise WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        "    FILTER(STR(?pn) IN (%s))\n"
        "    ?tender a epo:Tender ;\n"
        "            epo:isSubmitedBy ?tenderer .\n"
        "    ?tenderer epo:playedBy / epo:hasLegalName ?nom .\n"
        "    OPTIONAL {\n"
        "      ?tender epo:hasFinancialOfferValue ?ov .\n"
        "      ?ov epo:hasAmountValue ?montant ;\n"
        "          epo:hasCurrency ?devise .\n"
        "    }\n"
        "  }\n"
        "} LIMIT 50" % vs
    )


def _interroger(query, session=None):
    """POST la requete, renvoie le JSON (dict) ou None. Ne leve jamais :
    incremente le disjoncteur sur echec reseau."""
    if requests is None:
        return None
    try:
        s = session or requests
        rep = s.post(
            ENDPOINT,
            data={"query": query, "format": ACCEPT_JSON},
            headers={"Accept": ACCEPT_JSON},
            timeout=TIMEOUT,
        )
        rep.raise_for_status()
        data = rep.json()
        _ETAT["echecs"] = 0            # succes : on remet le compteur a zero
        return data
    except Exception:
        _ETAT["echecs"] += 1
        if _ETAT["echecs"] >= MAX_ECHECS:
            _ETAT["coupe"] = True
        return None


def titulaires_par_pn(pn, fetch=None, session=None):
    """Renvoie [{"nom","montant","devise"}] pour une attribution, ou [] si
    aucun titulaire / SPARQL indisponible. `fetch` injectable pour les tests :
    callable(query) -> dict JSON de bindings SPARQL."""
    if _ETAT["coupe"]:
        return []
    query = requete(pn)
    data = fetch(query) if fetch is not None else _interroger(query, session)
    if not isinstance(data, dict):
        return []
    out = []
    for b in data.get("results", {}).get("bindings", []):
        nom = (b.get("nom") or {}).get("value", "").strip()
        montant = (b.get("montant") or {}).get("value", "").strip()
        devise = _code_devise((b.get("devise") or {}).get("value", ""))
        if nom:
            out.append({"nom": nom, "montant": montant, "devise": devise})
    return out


def _fmt_valeur(montant, devise):
    """'960000' + 'RON' -> '960000 RON'. Meme forme que le parseur PDF, pour
    rester compatible avec le scoring (_valeur_en_millions detecte la devise)."""
    montant = str(montant or "").strip()
    devise = str(devise or "").strip()
    if montant and devise:
        return "{} {}".format(montant, devise)
    return montant or ""


def parse_depuis_sparql(pn, fetch=None, session=None):
    """Renvoie un dict au FORMAT de parser_gagnants (gagnants/total/
    sous_traitance), ou None si SPARQL n'a ramene aucun titulaire (=> le
    collecteur retombera sur le PDF)."""
    titulaires = titulaires_par_pn(pn, fetch=fetch, session=session)
    if not titulaires:
        return None
    gagnants = [{"nom": t["nom"], "valeur": _fmt_valeur(t["montant"], t["devise"])}
                for t in titulaires]
    # Dedup en preservant l'ordre (un titulaire peut apparaitre sur plusieurs lots).
    vus, uniques = set(), []
    for g in gagnants:
        cle = g["nom"].lower()
        if cle not in vus:
            vus.add(cle)
            uniques.append(g)
    return {"gagnants": uniques, "total": "", "sous_traitance": False}
