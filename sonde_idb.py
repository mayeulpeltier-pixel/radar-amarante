# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v12 (jetable) : les fichiers d'avis et d'attributions.
===============================================================================

RECTIFICATION
-------------
J'ai ecrit que data.iadb.org ne contenait que de la donnee de recherche. C'est
FAUX, et je l'avais juge sur les premiers noms alphabetiques renvoyes par
package_list (indices Infrascope, enquetes emploi, bases climat). La v11 a
montre le contraire :

    IDB Project procurement bidding notices and notification of contract...
    IDB Project Procurement Contract Awards Data
    IDB Projects Dataset

Ce sont exactement les AVIS et les ATTRIBUTIONS, sur un CKAN ouvert, hors du
pare-feu Cloudflare qui protege le reste du groupe. La lecon vaut d'etre
notee : ne jamais qualifier un catalogue sur un echantillon alphabetique.

CE QUE CETTE SONDE ETABLIT, ET RIEN D'AUTRE
-------------------------------------------
Trois questions, apres quoi le collecteur peut s'ecrire sans deviner :
  A. Les identifiants EXACTS de ces jeux (package_search sur plusieurs termes).
  B. L'URL, le format et la taille de leurs FICHIERS (package_show). Point
     critique : si les fichiers sont heberges sur iadb.org ils seront bloques ;
     s'ils sont sur data.iadb.org, la voie est libre.
  C. Le SCHEMA reel : on telecharge un echantillon et on lit l'en-tete plus
     deux lignes. Sans cela, le collecteur serait ecrit a l'aveugle -- c'est
     exactement ce qui a produit le bug des numeros de telephone ranges sous
     `publication_number`.

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

TIMEOUT = 60
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

CKAN = "https://data.iadb.org/api/3/action"
RECHERCHES = ["procurement bidding notices", "contract awards",
              "procurement", "projects dataset"]

# Ce qui nous interesse vraiment dans ces jeux.
MOTS_CLES = ["bidding", "notice", "award", "procurement", "contract", "project"]

RESULTATS = []
PAQUETS = {}          # nom -> titre


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


def _titre_lisible(brut):
    """Les titres CKAN de l'IDB sont parfois un dict {'en': ..., 'es': ...}
    serialise. On extrait l'anglais."""
    texte = _plat(brut)
    m = re.search(r"['\"]en['\"]\s*:\s*['\"](.*?)['\"]", texte)
    return m.group(1) if m else texte


def _appel(session, action, **params):
    url = "{}/{}".format(CKAN, action)
    try:
        r = session.get(url, params=params, timeout=TIMEOUT)
    except Exception as e:
        print("    exception : {}".format(_plat(e, 70)))
        return None
    if r.status_code >= 400:
        print("    statut {} sur {}".format(r.status_code, action))
        return None
    try:
        return (r.json() or {}).get("result")
    except Exception:
        print("    reponse non JSON sur {}".format(action))
        return None


def sonde_a(session):
    """A. Identifiants exacts des jeux qui nous interessent."""
    _titre("A. IDENTIFIANTS EXACTS DES JEUX D'AVIS ET D'ATTRIBUTIONS")
    for q in RECHERCHES:
        res = _appel(session, "package_search", q=q, rows=10)
        if not res:
            continue
        paquets = res.get("results") or []
        print("\n  recherche {!r} : {} sur {} annonce(s)".format(
            q, len(paquets), res.get("count", "?")))
        for p in paquets:
            nom = p.get("name") or ""
            titre = _titre_lisible(p.get("title"))
            pertinent = any(m in (nom + " " + titre).lower() for m in MOTS_CLES)
            print("    {:52} {}".format(_plat(nom, 52),
                                        "<-- " if pertinent else "    ") + _plat(titre, 60))
            if pertinent and nom:
                PAQUETS[nom] = titre
    _verdict("identifiants", bool(PAQUETS),
             "{} jeu(x) retenu(s)".format(len(PAQUETS)))


def sonde_b(session):
    """B. Les FICHIERS : ou sont-ils, quel format, quelle taille ?"""
    _titre("B. FICHIERS DE CES JEUX (url, format, hebergement)")
    fichiers = []
    for nom, titre in list(PAQUETS.items())[:8]:
        res = _appel(session, "package_show", id=nom)
        if not res:
            continue
        ressources = res.get("resources") or []
        print("\n  --- {} ---".format(_plat(titre, 66)))
        print("      identifiant : {} | {} ressource(s) | maj : {}".format(
            nom, len(ressources), _plat(res.get("metadata_modified"), 20)))
        for r in ressources[:6]:
            url = r.get("url") or ""
            hote = re.sub(r"^https?://([^/]+).*$", r"\1", url) if url else "?"
            bloque = " [DERRIERE CLOUDFLARE]" if hote.endswith("iadb.org") and hote != "data.iadb.org" else ""
            print("        {:8} {:9} {}{}".format(
                _plat(r.get("format"), 8), _plat(r.get("size"), 9),
                _plat(url, 88), bloque))
            if url and not bloque:
                fichiers.append((titre, _plat(r.get("format"), 10), url))
    _verdict("fichiers", bool(fichiers),
             "{} fichier(s) accessible(s)".format(len(fichiers)))
    return fichiers


def sonde_c(session, fichiers):
    """C. SCHEMA REEL : en-tete et deux lignes. Ne jamais coder a l'aveugle."""
    _titre("C. SCHEMA REEL DES FICHIERS (en-tete + echantillon)")
    lus = 0
    for titre, format_, url in fichiers[:4]:
        print("\n  --- {} | {} ---".format(_plat(titre, 56), format_))
        print("      {}".format(_plat(url, 100)))
        try:
            # Telechargement partiel : ces fichiers peuvent etre volumineux.
            r = session.get(url, timeout=TIMEOUT, stream=True)
            if r.status_code >= 400:
                print("      statut {}".format(r.status_code))
                continue
            morceaux, total = [], 0
            for bloc in r.iter_content(8192):
                morceaux.append(bloc)
                total += len(bloc)
                if total > 120000:
                    break
            r.close()
            brut = b"".join(morceaux).decode("utf-8", "replace")
        except Exception as e:
            print("      exception : {}".format(_plat(e, 70)))
            continue
        lus += 1
        bas = (format_ or "").lower()
        if "json" in bas or brut.lstrip().startswith(("{", "[")):
            try:
                donnees = json.loads(brut)
                lignes = donnees if isinstance(donnees, list) else \
                    (donnees.get("result") or donnees.get("data") or donnees)
                if isinstance(lignes, list) and lignes:
                    print("      {} champ(s) :".format(len(lignes[0])))
                    for cle in sorted(lignes[0]):
                        print("        {:34} = {}".format(
                            cle, _plat(lignes[0][cle], 46)))
                else:
                    print("      " + _plat(json.dumps(donnees)[:500]))
            except Exception:
                print("      (JSON tronque, apercu brut)")
                print("      " + _plat(brut, 700))
        else:
            lignes = [l for l in brut.splitlines() if l.strip()][:3]
            if lignes:
                colonnes = lignes[0].split(",") if "," in lignes[0] else lignes[0].split(";")
                print("      {} colonne(s) dans l'en-tete :".format(len(colonnes)))
                for c in colonnes[:40]:
                    print("        " + _plat(c, 60))
                print("\n      --- deux premieres lignes brutes ---")
                for l in lignes[1:3]:
                    print("      " + _plat(l, 300))
    _verdict("schema", lus > 0, "{} fichier(s) lu(s)".format(lus))


def main():
    print("SONDE v12 -- fichiers d'avis et d'attributions IDB. Lecture seule.")
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json, */*"})
    sonde_a(session)
    fichiers = sonde_b(session) if PAQUETS else []
    if fichiers:
        sonde_c(session, fichiers)
    else:
        _titre("C. SCHEMA REEL")
        print("  (aucun fichier accessible : rien a lire)")
        _verdict("schema", False, "non evalue")

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  {:16} {:12} {}".format(nom, "OK" if ok else "a creuser", detail))
    print("\nSUITE : avec le schema reel, j'ecris `idb_radar.py` (avis) et son")
    print("volet attributions, sur le modele des collecteurs existants :")
    print("mode RADAR_IDB_DEBUG=1 pour valider sans ecrire, tests en paire.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                            # une sonde n'echoue jamais
        print("Sonde interrompue : {}".format(e))
    sys.exit(0)
