# -*- coding: utf-8 -*-
"""
Radar Amarante - ENRICHISSEMENT des prospects IE (chantier IE-9, 01/11/2026).

Rend ACTIONNABLES les societes nommees captees par les declencheurs
(radar_ie_declencheurs : sanctions AMF + CJIP). Pour chaque societe, repond a
"qui appeler" : SIREN, identite officielle, dirigeant principal, activite (NAF),
ville, effectif, via l'API publique gratuite Recherche d'entreprises
(recherche-entreprises.api.gouv.fr, sans cle). Email pro OPTIONNEL (Hunter).

ISOLATION : volet IE, aucun fichier Operations touche. On ne reutilise PAS
enrichir_entreprises.py (pile Operations) : un petit client dedie, sans
dependance lourde. Table et onglet ARISE propres.

Le tableau de bord ARISE rattache ces infos aux lignes declencheurs par nom
normalise (cle_societe) : voir dashboard_ie.py.

ECRITURES
  - Postgres `radar_ie_enrichissement` : UPSERT (zone derivee, rafraichie, pas
    de la collecte -> l'upsert est legitime, contrairement a radar_ie_leads).
  - Sheet ARISE, onglet "Enrichissement" : ajout/maj par cle.
Budget par run (RADAR_IE_ENRICH_BUDGET) et cache de fraicheur (DELAI) : on
n'enrichit que les societes nouvelles ou anciennes, jamais deux fois de suite.
Deterministe hors API ; tolerant aux pannes (ne leve jamais en ecriture).
"""

import datetime
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

import config_env

API_GOUV = "https://recherche-entreprises.api.gouv.fr/search"
API_HUNTER = "https://api.hunter.io/v2/domain-search"
UA = {"User-Agent": "RadarAmarante/1.0 (veille ARISE)"}
TABLE = "radar_ie_enrichissement"
ONGLET = "Enrichissement"
TABLE_SOURCE = "radar_ie_declencheurs"
DELAI_JOURS = config_env.entier("RADAR_IE_ENRICH_DELAI", 120)
BUDGET = config_env.entier("RADAR_IE_ENRICH_BUDGET", 40)
PAUSE = 0.4

QUALITES_DIRIGEANTES = ("president", "directeur general", "gerant", "directeur",
                        "administrateur", "liquidateur", "directoire")
# Formes juridiques uniquement : les mots d'activite (capital, partners,
# gestion, finance...) sont DISCRIMINANTS pour ces societes et doivent rester.
FORMES = {"sa", "sas", "sasu", "sarl", "snc", "sca", "sce", "eurl", "scs",
          "selarl", "sel", "gie", "sci"}

COLONNES_SHEET = ["enrichi_le", "societe", "siren", "nom_officiel",
                  "dirigeant_principal", "autres_dirigeants", "activite_naf",
                  "ville", "effectif", "email", "source", "cle_societe"]
COL_CLE = COLONNES_SHEET.index("cle_societe")

COLONNES_PG = ("cle_societe", "societe", "siren", "nom_officiel",
               "dirigeant_principal", "autres_dirigeants", "activite_naf",
               "ville", "effectif", "email", "source", "enrichi_le")
DDL = """CREATE TABLE IF NOT EXISTS {t} (
    cle_societe TEXT PRIMARY KEY, societe TEXT, siren TEXT, nom_officiel TEXT,
    dirigeant_principal TEXT, autres_dirigeants TEXT, activite_naf TEXT,
    ville TEXT, effectif TEXT, email TEXT, source TEXT, enrichi_le DATE,
    cree_le TIMESTAMPTZ DEFAULT now())""".format(t=TABLE)


# ===========================================================================
# FONCTIONS PURES (testables)
# ===========================================================================
def _norm(texte):
    decompose = unicodedata.normalize("NFKD", str(texte or ""))
    sans = "".join(c for c in decompose if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sans.replace("’", "'").lower()).strip()


def cle_societe(nom):
    """Cle de jointure : nom normalise, ponctuation retiree."""
    t = _norm(nom)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens_signifiants(nom):
    return [m for m in re.findall(r"[a-z0-9]+", _norm(nom))
            if len(m) > 2 and m not in FORMES]


def noms_compatibles(demande, trouve):
    """Garde-fou anti-homonyme : la recherche API est floue. On exige un
    recouvrement reel des mots signifiants (sinon un homonyme francais
    heriterait d'une mauvaise identite)."""
    a, b = set(_tokens_signifiants(demande)), set(_tokens_signifiants(trouve))
    if not a or not b:
        # Noms faits uniquement de mots generiques : repli sur l'egalite
        # normalisee stricte.
        return _norm(demande) == _norm(trouve)
    communs = a & b
    return len(communs) / min(len(a), len(b)) >= 0.5


def extraire_dirigeants(dirigeants):
    """(principal, [autres]) : personnes physiques d'abord, qualites
    dirigeantes privilegiees."""
    personnes = []
    for d in dirigeants or []:
        if (d.get("type_dirigeant") or "").lower().startswith("personne mor"):
            nom = (d.get("denomination") or d.get("nom") or "").strip()
            if nom:
                personnes.append((nom, (d.get("qualite") or "").strip(), 0))
            continue
        prenom = (d.get("prenoms") or "").strip().split(" ")[0]
        nom = (d.get("nom") or "").strip()
        qualite = (d.get("qualite") or "").strip()
        complet = (prenom + " " + nom).strip()
        if complet:
            poids = 2 if any(q in qualite.lower() for q in QUALITES_DIRIGEANTES) else 1
            personnes.append((complet, qualite, poids))
    if not personnes:
        return "", []
    personnes.sort(key=lambda p: p[2], reverse=True)

    def libelle(p):
        return "{} ({})".format(p[0], p[1]) if p[1] else p[0]
    return libelle(personnes[0]), [libelle(p) for p in personnes[1:3]]


def parse_resultat(demande, donnees):
    """Transforme la reponse API en dict firmographique, ou None (introuvable
    ou homonyme ecarte)."""
    resultats = (donnees or {}).get("results") or []
    if not resultats:
        return None
    r = resultats[0]
    trouve = r.get("nom_complet") or r.get("nom_raison_sociale") or ""
    if not noms_compatibles(demande, trouve):
        return None
    siege = r.get("siege") or {}
    principal, autres = extraire_dirigeants(r.get("dirigeants"))
    return {
        "cle_societe": cle_societe(demande), "societe": demande,
        "siren": r.get("siren") or "", "nom_officiel": trouve,
        "dirigeant_principal": principal, "autres_dirigeants": " ; ".join(autres),
        "activite_naf": r.get("libelle_activite_principale")
        or (siege.get("activite_principale") or r.get("activite_principale") or ""),
        "ville": siege.get("libelle_commune") or "",
        "effectif": r.get("tranche_effectif_salarie") or "",
        "email": "", "source": "recherche-entreprises",
    }


def a_rafraichir(cle, deja_fraiches):
    return cle not in deja_fraiches


def _fraiche(enrichi_le, limite):
    """Vrai si la date d'enrichissement est >= limite (encore fraiche)."""
    return bool(enrichi_le) and enrichi_le >= limite


# ===========================================================================
# RESEAU (injectable)
# ===========================================================================
def _fetch_gouv(nom, essais=3):
    url = API_GOUV + "?" + urllib.parse.urlencode({"q": nom, "page": 1, "per_page": 1})
    dernier = ""
    for _ in range(essais):
        try:
            rep = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25)
            return json.loads(rep.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            dernier = str(exc)[:90]
            time.sleep(0.8)
    raise RuntimeError(dernier)


# ===========================================================================
# EXECUTION (coeur testable)
# ===========================================================================
def societes_a_enrichir(lignes_source):
    """Noms de societes distincts et non vides tires des declencheurs."""
    vus, sortie = set(), []
    for ligne in lignes_source:
        nom = (ligne.get("societe") or "").strip()
        cle = cle_societe(nom)
        if nom and cle and cle not in vus:
            vus.add(cle)
            sortie.append(nom)
    return sortie


def executer(lignes_source, deja_fraiches=None, fetch=None, aujourd=None, budget=None):
    fetch = fetch or _fetch_gouv
    deja_fraiches = deja_fraiches or set()
    aujourd = aujourd or datetime.date.today()
    budget = budget if budget is not None else BUDGET
    noms = societes_a_enrichir(lignes_source)
    a_faire = [n for n in noms if a_rafraichir(cle_societe(n), deja_fraiches)]
    enrichies, erreurs = [], []
    resolues = 0
    for nom in a_faire[:budget]:
        try:
            donnees = fetch(nom)
        except Exception as exc:  # noqa: BLE001
            erreurs.append("{} : {}".format(nom[:40], str(exc)[:90]))
            continue
        info = parse_resultat(nom, donnees)
        if info:
            info["enrichi_le"] = aujourd.isoformat()
            enrichies.append(info)
            resolues += 1
        if PAUSE:
            time.sleep(PAUSE)
    info = {"candidats": len(noms), "a_faire": len(a_faire), "resolues": resolues,
            "non_resolues": len(a_faire[:budget]) - resolues, "erreurs": erreurs,
            "hors_budget": max(0, len(a_faire) - budget)}
    return enrichies, info


def ligne_sheet(e):
    return [e.get(c, "") for c in COLONNES_SHEET]


def valeurs_pg(e):
    return [e.get(c) for c in COLONNES_PG]


# ===========================================================================
# STOCKAGE (best-effort, ne leve jamais)
# ===========================================================================
def memoire_pg(url, aujourd=None, delai=None):
    """Cles deja enrichies ET fraiches (< DELAI). Retourne None si base KO."""
    if not url:
        return None
    aujourd = aujourd or datetime.date.today()
    delai = delai if delai is not None else DELAI_JOURS
    limite = aujourd - datetime.timedelta(days=delai)
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute("SELECT cle_societe, enrichi_le FROM {}".format(TABLE))
                return {cle for cle, enrichi in cur.fetchall()
                        if _fraiche(enrichi, limite)}
    except Exception as exc:  # noqa: BLE001
        print("  (info) memoire enrichissement IE indisponible ({}).".format(str(exc)[:120]))
        return None


def lire_source_pg(url):
    """Societes des declencheurs (lecture seule)."""
    if not url:
        return []
    sources = []
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                # Declencheurs (sanctions AMF/CJIP) + cartographie devoir de
                # vigilance : deux populations de societes a enrichir.
                for table, colonne in ((TABLE_SOURCE, "societe"),
                                       ("radar_ie_vigilance", "nom")):
                    try:
                        cur.execute("SELECT {} FROM {}".format(colonne, table))
                        sources += [{"societe": r[0]} for r in cur.fetchall()]
                    except Exception:
                        conn.rollback()      # table absente : on ignore
        return sources
    except Exception as exc:  # noqa: BLE001
        print("  (info) sources a enrichir indisponibles ({}).".format(str(exc)[:120]))
        return sources


def ecrire_pg(url, enrichies):
    """UPSERT : zone derivee rafraichie (pas de la collecte)."""
    if not url:
        return "Postgres : DATABASE_URL absent."
    if not enrichies:
        return "Postgres : rien a enrichir."
    try:
        import psycopg
        maj = ", ".join("{0} = EXCLUDED.{0}".format(c) for c in COLONNES_PG[1:])
        sql = ("INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (cle_societe) "
               "DO UPDATE SET {m}").format(
                   t=TABLE, c=", ".join(COLONNES_PG),
                   v=", ".join(["%s"] * len(COLONNES_PG)), m=maj)
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                for e in enrichies:
                    cur.execute(sql, valeurs_pg(e))
        return "Postgres : {} societe(s) enrichie(s) dans {}.".format(len(enrichies), TABLE)
    except Exception as exc:  # noqa: BLE001
        return "Postgres : ECHEC d'ecriture ({}).".format(str(exc)[:160])


def ouvrir_onglet(sheet_id, fichier_cle):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier_cle, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        return classeur.worksheet(ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=ONGLET, rows=500, cols=len(COLONNES_SHEET))
        f.update([COLONNES_SHEET])
        f.freeze(rows=1)
        return f


def cles_onglet(feuille):
    try:
        return set(feuille.col_values(COL_CLE + 1)[1:])
    except Exception:  # noqa: BLE001
        return set()


def ecrire_sheet(feuille, enrichies):
    """Ajoute les cles nouvelles (les maj restent en base ; le Sheet est un
    export lisible, ajout seul pour ne pas reecrire les colonnes ARISE)."""
    if feuille is None:
        return "Sheet ARISE : non configure ou inaccessible."
    if not enrichies:
        return "Sheet ARISE : rien a enrichir."
    try:
        presentes = cles_onglet(feuille)
        lignes = [ligne_sheet(e) for e in enrichies if e["cle_societe"] not in presentes]
        if not lignes:
            return "Sheet ARISE : enrichissement deja present."
        feuille.append_rows(lignes, value_input_option="USER_ENTERED")
        return "Sheet ARISE : {} societe(s) ajoutee(s).".format(len(lignes))
    except Exception as exc:  # noqa: BLE001
        return "Sheet ARISE : ECHEC d'ecriture ({}).".format(str(exc)[:160])


def rapport_markdown(enrichies, info, ecritures, debug):
    lignes = ["# Enrichissement des prospects IE", "",
              "Mode : {}. Societes candidates : {} (a rafraichir : {}, resolues ce "
              "run : {}, non resolues : {}, hors budget : {}).".format(
                  "DEBUG (aucune ecriture)" if debug else "production",
                  info["candidats"], info["a_faire"], info["resolues"],
                  info["non_resolues"], info["hors_budget"]),
              "",
              "| Societe | SIREN | Dirigeant | NAF | Ville |",
              "|---|---|---|---|---|"]
    for e in enrichies:
        lignes.append("| {} | {} | {} | {} | {} |".format(
            e["societe"][:40], e["siren"], e["dirigeant_principal"][:40],
            e["activite_naf"][:30], e["ville"]))
    if not enrichies:
        lignes.append("| (aucune nouvelle societe) | | | | |")
    lignes += ["", "## Ecritures", ""] + ["- " + x for x in ecritures]
    if info["erreurs"]:
        lignes += ["", "## Erreurs", ""] + ["- " + x for x in info["erreurs"][:20]]
    return "\n".join(lignes) + "\n"


def main():
    import lentille_ie as L
    if not L.ACTIF:
        print("Enrichissement IE desactive (RADAR_IE=0).")
        return
    debug = os.environ.get("RADAR_IE_DEBUG", "0") not in ("", "0")
    aujourd = datetime.date.today()
    url = os.environ.get("DATABASE_URL")
    sheet_id = os.environ.get("IE_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    source = lire_source_pg(url)
    deja = memoire_pg(url, aujourd) or set()
    enrichies, info = executer(source, deja_fraiches=deja, aujourd=aujourd)
    if debug:
        ecritures = ["aucune (mode debug)"]
    else:
        feuille = None
        if sheet_id and fichier:
            try:
                feuille = ouvrir_onglet(sheet_id, fichier)
            except Exception as exc:  # noqa: BLE001
                print("  (info) classeur ARISE inaccessible ({}).".format(str(exc)[:120]))
        ecritures = [ecrire_pg(url, enrichies), ecrire_sheet(feuille, enrichies)]
    md = rapport_markdown(enrichies, info, ecritures, debug)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
