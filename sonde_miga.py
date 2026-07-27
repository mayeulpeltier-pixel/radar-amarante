# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE MIGA (jetable) : valider AVANT le collecteur.
=========================================================================

MIGA (Multilateral Investment Guarantee Agency, Groupe Banque Mondiale) assure
les investissements PRIVES TRANSFRONTALIERS contre le risque politique. Chaque
projet nomme le Guarantee Holder (l'investisseur), son pays d'origine, le pays
hote, le secteur, le montant. Par statut, l'investisseur est TOUJOURS etranger
au pays hote : c'est du signal ICP pur (l'inverse de Prozorro, domestique). Les
SPG (Summary of Proposed Guarantee) sont publies 30-60 j AVANT le board =
signal precoce.

Reconnaissance web deja faite : la donnee est nominative et etrangere. Cette
sonde valide ce que seule l'infra CI peut trancher (lecon ADB : un portail
accessible depuis un poste peut etre bloque ou vide depuis GitHub Actions), et
DUMPE LE BRUT pour concevoir le collecteur sur du reel, pas sur des suppositions
(lecon UNGM).

CE QUE CETTE SONDE TRANCHE :
  A. L'infra CI atteint-elle miga.org/projects (statut 200, contenu non vide) ?
     Combien de cartes projet, quels types de document (SPG / Project Brief /
     ESRS), pagination ?
  B. Le bouton "Download CSV" cache-t-il un endpoint structure ? (scrape HTML
     vs parsing CSV : decision d'architecture majeure pour le collecteur)
  C. La liste porte-t-elle, de facon parsable, le lien vers la fiche detail ?
  D. La fiche detail porte-t-elle en clair Guarantee Holder + Investor Country
     + montant ? (le coeur du signal ICP)

Aucune ecriture, aucun LLM. Ne depend que de `requests` (pas de parseur HTML :
on dumpe des tranches brutes et on compte par motif). Sortie toujours code 0.
Jetable : a supprimer une fois le collecteur ecrit (ou la source abandonnee).
"""

import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

BASE = "https://www.miga.org"
LISTE = BASE + "/projects"
TIMEOUT = 45
NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Echantillon de pays hotes du perimetre Amarante, pour mesurer le recouvrement.
ZONES_AMARANTE = [
    "Ukraine", "Nigeria", "Congo", "Mali", "Burkina", "Niger", "Somalia",
    "Sudan", "Libya", "Yemen", "Iraq", "Mozambique", "Burundi", "Jordan",
    "Chad", "Mauritania", "Haiti", "Venezuela", "Colombia", "Afghanistan",
]

RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OK" if ok else "a creuser", detail))


def _tranche(txt, motif, avant=200, apres=300):
    """Renvoie une tranche BRUTE autour de la 1re occurrence d'un motif, pour
    voir le vrai balisage HTML sans le supposer."""
    i = txt.find(motif)
    if i == -1:
        return ""
    debut = max(0, i - avant)
    return txt[debut:i + apres]


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": NAVIGATEUR,
                      "Accept": "text/html,application/xhtml+xml"})
    return s


# ===========================================================================
# A -- Accessibilite CI + structure de la liste
# ===========================================================================

def sonde_a(s):
    _titre("A -- MIGA /projects : acces depuis CI, cartes, types de document")
    try:
        r = s.get(LISTE, timeout=TIMEOUT)
    except Exception as e:
        _verdict("A acces", False, "portail injoignable depuis CI : {}".format(e))
        return None
    print("HTTP {} sur {} ({} octets)".format(r.status_code, LISTE, len(r.text)))
    if r.status_code != 200 or not r.text:
        _verdict("A acces", False, "statut/contenu inattendu")
        return None
    html = r.text

    liens = re.findall(r'href="(/project/[^"#?]+)"', html)
    liens = list(dict.fromkeys(liens))            # unique, ordre conserve
    n_spg = len(re.findall(r"\bSPG\b", html))
    n_brief = len(re.findall(r"Project Brief", html))
    n_esrs = len(re.findall(r"\bESRS\b", html))
    pagination = bool(re.search(r"[?&]page=\d+", html) or "Last page" in html)

    print("Cartes 'Project Brief' : {}".format(n_brief))
    print("Mentions 'SPG' (proposes, signal precoce) : {}".format(n_spg))
    print("Mentions 'ESRS' (revue E&S, pre-board) : {}".format(n_esrs))
    print("Liens fiche /project/... uniques sur la page : {}".format(len(liens)))
    print("Pagination detectee : {}".format(pagination))
    if liens:
        print("\nExemples de liens fiche :")
        for l in liens[:5]:
            print("   ", l)
        print("\nTranche BRUTE autour du 1er lien (balisage reel) :")
        print(_tranche(html, liens[0], avant=260, apres=200))

    ok = len(liens) > 0
    _verdict("A acces", ok,
             "{} fiches, SPG={}, Brief={} : liste exploitable".format(len(liens), n_spg, n_brief)
             if ok else "aucun lien fiche trouve, structure a revoir")
    return (html, liens)


# ===========================================================================
# B -- Endpoint CSV structure ?
# ===========================================================================

def sonde_b(s, html):
    _titre("B -- MIGA : endpoint 'Download CSV' (scrape HTML vs parsing CSV)")
    # On cherche tout href qui sent le CSV / l'export.
    candidats = re.findall(r'href="([^"]*(?:csv|export|download)[^"]*)"', html, re.I)
    candidats = list(dict.fromkeys(candidats))
    if candidats:
        print("Endpoints candidats reperes :")
        for c in candidats[:10]:
            print("   ", c if c.startswith("http") else BASE + c)
    else:
        print("Aucun href CSV/export dans le HTML de la liste.")
        print("(Le bouton 'Download CSV' peut etre injecte en JS -> a confirmer ;")
        print(" repli : parsing HTML des cartes, deja demontre exploitable.)")

    # On tente aussi une variante _format=csv (Drupal Views expose souvent cela).
    for essai in (LISTE + "?_format=csv", LISTE + "/export/csv"):
        try:
            rr = s.get(essai, timeout=TIMEOUT)
            tete = rr.text[:120].replace("\n", " ")
            estimation_csv = ("," in tete and "<" not in tete[:40])
            print("\nTest {} -> HTTP {} | debut : {}".format(essai, rr.status_code, tete))
            if rr.status_code == 200 and estimation_csv:
                _verdict("B csv", True, "CSV structure servi sur {}".format(essai))
                return
        except Exception as e:
            print("Test {} -> echec ({})".format(essai, e))

    _verdict("B csv", bool(candidats),
             "endpoint(s) CSV candidat(s) a tester" if candidats
             else "pas de CSV evident : collecteur en scrape HTML (facile, HTML server-rendered)")


# ===========================================================================
# C + D -- Fiche detail : Guarantee Holder + Investor Country + montant
# ===========================================================================

def sonde_cd(s, liens):
    _titre("C+D -- MIGA : fiche detail (Guarantee Holder, Investor Country, montant)")
    if not liens:
        _verdict("C+D fiche", False, "aucun lien fiche a inspecter")
        return
    url = BASE + liens[0]
    try:
        r = s.get(url, timeout=TIMEOUT)
    except Exception as e:
        _verdict("C+D fiche", False, "fiche injoignable : {}".format(e))
        return
    print("HTTP {} sur {}".format(r.status_code, url))
    html = r.text

    a_gh = "Guarantee Holder" in html
    a_ic = "Investor Country" in html
    a_secteur = ("Sector" in html) or ("Project Type" in html)
    a_montant = bool(re.search(r"(US\$|EUR|USD|€)\s?[\d,\.]+", html))

    print("Champ 'Guarantee Holder' present : {}".format(a_gh))
    print("Champ 'Investor Country' present : {}".format(a_ic))
    print("Secteur / Project Type present  : {}".format(a_secteur))
    print("Montant (US$/EUR...) present     : {}".format(a_montant))

    for champ in ("Guarantee Holder", "Investor Country"):
        tr = _tranche(html, champ, avant=40, apres=260)
        if tr:
            print("\nTranche BRUTE autour de '{}' :".format(champ))
            print(re.sub(r"\s+", " ", tr))

    ok = a_gh and (a_ic or a_montant)
    _verdict("C+D fiche", ok,
             "fiche nominative exploitable (Guarantee Holder + pays/montant)"
             if ok else "champs structures introuvables tels quels, extraction a revoir")


# ===========================================================================
# E -- Recouvrement zones Amarante (rapide, sur la page liste)
# ===========================================================================

def sonde_e(html):
    _titre("E -- Recouvrement avec les zones Amarante (page liste)")
    if not html:
        _verdict("E zones", False, "pas de HTML")
        return
    presents = {z: len(re.findall(re.escape(z), html)) for z in ZONES_AMARANTE}
    presents = {z: n for z, n in presents.items() if n}
    print("Pays du perimetre cites sur la page 1 :", presents or "(aucun sur cette page)")
    _verdict("E zones", bool(presents),
             "recouvrement direct avec le perimetre" if presents
             else "aucun pays du perimetre en page 1 (elargir l'echantillon)")


def main():
    print("SONDE MIGA -- diagnostic, aucune ecriture, aucun LLM.")
    s = _session()
    res_a = sonde_a(s)
    html = res_a[0] if res_a else ""
    liens = res_a[1] if res_a else []
    if html:
        sonde_b(s, html)
        sonde_cd(s, liens)
        sonde_e(html)

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {:14} {}".format("OK " if ok else "?? ", nom, detail))
    print("\n  LECTURE :")
    print("   - acces + fiche nominative OK -> ecrire le collecteur MIGA")
    print("     (SPG comme signal precoce, filtre pays perimetre + secteur,")
    print("      LLM pour le besoin surete). Volume faible = budget negligeable.")
    print("   - CSV structure trouve        -> collecteur trivial (parsing CSV).")
    print("     sinon                        -> scrape HTML server-rendered (facile).")
    print("\nSonde jetable : a supprimer une fois la decision prise.")
    sys.exit(0)


if __name__ == "__main__":
    main()
