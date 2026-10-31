# -*- coding: utf-8 -*-
"""
Radar Amarante - SANCTIONS DES BAILLEURS (volet IE, 31/10/2026).

Entreprises et personnes EXCLUES (debarred) des marches finances par les
banques multilaterales, pour fraude ou corruption. Signal d'integrite direct
pour ARISE (due diligence des tiers, conformite anticorruption).

SOURCE : liste OFFICIELLE de la Banque mondiale (registre public), via l'API
de sa propre page de consultation publique. La page
  https://www.worldbank.org/en/projects-operations/procurement/debarred-firms
embarque une cle de consultation PUBLIQUE (propApiKey) pour lire :
  https://apigwext.worldbank.org/.../FIRM/SANCTIONED_FIRM
C'est la source PRIMAIRE, gratuite et destinee a la consultation : aucune
licence (contrairement a l'agregateur OpenSanctions, CC-BY-NC = commercial
pour Amarante), aucun contournement de blocage.

COUVERTURE : la liste porte un indicateur `INELIG_FLG` :
  D = exclu par la Banque mondiale ;
  X = CROSS-DEBARMENT : exclu par une AUTRE banque (BAD, BAsD, BID, BERD) et
      reconnu par la Banque mondiale. Une seule source libre couvre donc
      l'essentiel de l'univers des bailleurs, AfDB incluse (dont le site
      propre reste bloque par Cloudflare).

Aucun LLM (donnee deja structuree et factuelle). Ecrit une table
`radar_ie_sanctions` (upsert : un statut peut evoluer) et un onglet "Sanctions"
du classeur ARISE (ajout seul des nouveaux ; colonnes de suivi preservees).

VARIABLES
  RADAR_IE=1                 active (meme interrupteur que le volet IE)
  RADAR_IE_DEBUG=1           collecte sans ecriture
  RADAR_IE_SANCTIONS_CLE     surcharge la cle publique (si la BM la fait tourner)
  IE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE, DATABASE_URL
"""

import datetime
import json
import os

import lentille_ie as L


URL_API = ("https://apigwext.worldbank.org/dvsvc/v1.0/json/APPLICATION/"
           "ADOBE_EXPRNCE_MGR/FIRM/SANCTIONED_FIRM")
# Cle de consultation PUBLIQUE, telle que servie sur la page publique de la BM
# (champ propApiKey). Surchargeable si la BM la renouvelle.
CLE_PUBLIQUE = os.environ.get("RADAR_IE_SANCTIONS_CLE") or "z9duUaFUiEUYSHs97CU38fcZO7ipOPvm"
LIEN_BM = "https://www.worldbank.org/en/projects-operations/procurement/debarred-firms"

TABLE = "radar_ie_sanctions"
ONGLET = "Sanctions"
DEBUG = os.environ.get("RADAR_IE_DEBUG", "0") == "1"

TYPES = {"F": "Entreprise", "I": "Personne", "C": "Entreprise", "U": "Inconnu"}

COLONNES_SHEET = [
    "detecte_le", "nom", "type", "pays", "ville", "exclu_par",
    "debar_debut", "debar_fin", "motif", "lien", "cle",
    "statut_arise", "commentaire_arise",
]
COL_CLE = COLONNES_SHEET.index("cle")
COLONNES_PG = ("cle", "source", "nom", "type", "pays", "pays_iso2", "ville",
               "exclu_par", "cross_debarment", "debar_debut", "debar_fin",
               "motif", "maj_source", "lien", "detecte_le")


# ===========================================================================
# RECUPERATION (seule I/O ; ne leve jamais)
# ===========================================================================
def recuperer(fetch=None):
    """Renvoie (lignes_brutes, erreur). `fetch()` injectable pour tests."""
    if fetch is not None:
        lot = fetch()
        return (lot, "") if isinstance(lot, list) else ([], "forme inattendue (liste attendue)")
    try:
        import requests
        rep = requests.get(URL_API, params={"apikey": CLE_PUBLIQUE},
                           headers={"User-Agent": "radar-amarante/1.0"}, timeout=60)
        rep.raise_for_status()
        charge = rep.json()
    except Exception as ex:  # noqa: BLE001
        return [], "API Banque mondiale indisponible : {}".format(str(ex)[:150])
    lot = (charge or {}).get("response", {}).get("ZPROCSUPP")
    if not isinstance(lot, list):
        return [], "forme de reponse inattendue (cle publique renouvelee ?)"
    return lot, ""


# ===========================================================================
# NORMALISATION (pur)
# ===========================================================================
def _date(v):
    s = str(v or "")[:10]
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        return None
    # La BM code les exclusions "permanentes" en 2999-12-31 : on garde tel quel
    # pour l'affichage mais on ne le traite pas comme une vraie echeance.
    return d


def normaliser(row, aujourd=None):
    """Ligne BM brute -> enregistrement sanction, ou None (incomplet)."""
    sid = str(row.get("SUPP_ID") or "").strip()
    nom = str(row.get("SUPP_NAME") or "").strip()
    if not sid or not nom:
        return None
    aujourd = aujourd or datetime.date.today()
    flg = str(row.get("INELIG_FLG") or "").strip().upper()
    debut = _date(row.get("DEBAR_FROM_DATE"))
    fin = _date(row.get("DEBAR_TO_DATE"))
    return {
        "cle": "wb:" + sid, "source": "Banque mondiale",
        "nom": nom[:300], "type": TYPES.get(str(row.get("SUPP_TYPE_CODE") or "").strip(), "Inconnu"),
        "pays": str(row.get("COUNTRY_NAME") or "").strip(),
        "pays_iso2": str(row.get("LAND1") or "").strip().upper(),
        "ville": str(row.get("SUPP_CITY") or "").strip(),
        "exclu_par": "Cross-debarment (autre banque)" if flg == "X" else "Banque mondiale",
        "cross_debarment": flg == "X",
        "debar_debut": debut.isoformat() if debut else "",
        "debar_fin": fin.isoformat() if fin else "",
        "motif": str(row.get("DEBAR_REASON") or "").strip()[:300],
        "maj_source": (_date(row.get("UPD_DATE")) or _date(row.get("LAST_REFRESH_DATE"))
                       or datetime.date.min).isoformat() if (row.get("UPD_DATE") or row.get("LAST_REFRESH_DATE")) else "",
        "lien": LIEN_BM, "detecte_le": aujourd.isoformat(),
    }


def collecter(fetch=None, aujourd=None):
    """(sanctions normalisees, erreur). Dedup par cle."""
    brut, err = recuperer(fetch=fetch)
    vus, out = set(), []
    for row in brut:
        s = normaliser(row, aujourd=aujourd)
        if s and s["cle"] not in vus:
            vus.add(s["cle"])
            out.append(s)
    return out, err


def recentes(sanctions, aujourd, jours):
    """Exclusions dont la date de debut est dans les N derniers jours : c'est
    le signal 'radar' (nouvelles exclusions), par opposition au stock complet
    qui sert de liste de reference."""
    seuil = aujourd - datetime.timedelta(days=jours)
    out = []
    for s in sanctions:
        d = _date(s["debar_debut"])
        if d and d >= seuil:
            out.append(s)
    return sorted(out, key=lambda s: s["debar_debut"], reverse=True)


# ===========================================================================
# STOCKAGE (ne leve jamais)
# ===========================================================================
DDL = """CREATE TABLE IF NOT EXISTS {t} (
    cle TEXT PRIMARY KEY, source TEXT, nom TEXT, type TEXT, pays TEXT,
    pays_iso2 TEXT, ville TEXT, exclu_par TEXT, cross_debarment BOOLEAN,
    debar_debut DATE, debar_fin DATE, motif TEXT, maj_source DATE, lien TEXT,
    detecte_le DATE, cree_le TIMESTAMPTZ DEFAULT now())""".format(t=TABLE)


def _d_sql(v):
    return v if v else None


def memoire_pg(url):
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute("SELECT cle FROM {}".format(TABLE))
                return {r[0] for r in cur.fetchall()}
    except Exception as ex:  # noqa: BLE001
        print("  (info) memoire sanctions indisponible ({}).".format(str(ex)[:120]))
        return None


def ecrire_pg(url, sanctions):
    """Upsert (le statut d'une exclusion peut evoluer : radiation, prolongation).
    C'est une table de REFERENCE, pas une table append-only de collecte."""
    if not url or not sanctions:
        return "Postgres : rien a ecrire." if url else "Postgres : DATABASE_URL absent."
    try:
        import psycopg
        maj = ", ".join("{0} = EXCLUDED.{0}".format(c) for c in COLONNES_PG[1:])
        sql = "INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (cle) DO UPDATE SET {m}".format(
            t=TABLE, c=", ".join(COLONNES_PG), v=", ".join(["%s"] * len(COLONNES_PG)), m=maj)
        dates = {"debar_debut", "debar_fin", "maj_source", "detecte_le"}
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                for s in sanctions:
                    cur.execute(sql, [_d_sql(s[c]) if c in dates else s[c] for c in COLONNES_PG])
        return "Postgres : {} exclusion(s) a jour dans {}.".format(len(sanctions), TABLE)
    except Exception as ex:  # noqa: BLE001
        return "Postgres : ECHEC d'ecriture ({}).".format(str(ex)[:160])


def ligne_sheet(s):
    v = {
        "detecte_le": s["detecte_le"], "nom": s["nom"], "type": s["type"],
        "pays": s["pays"], "ville": s["ville"], "exclu_par": s["exclu_par"],
        "debar_debut": s["debar_debut"], "debar_fin": s["debar_fin"],
        "motif": s["motif"], "lien": s["lien"], "cle": s["cle"],
        "statut_arise": "", "commentaire_arise": "",
    }
    return [v[c] for c in COLONNES_SHEET]


def ouvrir_onglet(sheet_id, fichier_cle):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier_cle, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        return classeur.worksheet(ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=ONGLET, rows=2000, cols=len(COLONNES_SHEET))
        f.update([COLONNES_SHEET])
        f.freeze(rows=1)
        return f


def ecrire_sheet(feuille, sanctions):
    """N'ajoute a ARISE que les exclusions NOUVELLES (cle absente de l'onglet),
    pour ne pas deverser 1500 lignes de reference a chaque run. Le stock complet
    vit en base et sur le tableau de bord."""
    if feuille is None:
        return "Sheet ARISE : non configure (IE_SHEET_ID absent)."
    if not sanctions:
        return "Sheet ARISE : aucune nouvelle exclusion."
    try:
        connues = {v for v in feuille.col_values(COL_CLE + 1)[1:] if v}
        nouvelles = [ligne_sheet(s) for s in sanctions if s["cle"] not in connues]
        if not nouvelles:
            return "Sheet ARISE : aucune nouvelle exclusion."
        feuille.append_rows(nouvelles, value_input_option="USER_ENTERED")
        return "Sheet ARISE : {} nouvelle(s) exclusion(s) ajoutee(s).".format(len(nouvelles))
    except Exception as ex:  # noqa: BLE001
        return "Sheet ARISE : ECHEC d'ecriture ({}).".format(str(ex)[:160])


# ===========================================================================
# ORCHESTRATION
# ===========================================================================
def executer(fetch=None, aujourd=None, jours=None):
    aujourd = aujourd or datetime.date.today()
    jours = int(os.environ.get("RADAR_IE_SANCTIONS_JOURS", "180")) if jours is None else jours
    sanctions, err = collecter(fetch=fetch, aujourd=aujourd)
    rec = recentes(sanctions, aujourd, jours)
    rapport = {"total": len(sanctions), "recentes": len(rec), "jours": jours,
               "cross": sum(1 for s in sanctions if s["cross_debarment"]), "erreur": err}
    return sanctions, rec, rapport


def rapport_markdown(rapport, ecritures):
    md = ["# Sanctions des bailleurs (Banque mondiale, cross-debarment inclus)", ""]
    if rapport["erreur"]:
        md += ["ERREUR : {}".format(rapport["erreur"]), ""]
    md += ["- Exclusions au total : {}".format(rapport["total"]),
           "- Dont cross-debarment (autres banques) : {}".format(rapport["cross"]),
           "- Nouvelles sur {} jours : {}".format(rapport["jours"], rapport["recentes"]),
           "", "## Ecritures", ""] + ["- " + e for e in ecritures]
    return "\n".join(md) + "\n"


def main():
    if not L.ACTIF:
        print("Sanctions IE desactive (RADAR_IE=0).")
        return
    url = os.environ.get("DATABASE_URL")
    sheet_id = os.environ.get("IE_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sanctions, rec, rapport = executer()
    if DEBUG:
        ecritures = ["aucune (mode debug)"]
    else:
        feuille = None
        if sheet_id and fichier:
            try:
                feuille = ouvrir_onglet(sheet_id, fichier)
            except Exception as ex:  # noqa: BLE001
                print("  (info) classeur ARISE inaccessible ({}).".format(str(ex)[:120]))
        ecritures = [ecrire_pg(url, sanctions),
                     ecrire_sheet(feuille, rec)]   # ARISE ne recoit que les nouvelles
    md = rapport_markdown(rapport, ecritures)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
