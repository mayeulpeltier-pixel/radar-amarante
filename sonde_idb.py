# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v10 (jetable) : IDB, exploiter les portes ouvertes.
============================================================================

CE QUE LA v9 A ETABLI, PREUVES A L'APPUI
----------------------------------------
  - Le blocage est CLOUDFLARE, et il vise l'IP : "Sorry, you have been
    blocked", Ray ID, IP 20.40.214.130 (plage Azure, celle des runners GitHub).
    Sept variantes testees (en-tetes navigateur, amorcage de cookie, httpx
    HTTP/1.1 et HTTP/2, curl en trois piles TLS) : aucune ne passe. Le
    grattage des pages iadb.org est DEFINITIVEMENT mort. Ce n'est plus une
    supposition, c'est mesure.
  - DEUX PORTES SONT OUVERTES, et c'est la que va cette sonde :
      1. data.iadb.org repond 200 avec 139 929 octets. Ce sous-domaine n'est
         PAS derriere la meme regle Cloudflare.
      2. Le registre IATI confirme l'IDB comme publieur : organisation "iadb",
         identifiant XI-IATI-IADB, 28 jeux de donnees. Le registre est un CKAN
         ouvert sur un domaine tiers.

CE QUE CETTE SONDE CHERCHE
--------------------------
  A. data.iadb.org : quelle technologie, quelle API, quel contenu ? On cherche
     les points d'entree standard (CKAN, Socrata, sitemap) ET on fouille le
     HTML de la page d'accueil a la recherche d'appels d'API.
  B. IATI, catalogue : les 28 jeux de l'IDB, et surtout l'URL des FICHIERS.
     Si ces fichiers sont heberges sur iadb.org, ils seront bloques ; s'ils
     sont sur un miroir, la voie est ouverte.
  C. IATI, miroirs et API tierces : d-portal.org et Code for IATI republient
     les donnees IATI avec des API libres, sans cle. C'est le contournement
     naturel du datastore officiel qui, lui, exige une cle (401 en v9).
  D. Verification du CONTENU : un flux n'a de valeur que s'il porte pays,
     titre, montant, secteur et organisme de mise en oeuvre.

HONNETETE SUR LA NATURE DU SIGNAL
---------------------------------
IATI publie des PROJETS, pas des avis d'appel d'offres. C'est une information
differente, et sans doute meilleure : on sait qui finance quoi, ou, pour
combien et avec quel operateur, EN AMONT de l'appel d'offres. Un projet
d'infrastructure de 200 M USD en Colombie est un prospect avant meme que le
marche soit publie. Mais ce n'est pas la meme chose qu'un avis : il faut le
dire clairement avant de construire.

AUCUNE ECRITURE. Sortie toujours en code 0.
"""

import json
import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

TIMEOUT = 45
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# A. data.iadb.org : le seul hote du groupe qui repond.
DATA_IADB = "https://data.iadb.org"
ENTREES_DATA = [
    ("accueil", DATA_IADB + "/"),
    ("robots", DATA_IADB + "/robots.txt"),
    ("sitemap", DATA_IADB + "/sitemap.xml"),
    ("CKAN package_list", DATA_IADB + "/api/3/action/package_list"),
    ("CKAN status", DATA_IADB + "/api/3/action/status_show"),
    ("Socrata views", DATA_IADB + "/api/views.json?limit=5"),
    ("DCAT data.json", DATA_IADB + "/data.json"),
    ("API racine", DATA_IADB + "/api"),
]

# B. Registre IATI : le catalogue et les fichiers de l'IDB.
IATI_ORG = ("https://iatiregistry.org/api/3/action/organization_show"
            "?id=iadb&include_datasets=true")

# C. Miroirs et API tierces sur les donnees IATI (sans cle).
MIROIRS_IATI = [
    ("d-portal (activites IDB)",
     "https://d-portal.org/q?reporting_ref=XI-IATI-IADB&limit=3&form=json"),
    ("d-portal (variante select)",
     "https://d-portal.org/q?form=json&select=aid,title,country_code,"
     "reporting_ref&reporting_ref=XI-IATI-IADB&limit=3"),
    ("Code for IATI (racine)", "https://api.codeforiati.org/"),
    ("Datastore classique",
     "https://iatidatastore.iatistandard.org/api/1/access/activity.json"
     "?reporting-org=XI-IATI-IADB&limit=2"),
    ("Code for IATI dump", "https://iati-data-dump.codeforiati.org/"),
]

# Marqueurs de contenu commercialement utile.
CHAMPS_UTILES = ["country", "recipient", "title", "budget", "value", "sector",
                 "organisation", "participating", "pais", "monto"]

RESULTATS = []
FICHIERS_IATI = []


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


def _get(session, url, **kw):
    try:
        return session.get(url, timeout=TIMEOUT, allow_redirects=True, **kw)
    except Exception as e:
        print("       exception : {}".format(_plat(e, 70)))
        return None


def sonde_a(session):
    """A. data.iadb.org : de quoi s'agit-il, et comment l'interroger ?"""
    _titre("A. data.iadb.org -- LE SEUL HOTE OUVERT")
    ouverts = []
    accueil = None
    for nom, url in ENTREES_DATA:
        r = _get(session, url)
        if r is None:
            print("  [KO] {:20}".format(nom))
            continue
        ctype = _plat(r.headers.get("Content-Type", ""), 30)
        taille = len(r.content or b"")
        print("  [{}] {:20} {} | {:30} | {} octets".format(
            "OK" if r.status_code < 400 else "KO", nom, r.status_code,
            ctype, taille))
        if r.status_code >= 400:
            continue
        ouverts.append(nom)
        if nom == "accueil":
            accueil = r.text or ""
        elif "json" in ctype.lower():
            print("       apercu JSON : " + _plat(r.text, 320))
        elif taille < 3000:
            print("       apercu : " + _plat(r.text, 300))

    # Fouille du HTML : les appels d'API d'une application monopage y sont
    # souvent en clair (chemins /api/, fichiers .json, endpoint graphql).
    if accueil:
        print("\n  --- CHEMINS D'API REPERES DANS LE HTML D'ACCUEIL ---")
        motifs = set()
        for m in re.finditer(r"[\"'](/(?:api|data|services|rest|graphql)[^\"'\s]{0,80})[\"']",
                             accueil):
            motifs.add(m.group(1))
        for m in re.finditer(r"[\"'](https?://[^\"'\s]{0,100}?(?:/api/|\.json)[^\"'\s]{0,40})[\"']",
                             accueil):
            motifs.add(m.group(1))
        for chemin in sorted(motifs)[:25]:
            print("      " + _plat(chemin, 100))
        if not motifs:
            print("      (aucun chemin d'API en clair)")
        # Indices de technologie.
        for techno, marqueur in (("CKAN", "ckan"), ("Socrata", "socrata"),
                                 ("Drupal", "drupal"), ("Tableau", "tableau"),
                                 ("PowerBI", "powerbi"), ("Angular", "ng-app"),
                                 ("React", "__NEXT_DATA__")):
            if marqueur.lower() in accueil.lower():
                print("      techno detectee : {}".format(techno))
        titre = re.search(r"(?is)<title[^>]*>(.*?)</title>", accueil)
        if titre:
            print("      titre de la page : " + _plat(titre.group(1), 90))
    _verdict("data.iadb.org", bool(ouverts),
             "{} point(s) d'entree ouvert(s) : {}".format(
                 len(ouverts), ", ".join(ouverts)))


def sonde_b(session):
    """B. Catalogue IATI de l'IDB : ou sont les FICHIERS ?"""
    _titre("B. CATALOGUE IATI DE L'IDB (28 jeux annonces)")
    r = _get(session, IATI_ORG)
    if r is None or r.status_code >= 400:
        _verdict("catalogue IATI", False,
                 "statut {}".format(r.status_code if r else "injoignable"))
        return
    try:
        res = (r.json() or {}).get("result") or {}
    except Exception:
        _verdict("catalogue IATI", False, "reponse non JSON")
        return
    paquets = res.get("packages") or []
    print("  organisation : {} | {} jeu(x) listes".format(
        res.get("name"), len(paquets)))
    hotes = {}
    for p in paquets[:30]:
        titre = _plat(p.get("title") or p.get("name"), 58)
        ressources = p.get("resources") or []
        url = ressources[0].get("url") if ressources else ""
        hote = re.sub(r"^https?://([^/]+).*$", r"\1", url) if url else "?"
        hotes[hote] = hotes.get(hote, 0) + 1
        print("    {:58} -> {}".format(titre, _plat(hote, 34)))
        if url:
            FICHIERS_IATI.append(url)
    print("\n  hebergement des fichiers : {}".format(
        ", ".join("{} x{}".format(h, n) for h, n in sorted(
            hotes.items(), key=lambda x: -x[1]))))
    bloques = sum(n for h, n in hotes.items() if "iadb.org" in h)
    print("  dont sur iadb.org (donc derriere Cloudflare) : {}".format(bloques))
    _verdict("catalogue IATI", bool(paquets),
             "{} jeu(x), {} heberges sur iadb.org".format(len(paquets), bloques))


def sonde_c(session):
    """C. Miroirs et API tierces : le contournement naturel."""
    _titre("C. MIROIRS ET API TIERCES SUR LES DONNEES IATI (sans cle)")
    exploitables = []
    for nom, url in MIROIRS_IATI:
        r = _get(session, url)
        if r is None:
            print("  [KO] {:26}".format(nom))
            continue
        ctype = _plat(r.headers.get("Content-Type", ""), 26)
        print("  [{}] {:26} {} | {:26} | {} octets".format(
            "OK" if r.status_code < 400 else "KO", nom, r.status_code,
            ctype, len(r.content or b"")))
        if r.status_code >= 400:
            continue
        try:
            donnees = r.json()
        except Exception:
            print("       (non JSON) " + _plat(r.text, 180))
            continue
        exploitables.append(nom)
        lignes = donnees
        if isinstance(donnees, dict):
            for cle in ("rows", "results", "iati-activities", "activities", "data"):
                if isinstance(donnees.get(cle), list):
                    lignes = donnees[cle]
                    break
        if isinstance(lignes, list) and lignes and isinstance(lignes[0], dict):
            print("       {} enregistrement(s) | {} champ(s) sur le premier :".format(
                len(lignes), len(lignes[0])))
            for cle in sorted(lignes[0])[:24]:
                marque = " <--" if any(u in cle.lower() for u in CHAMPS_UTILES) else ""
                print("         {:30} = {}{}".format(
                    cle, _plat(lignes[0][cle], 44), marque))
        else:
            print("       " + _plat(json.dumps(donnees)[:400]))
    _verdict("miroirs IATI", bool(exploitables),
             ", ".join(exploitables) if exploitables else "aucun miroir ouvert")


def sonde_d(session):
    """D. Un fichier IATI reel est-il telechargeable, et que contient-il ?"""
    _titre("D. TELECHARGEMENT D'UN FICHIER IATI REEL")
    if not FICHIERS_IATI:
        print("  (aucune URL de fichier collectee en B)")
        _verdict("fichier IATI", False, "non evalue")
        return
    reussis = 0
    for url in FICHIERS_IATI[:3]:
        print("\n  --- {} ---".format(_plat(url, 96)))
        r = _get(session, url)
        if r is None or r.status_code >= 400:
            print("      statut {}".format(r.status_code if r else "injoignable"))
            continue
        contenu = r.text or ""
        reussis += 1
        print("      {} octets | {}".format(
            len(r.content or b""), _plat(r.headers.get("Content-Type"), 40)))
        # Structure IATI : compter les activites et montrer la premiere.
        activites = re.findall(r"(?is)<iati-activity[^>]*>", contenu)
        print("      {} activite(s) IATI dans le fichier".format(len(activites)))
        prem = re.search(r"(?is)<iati-activity.*?</iati-activity>", contenu)
        if prem:
            extrait = prem.group(0)
            print("      --- premiere activite (brut, tronque) ---")
            print(extrait[:1800])
        else:
            print("      apercu brut : " + _plat(contenu, 400))
    _verdict("fichier IATI", reussis > 0,
             "{} fichier(s) telecharge(s)".format(reussis))


def main():
    print("SONDE v10 -- IDB : exploiter les portes ouvertes. Lecture seule.")
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, application/xml, text/html;q=0.8, */*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8,fr;q=0.7",
    })
    sonde_a(session)
    sonde_b(session)
    sonde_c(session)
    sonde_d(session)

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  {:22} {:12} {}".format(nom, "OK" if ok else "a creuser", detail))
    print("\nLECTURE DES RESULTATS :")
    print("  - API ouverte sur data.iadb.org  -> collecteur direct, ideal.")
    print("  - miroir IATI exploitable (C)    -> collecteur PIPELINE PROJETS :")
    print("      qui finance quoi, ou, pour combien, avec quel operateur, EN")
    print("      AMONT de l'appel d'offres. Signal different d'un avis, et")
    print("      commercialement plus precoce.")
    print("  - fichiers IATI telechargeables  -> meme chose, sans dependance a")
    print("      une API tierce (on lit le XML publie par l'IDB lui-meme).")
    print("  - tout ferme                     -> la, on acte, avec des preuves.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                            # une sonde n'echoue jamais
        print("Sonde interrompue : {}".format(e))
    sys.exit(0)
