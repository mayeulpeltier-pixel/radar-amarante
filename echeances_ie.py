# -*- coding: utf-8 -*-
"""
Radar Amarante - ECHEANCES IE (chantier IE-6, 30/10/2026).

Marches d'intelligence economique et de conformite DEJA ATTRIBUES, dont la
fin approche : ce sont les remises en concurrence a preparer. Source : DECP
consolidees (colibre.fr, ex-decp.info), Parquet quotidien sur data.gouv.fr.

Preuve de valeur (sonde IE-1, 30/10/2026) : l'accord-cadre AFA expirant en
fevrier 2027 expliquait l'avis 26-104139 publie trois mois avant. Le radar
l'aurait vu venir deux ans a l'avance.

AUCUN appel LLM : tout est deterministe (lexique de la lentille IE, SIREN,
dates). AUCUN module existant n'est modifie.

CORRECTIONS ISSUES DE LA SONDE IE-1
  - Titulaires surveilles par SIREN, jamais par nom : la sonde avait
    confondu Amarante avec "Bonheur d'Amarante" et ADIT avec une entreprise
    d'entretien de toilettes seches.
  - Lots regroupes en procedures (acheteur + objet + date) : l'accord-cadre
    AFA comptait a lui seul 68 lignes.
  - Achats d'outils (logiciel, abonnement, plateforme) ecartes de la vue
    ARISE : ARISE ne vend pas d'outil.
  - Montants : sous 1 000 EUR (bons de commande a 0 ou 340 EUR) ou au-dessus
    de 15 MEUR pour une prestation IE, le plafond est marque douteux.
  - Lien direct vers la fiche du marche sur colibre.fr (route verifiee dans
    le code source du site : /marches/<uid>).

LIMITE CONNUE : `dureeMois` est en general la duree INITIALE, sans les
reconductions. La fin estimee peut tomber trop tot ; elle est affichee comme
estimation.

ECRITURES
  - Postgres `radar_ie_echeances` : MISE A JOUR par cle (upsert). Exception
    assumee a l'ajout seul : ce sont des donnees de reference recalculees a
    chaque run depuis les DECP, sans saisie humaine.
  - Sheet ARISE, onglet "Echeances" : AJOUT des seules nouvelles procedures,
    jamais de reecriture (colonnes de suivi ARISE preservees).

Toutes les procedures retenues vont en base (IE, Analyste, portefeuille
Amarante complet) : les futures lentilles s'en serviront. L'onglet ARISE ne
montre que l'IE, hors achats d'outils.
"""

import datetime
import os
import re
from collections import defaultdict

import lentille_ie as L

URL_DECP = ("https://www.data.gouv.fr/api/1/datasets/r/"
            "11cea8e8-df3e-4ed1-932b-781e2635e432")
LIEN_MARCHE = "https://colibre.fr/marches/{}"
FICHIER = os.environ.get("RADAR_IE_DECP_FICHIER") or "decp.parquet"
TABLE = "radar_ie_echeances"
ONGLET = "Echeances"
RETRO_MOIS = 6
HORIZON_MOIS = 24
MONTANT_MIN, MONTANT_MAX_IE = 1000.0, 15e6

# SIREN surveilles. Verifies sur les DECP (sonde IE-1). Extensible sans code :
# RADAR_IE_SIRENS="123456789:Nom,987654321:Autre".
AMARANTE = {"499655967": "Amarante International",
            "509879227": "Amarante Risk Intelligence"}
CONCURRENTS = {"448747360": "ADIT", "795237023": "Decidento"}


def _sirens_supplementaires():
    out = {}
    for item in (os.environ.get("RADAR_IE_SIRENS") or "").split(","):
        if ":" in item:
            s, nom = item.split(":", 1)
            s = re.sub(r"\D", "", s)
            if len(s) == 9:
                out[s] = nom.strip()
    return out


CONCURRENTS.update(_sirens_supplementaires())
SURVEILLES = dict(AMARANTE, **CONCURRENTS)

OUTIL = ("logiciel", "licence", "abonnement", "saas", "plateforme", "outil ",
         "outil d", "solution", "base de donnees", "tierce maintenance",
         "maintenance applicative",
         # IE-6 (30/10) : fourniture de donnees (Infolegale), pas une prestation.
         "fourniture d'informations", "fourniture d informations",
         "fourniture de donnees")
# Un marche est juge DEJA RENOUVELE quand le meme acheteur a notifie un autre
# marche de la meme expertise autour de sa fin. Constate au premier run : France
# Travail (fin 05/2026) etait deja reattribue en 08/2026 et s'affichait comme
# une echeance a preparer. La fenetre evite de confondre deux lots notifies a
# quelques jours d'ecart (AFA : 06/02 et 16/02/2023), qui sont des marches
# paralleles, pas une succession.
FENETRE_SUCCESSION_MOIS = 12

COLONNES_SHEET = [
    "detecte_le", "fin_estimee", "position", "acheteur", "objet",
    "titulaires_sortants", "plafond_eur", "nb_lots", "duree_mois",
    "notification", "offres_recues", "region", "lien", "cle",
    "statut_arise", "commentaire_arise",
]
COL_CLE = COLONNES_SHEET.index("cle")


# ===========================================================================
# OUTILS
# ===========================================================================
def _date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _nombre(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def ajouter_mois(d, mois):
    m = d.month - 1 + int(mois)
    annee, mm = d.year + m // 12, m % 12 + 1
    bissex = annee % 4 == 0 and (annee % 100 != 0 or annee % 400 == 0)
    jours = [31, 29 if bissex else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mm - 1]
    return datetime.date(annee, mm, min(d.day, jours))


def siren(titulaire_id):
    chiffres = re.sub(r"\D", "", str(titulaire_id or ""))
    return chiffres[:9] if len(chiffres) in (9, 14) else ""


def est_outil(objet):
    o = L.normaliser_texte(objet) + " "
    return any(x in o for x in OUTIL)


def regex_sql(motifs):
    return r"\b(" + "|".join(re.escape(m) for m in motifs) + r")\b"


# ===========================================================================
# CHARGEMENT DECP (DuckDB, import paresseux)
# ===========================================================================
COLONNES_UTILES = ("uid", "objet", "acheteur_id", "acheteur_nom",
                   "acheteur_region_nom", "titulaire_id", "titulaire_nom",
                   "montant", "montant_rationalise", "dureeMois", "offresRecues",
                   "dateNotification", "donneesActuelles", "modification_id")


def charger(chemin=FICHIER):
    """Pre-filtre cote DuckDB : objet qui matche un terme FORT de la lentille,
    OU titulaire dont le SIREN est surveille. Renvoie (lignes, total)."""
    import duckdb
    con = duckdb.connect()
    presentes = [d[0] for d in con.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)", [chemin]).fetchall()]
    cols = [c for c in COLONNES_UTILES if c in presentes]
    total = con.execute("SELECT count(*) FROM read_parquet(?)", [chemin]).fetchone()[0]
    forts = [m for m, n in L.TERMES if n == "fort"]
    sirens = sorted(SURVEILLES)
    conds = ["(regexp_matches(lower(strip_accents(coalesce(CAST(objet AS VARCHAR), ''))), ?)"
             " OR substr(regexp_replace(coalesce(CAST(titulaire_id AS VARCHAR), ''), '[^0-9]', '', 'g'), 1, 9)"
             " IN ({}))".format(", ".join(["?"] * len(sirens)))]
    if "donneesActuelles" in presentes:
        conds.append("coalesce(donneesActuelles, true)")
    q = "SELECT {} FROM read_parquet(?) WHERE {}".format(
        ", ".join('"{}"'.format(c) for c in cols), " AND ".join(conds))
    cur = con.execute(q, [chemin, regex_sql(forts)] + sirens)
    noms = [d[0] for d in cur.description]
    return [dict(zip(noms, r)) for r in cur.fetchall()], total


# ===========================================================================
# REGROUPEMENT EN PROCEDURES
# ===========================================================================
def _montant(l):
    m = _nombre(l.get("montant_rationalise"))
    return m if m is not None else _nombre(l.get("montant"))


def cle_procedure(l):
    return "{}|{}|{}".format(
        str(l.get("acheteur_id") or "").strip(),
        str(_date(l.get("dateNotification")) or ""),
        L.normaliser_texte(l.get("objet"))[:80])


def regrouper(lignes, aujourd=None):
    """Lignes (marche x lot x version x titulaire) -> procedures."""
    aujourd = aujourd or datetime.date.today()
    groupes = defaultdict(list)
    for l in lignes:
        groupes[cle_procedure(l)].append(l)
    procs = []
    for cle, ls in groupes.items():
        # Version la plus recente de chaque marche (uid) si plusieurs survivent.
        par_uid = {}
        for l in sorted(ls, key=lambda x: _nombre(x.get("modification_id")) or 0):
            par_uid[l.get("uid") or id(l)] = l
        der = list(par_uid.values())
        ref = der[0]
        titulaires, sirens_vus = [], []
        for l in ls:
            nom = str(l.get("titulaire_nom") or "").strip()
            s = siren(l.get("titulaire_id"))
            if nom and nom not in titulaires:
                titulaires.append(nom)
            if s and s not in sirens_vus:
                sirens_vus.append(s)
        montants = [m for m in (_montant(l) for l in ls) if m is not None]
        plafond = max(montants) if montants else None
        douteux = plafond is not None and (plafond < MONTANT_MIN or plafond > MONTANT_MAX_IE)
        notif = _date(ref.get("dateNotification"))
        durees = [d for d in (_nombre(l.get("dureeMois")) for l in der) if d and d > 0]
        duree = max(durees) if durees else None
        fin = ajouter_mois(notif, duree) if notif and duree else None
        classe, termes = L.prefiltre(ref.get("objet") or "")
        uids = sorted({str(l.get("uid")) for l in ls if l.get("uid")})
        offres = [o for o in (_nombre(l.get("offresRecues")) for l in der) if o is not None]
        if any(s in AMARANTE for s in sirens_vus):
            position = "amarante_sortant"
        elif any(s in CONCURRENTS for s in sirens_vus):
            position = "concurrent_suivi"
        else:
            position = "autre"
        expertise = {"ie_titre": "ie", "hors_analyste": "analyste"}.get(classe, "autre")
        procs.append({
            "cle": cle, "objet": str(ref.get("objet") or "")[:400],
            "acheteur": str(ref.get("acheteur_nom") or "").strip(),
            "acheteur_id": str(ref.get("acheteur_id") or "").strip(),
            "region": str(ref.get("acheteur_region_nom") or "").strip(),
            "titulaires": titulaires, "sirens": sirens_vus,
            "sortants_suivis": [SURVEILLES[s] for s in sirens_vus if s in SURVEILLES],
            "position": position, "expertise": expertise, "classe": classe,
            "termes": termes, "outil": est_outil(ref.get("objet")),
            "plafond": None if douteux else plafond, "montant_douteux": douteux,
            "nb_lots": len(par_uid), "duree_mois": duree, "notification": notif,
            "fin": fin, "offres": max(offres) if offres else None,
            "uids": uids, "lien": LIEN_MARCHE.format(uids[0]) if uids else "",
        })
    return procs


def dans_fenetre(p, aujourd):
    if not p["fin"]:
        return False
    return ajouter_mois(aujourd, -RETRO_MOIS) <= p["fin"] <= ajouter_mois(aujourd, HORIZON_MOIS)


def marquer_successions(procs):
    """Pose `renouvele` et `successeur` (lien) sur chaque procedure. Le
    successeur doit partager un terme du lexique OU un titulaire : un meme
    acheteur passe souvent plusieurs marches IE distincts (veille, puis
    formation) qui ne se succedent pas. Cherche
    dans TOUTES les procedures, y compris hors fenetre : le successeur d'un
    marche echu finit souvent en 2030."""
    par_acheteur = defaultdict(list)
    for p in procs:
        if p["acheteur_id"] and p["notification"]:
            par_acheteur[p["acheteur_id"]].append(p)
    for p in procs:
        p["renouvele"], p["successeur"] = False, ""
        if not (p["fin"] and p["acheteur_id"]):
            continue
        debut = ajouter_mois(p["fin"], -FENETRE_SUCCESSION_MOIS)
        fin = ajouter_mois(p["fin"], FENETRE_SUCCESSION_MOIS)
        for q in par_acheteur.get(p["acheteur_id"], []):
            meme_sujet = (set(q["termes"]) & set(p["termes"])
                          or set(q["sirens"]) & set(p["sirens"]))
            if (q is not p and q["expertise"] == p["expertise"] and meme_sujet
                    and q["notification"] > p["notification"]
                    and debut <= q["notification"] <= fin
                    and q["fin"] and q["fin"] > p["fin"]):
                p["renouvele"], p["successeur"] = True, q["lien"]
                break
    return procs


def retenir(procs, aujourd):
    """(en base, vue ARISE). En base : IE, Analyste et tout le portefeuille
    surveille, dans la fenetre. Vue ARISE : IE seulement, hors outils, hors
    marches deja renouveles."""
    marquer_successions(procs)
    base = [p for p in procs if dans_fenetre(p, aujourd)
            and (p["expertise"] in ("ie", "analyste") or p["position"] != "autre")]
    arise = [p for p in base if p["expertise"] == "ie" and not p["outil"]
             and not p["renouvele"]]
    rang = {"amarante_sortant": 0, "concurrent_suivi": 1, "autre": 2}
    arise.sort(key=lambda p: (p["fin"], rang[p["position"]]))
    return base, arise


# ===========================================================================
# SORTIES
# ===========================================================================
def ligne_sheet(p, aujourd):
    v = {
        "detecte_le": aujourd.isoformat(), "fin_estimee": p["fin"].isoformat(),
        "position": p["position"], "acheteur": p["acheteur"], "objet": p["objet"],
        "titulaires_sortants": " ; ".join(p["titulaires"]),
        "plafond_eur": "" if p["plafond"] is None else round(p["plafond"]),
        "nb_lots": p["nb_lots"],
        "duree_mois": "" if p["duree_mois"] is None else int(p["duree_mois"]),
        "notification": p["notification"].isoformat() if p["notification"] else "",
        "offres_recues": "" if p["offres"] is None else int(p["offres"]),
        "region": p["region"], "lien": p["lien"], "cle": p["cle"],
        "statut_arise": "", "commentaire_arise": "",
    }
    return [v[c] for c in COLONNES_SHEET]


DDL = """CREATE TABLE IF NOT EXISTS {t} (
    cle TEXT PRIMARY KEY, fin_estimee DATE, notification DATE, duree_mois REAL,
    acheteur TEXT, acheteur_id TEXT, region TEXT, objet TEXT, titulaires TEXT,
    sirens TEXT, position TEXT, expertise TEXT, outil BOOLEAN, plafond REAL,
    montant_douteux BOOLEAN, nb_lots INTEGER, offres REAL, lien TEXT,
    maj_le TIMESTAMPTZ DEFAULT now())""".format(t=TABLE)
# Colonnes ajoutees apres la creation de la table (IE-6.1) : ALTER idempotent,
# la table existante du premier run est migree sans perte.
MIGRATIONS = ("ALTER TABLE {t} ADD COLUMN IF NOT EXISTS renouvele BOOLEAN".format(t=TABLE),
              "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS successeur TEXT".format(t=TABLE))
COLONNES_PG = ("cle", "fin_estimee", "notification", "duree_mois", "acheteur",
               "acheteur_id", "region", "objet", "titulaires", "sirens", "position",
               "expertise", "outil", "plafond", "montant_douteux", "nb_lots",
               "offres", "lien", "renouvele", "successeur")


def valeurs_pg(p):
    return [p["cle"], p["fin"], p["notification"], p["duree_mois"], p["acheteur"],
            p["acheteur_id"], p["region"], p["objet"], " ; ".join(p["titulaires"]),
            " ; ".join(p["sirens"]), p["position"], p["expertise"], p["outil"],
            p["plafond"], p["montant_douteux"], p["nb_lots"], p["offres"], p["lien"],
            bool(p.get("renouvele")), p.get("successeur", "")]


def ecrire_pg(url, procs):
    if not url:
        return "Postgres : DATABASE_URL absent."
    if not procs:
        return "Postgres : rien a ecrire."
    try:
        import psycopg
        maj = ", ".join("{0} = EXCLUDED.{0}".format(c) for c in COLONNES_PG[1:])
        sql = ("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (cle) DO UPDATE SET {}, "
               "maj_le = now()").format(TABLE, ", ".join(COLONNES_PG),
                                        ", ".join(["%s"] * len(COLONNES_PG)), maj)
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                for m in MIGRATIONS:
                    cur.execute(m)
                for p in procs:
                    cur.execute(sql, valeurs_pg(p))
        return "Postgres : {} procedure(s) a jour dans {}.".format(len(procs), TABLE)
    except Exception as e:  # noqa: BLE001
        return "Postgres : ECHEC d'ecriture ({}).".format(str(e)[:160])


def ouvrir_onglet(sheet_id, fichier_cle):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier_cle, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        return classeur.worksheet(ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=ONGLET, rows=1000, cols=len(COLONNES_SHEET))
        f.update([COLONNES_SHEET])
        f.freeze(rows=1)
        return f


def nouvelles_lignes(feuille_cles, arise, aujourd):
    """Seules les procedures absentes de l'onglet sont ajoutees (lecture de
    la colonne cle par POSITION, regle 4)."""
    return [ligne_sheet(p, aujourd) for p in arise if p["cle"] not in feuille_cles]


def cles_onglet(feuille):
    try:
        return set(feuille.col_values(COL_CLE + 1)[1:])
    except Exception:  # noqa: BLE001
        return set()


def ecrire_sheet(feuille, lignes):
    if feuille is None:
        return "Sheet ARISE : non configure ou inaccessible."
    if not lignes:
        return "Sheet ARISE : aucune nouvelle echeance."
    try:
        feuille.append_rows(lignes, value_input_option="USER_ENTERED")
        return "Sheet ARISE : {} echeance(s) ajoutee(s).".format(len(lignes))
    except Exception as e:  # noqa: BLE001
        return "Sheet ARISE : ECHEC d'ecriture ({}).".format(str(e)[:160])


def _eur(v):
    return "n.c." if v is None else "{:,.0f} EUR".format(v).replace(",", " ")


def rapport_markdown(total, procs, base, arise, ecritures, debug):
    L_ = ["# Echeances IE (DECP)", "",
          "Mode : {}. Lignes DECP lues : {}. Procedures pre-filtrees : {}.".format(
              "DEBUG (aucune ecriture)" if debug else "production", total, len(procs)),
          "", "- En base (IE, Analyste, portefeuilles surveilles) : {}".format(len(base)),
          "- Vue ARISE (IE hors outils) : {}".format(len(arise)), "",
          "## Echeances ARISE", "",
          "| Fin estimee | Position | Acheteur | Objet | Sortant(s) | Plafond | Lien |",
          "|---|---|---|---|---|---|---|"]
    for p in arise[:60]:
        L_.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            p["fin"], p["position"], p["acheteur"][:40], p["objet"][:70].replace("|", "/"),
            " ; ".join(p["titulaires"])[:50], _eur(p["plafond"]), p["lien"]))
    renouv = [p for p in base if p.get("renouvele") and p["expertise"] == "ie"]
    if renouv:
        L_ += ["", "## Deja renouveles (exclus de la vue ARISE)", "",
               "| Fin estimee | Acheteur | Objet | Successeur |", "|---|---|---|---|"]
        for p in renouv:
            L_.append("| {} | {} | {} | {} |".format(
                p["fin"], p["acheteur"][:40], p["objet"][:70].replace("|", "/"),
                p["successeur"]))
    port = [p for p in base if p["position"] == "amarante_sortant"]
    if port:
        L_ += ["", "## Portefeuille Amarante dans la fenetre (toutes expertises)", "",
               "| Fin estimee | Expertise | Acheteur | Objet | Plafond | Lien |",
               "|---|---|---|---|---|---|"]
        for p in sorted(port, key=lambda p: p["fin"]):
            L_.append("| {} | {} | {} | {} | {} | {} |".format(
                p["fin"], p["expertise"], p["acheteur"][:40],
                p["objet"][:70].replace("|", "/"), _eur(p["plafond"]), p["lien"]))
    L_ += ["", "## Ecritures", ""] + ["- " + e for e in ecritures]
    return "\n".join(L_) + "\n"


def main():
    if not L.ACTIF:
        print("Echeances IE desactivees (RADAR_IE=0).")
        return
    debug = os.environ.get("RADAR_IE_DEBUG", "0") not in ("", "0")
    aujourd = datetime.date.today()
    try:
        lignes, total = charger(FICHIER)
    except Exception as e:  # noqa: BLE001
        print("ERREUR : DECP non charge ({}). Rien n'est ecrit.".format(e))
        return
    procs = regrouper(lignes, aujourd)
    base, arise = retenir(procs, aujourd)
    if debug:
        ecritures = ["aucune (mode debug)"]
    else:
        url = os.environ.get("DATABASE_URL")
        sheet_id = os.environ.get("IE_SHEET_ID")
        fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        feuille = None
        if sheet_id and fichier:
            try:
                feuille = ouvrir_onglet(sheet_id, fichier)
            except Exception as e:  # noqa: BLE001
                print("  (info) classeur ARISE inaccessible ({}).".format(str(e)[:120]))
        nouvelles = nouvelles_lignes(cles_onglet(feuille), arise, aujourd) if feuille else []
        ecritures = [ecrire_pg(url, base), ecrire_sheet(feuille, nouvelles)]
    md = rapport_markdown(total, procs, base, arise, ecritures, debug)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
