# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v11 (jetable, LA DERNIERE) : forme des requetes IDB.
=============================================================================

CE QUI EST DEJA ETABLI, PREUVES A L'APPUI
-----------------------------------------
  - Grattage iadb.org : MORT. Cloudflare bloque l'IP du runner (plage Azure),
    sept variantes testees en vain.
  - data.iadb.org : CKAN OUVERT et complet (package_list, status_show,
    sitemap de 914 Ko). MAIS son contenu est de la donnee de RECHERCHE
    (indices Infrascope, enquetes marche du travail, bases climat), pas de la
    commande publique. A verifier une derniere fois ici, sans y croire.
  - d-portal.org : OUVERT et RICHE. Un enregistrement reel obtenu :
        aid        = XI-IATI-IADB-BR-L1607
        title      = State of Sao Paulo Highway Investment Program
        commitment = 480 133 500 USD
        day_start  = 20069  (soit le 12/12/2024)
    C'est exactement un signal de prospect : un programme routier de 480 M USD,
    connu AVANT publication des marches.

CE QU'IL RESTE A VERIFIER, ET RIEN D'AUTRE
------------------------------------------
Le collecteur a besoin de quatre choses. Cette sonde les valide, puis on code.
  A. FILTRER PAR PAYS. La table `act` de d-portal ne porte pas de colonne
     pays (le select country_code a renvoye une erreur). Le filtre passe
     probablement par un parametre qui declenche une jointure. A confirmer,
     sur les pays du perimetre Amarante.
  B. FILTRER PAR FRAICHEUR. Les dates sont des JOURS depuis 1970 : 20069 =
     12/12/2024, aujourd'hui = 20656. Sans filtre de recence, on rapatrierait
     tout l'historique.
  C. IDENTIFIER LES ORGANISATIONS. Le vrai prospect n'est pas la banque, c'est
     l'OPERATEUR qui met en oeuvre. Ce champ n'etait pas dans les 20 colonnes
     de `act` : il faut trouver ou il vit.
  D. SECTEUR. Pour ecarter la sante ou l'education et garder infrastructure,
     energie, transport, extractif : les chantiers a besoin de surete.

APRES CETTE SONDE, ON CODE. Aucune ecriture, sortie toujours en code 0.
"""

import json
import re
import sys
from datetime import date, timedelta

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

TIMEOUT = 45
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

Q = "https://d-portal.org/q"
IDB = "XI-IATI-IADB"

# Perimetre commercial Amarante, en ISO2 (le standard IATI pour les pays).
PAYS_ISO2 = ["MX", "VE", "EC", "HN", "CO", "GT", "PE", "BO", "BR", "AR", "CL"]

AUJOURD_HUI = (date.today() - date(1970, 1, 1)).days
IL_Y_A_18_MOIS = ((date.today() - timedelta(days=548)) - date(1970, 1, 1)).days

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


def _jour(n):
    try:
        return (date(1970, 1, 1) + timedelta(days=int(n))).isoformat()
    except Exception:
        return "?"


def _interroger(session, params, etiquette):
    """Appel d-portal + compte-rendu compact. Renvoie (lignes, erreur)."""
    p = dict(params)
    p.setdefault("form", "json")
    try:
        r = session.get(Q, params=p, timeout=TIMEOUT)
    except Exception as e:
        print("  [KO] {:34} exception : {}".format(etiquette, _plat(e, 45)))
        return [], str(e)
    if r.status_code >= 400:
        print("  [KO] {:34} statut {}".format(etiquette, r.status_code))
        return [], "statut {}".format(r.status_code)
    try:
        donnees = r.json()
    except Exception:
        print("  [KO] {:34} reponse non JSON".format(etiquette))
        return [], "non JSON"
    lignes = donnees.get("rows") or []
    err = _plat(donnees.get("err"), 90)
    print("  [{}] {:34} {} ligne(s) | total {}{}".format(
        "OK" if lignes else "--", etiquette, len(lignes),
        donnees.get("count", "?"), " | err: " + err if err else ""))
    return lignes, err


def sonde_a(session):
    """A. FILTRER PAR PAYS : le point le plus critique."""
    _titre("A. FILTRAGE PAR PAYS (le collecteur en depend entierement)")
    gagnant = None
    # Plusieurs noms de parametre possibles : on essaie, on ne suppose pas.
    for nom_param in ("country_code", "recipient_country_code", "country"):
        lignes, _e = _interroger(
            session, {"reporting_ref": IDB, nom_param: "CO", "limit": 3},
            "parametre {}=CO".format(nom_param))
        if lignes:
            gagnant = nom_param
            print("      exemple : {} | {}".format(
                _plat(lignes[0].get("title"), 56),
                _plat(lignes[0].get("aid"), 30)))
            break
    if not gagnant:
        # Repli : interroger la table country directement.
        lignes, _e = _interroger(
            session, {"from": "country", "country_code": "CO", "limit": 3},
            "table country directe")
        if lignes:
            print("      colonnes : " + ", ".join(sorted(lignes[0])[:14]))

    if gagnant:
        print("\n  --- volumetrie par pays du perimetre (parametre {}) ---".format(gagnant))
        total = 0
        for iso2 in PAYS_ISO2:
            lignes, _e = _interroger(
                session, {"reporting_ref": IDB, gagnant: iso2, "limit": 1},
                "  {}".format(iso2))
            total += 1 if lignes else 0
    _verdict("filtre pays", bool(gagnant),
             "parametre {!r}".format(gagnant) if gagnant else "aucun parametre ne filtre")
    return gagnant


def sonde_b(session):
    """B. FRAICHEUR : ne pas rapatrier vingt ans d'historique."""
    _titre("B. FILTRAGE PAR FRAICHEUR (dates = jours depuis 1970)")
    print("  aujourd'hui = {} | il y a 18 mois = {}".format(
        AUJOURD_HUI, IL_Y_A_18_MOIS))
    marche = None
    for essai in ({"day_start_gt": IL_Y_A_18_MOIS},
                  {"day_start_min": IL_Y_A_18_MOIS},
                  {"day_end_gt": AUJOURD_HUI},
                  {"day_start": ">{}".format(IL_Y_A_18_MOIS)}):
        params = {"reporting_ref": IDB, "limit": 3}
        params.update(essai)
        lignes, err = _interroger(session, params, str(essai))
        if lignes and not err:
            dates = [_jour(l.get("day_start")) for l in lignes]
            print("      dates obtenues : " + ", ".join(dates))
            marche = essai
            break
    # A defaut de filtre serveur, le tri suffit-il ?
    if not marche:
        lignes, _e = _interroger(
            session, {"reporting_ref": IDB, "limit": 5, "orderby": "day_start desc"},
            "tri day_start desc")
        if lignes:
            print("      dates : " + ", ".join(
                _jour(l.get("day_start")) for l in lignes))
    _verdict("fraicheur", bool(marche),
             str(marche) if marche else "pas de filtre serveur, filtrer en local")
    return marche


def sonde_c(session):
    """C. QUI MET EN OEUVRE : le vrai prospect, pas la banque."""
    _titre("C. ORGANISATIONS PARTICIPANTES (l'operateur = le prospect)")
    trouve = False
    # 1. Toutes les colonnes disponibles sur une activite.
    lignes, _e = _interroger(session, {"reporting_ref": IDB, "limit": 1},
                             "activite complete")
    if lignes:
        print("      colonnes de `act` ({}) : {}".format(
            len(lignes[0]), ", ".join(sorted(lignes[0]))))
    # 2. Tables annexes de d-portal.
    for table in ("organisation", "act_participating", "participating", "sector"):
        l2, err = _interroger(session, {"from": table, "limit": 2},
                              "table {}".format(table))
        if l2:
            trouve = True
            print("      colonnes : " + ", ".join(sorted(l2[0])[:16]))
            print("      exemple  : " + _plat(json.dumps(l2[0])[:260]))
    _verdict("organisations", trouve,
             "table annexe exploitable" if trouve
             else "operateur absent de d-portal, a chercher dans le XML IATI")


def sonde_d(session):
    """D. SECTEUR : garder infrastructure/energie/transport/extractif."""
    _titre("D. SECTEUR (ecarter sante et education, garder les chantiers)")
    ok = False
    for essai in ({"sector_code": "210"},      # transport
                  {"sector": "210"},
                  {"sector_group": "210"}):
        params = {"reporting_ref": IDB, "limit": 2}
        params.update(essai)
        lignes, err = _interroger(session, params, str(essai))
        if lignes and not err:
            ok = True
            print("      exemple : " + _plat(lignes[0].get("title"), 66))
            break
    _verdict("secteur", ok,
             "filtrage secteur possible" if ok
             else "pas de filtre secteur, tri par mots-cles du titre en local")


def sonde_e(session):
    """E. Derniere verification du CKAN data.iadb.org, sans y croire."""
    _titre("E. data.iadb.org : y a-t-il de la commande publique ?")
    trouve = 0
    for q in ("procurement", "contract", "projects", "operations"):
        url = ("https://data.iadb.org/api/3/action/package_search"
               "?q={}&rows=5".format(q))
        try:
            r = session.get(url, timeout=TIMEOUT)
            res = (r.json() or {}).get("result") or {}
        except Exception as e:
            print("  [KO] {:14} : {}".format(q, _plat(e, 50)))
            continue
        paquets = res.get("results") or []
        print("  [{}] q={:14} {} jeu(x) sur {} annonce(s)".format(
            "OK" if paquets else "--", q, len(paquets), res.get("count", "?")))
        for p in paquets[:4]:
            print("       - " + _plat(p.get("title"), 74))
            trouve += 1
    _verdict("CKAN IDB", trouve > 0,
             "{} jeu(x) a examiner".format(trouve) if trouve
             else "aucune donnee de commande publique (portail de recherche)")


def main():
    print("SONDE v11 (LA DERNIERE) -- forme des requetes. Lecture seule.")
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json, */*"})
    sonde_a(session)
    sonde_b(session)
    sonde_c(session)
    sonde_d(session)
    sonde_e(session)

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  {:18} {:12} {}".format(nom, "OK" if ok else "a creuser", detail))
    print("\nSUITE : avec le filtre pays et la fraicheur, j'ecris le collecteur")
    print("`idb_radar.py` sur le meme modele que les autres (mode verification")
    print("RADAR_IDB_DEBUG=1 avant toute ecriture, tests en paire).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                            # une sonde n'echoue jamais
        print("Sonde interrompue : {}".format(e))
    sys.exit(0)
