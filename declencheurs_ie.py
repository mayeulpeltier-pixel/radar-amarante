# -*- coding: utf-8 -*-
"""
Radar Amarante - DECLENCHEURS IE : CJIP (chantier IE-7, 30/10/2026).

Une CJIP financiere impose souvent a la societe un programme de mise en
conformite de 36 mois sous controle de l'AFA. La societe NOMMEE doit alors
acheter cartographie des risques, evaluation des tiers et audits : c'est un
acheteur prive identifie, pour ARISE.

Sonde IE-7 (30/10/2026) : 12 CJIP financieres en deux ans, 5 avec programme
AFA de 36 mois (Balt, PMC, Exclusive Networks, Paprec, Klubb). Mise en ligne
mediane un mois apres validation. Les CJIP de fraude fiscale (HSBC,
Santander) n'imposent pas de programme : elles restent en base, hors vue.

Source officielle : page CJIP de justice.gouv.fr. Deterministe : AUCUN appel
LLM. AUCUN module existant modifie.

LECTURE
  Communique de presse d'abord (court, en texte, annonce le programme en
  clair), convention ensuite. Texte normalise avant recherche : l'extraction
  PDF coupe les lignes ("Agence francaise\\nanticorruption"), defaut constate
  deux fois pendant la sonde. Un PDF sans texte (scan) est marque illisible,
  jamais lu comme "pas de programme".

ECRITURES
  - Postgres `radar_ie_declencheurs` : toutes les CJIP financieres lues
    (ajout seul). Memoire : une CJIP deja en base n'est jamais relue.
  - Sheet ARISE, onglet "Declencheurs" : programme AFA confirme, ou AFA citee
    sans programme detecte ("a verifier"). Ajout seul, colonnes ARISE
    preservees.
"""

import datetime
import html
import io
import os
import re
import time
import unicodedata

import lentille_ie as L

URL_CJIP = ("https://www.justice.gouv.fr/documentation/ressources/"
            "conventions-judiciaires-dinteret-public")
UA = {"User-Agent": "Mozilla/5.0 (compatible; RadarAmarante/1.0)"}
TABLE = "radar_ie_declencheurs"
ONGLET = "Declencheurs"
FENETRE_MOIS = 36          # un programme AFA dure 36 mois : au-dela, plus actif
MIN_CARACTERES = 500
PAUSE = 0.5

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
        "decembre": 12}

COLONNES_SHEET = [
    "detecte_le", "type", "statut", "societe", "validation", "duree_mois",
    "fin_suivi_afa", "juridiction", "lien_communique", "lien_convention", "cle",
    "statut_arise", "commentaire_arise",
]
COL_CLE = COLONNES_SHEET.index("cle")


# ===========================================================================
# PAGE CJIP
# ===========================================================================
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.replace("’", "'").lower()).strip()


def _texte(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


_RE_TITRE = re.compile(r"<h([2-4])[^>]*>(.*?)</h\1>", re.S | re.I)
_RE_LIEN = re.compile(r'<a[^>]+href="([^"]+?\.pdf)"[^>]*>(.*?)</a>', re.S | re.I)
_RE_DATE = re.compile(r"valid[ée]e? le (\d{1,2})(?:er)? ([a-zéû]+) (\d{4})", re.I)
_RE_JURIDICTION = re.compile(r"tribunal judiciaire d[e']\s*([^()]+?)\s*\((\d{2,3})\)", re.I)


def date_validation(titre):
    m = _RE_DATE.search(titre or "")
    if not m:
        return None
    mois = MOIS.get(_norm(m.group(2)))
    try:
        return datetime.date(int(m.group(3)), mois, int(m.group(1))) if mois else None
    except ValueError:
        return None


def type_document(href, libelle):
    h, l = href.upper(), _norm(libelle)
    if "_OV_" in h or "ordonnance" in l:
        return "ordonnance"
    if "_CP_" in h or "communiqu" in l:
        return "communique"
    if "_AIP_" in h:
        return "amende"
    return "convention"


def societe(titre):
    # "conclue entre le procureur ... et la societe X" ; certains titres
    # omettent "entre" (cas reel : SARL GUD, 2024).
    partie = re.split(r"conclue\s+(?:entre\s+)?", titre, maxsplit=1, flags=re.I)[-1]
    m = re.search(r"\bet (?:la |le |les |l')?(?:soci[ée]t[ée]s? )?(.+)$", partie)
    return m.group(1).strip(" .-") if m and len(m.group(1)) < 120 else ""


def parser_page(page):
    titres = [(m.start(), m.end(), _texte(m.group(2))) for m in _RE_TITRE.finditer(page)]
    entrees = []
    for i, (_, fin, titre) in enumerate(titres):
        if "convention judiciaire" not in _norm(titre):
            continue
        suite = titres[i + 1][0] if i + 1 < len(titres) else len(page)
        pdfs = {}
        for href, lib in _RE_LIEN.findall(page[fin:suite]):
            if href.startswith("/"):
                href = "https://www.justice.gouv.fr" + href
            pdfs.setdefault(type_document(href, _texte(lib)), href)
        t = _norm(titre)
        j = _RE_JURIDICTION.search(titre)
        entrees.append({
            "domaine": ("ENVIRONNEMENT" if "environnement" in t
                        else "FINANCIER" if "financier" in t else "AUTRE"),
            "validation": date_validation(titre), "societe": societe(titre),
            "juridiction": "{} ({})".format(j.group(1).strip(), j.group(2)) if j else "",
            "pdfs": pdfs,
            "cle": pdfs.get("convention") or pdfs.get("communique") or titre[:200],
        })
    return entrees


# ===========================================================================
# LECTURE DES PDF
# ===========================================================================
_RE_AFA = re.compile(r"agence fran[cç]aise anti-?corruption|\bafa\b", re.I)
_RE_PROGRAMME = re.compile(r"programme de mise en conformit", re.I)
_RE_DUREE = re.compile(r"(?:dur[ée]e|p[ée]riode) (?:maximale )?de (\d+|deux|trois|quatre|"
                       r"dix-huit|vingt-quatre|trente-six)(?: \(\d+\))? (ans|mois)", re.I)
_NOMBRES = {"deux": 2, "trois": 3, "quatre": 4, "dix-huit": 18, "vingt-quatre": 24,
            "trente-six": 36}


def analyser_texte(texte):
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", texte or "")
    t = re.sub(r"\s+", " ", t)
    programme = bool(_RE_PROGRAMME.search(t))
    duree = None
    m = _RE_DUREE.search(t) if programme else None
    if m:
        brut = m.group(1).lower()
        n = int(brut) if brut.isdigit() else _NOMBRES.get(brut)
        if n:
            duree = n * 12 if m.group(2).lower() == "ans" else n
    return {"afa": bool(_RE_AFA.search(t)), "programme": programme, "duree_mois": duree}


def texte_pdf(contenu):
    from pypdf import PdfReader
    lecteur = PdfReader(io.BytesIO(contenu))
    return " ".join((p.extract_text() or "") for p in lecteur.pages[:80])


def _get(url, binaire=False):
    import requests
    rep = requests.get(url, headers=UA, timeout=60)
    rep.raise_for_status()
    return rep.content if binaire else rep.text


def lire_cjip(entree, get=None, lire_pdf=None, pause=PAUSE):
    """Ajoute `lecture` et `analyse` a l'entree. Ne leve jamais."""
    get, lire_pdf = get or _get, lire_pdf or texte_pdf
    textes, erreurs = [], []
    for typ in ("communique", "convention"):
        url = entree["pdfs"].get(typ)
        if not url:
            continue
        try:
            t = lire_pdf(get(url, binaire=True))
            if len(t or "") >= MIN_CARACTERES:
                textes.append((typ, t))
        except Exception as e:  # noqa: BLE001
            erreurs.append("{} ({}) : {}".format(entree["societe"][:40], typ, str(e)[:100]))
        if pause:
            time.sleep(pause)
    entree["lecture"] = "+".join(t for t, _ in textes) or "illisible"
    entree["analyse"] = analyser_texte(" ".join(t for _, t in textes)) if textes else None
    # Un document non telecharge (erreur reseau, 502 constate sur IDEMIA) rend
    # la lecture INCOMPLETE : sans ce drapeau, un "sans programme" provisoire
    # devenait definitif, car la memoire ne relit jamais une CJIP connue.
    entree["incomplet"] = bool(erreurs)
    return erreurs


def statut(entree):
    a = entree.get("analyse")
    if not a:
        return "incomplet" if entree.get("incomplet") else "illisible"
    if a["programme"] and a["afa"]:
        return "programme_afa"
    if entree.get("incomplet"):
        return "incomplet"
    # L'un sans l'autre (Colas Rail Asia : AFA citee, programme non detecte) :
    # signal reel mais incomplet, a verifier a la main.
    if a["programme"] or a["afa"]:
        return "a_verifier"
    return "sans_programme"


def ajouter_mois(d, mois):
    m = d.month - 1 + int(mois)
    annee, mm = d.year + m // 12, m % 12 + 1
    bissex = annee % 4 == 0 and (annee % 100 != 0 or annee % 400 == 0)
    jours = [31, 29 if bissex else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mm - 1]
    return datetime.date(annee, mm, min(d.day, jours))


def fin_suivi(entree):
    a = entree.get("analyse") or {}
    if entree.get("validation") and a.get("duree_mois"):
        return ajouter_mois(entree["validation"], a["duree_mois"])
    return None


# ===========================================================================
# EXECUTION (coeur testable, sans ecriture)
# ===========================================================================
def executer(aujourd=None, deja_vus=None, get=None, lire_pdf=None, pause=PAUSE):
    aujourd = aujourd or datetime.date.today()
    get = get or _get
    deja_vus = deja_vus or set()
    erreurs = []
    try:
        entrees = parser_page(get(URL_CJIP))
    except Exception as e:  # noqa: BLE001
        return [], {"erreurs": ["page CJIP : {}".format(e)], "lues": 0, "deja_vues": 0,
                    "financieres": 0}
    limite = ajouter_mois(aujourd, -FENETRE_MOIS)
    fin = [e for e in entrees if e["domaine"] == "FINANCIER"
           and e["validation"] and e["validation"] >= limite]
    nouvelles = [e for e in fin if e["cle"] not in deja_vus]
    for e in nouvelles:
        erreurs += lire_cjip(e, get=get, lire_pdf=lire_pdf, pause=pause)
        e["statut"], e["fin_suivi"] = statut(e), fin_suivi(e)
    return nouvelles, {"erreurs": erreurs, "lues": len(nouvelles),
                       "deja_vues": len(fin) - len(nouvelles), "financieres": len(fin)}


def visible_arise(e):
    """Les scans illisibles sont MONTRES a ARISE : ce sont souvent des CJIP
    anciennes de corruption (ADP Ingenierie, Seves/Sediver) qu'un humain lit
    en deux minutes. Un statut incomplet attend la relecture du run suivant."""
    return e["statut"] in ("programme_afa", "a_verifier", "illisible")


def ligne_sheet(e, aujourd):
    a = e.get("analyse") or {}
    v = {"detecte_le": aujourd.isoformat(), "type": "CJIP",
         "statut": {"programme_afa": "programme AFA", "a_verifier": "AFA citee, a verifier",
                    "illisible": "PDF scanne, a verifier"}
         .get(e["statut"], e["statut"]),
         "societe": e["societe"], "validation": e["validation"].isoformat(),
         "duree_mois": a.get("duree_mois") or "",
         "fin_suivi_afa": e["fin_suivi"].isoformat() if e.get("fin_suivi") else "",
         "juridiction": e["juridiction"],
         "lien_communique": e["pdfs"].get("communique", ""),
         "lien_convention": e["pdfs"].get("convention", ""), "cle": e["cle"],
         "statut_arise": "", "commentaire_arise": ""}
    return [v[c] for c in COLONNES_SHEET]


# ===========================================================================
# STOCKAGE (best-effort, ne leve jamais)
# ===========================================================================
DDL = """CREATE TABLE IF NOT EXISTS {t} (
    cle TEXT PRIMARY KEY, type TEXT, societe TEXT, validation DATE, juridiction TEXT,
    statut TEXT, programme BOOLEAN, afa BOOLEAN, duree_mois INTEGER, fin_suivi DATE,
    lecture TEXT, lien_communique TEXT, lien_convention TEXT,
    cree_le TIMESTAMPTZ DEFAULT now())""".format(t=TABLE)
COLONNES_PG = ("cle", "type", "societe", "validation", "juridiction", "statut",
               "programme", "afa", "duree_mois", "fin_suivi", "lecture",
               "lien_communique", "lien_convention")


def valeurs_pg(e):
    a = e.get("analyse") or {}
    return [e["cle"], "CJIP", e["societe"], e["validation"], e["juridiction"], e["statut"],
            a.get("programme"), a.get("afa"), a.get("duree_mois"), e.get("fin_suivi"),
            e.get("lecture"), e["pdfs"].get("communique", ""), e["pdfs"].get("convention", "")]


def memoire_pg(url):
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute("SELECT cle FROM {} WHERE statut NOT IN {}".format(
                    TABLE, STATUTS_A_RELIRE_SQL))
                return {r[0] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        print("  (info) memoire Postgres indisponible ({}).".format(str(e)[:120]))
        return None


# Statuts provisoires : la CJIP est relue a chaque run, et sa ligne mise a
# jour des qu'une lecture complete aboutit.
STATUTS_A_RELIRE_SQL = "('illisible', 'incomplet')"


def ecrire_pg(url, entrees):
    """Ajout seul, SAUF pour une CJIP au statut provisoire (illisible ou
    incomplet) : sa ligne est mise a jour quand la lecture aboutit."""
    if not url:
        return "Postgres : DATABASE_URL absent."
    if not entrees:
        return "Postgres : rien a ecrire."
    try:
        import psycopg
        maj = ", ".join("{0} = EXCLUDED.{0}".format(c) for c in COLONNES_PG[1:])
        sql = ("INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (cle) DO UPDATE SET {m} "
               "WHERE {t}.statut IN {r}").format(r=STATUTS_A_RELIRE_SQL,
            t=TABLE, c=", ".join(COLONNES_PG), v=", ".join(["%s"] * len(COLONNES_PG)), m=maj)
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                for e in entrees:
                    cur.execute(sql, valeurs_pg(e))
        return "Postgres : {} CJIP ecrite(s) dans {}.".format(len(entrees), TABLE)
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
        return "Sheet ARISE : aucun nouveau declencheur."
    try:
        feuille.append_rows(lignes, value_input_option="USER_ENTERED")
        return "Sheet ARISE : {} declencheur(s) ajoute(s).".format(len(lignes))
    except Exception as e:  # noqa: BLE001
        return "Sheet ARISE : ECHEC d'ecriture ({}).".format(str(e)[:160])


def rapport_markdown(nouvelles, info, ecritures, debug):
    L_ = ["# Declencheurs IE (CJIP)", "",
          "Mode : {}. CJIP financieres sur {} mois : {} (deja connues : {}, lues ce run : {}).".format(
              "DEBUG (aucune ecriture)" if debug else "production", FENETRE_MOIS,
              info["financieres"], info["deja_vues"], info["lues"]), "",
          "| Validation | Societe | Statut | Duree | Fin suivi AFA | Communique |",
          "|---|---|---|---|---|---|"]
    for e in sorted(nouvelles, key=lambda x: x["validation"], reverse=True):
        a = e.get("analyse") or {}
        L_.append("| {} | {} | {} | {} | {} | {} |".format(
            e["validation"], e["societe"][:50], e["statut"],
            "{} mois".format(a["duree_mois"]) if a.get("duree_mois") else "-",
            e.get("fin_suivi") or "-", e["pdfs"].get("communique", "-")))
    if not nouvelles:
        L_.append("| (aucune nouvelle CJIP) | | | | | |")
    L_ += ["", "## Ecritures", ""] + ["- " + x for x in ecritures]
    if info["erreurs"]:
        L_ += ["", "## Erreurs", ""] + ["- " + x for x in info["erreurs"][:20]]
    return "\n".join(L_) + "\n"


def main():
    if not L.ACTIF:
        print("Declencheurs IE desactives (RADAR_IE=0).")
        return
    debug = os.environ.get("RADAR_IE_DEBUG", "0") not in ("", "0")
    aujourd = datetime.date.today()
    url = os.environ.get("DATABASE_URL")
    sheet_id = os.environ.get("IE_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    feuille = None
    if sheet_id and fichier and not debug:
        try:
            feuille = ouvrir_onglet(sheet_id, fichier)
        except Exception as e:  # noqa: BLE001
            print("  (info) classeur ARISE inaccessible ({}).".format(str(e)[:120]))
    deja = memoire_pg(url)
    if deja is None:
        deja = cles_onglet(feuille) if feuille is not None else set()
    nouvelles, info = executer(aujourd, deja_vus=deja)
    if debug:
        ecritures = ["aucune (mode debug)"]
    else:
        presentes = cles_onglet(feuille) if feuille is not None else set()
        lignes = [ligne_sheet(e, aujourd) for e in nouvelles
                  if visible_arise(e) and e["cle"] not in presentes]
        ecritures = [ecrire_pg(url, nouvelles), ecrire_sheet(feuille, lignes)]
    md = rapport_markdown(nouvelles, info, ecritures, debug)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
