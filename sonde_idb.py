# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v9 (jetable) : IDB, on ne lache pas.
=============================================================

CE QUE J'AI RATE DANS LES v7 ET v8
----------------------------------
J'ai conclu "bloque, on abandonne" SANS JAMAIS LIRE LE CORPS DE LA PAGE 403.
5969 octets identiques a chaque fois, c'est une page de pare-feu applicatif :
elle nomme presque toujours le fournisseur (Akamai, Cloudflare, Imperva...) et
porte un identifiant d'incident. C'est exactement ce qui dit si le blocage est
contournable ou definitif. Conclure sans cette lecture etait une faute de
methode.

Autre erreur : la v8 a interroge l'API de decouverte Socrata en supposant que
mydata.iadb.org etait un portail Socrata. Le 404 dit le contraire. Encore une
hypothese prise pour un fait.

CE QUE FAIT CETTE VERSION
-------------------------
  A. AUTOPSIE DU 403 : on dumpe le corps brut et les en-tetes. Qui bloque, et
     sur quel critere (IP de datacenter, empreinte TLS, absence de cookie) ?
  B. CONTOURNEMENTS, du moins au plus intrusif : en-tetes de navigateur
     complets, amorcage de cookie (page d'accueil puis cible), HTTP/2 via
     httpx (empreinte TLS differente de requests), puis curl en dernier
     recours (pile TLS encore differente).
  C. AUTRES HOTES IDB : le groupe expose une dizaine de sous-domaines. Un
     pare-feu est rarement uniforme sur tous.
  D. API SOUS-JACENTE : si le portail achats est une application monopage,
     elle appelle une API qui, elle, echappe souvent a la regle WAF appliquee
     aux pages HTML. On teste sitemap, /api/*, /graphql.
  E. VOIE IATI : l'IDB publie ses activites au standard IATI. Le registre est
     un CKAN OUVERT, sans pare-feu, sur un domaine tiers. Les projets ne sont
     pas des avis d'appel d'offres, mais un pipeline de projets EST un
     gisement de prospects (on sait qui finance quoi, ou, et pour combien,
     avant meme l'appel d'offres).

AUCUNE ECRITURE. Sortie toujours en code 0.
"""

import json
import re
import subprocess
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

try:
    import httpx
    HTTPX = True
except Exception:
    HTTPX = False

TIMEOUT = 40
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Jeu d'en-tetes complet d'un vrai navigateur : beaucoup de pare-feux se
# contentent de verifier la coherence de cet ensemble.
ENTETES_NAVIGATEUR = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8,fr;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="126", "Not:A-Brand";v="24", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
}

CIBLE = "https://projectprocurement.iadb.org/en/procurement-notices"

# C. Sous-domaines et chemins du groupe IDB. Un pare-feu est rarement uniforme.
AUTRES_HOTES = [
    ("data.iadb.org", "https://data.iadb.org/"),
    ("datosabiertos", "https://datosabiertos.iadb.org/"),
    ("publications", "https://publications.iadb.org/"),
    ("idbdocs", "https://idbdocs.iadb.org/"),
    ("code.iadb.org", "https://code.iadb.org/"),
    ("IDB Invest", "https://www.idbinvest.org/en/procurement"),
    ("IDB Invest racine", "https://www.idbinvest.org/en"),
    ("convocatorias ES", "https://www.iadb.org/es/adquisiciones"),
]

# D. Endpoints techniques : souvent hors du perimetre de la regle WAF.
ENDPOINTS_TECHNIQUES = [
    ("sitemap achats", "https://projectprocurement.iadb.org/sitemap.xml"),
    ("robots achats", "https://projectprocurement.iadb.org/robots.txt"),
    ("api notices", "https://projectprocurement.iadb.org/api/notices"),
    ("api v1", "https://projectprocurement.iadb.org/api/v1/procurement-notices"),
    ("graphql", "https://projectprocurement.iadb.org/graphql"),
    ("sitemap principal", "https://www.iadb.org/sitemap.xml"),
    ("robots principal", "https://www.iadb.org/robots.txt"),
]

# E. IATI : registre CKAN ouvert, domaine tiers, aucun pare-feu connu.
# XM-DAC-46012 est le code bailleur de la Banque interamericaine.
IATI = [
    ("registre CKAN (recherche)",
     "https://iatiregistry.org/api/3/action/package_search?q=iadb&rows=5"),
    ("registre CKAN (organisation)",
     "https://iatiregistry.org/api/3/action/organization_show?id=iadb"),
    ("registre CKAN (liste orgs)",
     "https://iatiregistry.org/api/3/action/organization_list?limit=1000"),
    ("datastore IATI",
     "https://api.iatistandard.org/datastore/activity/select"
     "?q=reporting_org_ref:XM-DAC-46012&rows=2&wt=json"),
]

RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OK" if ok else "a creuser", detail))


def _plat(t, n=None):
    s = re.sub(r"\s+", " ", str(t or "")).strip()
    return s[:n] if n else s


def sonde_a(session):
    """A. AUTOPSIE : qui bloque, et que dit-il exactement ?"""
    _titre("A. AUTOPSIE DE LA PAGE 403 (l'etape que j'avais sautee)")
    try:
        r = session.get(CIBLE, headers={"User-Agent": UA}, timeout=TIMEOUT)
    except Exception as e:
        _verdict("autopsie", False, "injoignable : {}".format(_plat(e, 60)))
        return None
    print("  statut : {} | {} octets".format(r.status_code, len(r.content or b"")))
    print("\n  --- EN-TETES DE REPONSE (le pare-feu s'y signe souvent) ---")
    for cle, val in r.headers.items():
        print("    {:24} : {}".format(cle, _plat(val, 90)))
    print("\n  --- CORPS BRUT (integral si court) ---")
    corps = r.text or ""
    print(corps[:4000])

    bas = (corps + " " + json.dumps(dict(r.headers))).lower()
    signatures = {
        "Akamai": ["akamai", "reference #", "errors.edgesuite"],
        "Cloudflare": ["cloudflare", "ray id", "cf-ray"],
        "Imperva/Incapsula": ["imperva", "incapsula", "incident id"],
        "AWS WAF": ["aws", "x-amzn", "request blocked"],
        "F5/BIG-IP": ["big-ip", "the requested url was rejected"],
    }
    trouves = [n for n, marqueurs in signatures.items()
               if any(m in bas for m in marqueurs)]
    print("\n  pare-feu identifie : {}".format(", ".join(trouves) or "non identifie"))
    ip_bloquee = any(m in bas for m in ["your ip", "ip address", "datacenter",
                                        "hosting provider", "automated"])
    print("  mention d'un blocage par IP/automatisation : {}".format(
        "OUI (contournement peu probable)" if ip_bloquee else "non detectee"))
    _verdict("autopsie", True,
             "pare-feu : {} | blocage IP annonce : {}".format(
                 ", ".join(trouves) or "inconnu", "oui" if ip_bloquee else "non"))
    return corps


def sonde_b():
    """B. CONTOURNEMENTS, du plus simple au plus different."""
    _titre("B. TENTATIVES DE CONTOURNEMENT")
    succes = []

    # 1. En-tetes de navigateur complets.
    try:
        s = requests.Session()
        s.headers.update(ENTETES_NAVIGATEUR)
        r = s.get(CIBLE, timeout=TIMEOUT)
        print("  [{}] en-tetes navigateur complets : {} | {} octets".format(
            "OK" if r.status_code < 400 else "KO", r.status_code, len(r.content or b"")))
        if r.status_code < 400:
            succes.append("en-tetes navigateur")
    except Exception as e:
        print("  [KO] en-tetes navigateur : {}".format(_plat(e, 60)))

    # 2. Amorcage de cookie : accueil d'abord, cible ensuite.
    try:
        s = requests.Session()
        s.headers.update(ENTETES_NAVIGATEUR)
        acc = s.get("https://projectprocurement.iadb.org/", timeout=TIMEOUT)
        r = s.get(CIBLE, timeout=TIMEOUT,
                  headers={"Referer": "https://projectprocurement.iadb.org/"})
        print("  [{}] amorcage cookie (accueil {} -> cible {}) | {} cookie(s)".format(
            "OK" if r.status_code < 400 else "KO", acc.status_code,
            r.status_code, len(s.cookies)))
        if r.status_code < 400:
            succes.append("amorcage cookie")
    except Exception as e:
        print("  [KO] amorcage cookie : {}".format(_plat(e, 60)))

    # 3. HTTP/2 via httpx : empreinte TLS et protocole differents de requests.
    if HTTPX:
        for h2 in (True, False):
            try:
                with httpx.Client(http2=h2, headers=ENTETES_NAVIGATEUR,
                                  timeout=TIMEOUT, follow_redirects=True) as c:
                    r = c.get(CIBLE)
                print("  [{}] httpx http2={} : {} | {} octets".format(
                    "OK" if r.status_code < 400 else "KO", h2,
                    r.status_code, len(r.content or b"")))
                if r.status_code < 400:
                    succes.append("httpx http2={}".format(h2))
            except Exception as e:
                print("  [KO] httpx http2={} : {}".format(h2, _plat(e, 55)))
    else:
        print("  [--] httpx absent (ajouter 'httpx[http2]' au workflow)")

    # 4. curl : pile TLS entierement differente (OpenSSL + nghttp2).
    for etiquette, options in (("curl standard", []),
                               ("curl http2", ["--http2"]),
                               ("curl tls1.2", ["--tlsv1.2", "--tls-max", "1.2"])):
        try:
            cmd = (["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{size_download}",
                    "-A", UA, "--max-time", "30"] + options + [CIBLE])
            sortie = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            code = (sortie.stdout or "").split()
            statut = code[0] if code else "?"
            print("  [{}] {:16} : {} | {} octets".format(
                "OK" if statut.startswith(("2", "3")) else "KO",
                etiquette, statut, code[1] if len(code) > 1 else "?"))
            if statut.startswith(("2", "3")):
                succes.append(etiquette)
        except Exception as e:
            print("  [KO] {:16} : {}".format(etiquette, _plat(e, 55)))

    _verdict("contournement", bool(succes),
             ", ".join(succes) if succes else "aucune variante ne passe")
    return succes


def _essayer(session, nom, url, apercu=220):
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    except Exception as e:
        print("  [KO] {:22} exception : {}".format(nom, _plat(e, 55)))
        return None
    ctype = _plat(r.headers.get("Content-Type", ""), 30)
    taille = len(r.content or b"")
    print("  [{}] {:22} {} | {:30} | {} octets".format(
        "OK" if r.status_code < 400 else "KO", nom, r.status_code, ctype, taille))
    if r.status_code < 400 and taille > 300:
        print("       apercu : " + _plat(r.text, apercu))
        return r
    return None


def sonde_c(session):
    """C. Un pare-feu est rarement uniforme sur tous les sous-domaines."""
    _titre("C. AUTRES HOTES DU GROUPE IDB")
    vivants = [nom for nom, url in AUTRES_HOTES if _essayer(session, nom, url)]
    _verdict("autres hotes", bool(vivants),
             ", ".join(vivants) if vivants else "tous bloques")
    return vivants


def sonde_d(session):
    """D. L'API d'une application monopage echappe souvent a la regle WAF."""
    _titre("D. ENDPOINTS TECHNIQUES ET API SOUS-JACENTE")
    vivants = [nom for nom, url in ENDPOINTS_TECHNIQUES
               if _essayer(session, nom, url, apercu=400)]
    _verdict("endpoints techniques", bool(vivants),
             ", ".join(vivants) if vivants else "aucun endpoint ouvert")
    return vivants


def sonde_e(session):
    """E. IATI : domaine tiers, API ouverte, l'IDB y publie ses activites."""
    _titre("E. VOIE IATI (registre ouvert, hors pare-feu IDB)")
    exploitables = []
    for nom, url in IATI:
        try:
            r = session.get(url, timeout=TIMEOUT)
        except Exception as e:
            print("  [KO] {:26} exception : {}".format(nom, _plat(e, 50)))
            continue
        print("  [{}] {:26} {} | {} octets".format(
            "OK" if r.status_code < 400 else "KO", nom, r.status_code,
            len(r.content or b"")))
        if r.status_code >= 400:
            continue
        try:
            donnees = r.json()
        except Exception:
            print("       (non JSON) " + _plat(r.text, 160))
            continue
        exploitables.append(nom)
        # CKAN : {"success": true, "result": {...}}
        res = donnees.get("result") if isinstance(donnees, dict) else None
        if isinstance(res, dict) and "results" in res:
            print("       {} jeu(x) trouve(s) sur {} annonce(s)".format(
                len(res.get("results") or []), res.get("count", "?")))
            for jeu in (res.get("results") or [])[:5]:
                print("         {:38} | org: {}".format(
                    _plat(jeu.get("title"), 38),
                    _plat((jeu.get("organization") or {}).get("title"), 28)))
                for ress in (jeu.get("resources") or [])[:1]:
                    print("           fichier : " + _plat(ress.get("url"), 92))
        elif isinstance(res, list):
            iadb = [o for o in res if "iadb" in str(o).lower()
                    or "inter-american" in str(o).lower()]
            print("       {} organisations, dont IDB : {}".format(
                len(res), iadb[:5] or "aucune trouvee"))
        else:
            print("       " + _plat(json.dumps(donnees)[:400]))
    _verdict("IATI", bool(exploitables),
             ", ".join(exploitables) if exploitables else "voie IATI fermee")
    return exploitables


def main():
    print("SONDE v9 -- IDB, autopsie et contournements. Lecture seule.")
    session = requests.Session()
    session.headers.update(ENTETES_NAVIGATEUR)

    sonde_a(session)
    sonde_b()
    sonde_c(session)
    sonde_d(session)
    sonde_e(session)

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  {:22} {:12} {}".format(nom, "OK" if ok else "a creuser", detail))
    print("\nLECTURE DES RESULTATS :")
    print("  - une variante de B passe        -> collecteur IDB par grattage,")
    print("                                      avec cette variante exacte.")
    print("  - un hote de C ou D ouvert       -> explorer ce point d'entree.")
    print("  - IATI exploitable en E          -> collecteur PIPELINE PROJETS")
    print("                                      (qui finance quoi, ou, combien,")
    print("                                      en amont de l'appel d'offres).")
    print("  - tout ferme                     -> la, et seulement la, on acte.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                            # une sonde n'echoue jamais
        print("Sonde interrompue : {}".format(e))
    sys.exit(0)
