# -*- coding: utf-8 -*-
"""
Sonde SURETE Ariba Discovery (jetable, lecture seule) -- chantier Operations.

Mesure EN ISOLATION si l'API publique Ariba Discovery porte un volume reel
d'appels d'offres PRIVES de securite PHYSIQUE (gardiennage, protection, surete)
exploitables par Amarante Operations, avant de cabler un collecteur sur le coeur
radar. Deterministe, sans LLM, reseau injectable.

Le plein texte "security" est large (cyber, IT, CCTV, logiciel) : la sonde
separe la securite PHYSIQUE de services du cyber et des achats de MATERIEL, et
localise les avis (zones Amarante vs autres).

Contrat d'API : voir claude/ariba_discovery.md. A SUPPRIMER une fois la decision
de cabler (ou non) le collecteur surete prise.
"""

import base64
import csv
import json
import os
import time
import unicodedata
import urllib.parse
import urllib.request

import config_env

SEARCH_URL = ("https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/"
              "discovery-core/public/rfx/v1/search")
ENTETES = {"Accept": "application/json", "Content-Type": "application/json",
           "User-Agent": "Mozilla/5.0 (compatible; RadarAmarante/1.0)"}
SORTIE = os.environ.get("SONDE_SORTIE") or "sonde_ariba_surete_sortie"
PAGE_SIZE = config_env.entier("SONDE_PAGESIZE", 50)
PAGES = config_env.entier("SONDE_PAGES", 2)
PAUSE = float(os.environ.get("SONDE_PAUSE", "0.5"))
SEUIL_MOIS = float(os.environ.get("SONDE_SEUIL_MOIS", "8"))

TERMES = ["security guard services", "manned guarding", "static guarding",
          "physical security services", "close protection", "executive protection",
          "security officer", "site security", "gardiennage", "surveillance humaine"]

# --- Lexique de classement (accent-insensible) ---
PHYSIQUE = ("guard", "guarding", "gardiennage", "manned", "patrol", "patrouille",
            "security officer", "close protection", "executive protection",
            "bodyguard", "protection rapprochee", "surete", "physical security",
            "site security", "static guard", "agent de securite", "surveillance humaine",
            "k9", "concierge security", "door supervis")
CYBER = ("cyber", "information security", "infosec", "siem", "endpoint", "firewall",
         "network security", "data loss", " dlp", "penetration test", "it security",
         "software", "saas", "ransomware", "soc ")
BIEN = ("cctv", "camera", "turnstile", "access control system", "alarm system",
        "supply and delivery", "supply, delivery", "installation of", "uniform",
        "equipment supply", "hardware")

# Zones Amarante (pays/regions a risque ou prioritaires).
ZONES_AMARANTE = ("mali", "niger", "burkina", "tchad", "chad", "mauritani", "senegal",
                  "nigeria", "libya", "libye", "iraq", "irak", "syria", "syrie",
                  "yemen", "afghan", "pakistan", "ukrain", "sahel", "somalia", "somalie",
                  "sudan", "soudan", "congo", "rdc", "central african", "centrafric",
                  "emirat", "saudi", "arabie", "qatar", "lebanon", "liban", "egypt",
                  "egypte", "kazakh", "uzbek", "ouzbek", "turkmen", "tajik", "azerbai",
                  "mozambiq", "mali ", "africa", "afrique", "moyen-orient", "middle east")


def _sa(texte):
    d = unicodedata.normalize("NFKD", str(texte or ""))
    return "".join(c for c in d if not unicodedata.combining(c)).lower()


def classer(titre, description, categories):
    """Classe un avis : physique / cyber / bien / pertinent (securite physique
    de services, hors cyber dominant et hors pur materiel)."""
    t = _sa(" ".join([titre or "", description or ""] + list(categories or [])))
    phys = any(k in t for k in PHYSIQUE)
    cyber = any(k in t for k in CYBER)
    bien = any(k in t for k in BIEN)
    generique = ("security" in t or "securite" in t) and not phys
    pertinent = phys and not cyber
    return {"physique": phys, "cyber": cyber, "bien": bien,
            "generique": bool(generique), "pertinent": bool(pertinent)}


def zone(lieux):
    t = _sa(" ".join(lieux or []) if isinstance(lieux, (list, tuple)) else str(lieux or ""))
    return "zone_amarante" if any(z in t for z in ZONES_AMARANTE) else "autre"


def verdict(journal):
    mois = max(1, journal.get("jours", 30)) / 30.0
    par_mois = journal["pertinents"] / mois
    if par_mois >= SEUIL_MOIS:
        return "EXPLOITABLE", "{:.1f} avis surete pertinents/mois.".format(par_mois)
    return "MARGINAL", "{:.1f} avis surete pertinents/mois (seuil {:.0f}).".format(par_mois, SEUIL_MOIS)


# ===========================================================================
# COLLECTE (reseau injectable)
# ===========================================================================
def _charge(terme, page, cursor):
    return {"page": page, "pageSize": PAGE_SIZE, "humanitarianAid": [],
            "freeText": terme, "sortModel": "RELEVANCE", "searchType": "ALL_LEADS",
            "searchAfter": cursor, "commodities": [], "territories": [], "orgAnid": None,
            "leadType": None, "publicSector": None, "minAmount": None, "maxAmount": None,
            "postedDate": None, "deadlineDate": None}


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
            time.sleep(0.8)
    raise RuntimeError(dernier)


def sonder(fetch=None, termes=TERMES, pages=PAGES):
    fetch = fetch or _fetch
    vus = {}
    erreurs = []
    totals = {}
    for terme in termes:
        cursor, page, total = [], 1, None
        try:
            while page <= pages:
                data = fetch(terme, page, cursor)
                if total is None:
                    total = (data or {}).get("totalNumberOfRecords") or 0
                for rec in (data or {}).get("records") or []:
                    rfx = str(rec.get("rfxID") or "")
                    if rfx and rfx not in vus:
                        vus[rfx] = rec
                cursor = (data or {}).get("searchAfter")
                if not ((data or {}).get("records")) or not cursor:
                    break
                page += 1
            totals[terme] = total or 0
        except Exception as exc:  # noqa: BLE001
            erreurs.append("{} : {}".format(terme, str(exc)[:90]))
    pertinents, geo, exemples = 0, {"zone_amarante": 0, "autre": 0}, []
    cl_compte = {"physique": 0, "cyber": 0, "bien": 0, "generique": 0}
    for rec in vus.values():
        cats = rec.get("productsAndServicesCategories") or []
        c = classer(rec.get("title"), rec.get("description"), cats)
        for k in cl_compte:
            cl_compte[k] += 1 if c[k] else 0
        if c["pertinent"]:
            pertinents += 1
            z = zone(rec.get("shipToOrServiceLocations"))
            geo[z] += 1
            if len(exemples) < 20:
                exemples.append({"titre": (rec.get("title") or "")[:70],
                                 "acheteur": rec.get("customerName") or "",
                                 "lieux": rec.get("shipToOrServiceLocations") or [],
                                 "zone": z})
    return {"totals": totals, "uniques": len(vus), "pertinents": pertinents,
            "classes": cl_compte, "geo": geo, "exemples": exemples,
            "erreurs": erreurs, "jours": 0}


def rapport(journal, jours):
    journal = dict(journal, jours=jours)
    v = verdict(journal)
    md = ["# Sonde sûreté Ariba Discovery (Opérations)", "",
          "Lecture seule, sans LLM. Fenêtre indicative {} j (postings actifs).".format(jours), "",
          "| Mesure | Valeur |", "|---|---|",
          "| Avis uniques collectés | {} |".format(journal["uniques"]),
          "| Sécurité physique (services) | {} |".format(journal["classes"]["physique"]),
          "| Cyber (écartés) | {} |".format(journal["classes"]["cyber"]),
          "| Matériel/installation (écartés) | {} |".format(journal["classes"]["bien"]),
          "| **Pertinents sûreté (physique, hors cyber)** | **{}** |".format(journal["pertinents"]),
          "| dont zones Amarante | {} |".format(journal["geo"]["zone_amarante"]),
          "| dont autres pays | {} |".format(journal["geo"]["autre"]),
          "", "## Verdict : {}".format(v[0]), "", v[1], "",
          "## Totaux par requête", "", "| Requête | total marché |", "|---|---|"]
    for terme, n in journal["totals"].items():
        md.append("| {} | {} |".format(terme, n))
    md += ["", "## Exemples pertinents", "",
           "| Titre | Acheteur | Zone | Lieux |", "|---|---|---|---|"]
    for e in journal["exemples"]:
        md.append("| {} | {} | {} | {} |".format(
            e["titre"], (e["acheteur"] or "?")[:24], e["zone"], ", ".join(e["lieux"])[:40]))
    for er in journal["erreurs"][:5]:
        md.append("- Erreur : {}".format(er))
    md += ["", "## Lecture", "",
           "Un avis « pertinent » = service de sécurité physique (gardiennage, "
           "protection, sûreté), hors cyber et hors achat de matériel. La part en "
           "zones Amarante indique l'intérêt direct ; le reste alimente la veille "
           "concurrentielle et les grands comptes."]
    return "\n".join(md) + "\n"


def main():
    jours = config_env.entier("SONDE_JOURS", 90)
    journal = sonder()
    md = rapport(journal, jours)
    print(md)
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "rapport.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(SORTIE, "pertinents.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["titre", "acheteur", "zone", "lieux"])
        for e in journal["exemples"]:
            w.writerow([e["titre"], e["acheteur"], e["zone"], " | ".join(e["lieux"])])
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
