# -*- coding: utf-8 -*-
"""
Radar Amarante - COLLECTEUR IE (chantier IE-4, 30/10/2026).

Appels d'offres d'intelligence economique et de conformite publies au BOAMP,
lus par la lentille IE (lentille_ie.py) et restitues a ARISE dans un classeur
Google Sheet A PART, distinct du Sheet Operations : ARISE ne voit que ses
leads, jamais les donnees Operations.

AUCUN module existant n'est modifie. Le collecteur a :
  - son propre classeur (secret IE_SHEET_ID), onglet "Leads IE" ;
  - sa propre table Postgres `radar_ie_leads` (ajout seul, comme radar_lignes) ;
  - son propre workflow (ie.yml).
L'integration dans l'application commune est le chantier IE-2.

ENTONNOIR (du moins cher au plus cher)
---------------------------------------
  1. BOAMP : recherche limitee au TITRE (`search(objet, ...)`), fenetre recente.
     Repli automatique sur la recherche plein texte (prouvee par la sonde
     IE-0) si l'API refuse la syntaxe : le pre-filtre local recadre de toute
     facon sur le titre.
  2. Avis encore ouverts seulement : ni resultat, ni rectificatif, ni date
     limite depassee.
  3. Pre-filtre lexical de la lentille (aucun appel LLM pour le bruit).
  4. MEMOIRE AVANT PLAFOND : les avis deja analyses sont retires AVANT
     d'appliquer le budget (regle 9 du projet).
  5. Lentille : extraction LLM, bareme, escalade.

ECRITURES (best-effort absolu : un echec d'ecriture ne fait jamais tomber
le run, il est journalise)
  - Postgres : TOUT avis analyse (contacter, surveiller, ignorer, ombre). C'est
    la memoire inter-runs et la matiere de la revue D1 a 8 semaines. Un avis
    dont l'extraction a echoue n'est PAS ecrit : il repartira au run suivant.
  - Sheet ARISE : seulement le perimetre affiche, actions contacter et
    surveiller. Ajout en fin de feuille, jamais de reecriture : les colonnes
    de suivi remplies par ARISE ne sont jamais ecrasees.

VARIABLES
  RADAR_IE=1            active le collecteur (defaut : off)
  RADAR_IE_DEBUG=1      collecte et pre-filtre SANS LLM ni ecriture
  RADAR_IE_JOURS        fenetre de publication (defaut 30)
  RADAR_IE_BUDGET       avis analyses max par run (defaut 30)
  IE_SHEET_ID           classeur ARISE (partage avec le compte de service)
  GOOGLE_SERVICE_ACCOUNT_FILE, DATABASE_URL, ANTHROPIC_API_KEY
"""

import datetime
import json
import os
import re
import time

import lentille_ie as L

# ===========================================================================
# CONFIGURATION
# ===========================================================================
URL_BOAMP = ("https://boamp-datadila.opendatasoft.com/api/explore/v2.1/"
             "catalog/datasets/boamp/records")
LIEN_AVIS = "https://www.boamp.fr/pages/avis/?q=idweb:{}"
PAGE = 100
MAX_PAR_REQUETE = 300
PAUSE = 0.3
SOURCE = "BOAMP"
TABLE = "radar_ie_leads"
ONGLET = "Leads IE"


def _entier(nom, defaut):
    try:
        return int(os.environ.get(nom) or defaut)
    except ValueError:
        return defaut


JOURS = _entier("RADAR_IE_JOURS", 30)
BUDGET = _entier("RADAR_IE_BUDGET", 30)
DEBUG = os.environ.get("RADAR_IE_DEBUG", "0") not in ("", "0")

# Requetes serveur : formes accentuees (le BOAMP est en francais). Seuls les
# termes FORTS de la lentille sont interroges ; les ambigus n'apportent que
# du bruit cote serveur.
REQUETES = [
    "intelligence économique", "intelligence stratégique", "sécurité économique",
    "renseignement économique", "veille stratégique", "veille concurrentielle",
    "veille économique", "veille sectorielle", "due diligence",
    "diligence raisonnable", "évaluation des tiers", "intégrité des tiers",
    "cartographie des acteurs", "anticorruption", "anti-corruption", "probité",
    "Sapin II", "Sapin 2", "OSINT",
]

# Colonnes du Sheet ARISE. LECTURE ET ECRITURE POSITIONNELLES (regle 4 du
# projet). Les deux dernieres sont la zone de saisie d'ARISE.
COLONNES_SHEET = [
    "date_detection", "action", "score", "famille", "acheteur", "titre",
    "date_limite", "lien", "resume", "adequation", "commercial", "motifs",
    "source", "id_avis", "statut_arise", "commentaire_arise",
]
COL_ID_AVIS = COLONNES_SHEET.index("id_avis")

# ===========================================================================
# EXTRACTION DEFENSIVE DES AVIS BOAMP
# ===========================================================================
def _premier(rec, *cles):
    for c in cles:
        v = rec.get(c)
        if isinstance(v, dict):
            v = v.get("libelle") or v.get("valeur") or ""
        if isinstance(v, (list, tuple)):
            v = " ; ".join(str(x) for x in v if x)
        if v not in (None, "", []):
            return str(v).strip()
    return ""


def _texte_donnees(d):
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except (ValueError, TypeError):
            return d
    try:
        return json.dumps(d, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(d)


_RE_DESCRIPTION = re.compile(
    r'"[^"]*(?:[Dd]escription|OBJET_COMPLET|objet_complet)[^"]*"\s*:\s*'
    r'(?:\{\s*"#text"\s*:\s*)?"((?:[^"\\]|\\.){40,})"')


def description_depuis_donnees(brut):
    """Quelques descriptions longues tirees du blob eForms/ancien schema,
    sans doublon. Vide si rien d'exploitable : le titre suffit alors."""
    vus = []
    for m in _RE_DESCRIPTION.finditer(brut or ""):
        t = m.group(1).replace('\\"', '"').replace("\\n", " ").strip()
        if t and t not in vus:
            vus.append(t)
        if len(vus) >= 3:
            break
    return " ".join(vus)[:2500]


def _date(v):
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


EXCLUS_NATURE = ("resultat", "attribution", "rectificatif", "annul", "modification")


def normaliser(rec):
    idweb = _premier(rec, "idweb", "id")
    objet = _premier(rec, "objet", "objet_complet", "titre_marche")
    brut = _texte_donnees(rec.get("donnees"))
    description = description_depuis_donnees(brut)
    corps = " ".join([_premier(rec, "descripteur_libelle"), description])
    return {
        "source": SOURCE,
        "id_avis": idweb,
        "titre": objet[:400],
        "acheteur": _premier(rec, "nomacheteur", "acheteur", "nom_acheteur"),
        "nature": _premier(rec, "nature_libelle", "nature", "type_avis"),
        "date_publication": _premier(rec, "dateparution")[:10],
        "date_limite": _premier(rec, "datelimitereponse")[:10],
        "description": (description or objet)[:2500],
        "corps": corps,
        "lien": _premier(rec, "url_avis") or (LIEN_AVIS.format(idweb) if idweb else ""),
    }


def est_ouvert(avis, aujourd):
    nature = L.normaliser_texte(avis["nature"])
    if any(x in nature for x in EXCLUS_NATURE):
        return False
    limite = _date(avis["date_limite"])
    return limite is None or limite >= aujourd


# ===========================================================================
# COLLECTE BOAMP
# ===========================================================================
def where_titre(requete, depuis):
    return "search(objet, \"{}\") AND dateparution >= date'{}'".format(
        requete.replace('"', ""), depuis.isoformat())


def where_plein_texte(requete, depuis):
    return "\"{}\" AND dateparution >= date'{}'".format(
        requete.replace('"', ""), depuis.isoformat())


class SyntaxeRefusee(Exception):
    pass


def _fetch_http(url, params):
    import requests
    derniere = None
    for tentative in range(2):
        try:
            rep = requests.get(url, params=params, timeout=60)
        except (requests.ConnectionError, requests.Timeout) as e:
            derniere = e
            time.sleep(3)
            continue
        if rep.status_code == 400:
            raise SyntaxeRefusee(rep.text[:200])
        if rep.status_code in (429, 500, 502, 503, 504) and tentative == 0:
            time.sleep(3)
            continue
        if rep.status_code >= 400:
            raise RuntimeError("HTTP {} : {}".format(rep.status_code, rep.text[:200]))
        return rep.json()
    raise RuntimeError("BOAMP injoignable ({})".format(derniere))


def collecter(depuis, fetch=None, requetes=REQUETES):
    """Renvoie (avis_bruts_normalises, journal). Bascule une fois pour toutes
    sur la recherche plein texte si l'API refuse `search(objet, ...)`."""
    fetch = fetch or _fetch_http
    journal = {"mode": "titre", "erreurs": [], "par_requete": {}}
    avis = {}
    for requete in requetes:
        offset, lus = 0, 0
        try:
            while lus < MAX_PAR_REQUETE:
                construire = where_titre if journal["mode"] == "titre" else where_plein_texte
                params = {"where": construire(requete, depuis),
                          "order_by": "dateparution desc", "limit": PAGE,
                          "offset": offset}
                try:
                    rep = fetch(URL_BOAMP, params) or {}
                except SyntaxeRefusee as e:
                    if journal["mode"] == "titre":
                        journal["mode"] = "plein_texte"
                        journal["erreurs"].append("search(objet) refuse, repli : {}".format(e))
                        continue
                    raise
                res = rep.get("results") or []
                for r in res:
                    a = normaliser(r)
                    if a["id_avis"] and a["id_avis"] not in avis:
                        avis[a["id_avis"]] = a
                lus += len(res)
                offset += PAGE
                if len(res) < PAGE:
                    break
                time.sleep(PAUSE)
        except Exception as e:  # noqa: BLE001 - une requete en echec n'arrete rien
            journal["erreurs"].append("{} : {}".format(requete, str(e)[:160]))
        journal["par_requete"][requete] = lus
    return list(avis.values()), journal


# ===========================================================================
# MEMOIRE ET FILE D'ANALYSE
# ===========================================================================
def cle_avis(avis):
    return "{}:{}".format(avis["source"].lower(), avis["id_avis"])


def file_d_analyse(avis, deja_vus, aujourd, budget=None):
    """Applique ouverture, pre-filtre, memoire PUIS plafond (dans cet ordre,
    verrouille par les tests). Renvoie (file, entonnoir)."""
    budget = BUDGET if budget is None else budget
    ent = {"collectes": len(avis), "fermes": 0, "deja_vus": 0, "classes": {}}
    retenus = []
    for a in avis:
        if not est_ouvert(a, aujourd):
            ent["fermes"] += 1
            continue
        classe, termes = L.prefiltre(a["titre"], a["corps"])
        ent["classes"][classe] = ent["classes"].get(classe, 0) + 1
        if not L.merite_llm(classe):
            continue
        a = dict(a, classe=classe, termes=termes)
        if cle_avis(a) in deja_vus:
            ent["deja_vus"] += 1
            continue
        retenus.append(a)
    # Priorite : terme dans le titre d'abord, puis le plus recent.
    retenus = (sorted([a for a in retenus if a["classe"] == "ie_titre"],
                      key=lambda a: a["date_publication"], reverse=True)
               + sorted([a for a in retenus if a["classe"] != "ie_titre"],
                        key=lambda a: a["date_publication"], reverse=True))
    ent["en_attente"] = len(retenus)
    ent["hors_budget"] = max(0, len(retenus) - budget)
    return retenus[:budget], ent


# ===========================================================================
# LIGNES DE SORTIE (fonctions pures)
# ===========================================================================
def ligne_pg(avis, resultat, aujourd):
    ext, note = resultat["extraction"], resultat["note"]
    return {
        "cle": cle_avis(avis), "source": avis["source"], "id_avis": avis["id_avis"],
        "date_detection": aujourd.isoformat(),
        "date_publication": avis["date_publication"] or None,
        "date_limite": avis["date_limite"] or None,
        "acheteur": avis["acheteur"], "titre": avis["titre"], "lien": avis["lien"],
        "classe": avis.get("classe", ""), "termes": " | ".join(avis.get("termes", [])),
        "famille": ext["famille_offre"], "perimetre": note["perimetre"],
        "action": note["action"], "score": note["score"],
        "adequation": note["adequation"], "commercial": note["commercial"],
        "escalade": bool(resultat.get("escalade")),
        "motifs": " ; ".join(note["motifs"]), "resume": ext.get("resume", ""),
        "extraction": ext,
    }


def visible_arise(note):
    return note["perimetre"] == "affiche" and note["action"] in ("contacter", "surveiller")


def ligne_sheet(lp):
    v = {
        "date_detection": lp["date_detection"], "action": lp["action"],
        "score": lp["score"], "famille": lp["famille"], "acheteur": lp["acheteur"],
        "titre": lp["titre"], "date_limite": lp["date_limite"] or "",
        "lien": lp["lien"], "resume": lp["resume"], "adequation": lp["adequation"],
        "commercial": lp["commercial"], "motifs": lp["motifs"],
        "source": lp["source"], "id_avis": lp["id_avis"],
        "statut_arise": "", "commentaire_arise": "",
    }
    return [v[c] for c in COLONNES_SHEET]


# ===========================================================================
# STOCKAGE (Postgres puis Sheet ; chaque fonction avale ses erreurs)
# ===========================================================================
DDL = """CREATE TABLE IF NOT EXISTS {t} (
    cle TEXT PRIMARY KEY, source TEXT, id_avis TEXT, date_detection DATE,
    date_publication DATE, date_limite DATE, acheteur TEXT, titre TEXT, lien TEXT,
    classe TEXT, termes TEXT, famille TEXT, perimetre TEXT, action TEXT,
    score REAL, adequation REAL, commercial REAL, escalade BOOLEAN,
    motifs TEXT, resume TEXT, extraction JSONB,
    cree_le TIMESTAMPTZ DEFAULT now())""".format(t=TABLE)
COLONNES_PG = ("cle", "source", "id_avis", "date_detection", "date_publication",
               "date_limite", "acheteur", "titre", "lien", "classe", "termes",
               "famille", "perimetre", "action", "score", "adequation",
               "commercial", "escalade", "motifs", "resume", "extraction")


def memoire_pg(url):
    """Cles deja analysees. Connexion ouverte le temps de la lecture seulement
    (lecon Render : ne jamais garder une connexion pendant un long calcul)."""
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
        print("  (info) memoire Postgres indisponible ({}).".format(str(e)[:120]))
        return None


def ecrire_pg(url, lignes):
    if not url or not lignes:
        return "Postgres : rien a ecrire." if url else "Postgres : DATABASE_URL absent."
    try:
        import psycopg
        from psycopg.types.json import Jsonb
        sql = "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (cle) DO NOTHING".format(
            TABLE, ", ".join(COLONNES_PG), ", ".join(["%s"] * len(COLONNES_PG)))
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                for lp in lignes:
                    cur.execute(sql, [Jsonb(lp[c]) if c == "extraction" else lp[c]
                                      for c in COLONNES_PG])
        return "Postgres : {} ligne(s) ecrite(s) dans {}.".format(len(lignes), TABLE)
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


def ids_sheet(feuille):
    """Repli memoire si Postgres est absent : colonne id_avis, lue par
    POSITION (regle 4), en-tete exclu."""
    try:
        col = feuille.col_values(COL_ID_AVIS + 1)[1:]
        return {"{}:{}".format(SOURCE.lower(), v) for v in col if v}
    except Exception:  # noqa: BLE001
        return set()


def ecrire_sheet(feuille, lignes):
    if feuille is None:
        return "Sheet ARISE : non configure (IE_SHEET_ID absent)."
    if not lignes:
        return "Sheet ARISE : aucun nouveau lead visible."
    try:
        feuille.append_rows(lignes, value_input_option="USER_ENTERED")
        return "Sheet ARISE : {} lead(s) ajoute(s).".format(len(lignes))
    except Exception as e:  # noqa: BLE001
        return "Sheet ARISE : ECHEC d'ecriture ({}).".format(str(e)[:160])


# ===========================================================================
# ORCHESTRATION
# ===========================================================================
def executer(aujourd=None, fetch=None, appel=None, deja_vus=None, debug=None,
             budget=None):
    """Coeur testable : collecte -> file -> analyse. Ne fait AUCUNE ecriture ;
    renvoie ce qu'il faudrait ecrire et un rapport."""
    aujourd = aujourd or datetime.date.today()
    debug = DEBUG if debug is None else debug
    depuis = aujourd - datetime.timedelta(days=JOURS)
    avis, journal = collecter(depuis, fetch=fetch)
    file_, ent = file_d_analyse(avis, deja_vus or set(), aujourd, budget=budget)
    lignes_pg, lignes_sheet, actions = [], [], {}
    if not debug:
        for a in file_:
            r = L.analyser(a, appel=appel, pause=0.3 if appel is None else 0.0)
            if r["extraction"] is None:
                actions["echec_extraction"] = actions.get("echec_extraction", 0) + 1
                continue
            lp = ligne_pg(a, r, aujourd)
            lignes_pg.append(lp)
            actions[lp["action"]] = actions.get(lp["action"], 0) + 1
            if visible_arise(r["note"]):
                lignes_sheet.append(ligne_sheet(lp))
    rapport = {"depuis": depuis, "journal": journal, "entonnoir": ent,
               "actions": actions, "file": file_, "debug": debug}
    return lignes_pg, lignes_sheet, rapport


def rapport_markdown(rapport, ecritures):
    ent, j = rapport["entonnoir"], rapport["journal"]
    L_ = ["# Collecteur IE (BOAMP)", "",
          "Mode : {}. Fenetre depuis {}. Recherche : {}.".format(
              "DEBUG (sans LLM ni ecriture)" if rapport["debug"] else "production",
              rapport["depuis"].isoformat(), j["mode"]), "",
          "## Entonnoir", "",
          "- Avis collectes : {}".format(ent["collectes"]),
          "- Fermes (resultat, rectificatif, date limite passee) : {}".format(ent["fermes"]),
          "- Pre-filtre : {}".format(", ".join("{} {}".format(k, v)
                                               for k, v in sorted(ent["classes"].items())) or "-"),
          "- Deja analyses (memoire) : {}".format(ent["deja_vus"]),
          "- En attente d'analyse : {} (hors budget ce run : {})".format(
              ent["en_attente"], ent["hors_budget"]), ""]
    if rapport["actions"]:
        L_ += ["## Analyse", ""] + ["- {} : {}".format(k, v)
                                    for k, v in sorted(rapport["actions"].items())] + [""]
    if rapport["debug"] and rapport["file"]:
        L_ += ["## File qui partirait au LLM", ""]
        L_ += ["- [{}] {} | {} | {} | {}".format(a["classe"], a["date_publication"],
                                                 a["acheteur"][:40], a["titre"][:90], a["lien"])
               for a in rapport["file"]] + [""]
    L_ += ["## Ecritures", ""] + ["- " + e for e in ecritures]
    if j["erreurs"]:
        L_ += ["", "## Erreurs", ""] + ["- " + e for e in j["erreurs"][:20]]
    return "\n".join(L_) + "\n"


def main():
    if not L.ACTIF:
        print("Collecteur IE desactive (RADAR_IE=0).")
        return
    if not DEBUG and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return
    url = os.environ.get("DATABASE_URL")
    sheet_id = os.environ.get("IE_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    feuille = None
    if sheet_id and fichier and not DEBUG:
        try:
            feuille = ouvrir_onglet(sheet_id, fichier)
        except Exception as e:  # noqa: BLE001
            print("  (info) classeur ARISE inaccessible ({}). Partage-t-il "
                  "bien le classeur avec le compte de service ?".format(str(e)[:120]))
    deja_vus = memoire_pg(url)
    if deja_vus is None:
        deja_vus = ids_sheet(feuille) if feuille is not None else set()

    lignes_pg, lignes_sheet, rapport = executer(deja_vus=deja_vus)
    if rapport["debug"]:
        ecritures = ["aucune (mode debug)"]
    else:
        ecritures = [ecrire_pg(url, lignes_pg), ecrire_sheet(feuille, lignes_sheet)]
    md = rapport_markdown(rapport, ecritures)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
