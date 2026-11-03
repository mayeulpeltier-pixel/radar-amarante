# -*- coding: utf-8 -*-
"""
Radar Amarante - COLLECTEUR PRESSE M&A / IMPLANTATION (chantier IE-11, 03/11/2026).

Declencheur due-diligence pour ARISE : une entreprise qui ACQUIERT, prend une
participation, ou S'IMPLANTE a l'etranger (surtout en zone a risque) a besoin
de due diligence sur sa cible / son marche, et souvent de securite sur place.
C'est le chevauchement ARISE (DD) + Operations (surete du deploiement).

POURQUOI LA PRESSE ET PAS BODACC (sondes du 01-02/11, voir
strategie_arise_elargissement_2026-11.md)
  BODACC donne ~1% de grandes societes dans les procedures/cessions (quasi rien
  d'exploitable). La presse M&A, elle, rapporte des dizaines d'evenements
  NOMMES/mois pile sur l'angle "entree en zone a risque" (Hilton Afrique de
  l'Ouest, JPMorgan Nigeria, Dangote Cameroun...). Source gratuite, sans cle :
  Google News RSS.

DOCTRINE (comme tout le volet IE)
  Le LLM fournit les faits, le code applique la doctrine. Pre-filtre lexical
  deterministe AVANT tout appel LLM (on ne paie pas le modele pour de la
  diplomatie d'Etat ou du sport). Memoire avant plafond (regle 9). Isolation :
  aucun fichier Operations touche ; on reutilise la PLOMBERIE de lentille_ie
  (appel LLM, parsing, ACTIF), pas sa grille (batie pour les avis de marche).

ECRITURES
  - Postgres `radar_ie_presse` : ajout seul (un article deja vu n'est pas relu).
  - Sheet ARISE, onglet "Presse M&A" : ajout seul, colonnes ARISE preservees.

FLAG : RADAR_IE=1 (via lentille_ie.ACTIF). RADAR_IE_DEBUG=1 : aucune ecriture.
"""

import datetime
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import config_env
import lentille_ie as L

TABLE = "radar_ie_presse"
ONGLET = "Presse M&A"
UA = {"User-Agent": "Mozilla/5.0 (compatible; RadarAmarante/1.0)"}
BUDGET = config_env.entier("RADAR_IE_PRESSE_BUDGET", 40)
FENETRE_JOURS = config_env.entier("RADAR_IE_PRESSE_JOURS", 30)
PAUSE = 0.3

# Requetes Google News ciblant les declencheurs DD d'ARISE. Surchargeables par
# RADAR_IE_PRESSE_REQUETES (separees par "|").
REQUETES_DEFAUT = [
    '(acquisition OR rachat OR "prise de participation") (Afrique OR Sahel OR "Moyen-Orient" OR Ukraine OR international)',
    '("nouvelle filiale" OR "s\'implante" OR implantation OR "ouvre un bureau") (Afrique OR Mali OR Niger OR Nigeria OR Irak OR "zone a risque")',
    '(acquisition OR rachat OR "prise de participation") entreprise',
]


def requetes():
    brut = os.environ.get("RADAR_IE_PRESSE_REQUETES")
    return [q.strip() for q in brut.split("|") if q.strip()] if brut else list(REQUETES_DEFAUT)


# ===========================================================================
# 1. COLLECTE RSS (reseau injectable) + PARSING (pur)
# ===========================================================================
def url_rss(query):
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
            + "&hl=fr&gl=FR&ceid=FR:fr")


def _get(query):
    req = urllib.request.Request(url_rss(query), headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def parser_rss(xml_bytes):
    """XML RSS Google News -> liste d'items (titre, lien, date, source). Pur."""
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        src = it.find("{*}source")
        if src is None:
            src = it.find("source")
        items.append({
            "titre": (it.findtext("title") or "").strip(),
            "lien": (it.findtext("link") or "").strip(),
            "date_article": (it.findtext("pubDate") or "").strip(),
            "source": (src.text if src is not None else "").strip() or "",
        })
    return items


_MOIS_EN = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_RE_RFC822 = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})")


def _jours(pub, aujourd):
    """Jours entre la date RFC822 d'un article et aujourd. Parsing manuel du
    mois (anglais) : `strptime('%b')` depend de la locale du runner (piege
    ADB), alors que les dates Google News sont toujours en anglais."""
    m = _RE_RFC822.search(str(pub or ""))
    if not m:
        return None
    mois = _MOIS_EN.get(m.group(2).lower())
    if not mois:
        return None
    try:
        d = datetime.date(int(m.group(3)), mois, int(m.group(1)))
    except ValueError:
        return None
    return (aujourd - d).days


def cle_item(item):
    """Cle stable d'un article : titre normalise (sans la source en suffixe
    '- Media' que Google News ajoute). Deux flux pointant le meme evenement
    convergent ainsi vers UNE cle (dedup)."""
    t = L.normaliser_texte(item.get("titre"))
    t = re.sub(r"\s+-\s+[^-]+$", "", t)          # retire le '- Source' final
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


# ===========================================================================
# 2. PRE-FILTRE LEXICAL (deterministe, avant LLM)
# ===========================================================================
# Signaux d'un VRAI evenement d'entreprise (M&A / implantation).
SIGNAUX = ("acquisition", "acquiert", "rachat", "rachete", "rachat de",
           "prise de participation", "s'implante", "implante", "implantation",
           "nouvelle filiale", "ouvre un bureau", "co-entreprise", "coentreprise",
           "joint-venture", "joint venture", "fusion", "absorbe",
           "prise de controle", "prend le controle", "acquiere")
# Bruit : diplomatie d'Etat, aide militaire, sport, politique, ET finance de
# marche (rachats d'ACTIONS = buybacks, pas des M&A d'entreprise). Affiné sur la
# validation live du 03/11 : 57/62 passaient, dont des buybacks (Magna, Standex)
# et "acquisition d'armes OTAN". Le bruit PRIME sur le signal (prefiltre).
BRUIT = ("partenariat strategique global", "sommet", "visite d'etat",
         "accord bilateral", "aide militaire", "union internationale",
         "biathlon", "championnat", "ligue des", "parlement europeen",
         "election", "coupe du monde", "jeux olympiques", "diplomatie",
         # finance de marche (buybacks) : "rachat d'actions", "programme de rachat"
         "programme de rachat", "rachat d'action", "rachat de ses actions",
         "rachat d'un montant", "rachat de titres",
         # armes / defense etatique (pas une operation d'entreprise ARISE)
         "armes", "otan", "missile", "munitions")


def prefiltre_presse(titre):
    """(classe, signaux). Classes : bruit > candidat > sans_signal. Seul
    `candidat` part au LLM. Pur et testable."""
    t = L.normaliser_texte(titre)
    if any(b in t for b in BRUIT):
        return "bruit", []
    trouves = [s for s in SIGNAUX if s in t]
    if trouves:
        return "candidat", sorted(set(trouves))
    return "sans_signal", []


# ===========================================================================
# 3. LENTILLE PRESSE (grille dediee ; reutilise la plomberie LLM de lentille_ie)
# ===========================================================================
OPERATIONS = ("acquisition", "prise_participation", "implantation",
              "coentreprise", "autre")
BESOINS = ("due_diligence", "securite", "les_deux", "aucun")

PROMPT_PRESSE = """Tu es analyste pour ARISE, le pole intelligence economique et conformite d'Amarante (societe francaise de surete). On te donne un TITRE DE PRESSE (et sa source). Determine s'il s'agit d'un EVENEMENT D'ENTREPRISE (acquisition, prise de participation, implantation ou ouverture a l'etranger, co-entreprise) creant un besoin de DUE DILIGENCE (verifier une cible, un partenaire, un marche) ou de SECURITE (deploiement en zone a risque) pour un acteur IDENTIFIABLE.

Tu ne notes pas : tu remplis la grille, factuellement.

- entreprise : l'entreprise ACTRICE (celle qui acquiert / s'implante), ou "" si aucune identifiable
- operation : acquisition | prise_participation | implantation | coentreprise | autre
- pays : le ou les pays/zones concernes, ou ""
- zone_risque : true si cela concerne une zone a risque (Afrique subsaharienne, Sahel, Maghreb, Moyen-Orient, Asie centrale, zones de conflit), false sinon
- besoin : due_diligence | securite | les_deux | aucun
- pertinent_arise : true si un acteur identifiable a un besoin reel de DD ou de securite ; false pour de la diplomatie d'Etat, du sport, un simple cours de bourse, une rumeur sans acteur clair
- resume : une phrase factuelle
- confiance : 0.0 a 1.0

N'invente rien. Dans le doute : operation=autre, besoin=aucun, pertinent_arise=false.

TITRE : {titre}
SOURCE : {source}

Reponds UNIQUEMENT en JSON valide, sans texte autour :
{{"entreprise": "...", "operation": "...", "pays": "...", "zone_risque": false, "besoin": "...", "pertinent_arise": false, "resume": "...", "confiance": 0.0}}"""


def construire_prompt(item):
    return PROMPT_PRESSE.format(titre=str(item.get("titre") or "")[:300],
                                source=item.get("source") or "n.c.")


def valider(brut):
    if not isinstance(brut, dict):
        return None
    try:
        conf = float(brut.get("confiance", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "entreprise": str(brut.get("entreprise") or "").strip()[:160],
        "operation": L._enum(brut.get("operation"), OPERATIONS, "autre"),
        "pays": str(brut.get("pays") or "").strip()[:120],
        "zone_risque": L._bool(brut.get("zone_risque")),
        "besoin": L._enum(brut.get("besoin"), BESOINS, "aucun"),
        "pertinent_arise": L._bool(brut.get("pertinent_arise")),
        "resume": str(brut.get("resume") or "")[:300],
        "confiance": max(0.0, min(conf, 1.0)),
    }


def action(ext):
    """Doctrine en code : un evenement en zone a risque avec besoin de securite
    est le plus chaud (DD + deploiement Amarante) ; un besoin DD seul vaut la
    surveillance ; le reste est ignore."""
    if not ext or not ext["pertinent_arise"] or ext["operation"] == "autre":
        return "ignorer"
    if ext["zone_risque"] and ext["besoin"] in ("securite", "les_deux"):
        return "contacter"
    if ext["besoin"] != "aucun":
        return "surveiller"
    return "ignorer"


def extraire(item, appel=None, modele=None, pause=PAUSE):
    """Item -> extraction validee, ou None. Ne leve jamais (regle du volet)."""
    appel = appel or L._appel_http
    try:
        texte = appel(construire_prompt(item), modele or L.MODELE)
    except Exception as e:  # noqa: BLE001
        print("  (info) extraction presse echouee ({}).".format(str(e)[:120]))
        return None
    finally:
        if pause:
            time.sleep(pause)
    return valider(L.parser_json(texte))


# ===========================================================================
# 4. EXECUTION (coeur testable, sans reseau ni ecriture)
# ===========================================================================
def collecter_items(aujourd, get=None, qs=None):
    """Interroge les requetes, parse, filtre par fraicheur, DEDUPLIQUE. Pur si
    `get` est injecte (callable(query) -> xml bytes)."""
    get = get or _get
    vus, items = set(), []
    for q in (qs or requetes()):
        try:
            bruts = parser_rss(get(q))
        except Exception as e:  # noqa: BLE001
            print("  (info) requete presse en echec ({}).".format(str(e)[:100]))
            continue
        for it in bruts:
            j = _jours(it.get("date_article"), aujourd)
            if j is not None and j > FENETRE_JOURS:
                continue
            cle = cle_item(it)
            if not cle or cle in vus:
                continue
            vus.add(cle)
            it["cle"] = cle
            items.append(it)
    return items


def executer(items, deja_vus=None, appel=None, aujourd=None, budget=None, pause=PAUSE):
    """Items pre-collectes -> declencheurs analyses. Memoire AVANT plafond :
    on ecarte d'abord les connus, puis on plafonne (regle 9)."""
    deja_vus = deja_vus or set()
    aujourd = aujourd or datetime.date.today()
    budget = budget if budget is not None else BUDGET
    candidats = [it for it in items
                 if prefiltre_presse(it["titre"])[0] == "candidat"
                 and it["cle"] not in deja_vus]
    retenus, erreurs = [], []
    for it in candidats[:budget]:
        ext = extraire(it, appel=appel, pause=pause)
        if ext is None:
            erreurs.append(it["titre"][:60])
            continue
        it["extraction"], it["action"] = ext, action(ext)
        it["detecte_le"] = aujourd.isoformat()
        retenus.append(it)
    info = {"collectes": len(items), "candidats": len(candidats),
            "analyses": len(candidats[:budget]), "retenus": len(retenus),
            "hors_budget": max(0, len(candidats) - budget), "erreurs": erreurs}
    return retenus, info


def visible_arise(it):
    """Affiche a ARISE ce qui appelle une action (contacter/surveiller)."""
    return it.get("action") in ("contacter", "surveiller")


# ===========================================================================
# 5. STOCKAGE (best-effort, ne leve jamais)
# ===========================================================================
COLONNES_SHEET = ["detecte_le", "action", "entreprise", "operation", "pays",
                  "zone_risque", "besoin", "resume", "source", "lien",
                  "date_article", "cle", "statut_arise", "commentaire_arise"]
COL_CLE = COLONNES_SHEET.index("cle")

COLONNES_PG = ("cle", "detecte_le", "action", "entreprise", "operation", "pays",
               "zone_risque", "besoin", "resume", "source", "lien",
               "date_article", "confiance")
DDL = """CREATE TABLE IF NOT EXISTS {t} (
    cle TEXT PRIMARY KEY, detecte_le DATE, action TEXT, entreprise TEXT,
    operation TEXT, pays TEXT, zone_risque BOOLEAN, besoin TEXT, resume TEXT,
    source TEXT, lien TEXT, date_article TEXT, confiance REAL,
    cree_le TIMESTAMPTZ DEFAULT now())""".format(t=TABLE)


def valeurs_pg(it):
    e = it["extraction"]
    return [it["cle"], it["detecte_le"], it["action"], e["entreprise"],
            e["operation"], e["pays"], e["zone_risque"], e["besoin"],
            e["resume"], it.get("source", ""), it.get("lien", ""),
            it.get("date_article", ""), e["confiance"]]


def ligne_sheet(it):
    e = it["extraction"]
    v = {"detecte_le": it["detecte_le"], "action": it["action"],
         "entreprise": e["entreprise"], "operation": e["operation"],
         "pays": e["pays"], "zone_risque": "oui" if e["zone_risque"] else "non",
         "besoin": e["besoin"], "resume": e["resume"], "source": it.get("source", ""),
         "lien": it.get("lien", ""), "date_article": it.get("date_article", ""),
         "cle": it["cle"], "statut_arise": "", "commentaire_arise": ""}
    return [v[c] for c in COLONNES_SHEET]


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
    except Exception as e:  # noqa: BLE001
        print("  (info) memoire presse IE indisponible ({}).".format(str(e)[:120]))
        return None


def ecrire_pg(url, retenus):
    if not url:
        return "Postgres : DATABASE_URL absent."
    if not retenus:
        return "Postgres : rien a ecrire."
    try:
        import psycopg
        sql = ("INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (cle) DO NOTHING"
               ).format(t=TABLE, c=", ".join(COLONNES_PG),
                        v=", ".join(["%s"] * len(COLONNES_PG)))
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                for it in retenus:
                    cur.execute(sql, valeurs_pg(it))
        return "Postgres : {} declencheur(s) presse ecrit(s) dans {}.".format(len(retenus), TABLE)
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


def cles_onglet(feuille):
    try:
        return set(feuille.col_values(COL_CLE + 1)[1:])
    except Exception:  # noqa: BLE001
        return set()


def ecrire_sheet(feuille, retenus):
    if feuille is None:
        return "Sheet ARISE : non configure ou inaccessible."
    affichables = [it for it in retenus if visible_arise(it)]
    if not affichables:
        return "Sheet ARISE : aucun declencheur presse a afficher."
    try:
        presentes = cles_onglet(feuille)
        lignes = [ligne_sheet(it) for it in affichables if it["cle"] not in presentes]
        if not lignes:
            return "Sheet ARISE : declencheurs presse deja presents."
        feuille.append_rows(lignes, value_input_option="USER_ENTERED")
        return "Sheet ARISE : {} declencheur(s) presse ajoute(s).".format(len(lignes))
    except Exception as e:  # noqa: BLE001
        return "Sheet ARISE : ECHEC d'ecriture ({}).".format(str(e)[:160])


# ===========================================================================
# 6. RAPPORT + POINT D'ENTREE
# ===========================================================================
def rapport_markdown(retenus, info, ecritures, debug):
    lignes = ["# Declencheurs IE - Presse M&A / implantation", "",
              "Mode : {}. Collectes : {} | candidats : {} | analyses : {} | "
              "retenus : {} | hors budget : {}.".format(
                  "DEBUG (aucune ecriture)" if debug else "production",
                  info["collectes"], info["candidats"], info["analyses"],
                  info["retenus"], info["hors_budget"]), "",
              "| Action | Entreprise | Operation | Pays | Zone risque | Besoin | Source |",
              "|---|---|---|---|---|---|---|"]
    for it in sorted(retenus, key=lambda x: x["action"]):
        e = it["extraction"]
        lignes.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            it["action"], (e["entreprise"] or "?")[:32], e["operation"],
            (e["pays"] or "")[:24], "oui" if e["zone_risque"] else "non",
            e["besoin"], it.get("source", "")[:20]))
    if not retenus:
        lignes.append("| (aucun declencheur presse) | | | | | | |")
    lignes += ["", "## Ecritures", ""] + ["- " + x for x in ecritures]
    if info["erreurs"]:
        lignes += ["", "## Extractions en echec", ""] + ["- " + x for x in info["erreurs"][:15]]
    return "\n".join(lignes) + "\n"


def main():
    if not L.ACTIF:
        print("Collecteur presse M&A IE desactive (RADAR_IE=0).")
        return
    debug = os.environ.get("RADAR_IE_DEBUG", "0") not in ("", "0")
    aujourd = datetime.date.today()
    url = os.environ.get("DATABASE_URL")
    sheet_id = os.environ.get("IE_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    items = collecter_items(aujourd)
    deja = memoire_pg(url) or set()
    retenus, info = executer(items, deja_vus=deja, aujourd=aujourd)

    if debug:
        ecritures = ["aucune (mode debug)"]
    else:
        feuille = None
        if sheet_id and fichier:
            try:
                feuille = ouvrir_onglet(sheet_id, fichier)
            except Exception as e:  # noqa: BLE001
                print("  (info) classeur ARISE inaccessible ({}).".format(str(e)[:120]))
        ecritures = [ecrire_pg(url, retenus), ecrire_sheet(feuille, retenus)]
    md = rapport_markdown(retenus, info, ecritures, debug)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
