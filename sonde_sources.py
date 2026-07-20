# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v4 (jetable) : finir les deux pistes ouvertes.
======================================================================

La sonde v3 a ouvert deux portes. Celle-ci les franchit.

  A. UNGM. Le bundle `ungmcommon` (662 Ko) contient bien l'objet
     `window.UNGM.ContractAwardSearch = { pageIndex, selectedCountries, ... }`.
     La fenetre d'affichage de la v3 etait trop etroite pour capturer la
     FONCTION qui lance la requete. On dumpe donc l'objet en entier et tous
     les appels AJAX du fichier qui mentionnent "Award".

  B. IATI (miroir ouvert Code for IATI, accessible SANS cle : verifie).
     Attention a la qualite : beaucoup de transactions sont AGREGEES
     ("Total expenditure to date") et ne nomment aucune contrepartie. On
     mesure donc la PROPORTION de transactions qui nomment un vrai
     fournisseur, agence ONU par agence ONU, sur des pays a risque.

Aucune ecriture. Sortie toujours en code 0.
LANCEMENT : workflow "Sonde sources" (declenchement manuel).
"""

import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

TIMEOUT = 60
NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGE_AWARDS = "https://www.ungm.org/Public/ContractAward"
RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72); print(t); print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("RESOLU" if ok else "non resolu", detail))


def _plat(t, n=None):
    s = " ".join(str(t or "").split())
    return s[:n] if n else s


# ===========================================================================
# A -- Dumper l'objet de recherche UNGM en entier
# ===========================================================================

def sonde_a(s):
    _titre("A -- UNGM : contenu COMPLET de UNGM.ContractAwardSearch")
    try:
        html = s.get(PAGE_AWARDS, timeout=TIMEOUT).text or ""
    except Exception as e:
        _verdict("A UNGM", False, "page illisible : {}".format(e))
        return

    srcs = re.findall(r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']', html, re.I)
    cible = None
    for src in srcs:
        if "ungmcommon" not in src.lower():
            continue
        cible = src if src.startswith("http") else (
            "https://www.ungm.org" + ("" if src.startswith("/") else "/") + src)
        break
    if not cible:
        _verdict("A UNGM", False, "bundle ungmcommon introuvable dans la page.")
        return

    try:
        code = s.get(cible, timeout=TIMEOUT).text or ""
    except Exception as e:
        _verdict("A UNGM", False, "bundle illisible : {}".format(e))
        return
    print("Bundle recupere : {} octets".format(len(code)))

    # 1. L'objet complet : c'est la que vit la fonction de recherche.
    m = re.search(r"UNGM\.ContractAwardSearch\s*=\s*\{", code)
    if m:
        extrait = code[m.start():m.start() + 9000]
        print("\n[A1] Objet UNGM.ContractAwardSearch (9 000 premiers caracteres) :")
        print("-" * 72)
        print(_plat(extrait))
        print("-" * 72)
    else:
        print("\n[A1] objet UNGM.ContractAwardSearch introuvable.")

    # 2. Tous les appels AJAX du fichier mentionnant "Award".
    print("\n[A2] Appels AJAX/POST mentionnant 'Award' :")
    trouves = []
    for m in re.finditer(r"\$\.(?:ajax|post|get)\s*\(", code):
        bloc = code[m.start():m.start() + 700]
        if not re.search(r"(?i)award", bloc):
            continue
        trouves.append(_plat(bloc, 620))
    for t in trouves[:8]:
        print("    - {}".format(t))
    if not trouves:
        print("    (aucun)")

    # 3. Toute URL citee a proximite du mot Award.
    print("\n[A3] Chemins cites pres de 'Award' :")
    chemins = set()
    for m in re.finditer(r"(?i)award", code):
        fenetre = code[max(0, m.start() - 260): m.start() + 260]
        for u in re.findall(r'["\'](\/[A-Za-z0-9_\-/]{4,90})["\']', fenetre):
            chemins.add(u)
    for c in sorted(chemins)[:30]:
        print("    {}".format(c))

    _verdict("A UNGM", bool(m or trouves),
             "objet dumpe : lire [A1]/[A2] pour l'URL et la charge utile.")


# ===========================================================================
# B -- IATI : mesurer la proportion de fournisseurs NOMMES
# ===========================================================================

BASE_IATI = "https://datastore.codeforiati.org/api/1/access/transaction.csv"
# Identifiants IATI des agences ONU qui deploient en zone a risque.
AGENCES = [("PNUD", "XM-DAC-41114"), ("UNICEF", "XM-DAC-41122"),
           ("PAM", "XM-DAC-41140"), ("UNOPS", "XM-DAC-41127"),
           ("HCR", "XM-DAC-41121"), ("OIM", "XM-DAC-47066")]
PAYS = [("Mali", "ML"), ("Somalie", "SO"), ("Soudan du Sud", "SS"),
        ("RDC", "CD"), ("Afghanistan", "AF"), ("Ukraine", "UA")]


def _colonnes(entete):
    return [c.strip().strip('"') for c in entete.split(",")]


def sonde_b(s):
    _titre("B -- IATI : les agences ONU nomment-elles leurs fournisseurs ?")
    import csv, io

    # B1. Structure : quelles colonnes servent a identifier une contrepartie ?
    try:
        r = s.get(BASE_IATI, params={"recipient-country": "ML", "limit": 3},
                  timeout=TIMEOUT)
        lignes = (r.text or "").splitlines()
        if lignes:
            cols = _colonnes(lignes[0])
            interessantes = [c for c in cols if re.search(
                r"(?i)provider|receiver|participating|reporting|description|"
                r"transaction-type|value", c)]
            print("[B1] {} colonnes. Colonnes utiles :".format(len(cols)))
            for c in interessantes:
                print("     {}".format(c))
    except Exception as e:
        print("[B1] exception : {}".format(e))

    # B2. Proportion de transactions nommant un beneficiaire, par agence.
    print("\n[B2] Part des transactions qui NOMMENT une contrepartie :")
    total_ok = 0
    for nom_ag, ident in AGENCES:
        for nom_p, iso2 in PAYS[:3]:
            try:
                r = s.get(BASE_IATI, params={
                    "reporting-org": ident, "recipient-country": iso2,
                    "transaction-type": "3", "limit": 40}, timeout=TIMEOUT)
                if r.status_code >= 400:
                    print("    {:7} {:14} HTTP {}".format(nom_ag, nom_p, r.status_code))
                    continue
                texte = r.text or ""
                lecteur = list(csv.DictReader(io.StringIO(texte)))
                if not lecteur:
                    print("    {:7} {:14} aucune transaction".format(nom_ag, nom_p))
                    continue
                cle_recv = next((c for c in lecteur[0]
                                 if c and re.search(r"(?i)receiver.*org", c)), None)
                nommes = [l for l in lecteur
                          if cle_recv and (l.get(cle_recv) or "").strip()]
                print("    {:7} {:14} {:3} transaction(s), {:3} avec beneficiaire nomme"
                      .format(nom_ag, nom_p, len(lecteur), len(nommes)))
                for l in nommes[:2]:
                    desc = next((l[c] for c in l if c and "description" in c.lower()
                                 and l[c]), "")
                    print("            -> {} | {} | {}".format(
                        _plat(l.get(cle_recv), 40), l.get("transaction-value", ""),
                        _plat(desc, 46)))
                total_ok += len(nommes)
            except Exception as e:
                print("    {:7} {:14} exception {}".format(nom_ag, nom_p, _plat(e, 60)))

    _verdict("B IATI", total_ok > 0,
             "{} transaction(s) avec beneficiaire nomme au total.".format(total_ok))


def main():
    print("SONDE v4 -- finir les deux pistes ouvertes (aucune ecriture)")
    s = requests.Session()
    s.headers.update({"User-Agent": NAVIGATEUR, "Accept-Language": "en-US,en;q=0.9"})
    for f in (sonde_a, sonde_b):
        try:
            f(s)
        except Exception as e:
            _verdict(f.__name__, False, "exception non rattrapee : {}".format(e))
    _titre("VERDICT v4")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {} -- {}".format("OUI" if ok else "NON", nom, detail))


if __name__ == "__main__":
    main()
