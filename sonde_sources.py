# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v5 (jetable) : IsDB (Banque islamique de developpement).
=================================================================================

POURQUOI CETTE SOURCE
---------------------
IsDB finance dans EXACTEMENT la zone d'Amarante : Mauritanie, Guinee, Togo,
Benin, Centrafrique, Sierra Leone, Mozambique, Gambie, Tadjikistan, Azerbaidjan.
Et son portail publie un type d'avis "Contract Award", donc potentiellement des
TITULAIRES NOMMES, comme la Banque Mondiale.

CE QUE LA SONDE TRANCHE, AVANT D'ECRIRE LE MOINDRE COLLECTEUR
--------------------------------------------------------------
  A. Le contenu est-il DANS le HTML (rendu serveur, donc lisible) ou charge en
     JavaScript ? C'est la question qui a coute trois tours sur UNGM et qui a
     tue le collecteur ADB.
  B. Existe-t-il un flux structure (RSS, JSON:API Drupal) ? Ce serait bien
     meilleur qu'un grattage de page.
  C. Quelle est la structure d'une ligne : type d'avis, pays, date, titre ?
  D. Les avis d'attribution nomment-ils le titulaire ?

Les corps de reponse COURTS sont systematiquement imprimes : ne pas les avoir
imprimes a fait perdre deux tours sur UNGM.

Aucune ecriture. Sortie toujours en code 0.
LANCEMENT : workflow "Sonde sources" (declenchement manuel).
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
BASE = "https://www.isdb.org"
PAGE_TENDERS = BASE + "/project-procurement/tenders"
RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72); print(t); print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("EXPLOITABLE" if ok else "a creuser", detail))


def _plat(t, n=None):
    s = re.sub(r"\s+", " ", str(t or "")).strip()
    return s[:n] if n else s


def _texte(html):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(html or ""))
    t = re.sub(r"(?i)</\s*(div|p|tr|td|li|h\d|span)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    import html as _h
    return _h.unescape(t)


# ===========================================================================
# A -- Le contenu est-il dans le HTML ?
# ===========================================================================

def sonde_a(s):
    _titre("A -- IsDB : le contenu est-il rendu cote serveur ?")
    try:
        r = s.get(PAGE_TENDERS, timeout=TIMEOUT)
        html = r.text or ""
    except Exception as e:
        _verdict("A rendu", False, "page illisible : {}".format(e))
        return ""
    print("GET {} -> HTTP {} ({} octets)".format(PAGE_TENDERS, r.status_code, len(r.content)))

    # Marqueurs de contenu reel : types d'avis et pays doivent apparaitre.
    types = ["Contract Award", "General Procurement Notice", "Expression of Interest",
             "Specific Procurement Notice", "Pre-Qualification"]
    presents = [t for t in types if t.lower() in html.lower()]
    print("  types d'avis presents dans le HTML : {}".format(presents or "AUCUN"))
    pays_test = ["Mauritania", "Guinea", "Benin", "Sierra Leone", "Mozambique",
                 "Togo", "Gambia", "Tajikistan", "Central African Republic"]
    pays_vus = [p for p in pays_test if p.lower() in html.lower()]
    print("  pays reconnus dans le HTML : {}".format(pays_vus or "AUCUN"))
    lignes_tab = html.count("<tr") + html.count('role="row"') + html.count("views-row")
    print("  marqueurs de ligne (<tr>, role=row, views-row) : {}".format(lignes_tab))

    ok = bool(presents and pays_vus)
    _verdict("A rendu", ok,
             "contenu {} dans le HTML".format("PRESENT" if ok else "absent"))
    return html


# ===========================================================================
# B -- Un flux structure existe-t-il ?
# ===========================================================================

def sonde_b(s, html):
    _titre("B -- IsDB : flux RSS ou API structuree ?")
    candidats = [
        BASE + "/project-procurement/tenders/rss.xml",
        BASE + "/project-procurement/rss.xml",
        BASE + "/rss.xml",
        BASE + "/jsonapi",
        BASE + "/jsonapi/node/tender",
    ]
    # Un lien de flux declare dans la page prime sur nos suppositions.
    for m in re.findall(r'<link[^>]+type="application/(?:rss\+xml|atom\+xml)"[^>]+>',
                        html or "", re.I):
        u = re.search(r'href="([^"]+)"', m)
        if u:
            lien = u.group(1)
            candidats.insert(0, lien if lien.startswith("http") else BASE + lien)

    trouve = ""
    for u in candidats:
        try:
            r = s.get(u, timeout=TIMEOUT)
            corps = (r.text or "")[:400]
            print("  {} -> HTTP {} ({} octets)".format(u, r.status_code, len(r.content)))
            if r.status_code >= 400:
                continue
            print("      {}".format(_plat(corps, 240)))
            if "<item" in corps.lower() or "<entry" in corps.lower():
                trouve = u
                print("      *** FLUX RSS/ATOM EXPLOITABLE ***")
                break
            if corps.strip().startswith("{"):
                try:
                    data = json.loads(r.text)
                    if isinstance(data, dict) and data.get("data") is not None:
                        trouve = u
                        print("      *** API JSON EXPLOITABLE ***")
                        break
                except Exception:
                    pass
        except Exception as e:
            print("  {} -> exception {}".format(u, _plat(e, 60)))
    _verdict("B flux", bool(trouve), trouve or "aucun flux structure trouve.")


# ===========================================================================
# C -- Structure d'une ligne d'avis
# ===========================================================================

def sonde_c(s, html):
    _titre("C -- IsDB : structure d'une ligne d'avis")
    if not html:
        _verdict("C structure", False, "pas de HTML a analyser.")
        return
    # Drupal rend generalement ses listes en .views-row ou en <tr>.
    blocs = re.findall(r'<(?:div|tr)[^>]*class="[^"]*(?:views-row|tender)[^"]*"[^>]*>(.{80,1400}?)</(?:div|tr)>',
                       html, re.I | re.S)
    if not blocs:
        blocs = re.findall(r"<tr[^>]*>(.{80,1400}?)</tr>", html, re.I | re.S)
    print("  {} bloc(s) de ligne detecte(s).".format(len(blocs)))
    for i, b in enumerate(blocs[:4], 1):
        lignes = [l for l in (_plat(x) for x in _texte(b).split("\n")) if l]
        print("  --- ligne {} : {} champ(s) ---".format(i, len(lignes)))
        for j, l in enumerate(lignes[:10]):
            print("      [{}] {}".format(j, l[:110]))
    # Les liens de detail donnent la forme des URL a suivre.
    liens = sorted(set(re.findall(r'href="(/project-procurement/tenders/[^"]{4,120})"',
                                  html, re.I)))
    print("\n  liens de detail (echantillon) :")
    for l in liens[:8]:
        print("      {}".format(l))
    _verdict("C structure", bool(blocs), "{} ligne(s) lisible(s)".format(len(blocs)))


# ===========================================================================
# D -- Les attributions nomment-elles le titulaire ?
# ===========================================================================

def sonde_d(s, html):
    _titre("D -- IsDB : un avis d'ATTRIBUTION nomme-t-il le titulaire ?")
    liens = sorted(set(re.findall(r'href="(/project-procurement/tenders/[^"]{4,140})"',
                                  html or "", re.I)))
    # On privilegie une URL qui sent l'attribution.
    cible = next((l for l in liens if re.search(r"(?i)award|attribution", l)), None)
    if not cible and liens:
        cible = liens[0]
    if not cible:
        _verdict("D titulaire", False, "aucun lien de detail a suivre.")
        return
    url = BASE + cible
    try:
        r = s.get(url, timeout=TIMEOUT)
        print("GET {} -> HTTP {} ({} octets)".format(url, r.status_code, len(r.content)))
        texte = _texte(r.text)
        lignes = [l for l in (_plat(x) for x in texte.split("\n")) if l]
        print("\n  Premieres lignes de la fiche :")
        for l in lignes[:35]:
            print("      {}".format(l[:120]))
        marqueurs = [m for m in ("awarded", "contractor", "supplier", "winner",
                                 "attributaire", "titulaire", "contract award",
                                 "amount", "montant", "value")
                     if m in texte.lower()]
        print("\n  marqueurs de titulaire/montant : {}".format(marqueurs or "AUCUN"))
        _verdict("D titulaire", bool(marqueurs),
                 "marqueurs trouves : {}".format(marqueurs or "aucun"))
    except Exception as e:
        _verdict("D titulaire", False, "fiche illisible : {}".format(e))


def main():
    print("SONDE v5 -- IsDB (aucune ecriture)")
    s = requests.Session()
    s.headers.update({"User-Agent": NAVIGATEUR, "Accept-Language": "en-US,en;q=0.9"})
    html = ""
    try:
        html = sonde_a(s) or ""
    except Exception as e:
        _verdict("A rendu", False, "exception : {}".format(e))
    for f in (sonde_b, sonde_c, sonde_d):
        try:
            f(s, html)
        except Exception as e:
            _verdict(f.__name__, False, "exception non rattrapee : {}".format(e))
    _titre("VERDICT v5")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {} -- {}".format("OUI" if ok else "NON", nom, detail))
    print("\nSi A est PRESENT, la source est grattable sans piege JavaScript.")
    print("Si B trouve un flux, c'est encore mieux : pas de grattage du tout.")


if __name__ == "__main__":
    main()
