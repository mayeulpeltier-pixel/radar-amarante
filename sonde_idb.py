# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v7 (jetable) : IDB, Banque interamericaine.
====================================================================

POURQUOI
--------
Le perimetre commercial s'est ouvert le 22/07/2026 a 13 pays d'Amerique latine
et d'Asie. Les sources actuelles y sont generalistes (BM, UNGM, ReliefWeb) :
aucune source REGIONALE ne couvre l'Amerique latine, alors que la Banque
interamericaine de developpement (IDB / BID) y est le bailleur principal,
l'equivalent exact de ce qu'est l'AfDB pour l'Afrique ou l'EBRD pour l'Est.

REGLE DU PROJET : SONDE AVANT COLLECTEUR.
Ne PAS ecrire un collecteur avant d'avoir verifie, DEPUIS L'INFRA GITHUB
ACTIONS, que la source est joignable et grattable. Deux precedents coutent
cher :
  - ADB   : 403 puis portail rendu en JavaScript -> collecteur inutile.
  - UNGM  : deux tours perdus faute d'avoir dumpe le HTML BRUT d'emblee.
Cette sonde dumpe donc systematiquement du BRUT.

CE QU'ELLE ETABLIT
------------------
  A. Quelles URL repondent (statut, type de contenu, taille).
  B. Le contenu est-il RENDU COTE SERVEUR, ou faut-il un navigateur ?
     Signal decisif : retrouve-t-on des noms de pays et des libelles d'avis
     dans le HTML brut, ou seulement un squelette et des <script> ?
  C. Existe-t-il une voie DONNEES (JSON/API/Socrata) plutot que du grattage ?
     Une API rend le collecteur dix fois plus robuste.
  D. Les avis portent-ils un identifiant, une date, un pays exploitables ?

AUCUNE ECRITURE. Sortie toujours en code 0 : une sonde ne doit jamais faire
echouer un workflow.
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
NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Candidates classees de la plus souhaitable (donnees structurees) a la moins
# souhaitable (grattage de page). On ne SAIT PAS laquelle existe : c'est
# precisement l'objet de la sonde. Une 404 est une reponse utile.
CANDIDATES = [
    # -- Voie donnees (ideale) --
    ("API projets IDB", "https://api.iadb.org/projects/v1/projects?limit=5"),
    ("Socrata mydata (avis)", "https://mydata.iadb.org/resource/p6f5-3xnr.json?$limit=5"),
    ("Socrata mydata (catalogue)", "https://mydata.iadb.org/api/catalog/v1?q=procurement&limit=5"),
    # -- Portail achats (grattage) --
    ("Portail achats projets", "https://projectprocurement.iadb.org/en/procurement-notices"),
    ("Portail achats (racine)", "https://projectprocurement.iadb.org/en"),
    ("Avis IDB (site principal)", "https://www.iadb.org/en/procurement-notices"),
    ("Projets IDB", "https://www.iadb.org/en/projects"),
    # -- Flux --
    ("RSS eventuel", "https://www.iadb.org/en/rss/procurement"),
]

# Marqueurs de contenu reellement utile (pays du perimetre + vocabulaire achat).
PAYS_CIBLES = ["Mexico", "Colombia", "Ecuador", "Peru", "Bolivia", "Honduras",
               "Guatemala", "Venezuela", "Brazil", "Argentina", "Chile"]
MOTS_ACHAT = ["procurement", "tender", "bidding", "notice", "contract",
              "consultant", "solicitation", "licitacion", "adquisicion"]

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


def _texte(html):
    t = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", str(html or ""))
    t = re.sub(r"(?i)</\s*(div|p|tr|td|th|li|h\d|span)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return "\n".join(l for l in (_plat(x) for x in t.split("\n")) if l)


def sonde_a(session):
    """A. Qui repond ? On teste toutes les candidates, sans a priori."""
    _titre("A. ACCESSIBILITE DEPUIS L'INFRA GITHUB ACTIONS")
    vivantes = []
    for nom, url in CANDIDATES:
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            ctype = _plat(r.headers.get("Content-Type", ""), 40)
            taille = len(r.content or b"")
            print("  [{}] {:28} {:6} | {:32} | {} octets".format(
                "OK " if r.status_code < 400 else "KO ", nom[:28],
                r.status_code, ctype, taille))
            if r.url != url:
                print("       redirige vers : {}".format(_plat(r.url, 90)))
            if r.status_code < 400 and taille > 500:
                vivantes.append((nom, r))
        except Exception as e:
            print("  [KO ] {:28} exception : {}".format(nom[:28], _plat(e, 70)))
    _verdict("accessibilite", bool(vivantes),
             "{} URL exploitable(s) sur {}".format(len(vivantes), len(CANDIDATES)))
    return vivantes


def sonde_b(vivantes):
    """B. Rendu serveur ou piege JavaScript ? (le point qui a tue ADB)"""
    _titre("B. CONTENU RENDU COTE SERVEUR ?")
    grattables = []
    for nom, r in vivantes:
        html = r.text or ""
        texte = _texte(html)
        pays = [p for p in PAYS_CIBLES if p.lower() in texte.lower()]
        mots = [m for m in MOTS_ACHAT if m in texte.lower()]
        scripts = len(re.findall(r"(?i)<script", html))
        # Un squelette JS : beaucoup de <script>, peu de texte utile.
        verdict = "RENDU SERVEUR" if (pays and mots and len(texte) > 2000) else "squelette probable"
        print("\n  --- {} ---".format(nom))
        print("      texte utile : {} caracteres | {} balises <script>".format(
            len(texte), scripts))
        print("      pays cibles trouves : {}".format(", ".join(pays[:8]) or "AUCUN"))
        print("      vocabulaire achat   : {}".format(", ".join(mots[:6]) or "AUCUN"))
        print("      => {}".format(verdict))
        if verdict == "RENDU SERVEUR":
            grattables.append((nom, r))
    _verdict("rendu serveur", bool(grattables),
             "{} source(s) grattable(s)".format(len(grattables)))
    return grattables


def sonde_c(vivantes):
    """C. Une voie DONNEES existe-t-elle ? Bien plus robuste qu'un grattage."""
    _titre("C. VOIE DONNEES (JSON / API)")
    trouvee = False
    for nom, r in vivantes:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "json" not in ctype:
            continue
        try:
            donnees = r.json()
        except Exception as e:
            print("  {} : JSON annonce mais illisible ({})".format(nom, _plat(e, 60)))
            continue
        trouvee = True
        echantillon = donnees[0] if isinstance(donnees, list) and donnees else donnees
        print("\n  --- {} : JSON VALIDE ---".format(nom))
        if isinstance(echantillon, dict):
            print("      champs disponibles ({}) :".format(len(echantillon)))
            for cle in sorted(echantillon)[:30]:
                print("        {:32} = {}".format(cle, _plat(echantillon[cle], 60)))
        else:
            print("      " + _plat(json.dumps(donnees)[:600]))
    _verdict("voie donnees", trouvee,
             "API JSON exploitable" if trouvee else "aucune API JSON, grattage a prevoir")
    return trouvee


def sonde_d(grattables):
    """D. HTML BRUT autour d'un avis : la lecon UNGM, ne jamais l'omettre."""
    _titre("D. HTML BRUT AUTOUR D'UN AVIS (balisage reel)")
    if not grattables:
        print("  (aucune source grattable : rien a dumper)")
        _verdict("balisage", False, "non evalue")
        return
    nom, r = grattables[0]
    html = r.text or ""
    ancre = None
    for mot in ("procurement-notice", "tender", "notice", "licitacion"):
        m = re.search(r"(?i)href=[\"'][^\"']*" + mot + r"[^\"']*[\"']", html)
        if m:
            ancre = m
            break
    print("  source : {}".format(nom))
    if ancre:
        debut = max(0, ancre.start() - 900)
        print("\n  --- extrait BRUT autour du premier lien d'avis ---")
        print(html[debut:ancre.end() + 1400])
    else:
        print("\n  --- aucun lien d'avis reconnu, extrait BRUT du corps ---")
        corps = re.search(r"(?is)<main.*?>(.*?)</main>", html) or \
            re.search(r"(?is)<body.*?>(.*?)</body>", html)
        print((corps.group(1) if corps else html)[:2200])
    liens = re.findall(r"(?i)href=[\"']([^\"']*(?:notice|tender|licitacion)[^\"']*)[\"']", html)
    uniques = sorted(set(liens))[:12]
    print("\n  liens d'avis reperes ({}) :".format(len(set(liens))))
    for l in uniques:
        print("    " + _plat(l, 100))
    _verdict("balisage", bool(uniques),
             "{} lien(s) d'avis identifie(s)".format(len(set(liens))))


def main():
    print("SONDE IDB (Banque interamericaine) -- aucune ecriture, lecture seule.")
    session = requests.Session()
    session.headers.update({
        "User-Agent": NAVIGATEUR,
        "Accept": "text/html,application/json,application/xhtml+xml,*/*",
        "Accept-Language": "en,es;q=0.9,fr;q=0.8",
    })
    vivantes = sonde_a(session)
    grattables = []
    if vivantes:
        grattables = sonde_b(vivantes)
        sonde_c(vivantes)
    sonde_d(grattables)

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  {:18} {:12} {}".format(nom, "OK" if ok else "a creuser", detail))
    print("\nDECISION ATTENDUE :")
    print("  - voie donnees OK        -> collecteur sur API (robuste, prioritaire)")
    print("  - rendu serveur OK       -> collecteur par grattage (motif AfDB/EBRD)")
    print("  - aucun des deux         -> desactiver comme ADB, chercher une")
    print("                              alternative regionale (CAF, UNDP/UNGM)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                            # une sonde n'echoue jamais
        print("Sonde interrompue : {}".format(e))
    sys.exit(0)
