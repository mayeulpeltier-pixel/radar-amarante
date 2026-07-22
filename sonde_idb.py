# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v8 (jetable) : Amerique latine, voie DONNEES.
======================================================================

CE QUE LA v7 A ETABLI
---------------------
  - Les portails WEB de l'IDB renvoient tous un 403 de 5969 octets, taille
    IDENTIQUE partout : c'est une page de blocage anti-robot, pas une panne.
    Le grattage est donc mort, comme pour ADB.
  - MAIS mydata.iadb.org a repondu 404, pas 403. Nuance decisive : l'hote
    traite la requete, il refuse juste les identifiants de jeux de donnees
    que j'avais DEVINES. Aucun blocage de ce cote.

CE QUE CELLE-CI CORRIGE
-----------------------
On arrete de deviner. Socrata expose une API de DECOUVERTE hebergee sur
api.us.socrata.com, donc SUR UN AUTRE DOMAINE que iadb.org : elle echappe au
blocage. Elle liste les jeux de donnees d'un portail avec leurs identifiants
REELS. La sonde ENCHAINE ensuite automatiquement : decouverte -> meilleurs
candidats -> echantillon -> liste des champs. Un seul run doit suffire a
decider, au lieu d'un aller-retour par hypothese.

PISTE 2, EN PARALLELE : les portails nationaux au standard ouvert (Colombie
SECOP sur Socrata, Chili, Mexique). Concus pour l'acces programmatique, sans
protection anti-robot. Plus de bruit local a filtrer qu'une banque de
developpement, mais un volume complet et une bien meilleure disponibilite.

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
NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# API de decouverte Socrata : hebergee par Socrata, PAS par le portail cible.
DECOUVERTE = "https://api.us.socrata.com/api/catalog/v1"

# Portails Socrata a interroger : (etiquette, domaine, termes de recherche)
PORTAILS_SOCRATA = [
    ("IDB / BID", "mydata.iadb.org",
     ["procurement", "contract", "adquisicion", "operations"]),
    ("Colombie SECOP", "www.datos.gov.co",
     ["SECOP procesos", "contratacion", "procurement"]),
]

# Points d'entree STANDARD d'un portail Socrata (au lieu d'identifiants devines).
ENDPOINTS_STANDARD = [
    ("IDB catalogue DCAT", "https://mydata.iadb.org/data.json"),
    ("IDB vues", "https://mydata.iadb.org/api/views.json?limit=5"),
]

# Piste 2 : autres portails nationaux, simple test d'accessibilite.
PORTAILS_NATIONAUX = [
    ("Chili Mercado Publico", "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json"),
    ("Mexique datos.gob", "https://api.datos.gob.mx/v1/"),
    ("Registre OCDS", "https://data.open-contracting.org/en/publications/"),
]

MOTS_ACHAT = ["procurement", "contract", "tender", "bidding", "adquisici",
              "contrataci", "licitaci", "proces"]

RESULTATS = []
CANDIDATS = []          # (etiquette, domaine, id, nom) retenus pour echantillon


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


def _pertinent(texte):
    bas = (texte or "").lower()
    return any(m in bas for m in MOTS_ACHAT)


def sonde_a(session):
    """A. DECOUVERTE : quels jeux de donnees existent REELLEMENT ?"""
    _titre("A. DECOUVERTE SOCRATA (api.us.socrata.com, hors domaine bloque)")
    trouves = 0
    for etiquette, domaine, requetes in PORTAILS_SOCRATA:
        print("\n  --- {} ({}) ---".format(etiquette, domaine))
        vus = set()
        for q in requetes:
            params = {"domains": domaine, "q": q, "limit": 12}
            try:
                r = session.get(DECOUVERTE, params=params, timeout=TIMEOUT)
            except Exception as e:
                print("    [KO] requete {!r} : {}".format(q, _plat(e, 60)))
                continue
            if r.status_code >= 400:
                print("    [KO] requete {!r} : statut {}".format(q, r.status_code))
                continue
            try:
                donnees = r.json()
            except Exception:
                print("    [KO] requete {!r} : reponse non JSON".format(q))
                continue
            resultats = donnees.get("results") or []
            print("    requete {!r} : {} resultat(s) (total annonce {})".format(
                q, len(resultats), donnees.get("resultSetSize", "?")))
            for res in resultats:
                ressource = res.get("resource") or {}
                ident = ressource.get("id") or ""
                nom = _plat(ressource.get("name"), 70)
                if not ident or ident in vus:
                    continue
                vus.add(ident)
                pertinent = _pertinent(
                    nom + " " + _plat(ressource.get("description"), 200))
                print("      {:12} {}{}".format(
                    ident, nom, " <-- pertinent" if pertinent else ""))
                if pertinent:
                    CANDIDATS.append((etiquette, domaine, ident, nom))
                    trouves += 1
    _verdict("decouverte", trouves > 0,
             "{} jeu(x) de donnees pertinent(s) identifie(s)".format(trouves))


def sonde_b(session):
    """B. Points d'entree STANDARD du portail IDB (sans deviner d'identifiant)."""
    _titre("B. ENDPOINTS STANDARD SOCRATA SUR mydata.iadb.org")
    ok_global = False
    for nom, url in ENDPOINTS_STANDARD:
        try:
            r = session.get(url, timeout=TIMEOUT)
        except Exception as e:
            print("  [KO] {:22} exception : {}".format(nom, _plat(e, 60)))
            continue
        taille = len(r.content or b"")
        print("  [{}] {:22} {} | {} octets".format(
            "OK" if r.status_code < 400 else "KO", nom, r.status_code, taille))
        if r.status_code >= 400:
            continue
        ok_global = True
        try:
            donnees = r.json()
        except Exception:
            print("       (reponse non JSON)")
            continue
        jeux = donnees.get("dataset") if isinstance(donnees, dict) else donnees
        if isinstance(jeux, list):
            pertinents = [j for j in jeux if _pertinent(json.dumps(j)[:400])][:10]
            print("       {} jeu(x) au catalogue, {} pertinent(s) :".format(
                len(jeux), len(pertinents)))
            for j in pertinents:
                titre = _plat(j.get("title") or j.get("name"), 66)
                ident = str(j.get("identifier") or j.get("id") or "")
                print("         {:14} {}".format(_plat(ident, 14), titre))
                m = re.search(r"([a-z0-9]{4}-[a-z0-9]{4})", ident)
                if m:
                    CANDIDATS.append(("IDB / BID", "mydata.iadb.org",
                                      m.group(1), titre))
    _verdict("endpoints standard", ok_global,
             "catalogue lisible" if ok_global else "aucun endpoint standard joignable")


def sonde_c(session):
    """C. ENCHAINEMENT : echantillon reel du meilleur candidat + ses champs.
    C'est ce qui evite un aller-retour supplementaire."""
    _titre("C. ECHANTILLON REEL ET CHAMPS DISPONIBLES")
    if not CANDIDATS:
        print("  (aucun candidat : rien a echantillonner)")
        _verdict("echantillon", False, "non evalue")
        return
    reussis = 0
    vus = set()
    for etiquette, domaine, ident, nom in CANDIDATS[:6]:
        if ident in vus:
            continue
        vus.add(ident)
        url = "https://{}/resource/{}.json?$limit=3".format(domaine, ident)
        try:
            r = session.get(url, timeout=TIMEOUT)
        except Exception as e:
            print("  [KO] {} {} : {}".format(etiquette, ident, _plat(e, 55)))
            continue
        if r.status_code >= 400:
            print("  [KO] {} {} : statut {}".format(etiquette, ident, r.status_code))
            continue
        try:
            lignes = r.json()
        except Exception:
            print("  [KO] {} {} : reponse non JSON".format(etiquette, ident))
            continue
        if not isinstance(lignes, list) or not lignes:
            print("  [--] {} {} : jeu vide".format(etiquette, ident))
            continue
        reussis += 1
        print("\n  --- {} | {} | {} ---".format(etiquette, ident, nom))
        print("      {} champ(s) :".format(len(lignes[0])))
        for cle in sorted(lignes[0]):
            print("        {:34} = {}".format(cle, _plat(lignes[0][cle], 52)))
    _verdict("echantillon", reussis > 0,
             "{} jeu(x) lisible(s) avec leurs champs".format(reussis))


def sonde_d(session):
    """D. Piste 2 : autres portails nationaux (accessibilite brute)."""
    _titre("D. PORTAILS NATIONAUX (piste de repli)")
    vivants = 0
    for nom, url in PORTAILS_NATIONAUX:
        try:
            r = session.get(url, timeout=TIMEOUT)
            ctype = _plat(r.headers.get("Content-Type", ""), 34)
            taille = len(r.content or b"")
            if r.status_code < 400:
                vivants += 1
            print("  [{}] {:24} {} | {:34} | {} octets".format(
                "OK" if r.status_code < 400 else "KO", nom, r.status_code,
                ctype, taille))
            if r.status_code < 400 and "json" in ctype.lower():
                print("       apercu : " + _plat(r.text, 200))
        except Exception as e:
            print("  [KO] {:24} exception : {}".format(nom, _plat(e, 55)))
    _verdict("portails nationaux", vivants > 0,
             "{} portail(s) joignable(s)".format(vivants))


def main():
    print("SONDE v8 -- Amerique latine, voie DONNEES. Lecture seule.")
    session = requests.Session()
    session.headers.update({
        "User-Agent": NAVIGATEUR,
        "Accept": "application/json, text/html;q=0.8, */*;q=0.5",
        "Accept-Language": "en,es;q=0.9,fr;q=0.8",
    })
    sonde_a(session)
    sonde_b(session)
    sonde_c(session)
    sonde_d(session)

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  {:20} {:12} {}".format(nom, "OK" if ok else "a creuser", detail))
    print("\nDECISION ATTENDUE :")
    print("  - echantillon OK cote IDB   -> collecteur IDB sur API Socrata")
    print("  - echantillon OK cote SECOP -> collecteur Colombie, puis autres")
    print("                                 pays au meme standard")
    print("  - aucun des deux            -> abandonner l'Amerique latine")
    print("                                 regionale : la Banque Mondiale et")
    print("                                 UNGM la couvrent deja, c'etait un")
    print("                                 renfort, pas une necessite.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                            # une sonde n'echoue jamais
        print("Sonde interrompue : {}".format(e))
    sys.exit(0)
