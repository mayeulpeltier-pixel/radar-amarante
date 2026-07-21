# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v6 (jetable) : IsDB, finir le diagnostic.
==================================================================

La v5 a etabli l'essentiel : le contenu est RENDU COTE SERVEUR (pas de piege
JavaScript comme UNGM ou ADB), les cinq types d'avis et les pays de la zone
Amarante sont dans le HTML. La source est donc grattable.

Trois points restaient flous, par insuffisance de MA sonde :
  A. Structure des lignes. La v5 decoupait des CELLULES (150 blocs a un seul
     champ) au lieu de lignes. On dumpe donc le HTML BRUT autour d'un avis
     pour voir le balisage reel.
  B. Fiche d'attribution. La v5 n'affichait que les 35 premieres lignes, toutes
     occupees par le menu de navigation. On saute la navigation et on dumpe le
     CORPS de la fiche : c'est la que se trouverait le titulaire.
  C. Filtrage et pagination. Les URL vues (/tenders/2024/contract-award/...)
     suggerent une taxonomie Drupal. Si on peut filtrer par type d'avis et
     paginer, le collecteur ne rapatriera que l'utile.

Le HTML brut est imprime tel quel : ne pas l'avoir fait a coute deux tours
sur UNGM.

Aucune ecriture. Sortie toujours en code 0.
"""

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
BASE = "https://www.isdb.org"
PAGE_TENDERS = BASE + "/project-procurement/tenders"
RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72); print(t); print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OK" if ok else "a creuser", detail))


def _plat(t, n=None):
    s = re.sub(r"\s+", " ", str(t or "")).strip()
    return s[:n] if n else s


def _texte(html):
    t = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", str(html or ""))
    t = re.sub(r"(?i)</\s*(div|p|tr|td|th|li|h\d|span|dt|dd)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    import html as _h
    return _h.unescape(t)


# ===========================================================================
# A -- Balisage REEL d'une ligne d'avis
# ===========================================================================

def sonde_a(s):
    _titre("A -- IsDB : balisage brut autour d'un avis")
    try:
        html = s.get(PAGE_TENDERS, timeout=TIMEOUT).text or ""
    except Exception as e:
        _verdict("A balisage", False, "page illisible : {}".format(e))
        return ""
    print("Page : {} octets".format(len(html)))

    # On se cale sur la premiere occurrence d'un type d'avis et on montre le
    # balisage autour : c'est lui qui dira comment decouper les lignes.
    m = re.search(r"(?i)contract award", html)
    if not m:
        m = re.search(r"(?i)general procurement notice", html)
    if m:
        deb, fin = max(0, m.start() - 1800), min(len(html), m.end() + 1200)
        print("\n[A1] HTML BRUT autour du premier avis (3 000 caracteres) :")
        print("-" * 72)
        print(_plat(html[deb:fin], 3000))
        print("-" * 72)

    # Classes de conteneur les plus frequentes : candidates au decoupage.
    classes = re.findall(r'class="([^"]{3,90})"', html)
    from collections import Counter
    freq = Counter(c.strip() for c in classes)
    print("\n[A2] Classes les plus repetees (candidates pour decouper les lignes) :")
    for cls, n in freq.most_common(18):
        if n >= 5:
            print("    {:4}x  {}".format(n, cls[:80]))
    _verdict("A balisage", bool(m), "balisage dumpe, lire [A1]/[A2].")
    return html


# ===========================================================================
# B -- Le CORPS d'une fiche d'attribution
# ===========================================================================

def sonde_b(s, html):
    _titre("B -- IsDB : le corps d'une fiche d'ATTRIBUTION nomme-t-il le titulaire ?")
    liens = sorted(set(re.findall(
        r'href="(/project-procurement/tenders/[^"]{4,160})"', html or "", re.I)))
    awards = [l for l in liens if "contract-award" in l.lower()]
    if not awards:
        _verdict("B fiche", False, "aucun lien d'attribution dans la page.")
        return
    url = BASE + awards[0]
    try:
        r = s.get(url, timeout=TIMEOUT)
        page = r.text or ""
        print("GET {} -> HTTP {} ({} octets)".format(url, r.status_code, len(r.content)))
    except Exception as e:
        _verdict("B fiche", False, "fiche illisible : {}".format(e))
        return

    # On isole la region de contenu : la navigation occupait tout le dump v5.
    region = page
    for motif in (r'(?is)<main[^>]*>(.*?)</main>',
                  r'(?is)<article[^>]*>(.*?)</article>',
                  r'(?is)<div[^>]*class="[^"]*(?:node__content|field--name-body|'
                  r'region-content|main-content)[^"]*"[^>]*>(.*?)</div>\s*</div>'):
        mm = re.search(motif, page)
        if mm and len(mm.group(1)) > 400:
            region = mm.group(1)
            print("  region de contenu isolee ({} octets).".format(len(region)))
            break

    lignes = [l for l in (_plat(x) for x in _texte(region).split("\n")) if l]
    # On saute le chrome de navigation, repere par ses libelles recurrents.
    chrome = {"home", "tenders", "documents", "search", "register", "log in",
              "english", "français", "learning resources", "consultants portal",
              "vendor registration", "skip to main content", "isdb",
              "project procurement", "main navigation", "user account menu"}
    utiles = [l for l in lignes if l.strip().lower() not in chrome and len(l) > 2]
    print("\n[B1] Corps de la fiche ({} lignes utiles, 60 premieres) :".format(len(utiles)))
    for l in utiles[:60]:
        print("    {}".format(l[:130]))

    bas = _texte(region).lower()
    marqueurs = [m for m in ("awarded to", "contractor", "supplier", "winner",
                             "successful bidder", "attributaire", "titulaire",
                             "amount", "montant", "contract value", "price",
                             "company", "firm")
                 if m in bas]
    print("\n[B2] Marqueurs de titulaire/montant dans le CORPS : {}".format(
        marqueurs or "AUCUN"))
    # Les fiches renvoient souvent vers un document : c'est peut-etre la que
    # figure le nom du titulaire.
    docs = sorted(set(re.findall(r'href="([^"]+\.(?:pdf|docx?|xlsx?))"', page, re.I)))
    print("[B3] Documents joints : {}".format(docs[:6] or "aucun"))
    _verdict("B fiche", bool(marqueurs),
             "marqueurs : {}".format(marqueurs or "aucun"))


# ===========================================================================
# C -- Filtrage par type d'avis et pagination
# ===========================================================================

def sonde_c(s):
    _titre("C -- IsDB : peut-on filtrer les attributions et paginer ?")
    essais = [
        ("page 2", PAGE_TENDERS + "?page=1"),
        ("filtre type (texte)", PAGE_TENDERS + "?type=contract-award"),
        ("filtre type (champ)", PAGE_TENDERS + "?field_tender_type=contract-award"),
        ("filtre pays", PAGE_TENDERS + "?country=Mali"),
        ("taxonomie award", BASE + "/project-procurement/taxonomy/term/207"),
    ]
    for nom, url in essais:
        try:
            r = s.get(url, timeout=TIMEOUT)
            page = r.text or ""
            n_award = len(re.findall(r"(?i)contract award", page))
            n_gpn = len(re.findall(r"(?i)general procurement notice", page))
            n_liens = len(set(re.findall(
                r'href="/project-procurement/tenders/[^"]{4,160}"', page)))
            print("  {:22} -> HTTP {} | {} liens | 'contract award' x{} | 'GPN' x{}"
                  .format(nom, r.status_code, n_liens, n_award, n_gpn))
        except Exception as e:
            print("  {:22} -> exception {}".format(nom, _plat(e, 60)))
    _verdict("C filtrage", True, "comparer les compteurs ci-dessus.")


def main():
    print("SONDE v6 -- IsDB : finir le diagnostic (aucune ecriture)")
    s = requests.Session()
    s.headers.update({"User-Agent": NAVIGATEUR, "Accept-Language": "en-US,en;q=0.9"})
    html = ""
    try:
        html = sonde_a(s) or ""
    except Exception as e:
        _verdict("A balisage", False, "exception : {}".format(e))
    try:
        sonde_b(s, html)
    except Exception as e:
        _verdict("B fiche", False, "exception : {}".format(e))
    try:
        sonde_c(s)
    except Exception as e:
        _verdict("C filtrage", False, "exception : {}".format(e))
    _titre("VERDICT v6")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {} -- {}".format("OUI" if ok else "NON", nom, detail))


if __name__ == "__main__":
    main()
