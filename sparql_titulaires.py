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

PREFIXES = (
    "PREFIX epo: <http://data.europa.eu/a4g/ontology#>\n"
    "PREFIX cccev: <http://data.europa.eu/m8g/>\n"
)

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


def _dernier_segment(uri):
    """'.../authority/country/DEU' -> 'DEU'. Renvoie tel quel si non-URI.
    Generique : extrait un code (devise, pays, NUTS) du dernier segment d'un
    IRI de codelist ePO."""
    s = str(uri or "").strip()
    return s.rsplit("/", 1)[-1] if "/" in s else s


def _code_devise(uri):
    """'.../authority/currency/RON' -> 'RON'. Renvoie tel quel si non-URI."""
    return _dernier_segment(uri)


def requete(pn):
    vs = ", ".join('"%s"' % v for v in _variantes_pn(pn))
    return PREFIXES + (
        "SELECT ?nom ?montant ?devise ?pays ?nuts WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        "    FILTER(STR(?pn) IN (%s))\n"
        "    ?tender a epo:Tender ;\n"
        "            epo:isSubmitedBy ?tenderer .\n"
        "    ?tenderer epo:playedBy ?org .\n"
        "    ?org epo:hasLegalName ?nom .\n"
        "    OPTIONAL {\n"
        "      ?tender epo:hasFinancialOfferValue ?ov .\n"
        "      ?ov epo:hasAmountValue ?montant ;\n"
        "          epo:hasCurrency ?devise .\n"
        "    }\n"
        "    OPTIONAL {\n"
        "      ?org cccev:registeredAddress ?adr .\n"
        "      ?adr epo:hasCountryCode ?pays .\n"
        "      OPTIONAL { ?adr epo:hasNutsCode ?nuts . }\n"
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
        pays = _dernier_segment((b.get("pays") or {}).get("value", ""))
        nuts = _dernier_segment((b.get("nuts") or {}).get("value", ""))
        if nom:
            out.append({"nom": nom, "montant": montant, "devise": devise,
                        "pays": pays, "nuts": nuts})
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
    # Pays des titulaires (adresse enregistree, ISO3), dedup en preservant
    # l'ordre : alimente le socle DETERMINISTE `pays_titulaire` de l'onglet brut
    # (fait factuel, complementaire de l'inference d'origine du LLM).
    pays = []
    for t in titulaires:
        p = (t.get("pays") or "").strip()
        if p and p not in pays:
            pays.append(p)
    return {"gagnants": uniques, "total": "", "sous_traitance": False,
            "pays_titulaire": "; ".join(pays)}


# ===========================================================================
# RENOUVELLEMENT : date de fin de contrat = date de conclusion + duree.
# Sonde confirmee : ?a epo:hasContractConclusionDate ?d (ex "2025-12-08") ;
# ?b epo:definesContractDuration ?dur ; ?dur time:numericDuration ?val ;
# ?dur time:unitType <.../unitMonth|unitYear|unitDay|unitWeek>.
# ===========================================================================
import datetime as _dt

PREFIXES_RENOUV = PREFIXES + "PREFIX time: <http://www.w3.org/2006/time#>\n"

# Horizons d'alerte (mois), configurables. Un marche public se retravaille en
# amont de son echeance : imminent = a traiter, a_venir = a surveiller.
HORIZON_IMMINENT = int(os.environ.get("RADAR_RENOUV_IMMINENT", "6"))
HORIZON_VEILLE = int(os.environ.get("RADAR_RENOUV_VEILLE", "12"))

_JOURS_PAR_UNITE = {"year": 365.25, "month": 30.44, "week": 7.0, "day": 1.0}


def requete_renouvellement(pn):
    vs = ", ".join('"%s"' % v for v in _variantes_pn(pn))
    return PREFIXES_RENOUV + (
        "SELECT ?conclusion ?dureeVal ?dureeUnit WHERE {\n"
        "  GRAPH ?g {\n"
        "    ?notice a epo:Notice ;\n"
        "            epo:hasNoticePublicationNumber ?pn .\n"
        "    FILTER(STR(?pn) IN (%s))\n"
        "    OPTIONAL { ?a epo:hasContractConclusionDate ?conclusion . }\n"
        "    OPTIONAL {\n"
        "      ?b epo:definesContractDuration ?dur .\n"
        "      ?dur time:numericDuration ?dureeVal .\n"
        "      OPTIONAL { ?dur time:unitType ?dureeUnit . }\n"
        "    }\n"
        "  }\n"
        "} LIMIT 50" % vs
    )


def _date_fin(conclusion, val, unit):
    """date de conclusion + duree -> datetime.date de fin, ou None."""
    try:
        d = _dt.datetime.strptime(str(conclusion)[:10], "%Y-%m-%d").date()
        v = float(val)
    except (ValueError, TypeError):
        return None
    u = str(unit or "").lower()
    facteur = next((j for cle, j in _JOURS_PAR_UNITE.items() if cle in u), None)
    if facteur is None:
        return None
    return d + _dt.timedelta(days=v * facteur)


def statut_renouvellement(mois_avant):
    """imminent (<= HORIZON_IMMINENT), a_venir (<= HORIZON_VEILLE), sinon ''.
    Un contrat deja expire ou trop lointain ne declenche pas d'alerte."""
    if mois_avant is None or mois_avant < 0:
        return ""
    if mois_avant <= HORIZON_IMMINENT:
        return "imminent"
    if mois_avant <= HORIZON_VEILLE:
        return "a_venir"
    return ""


def renouvellement_par_pn(pn, fetch=None, session=None, aujourdhui=None):
    """Renvoie {"fin": "AAAA-MM-JJ", "mois_avant": int, "statut": str} pour une
    attribution, ou {} si donnees absentes. On retient la date de fin la PLUS
    LOINTAINE (le contrat court tant qu'un lot reste actif) et on deduplique les
    lots identiques. `fetch` injectable pour tests : callable(query) -> dict."""
    if _ETAT["coupe"]:
        return {}
    query = requete_renouvellement(pn)
    data = fetch(query) if fetch is not None else _interroger(query, session)
    if not isinstance(data, dict):
        return {}
    fin_max = None
    for b in data.get("results", {}).get("bindings", []):
        conclusion = (b.get("conclusion") or {}).get("value", "")
        val = (b.get("dureeVal") or {}).get("value", "")
        unit = (b.get("dureeUnit") or {}).get("value", "")
        fin = _date_fin(conclusion, val, unit)
        if fin and (fin_max is None or fin > fin_max):
            fin_max = fin
    if fin_max is None:
        return {}
    ref = aujourdhui or _dt.date.today()
    mois_avant = round((fin_max - ref).days / 30.44, 1)
    return {"fin": fin_max.isoformat(), "mois_avant": mois_avant,
            "statut": statut_renouvellement(mois_avant)}
