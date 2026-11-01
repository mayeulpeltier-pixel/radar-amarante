# -*- coding: utf-8 -*-
"""
Radar Amarante - CARTOGRAPHIE devoir de vigilance (chantier IE-10, 01/11/2026).

Capte le MARCHE PRIVE adressable d'ARISE : les grandes sociétes assujetties a
la loi sur le devoir de vigilance (2017). Elles DOIVENT etablir un plan de
vigilance = cartographie des risques + evaluation des tiers + plan d'action,
c'est-a-dire l'offre Amarante. La population est identifiable directement dans
SIRENE, sans dependre d'une source fragile : societes dont l'effectif depasse
5 000 salaries (tranches INSEE 52 et 53), via l'API publique gratuite
Recherche d'entreprises (sans cle).

PERIMETRE : seuil FR >= 5 000 salaries (tranches 52/53). Le seuil "10 000 dans
le monde" peut viser une societe a <5 000 en France : angle mort assume en V1
(non visible dans SIRENE France). On ECARTE le public (nature juridique 7xxx,
NAF 84 administration) et les associations/fondations (nature juridique 9xxx) :
la loi vise les societes commerciales.

ISOLATION : volet IE, aucun fichier Operations touche. Aucun LLM. Table et
onglet ARISE propres. Les dirigeants sont remplis par `enrichir_ie.py` (qui lit
aussi cette table). Tolerant aux pannes (ne leve jamais en ecriture).
"""

import datetime
import json
import os
import time
import urllib.parse
import urllib.request

import config_env

API = "https://recherche-entreprises.api.gouv.fr/search"
UA = {"User-Agent": "RadarAmarante/1.0 (veille ARISE)"}
TABLE = "radar_ie_vigilance"
ONGLET = "Devoir de vigilance"
TRANCHES = os.environ.get("RADAR_IE_VIGILANCE_TRANCHES") or "52,53"
PER_PAGE = 25
PAGES_MAX = config_env.entier("RADAR_IE_VIGILANCE_PAGES_MAX", 40)
PAUSE = 0.4

EFFECTIF = {"52": "5 000 a 9 999", "53": "10 000 et plus"}

COLONNES_SHEET = ["detecte_le", "nom", "siren", "effectif", "activite_naf",
                  "ville", "categorie", "cle"]
COL_CLE = COLONNES_SHEET.index("cle")
COLONNES_PG = ("cle", "nom", "siren", "nature_juridique", "effectif",
               "activite_naf", "ville", "categorie", "detecte_le")
DDL = """CREATE TABLE IF NOT EXISTS {t} (
    cle TEXT PRIMARY KEY, nom TEXT, siren TEXT, nature_juridique TEXT,
    effectif TEXT, activite_naf TEXT, ville TEXT, categorie TEXT,
    detecte_le DATE, cree_le TIMESTAMPTZ DEFAULT now())""".format(t=TABLE)


# ===========================================================================
# FONCTIONS PURES (testables)
# ===========================================================================
def est_assujettie(rec):
    """Societe commerciale (nature juridique 5xxx/6xxx), hors administration
    publique (NAF 84) et hors association/fondation (9xxx)."""
    nj = str(rec.get("nature_juridique") or "")
    naf = str((rec.get("siege") or {}).get("activite_principale") or "")
    if nj[:1] not in ("5", "6"):
        return False
    if naf.startswith("84"):
        return False
    return True


def ligne_depuis(rec, aujourd):
    siege = rec.get("siege") or {}
    tranche = str(rec.get("tranche_effectif_salarie") or "")
    return {
        "cle": rec.get("siren") or "",
        "nom": rec.get("nom_complet") or rec.get("nom_raison_sociale") or "",
        "siren": rec.get("siren") or "",
        "nature_juridique": str(rec.get("nature_juridique") or ""),
        "effectif": EFFECTIF.get(tranche, tranche),
        "activite_naf": rec.get("libelle_activite_principale")
        or siege.get("activite_principale") or "",
        "ville": siege.get("libelle_commune") or "",
        "categorie": rec.get("categorie_entreprise") or "",
        "detecte_le": aujourd.isoformat(),
    }


def ligne_sheet(v):
    return [v.get(c, "") for c in COLONNES_SHEET]


def valeurs_pg(v):
    return [v.get(c) for c in COLONNES_PG]


# ===========================================================================
# COLLECTE (reseau injectable)
# ===========================================================================
def _fetch(page, per_page=PER_PAGE, tranches=TRANCHES, essais=4):
    params = {"tranche_effectif_salarie": tranches, "page": page, "per_page": per_page}
    url = API + "?" + urllib.parse.urlencode(params)
    dernier = ""
    for _ in range(essais):
        try:
            rep = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25)
            return json.loads(rep.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            dernier = str(exc)[:90]
            time.sleep(0.8)
    raise RuntimeError(dernier)


def enumerer(fetch=None, pause=PAUSE):
    """Toutes les sociétes des tranches visees, page par page."""
    fetch = fetch or _fetch
    page1 = fetch(1)
    total = page1.get("total_results", 0)
    pages = min(page1.get("total_pages", 1), PAGES_MAX)
    records = list(page1.get("results") or [])
    for page in range(2, pages + 1):
        if pause:
            time.sleep(pause)
        records += list((fetch(page) or {}).get("results") or [])
    return records, total


def executer(fetch=None, aujourd=None, pause=PAUSE):
    aujourd = aujourd or datetime.date.today()
    try:
        records, total = enumerer(fetch=fetch, pause=pause)
    except Exception as exc:  # noqa: BLE001
        return [], {"erreurs": ["API : {}".format(str(exc)[:120])], "total": 0,
                    "lues": 0, "assujetties": 0, "ecartees": 0}
    lignes, ecartees = [], 0
    vus = set()
    for rec in records:
        if not est_assujettie(rec):
            ecartees += 1
            continue
        v = ligne_depuis(rec, aujourd)
        if v["cle"] and v["cle"] not in vus:
            vus.add(v["cle"])
            lignes.append(v)
    info = {"erreurs": [], "total": total, "lues": len(records),
            "assujetties": len(lignes), "ecartees": ecartees}
    return lignes, info


# ===========================================================================
# STOCKAGE (best-effort, ne leve jamais ; UPSERT = cartographie rafraichie)
# ===========================================================================
def ecrire_pg(url, lignes):
    if not url:
        return "Postgres : DATABASE_URL absent."
    if not lignes:
        return "Postgres : rien a ecrire."
    try:
        import psycopg
        maj = ", ".join("{0} = EXCLUDED.{0}".format(c) for c in COLONNES_PG[1:])
        sql = ("INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (cle) "
               "DO UPDATE SET {m}").format(
                   t=TABLE, c=", ".join(COLONNES_PG),
                   v=", ".join(["%s"] * len(COLONNES_PG)), m=maj)
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                for v in lignes:
                    cur.execute(sql, valeurs_pg(v))
        return "Postgres : {} societe(s) assujettie(s) dans {}.".format(len(lignes), TABLE)
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


def ecrire_sheet(feuille, lignes):
    if feuille is None:
        return "Sheet ARISE : non configure ou inaccessible."
    if not lignes:
        return "Sheet ARISE : rien a ecrire."
    try:
        presentes = cles_onglet(feuille)
        neuves = [ligne_sheet(v) for v in lignes if v["cle"] not in presentes]
        if not neuves:
            return "Sheet ARISE : cartographie deja presente."
        feuille.append_rows(neuves, value_input_option="USER_ENTERED")
        return "Sheet ARISE : {} societe(s) ajoutee(s).".format(len(neuves))
    except Exception as exc:  # noqa: BLE001
        return "Sheet ARISE : ECHEC d'ecriture ({}).".format(str(exc)[:160])


def rapport_markdown(lignes, info, ecritures, debug):
    out = ["# Cartographie devoir de vigilance", "",
           "Mode : {}. Sociétes >= 5 000 salaries (SIRENE tranches {}) : {} lues, "
           "{} assujetties retenues, {} ecartees (public / association).".format(
               "DEBUG (aucune ecriture)" if debug else "production", TRANCHES,
               info["lues"], info["assujetties"], info["ecartees"]),
           "",
           "| Societe | SIREN | Effectif | NAF | Ville |", "|---|---|---|---|---|"]
    for v in sorted(lignes, key=lambda x: x["nom"])[:60]:
        out.append("| {} | {} | {} | {} | {} |".format(
            v["nom"][:45], v["siren"], v["effectif"], v["activite_naf"][:28], v["ville"]))
    if len(lignes) > 60:
        out.append("| ... (+{} autres) | | | | |".format(len(lignes) - 60))
    out += ["", "## Ecritures", ""] + ["- " + x for x in ecritures]
    if info["erreurs"]:
        out += ["", "## Erreurs", ""] + ["- " + x for x in info["erreurs"][:10]]
    return "\n".join(out) + "\n"


def main():
    import lentille_ie as L
    if not L.ACTIF:
        print("Cartographie IE desactivee (RADAR_IE=0).")
        return
    debug = os.environ.get("RADAR_IE_DEBUG", "0") not in ("", "0")
    aujourd = datetime.date.today()
    lignes, info = executer(aujourd=aujourd)
    if debug:
        ecritures = ["aucune (mode debug)"]
    else:
        url = os.environ.get("DATABASE_URL")
        feuille = None
        sheet_id = os.environ.get("IE_SHEET_ID")
        fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        if sheet_id and fichier:
            try:
                feuille = ouvrir_onglet(sheet_id, fichier)
            except Exception as exc:  # noqa: BLE001
                print("  (info) classeur ARISE inaccessible ({}).".format(str(exc)[:120]))
        ecritures = [ecrire_pg(url, lignes), ecrire_sheet(feuille, lignes)]
    md = rapport_markdown(lignes, info, ecritures, debug)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
