# -*- coding: utf-8 -*-
"""
Radar Amarante - COLLECTEUR IE ARIBA DISCOVERY (chantier IE-11, 01/11/2026).

Capte les APPELS D'OFFRES PRIVES via l'API PUBLIQUE de SAP Ariba Discovery :
les acheteurs (entreprises privees ET publiques) y publient des RFx ouverts
pour etre trouves par des fournisseurs. C'est la seule facon legitime de capter
du prive : des avis PUBLIES, pas un portail ferme. Meme pole ARISE, meme
classeur, meme table, meme lentille que le BOAMP : ce module n'est qu'une SOURCE
de plus (comme collecteur_international_ie pour TED/UNGM). Il IMPORTE
collecteur_ie et REUTILISE son ecriture, son schema, sa memoire et ses lignes.

ACCES (verifie en direct le 01/11/2026, sans login ni cookie)
  GET .../discovery-core/public/rfx/v1/search?filter=<JSON base64>
    en-tetes Accept + Content-Type application/json ; renvoie `records`,
    `totalNumberOfRecords`, curseur `searchAfter`. Champs record : title,
    description, customerName, datePosted, endDate, productsAndServicesCategories,
    shipToOrServiceLocations, publicSectorRfx, adRfxType, min/maxAmount.
  Source du contrat : projet open-source CivicTechTO/toronto-bids (doc 20/10/2026).

ETAT : CAPACITE PROUVEE, DESACTIVEE PAR DEFAUT (motif ADB).
  Sonde du 01/11/2026 : l'API publique fonctionne et renvoie des avis PRIVES,
  mais le rendement POUR L'IE est quasi nul. Les recherches "due diligence /
  integrity / KYC" ne ramenent que des faux positifs (support SAP, audit de
  facturation, CRM) : Ariba Discovery ne porte pas d'achats de prestations IE.
  Le code est conserve et coupe par `RADAR_IE_ARIBA` (defaut 0) : il reste
  la base d'un futur collecteur SURETE pour Amarante OPERATIONS, ou Ariba est
  riche (security guard services ~880, risk management security ~290, dont
  Pakistan/Emirats/Singapour).

PERIMETRE (si reactive)
  - Requetes precises bilingues IE/DD ; la lentille (pre-filtre + LLM) tranche.
  - Acheteurs FRANCAIS ecartes (deja couverts par le BOAMP).
  - Mondial.

VARIABLES
  RADAR_IE_ARIBA=1         ACTIVE ce collecteur (defaut 0 = coupe, rendement IE nul)
  RADAR_IE=1, RADAR_IE_DEBUG=1 (collecte + pre-filtre sans LLM ni ecriture),
  RADAR_IE_ARIBA_JOURS (30), RADAR_IE_ARIBA_BUDGET (20),
  RADAR_IE_ARIBA_PAGES (3), RADAR_IE_ARIBA_PAGESIZE (50).
  IE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE, DATABASE_URL, ANTHROPIC_API_KEY.
"""

import base64
import datetime
import json
import os
import re
import time
import urllib.parse
import urllib.request

import config_env
import lentille_ie as L
import collecteur_ie as C

DEBUG = os.environ.get("RADAR_IE_DEBUG", "0") == "1"
JOURS = config_env.entier("RADAR_IE_ARIBA_JOURS", 30)
BUDGET = config_env.entier("RADAR_IE_ARIBA_BUDGET", 20)
PAGES = config_env.entier("RADAR_IE_ARIBA_PAGES", 3)
PAGE_SIZE = config_env.entier("RADAR_IE_ARIBA_PAGESIZE", 50)
PAUSE = float(os.environ.get("RADAR_IE_ARIBA_PAUSE", "0.8"))

SEARCH_URL = ("https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/"
              "discovery-core/public/rfx/v1/search")
LIEN = ("https://portal.us.bn.cloud.ariba.com/dashboard/public/appext/"
        "comsapsbncdiscoveryui#/RfxEvent/preview/{}?anId=ANONYMOUS")
ENTETES = {"Accept": "application/json", "Content-Type": "application/json",
           "User-Agent": "Mozilla/5.0 (compatible; RadarAmarante/1.0)"}

# Requetes PRECISES et bilingues : le plein texte Ariba est flou et anglophone.
# On evite les termes larges ("compliance", "background check" -> pompes,
# chaudieres) et on vise le coeur IE/DD/conformite. La lentille (LLM,
# multilingue) tranche ensuite la pertinence reelle.
REQUETES = [
    "due diligence", "integrity due diligence", "third party risk",
    "third party due diligence", "KYC", "sanctions screening",
    "anti-corruption", "fraud investigation", "corporate intelligence",
    "business intelligence services", "intelligence economique",
]


# ===========================================================================
# FONCTIONS PURES (testables)
# ===========================================================================
def _iso(brut):
    s = str(brut or "")
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else ""


def lien_public(rfx_id):
    return LIEN.format(rfx_id) if rfx_id else ""


def normaliser(rec):
    """Record de recherche Ariba -> avis au format lentille (comme TED/UNGM)."""
    rfx = str(rec.get("rfxID") or "")
    lieux = rec.get("shipToOrServiceLocations") or []
    if isinstance(lieux, str):
        lieux = [lieux]
    cats = rec.get("productsAndServicesCategories") or []
    if isinstance(cats, str):
        cats = [cats]
    titre = re.sub(r"\s+", " ", str(rec.get("title") or "")).strip()
    corps = " ".join([str(rec.get("description") or "")] + cats + lieux).strip()
    pays = ", ".join(lieux)
    return {
        "source": "Ariba", "id_avis": rfx,
        "titre": titre[:400], "acheteur": str(rec.get("customerName") or ""),
        "nature": "ouvert",
        "date_publication": _iso(rec.get("datePosted") or rec.get("startDate")),
        "date_limite": _iso(rec.get("endDate")),
        "description": (corps or titre)[:2500], "corps": corps,
        "pays": pays, "francais": _est_francais(pays),
        "lien": lien_public(rfx),
    }


def _est_francais(pays):
    p = pays.lower()
    return "france" in p or "french" in p


def est_ouvert(avis, aujourd):
    """Date limite non depassee (ou absente = on garde)."""
    lim = avis.get("date_limite") or ""
    if not lim:
        return True
    try:
        return datetime.date.fromisoformat(lim[:10]) >= aujourd
    except ValueError:
        return True


def dans_fenetre(avis, debut):
    """Publie dans la fenetre (le tri Ariba est par pertinence, pas par date)."""
    pub = avis.get("date_publication") or ""
    if not pub:
        return True
    try:
        return datetime.date.fromisoformat(pub[:10]) >= debut
    except ValueError:
        return True


def file_d_analyse(avis, deja_vus, aujourd, debut, budget=None):
    """Ouverture -> fenetre -> hors France -> pre-filtre lentille -> memoire ->
    plafond (regle 9 : memoire AVANT plafond). Terme dans le titre d'abord."""
    budget = BUDGET if budget is None else budget
    ent = {"collectes": len(avis), "fermes": 0, "hors_fenetre": 0, "francais": 0,
           "deja_vus": 0, "classes": {}}
    retenus = []
    for a in avis:
        if not est_ouvert(a, aujourd):
            ent["fermes"] += 1
            continue
        if not dans_fenetre(a, debut):
            ent["hors_fenetre"] += 1
            continue
        if a.get("francais"):
            ent["francais"] += 1
            continue
        classe, termes = L.prefiltre(a["titre"], a.get("corps", ""))
        ent["classes"][classe] = ent["classes"].get(classe, 0) + 1
        # Ariba a DEJA filtre par notre terme de requete ; on fait donc confiance
        # a un signal IE meme faible ("a_mesurer"), que le LLM departagera. On ne
        # jette que le vide (sans_terme) et le bruit explicite (hors_analyste, bruit).
        if classe not in ("ie_titre", "ie_corps", "a_mesurer"):
            continue
        a = dict(a, classe=classe, termes=termes)
        if C.cle_avis(a) in deja_vus:
            ent["deja_vus"] += 1
            continue
        retenus.append(a)
    retenus = (sorted([a for a in retenus if a["classe"] == "ie_titre"],
                      key=lambda a: a["date_publication"], reverse=True)
               + sorted([a for a in retenus if a["classe"] != "ie_titre"],
                        key=lambda a: a["date_publication"], reverse=True))
    ent["en_attente"] = len(retenus)
    ent["hors_budget"] = max(0, len(retenus) - budget)
    return retenus[:budget], ent


# ===========================================================================
# COLLECTE (reseau injectable)
# ===========================================================================
def _charge(terme, page, cursor):
    return {"page": page, "pageSize": PAGE_SIZE, "leadType": None,
            "publicSector": None, "minAmount": None, "maxAmount": None,
            "postedDate": None, "deadlineDate": None, "humanitarianAid": [],
            "freeText": terme, "sortModel": "RELEVANCE", "searchType": "ALL_LEADS",
            "searchAfter": cursor, "commodities": [], "territories": [], "orgAnid": None}


def _fetch(terme, page, cursor, essais=4):
    enc = base64.b64encode(json.dumps(_charge(terme, page, cursor),
                                      separators=(",", ":")).encode()).decode()
    url = SEARCH_URL + "?" + urllib.parse.urlencode({"filter": enc})
    dernier = ""
    for _ in range(essais):
        try:
            rep = urllib.request.urlopen(urllib.request.Request(url, headers=ENTETES), timeout=30)
            return json.loads(rep.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            dernier = str(exc)[:90]
            time.sleep(1.0)
    raise RuntimeError(dernier)


def collecter(fetch=None, requetes=REQUETES, pages=PAGES):
    """Interroge chaque terme, pagine par curseur, dedoublonne par rfxID."""
    fetch = fetch or _fetch
    par_id, erreurs, requetes_ok = {}, [], 0
    for terme in requetes:
        cursor, page = [], 1
        try:
            while page <= pages:
                data = fetch(terme, page, cursor)
                records = (data or {}).get("records") or []
                for rec in records:
                    rfx = str(rec.get("rfxID") or "")
                    if rfx:
                        par_id.setdefault(rfx, normaliser(rec))
                cursor = (data or {}).get("searchAfter")
                if not records or not cursor:
                    break
                page += 1
            requetes_ok += 1
        except Exception as exc:  # noqa: BLE001
            erreurs.append("{} : {}".format(terme, str(exc)[:90]))
        if PAUSE:
            time.sleep(PAUSE)
    return list(par_id.values()), {"erreurs": erreurs, "requetes": requetes_ok,
                                   "termes": len(requetes)}


# ===========================================================================
# ORCHESTRATION (ecriture et lignes reutilisees de collecteur_ie)
# ===========================================================================
def executer(aujourd=None, fetch=None, appel=None, deja_vus=None, debug=None, budget=None):
    aujourd = aujourd or datetime.date.today()
    debug = DEBUG if debug is None else debug
    debut = aujourd - datetime.timedelta(days=JOURS)
    avis, journal = collecter(fetch=fetch)
    file_, ent = file_d_analyse(avis, deja_vus or set(), aujourd, debut, budget=budget)
    lignes_pg, lignes_sheet, actions = [], [], {}
    if not debug:
        for a in file_:
            r = L.analyser(a, appel=appel, pause=PAUSE if appel is None else 0.0)
            if r["extraction"] is None:
                actions["echec_extraction"] = actions.get("echec_extraction", 0) + 1
                continue
            lp = C.ligne_pg(a, r, aujourd)
            lignes_pg.append(lp)
            actions[lp["action"]] = actions.get(lp["action"], 0) + 1
            if C.visible_arise(r["note"]):
                lignes_sheet.append(C.ligne_sheet(lp))
    rapport = {"debut": debut, "ent": ent, "actions": actions, "file": file_,
               "debug": debug, "erreurs": journal["erreurs"],
               "collecte": len(avis), "requetes": journal["requetes"]}
    return lignes_pg, lignes_sheet, rapport


def rapport_markdown(rapport, ecritures):
    ent = rapport["ent"]
    out = ["# Collecteur IE - Ariba Discovery (appels d'offres prives)", "",
           "Mode : {}. Depuis {}. Collectes : {} | ouverts hors fenetre : {} | "
           "francais ecartes : {} | deja vus : {} | en attente : {} | hors budget : {}.".format(
               "DEBUG (aucune ecriture)" if rapport["debug"] else "production",
               rapport["debut"], ent["collectes"], ent["hors_fenetre"],
               ent["francais"], ent["deja_vus"], ent.get("en_attente", 0),
               ent.get("hors_budget", 0)),
           "", "Pre-filtre : " + json.dumps(ent["classes"], ensure_ascii=False), "",
           "| Classe | Source | Publie | Acheteur | Titre |", "|---|---|---|---|---|"]
    for a in rapport["file"]:
        out.append("| {} | {} | {} | {} | {} |".format(
            a["classe"], a["source"], a["date_publication"], (a["acheteur"] or "?")[:30],
            a["titre"][:55]))
    if not rapport["file"]:
        out.append("| (aucun avis retenu) | | | | |")
    out += ["", "## Actions", "", json.dumps(rapport["actions"], ensure_ascii=False),
            "", "## Ecritures", ""] + ["- " + x for x in ecritures]
    if rapport["erreurs"]:
        out += ["", "## Erreurs", ""] + ["- " + x for x in rapport["erreurs"][:10]]
    return "\n".join(out) + "\n"


def main():
    if not L.ACTIF:
        print("Collecteur Ariba IE desactive (RADAR_IE=0).")
        return
    if os.environ.get("RADAR_IE_ARIBA", "0") != "1":
        print("Collecteur Ariba IE coupe (RADAR_IE_ARIBA=0 : rendement IE nul, "
              "capacite conservee pour un futur collecteur surete Operations).")
        return
    aujourd = datetime.date.today()
    url = os.environ.get("DATABASE_URL")
    sheet_id = os.environ.get("IE_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    feuille = None
    if sheet_id and fichier and not DEBUG:
        try:
            feuille = C.ouvrir_onglet(sheet_id, fichier)
        except Exception as exc:  # noqa: BLE001
            print("  (info) classeur ARISE inaccessible ({}).".format(str(exc)[:120]))
    deja_vus = C.memoire_pg(url) if not DEBUG else set()
    lignes_pg, lignes_sheet, rapport = executer(deja_vus=deja_vus)
    if DEBUG:
        ecritures = ["aucune (mode debug)"]
    else:
        ecritures = [C.ecrire_pg(url, lignes_pg), C.ecrire_sheet(feuille, lignes_sheet)]
    md = rapport_markdown(rapport, ecritures)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
