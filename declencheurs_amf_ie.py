# -*- coding: utf-8 -*-
"""
Radar Amarante - DECLENCHEURS IE : sanctions AMF (chantier IE-8, 31/10/2026).

Meme logique que les CJIP (declencheurs_ie.py), etendue au secteur financier.
Une decision de la commission des sanctions de l'AMF qui vise le DISPOSITIF de
conformite / LCB-FT d'une societe NOMMEE cree un besoin de remediation
(evaluation des tiers, KYC, cartographie des risques) : acheteur prive
identifie et date, pour ARISE. Les pures affaires d'ABUS DE MARCHE (information
privilegiee, manipulation de cours) sont stockees mais NON montrees : amende
sans besoin de remediation de dispositif.

Sonde IE-8 (PR #9, 31/10/2026) : AMF EXPLOITABLE, ~6 decisions pertinentes/an
nommant une societe. ACPR ecartee (banque-france.fr bloque meme les IP Actions).

ACCES (verifie le 31/10/2026)
  sitemap.xml public (454 decisions) -> page HTML de chaque decision (titre =
  societe nommee + date, en balise og) -> PDF STATIQUE (texte integral pour le
  motif). Rien de cache, sans compte ni JS. Deterministe : AUCUN appel LLM.

ECRITURES (table et onglet PARTAGES avec les CJIP)
  - Postgres `radar_ie_declencheurs`, type = "sanction AMF". Ajout seul, SAUF
    statut provisoire (illisible/incomplet), relu au run suivant.
  - Sheet ARISE, onglet "Declencheurs" : seules les lignes montrees (societe
    nommee + conformite, ou PDF illisible a verifier). Colonnes ARISE preservees.

Schema partage importe depuis declencheurs_ie : une seule source de verite pour
la table, l'onglet et les colonnes.
"""

import datetime
import html
import io
import os
import re
import time
import unicodedata

import config_env
import declencheurs_ie as D
import lentille_ie as L

SITEMAP = "https://www.amf-france.org/sitemap.xml"
MARQUE_DECISION = "/decisions-de-la-commission-des-sanctions/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; RadarAmarante/1.0)"}
FENETRE_MOIS = config_env.entier("RADAR_IE_AMF_MOIS", 24)
MIN_CARACTERES = 400
PAUSE = 0.3

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
        "decembre": 12}

# Lexique du motif (accent-insensible).
_CONF = ("lcb-ft", "lcb ft", "blanchiment", "financement du terrorisme",
         "dispositif de conformite", "controle interne", "obligations professionnelles",
         "evaluation des tiers", "due diligence", "diligences", "kyc",
         "connaissance client", "gel des avoirs", "cartographie des risques",
         "programme de conformite", "deontolog")
_ABUS = ("information privilegiee", "manipulation de cours", "manquement declaratif",
         "abus de marche", "fausse information", "franchissement de seuil",
         "operation d'initie", "diffusion de fausse")
_REMEDIATION = ("injonction", "mise en conformite", "se mettre en conformite",
                "remediation", "plan d'action", "renforcer son dispositif",
                "sous astreinte")


# ===========================================================================
# FONCTIONS PURES (testables sans reseau)
# ===========================================================================
def _norm(texte):
    decompose = unicodedata.normalize("NFKD", texte or "")
    sans = "".join(c for c in decompose if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sans.replace("’", "'").lower()).strip()


def decisions_du_sitemap(xml):
    return [u for u in re.findall(r"<loc>([^<]+)</loc>", xml or "")
            if MARQUE_DECISION in u]


def titre_og(page):
    m = re.search(r'<meta property="og:title" content="([^"]+)"', page or "")
    if not m:
        m = re.search(r"<title>([^<]+)</title>", page or "")
    brut = html.unescape(m.group(1)) if m else ""
    return brut.split(" | ")[0].strip()


def lien_pdf(page):
    m = re.search(r'href="(https://www\.amf-france\.org/sites/[^"]+\.pdf[^"]*)"', page or "")
    return html.unescape(m.group(1)) if m else ""


def date_decision(titre):
    t = _norm(titre)
    m = re.search(r"du\s+(\d{1,2})(?:er)?\s+([a-z]+)\s+(\d{4})", t)
    if not m:
        return None
    mois = MOIS.get(m.group(2))
    try:
        return datetime.date(int(m.group(3)), mois, int(m.group(1))) if mois else None
    except ValueError:
        return None


def societe_nommee(titre):
    t = _norm(titre)
    if "societe" in t or "banque" in t or "assurance" in t or "etablissement" in t:
        return True
    return bool(re.search(r"\b(sa|sas|sasu|sgp|sarl|snc)\b", t))


def nom_societe(titre):
    """Extrait le nom de la societe apres 'a l'egard de(s) (la/les) societe(s)'.

    Motif tolerant aux accents (titre brut : "a l'egard" ou "à l'égard"),
    mais on renvoie le nom d'origine (casse et accents conserves)."""
    t = re.split(r"[àa] l['’][ée]gard d[eu]s?\s+", titre or "", maxsplit=1, flags=re.I)
    if len(t) < 2:
        return ""
    reste = re.sub(r"^(?:la |le |les |l['’]|des |du )?(?:soci[ée]t[ée]s? )?", "",
                   t[-1], flags=re.I)
    # Couper avant la premiere personne physique citee (" et de M.", ", de M.").
    reste = re.split(r"(?:\s+et|,)\s+(?:de\s+)?(?:MM?\.|Mme|Mlle|M\s)", reste)[0]
    return reste.strip(" .,-")[:120]


def _abus_domine(texte_norm):
    na = sum(texte_norm.count(k) for k in _ABUS)
    nc = sum(texte_norm.count(k) for k in _CONF)
    return na > nc


def classer_motif(texte_pdf):
    t = _norm(texte_pdf)
    conf = any(k in t for k in _CONF)
    abus = any(k in t for k in _ABUS)
    remed = any(k in t for k in _REMEDIATION)
    pertinent = conf and not (abus and not remed and _abus_domine(t))
    return {"conformite": conf, "abus": abus, "remediation": remed,
            "pertinent": bool(pertinent)}


def statut(entree):
    """sanction_conformite (montre) | abus_marche | hors_cible | illisible | incomplet."""
    if not entree.get("societe"):
        return "hors_cible"
    a = entree.get("analyse")
    if not a:
        return "incomplet" if entree.get("incomplet") else "illisible"
    if a["pertinent"]:
        return "sanction_conformite"
    return "abus_marche"


def visible_arise(entree):
    """Montre a ARISE : sanction de dispositif (prospect) ou PDF illisible a lire."""
    return entree["statut"] in ("sanction_conformite", "illisible")


# ===========================================================================
# LECTURE (reseau injectable ; PDF via declencheurs_ie.texte_pdf)
# ===========================================================================
def _get(url, binaire=False):
    import requests
    rep = requests.get(url, headers=UA, timeout=60)
    rep.raise_for_status()
    return rep.content if binaire else rep.text


def lire_decision(entree, get=None, lire_pdf=None, pause=PAUSE):
    """Ajoute `analyse` et `incomplet`. Ne leve jamais."""
    get, lire_pdf = get or _get, lire_pdf or D.texte_pdf
    url = entree["lien_convention"]
    try:
        texte = lire_pdf(get(url, binaire=True))
    except Exception as exc:  # noqa: BLE001
        entree["analyse"] = None
        entree["incomplet"] = True
        entree["lecture"] = "illisible"
        if pause:
            time.sleep(pause)
        return ["{} : {}".format(entree["societe"][:40] or url[-40:], str(exc)[:100])]
    if pause:
        time.sleep(pause)
    if len((texte or "").strip()) < MIN_CARACTERES:
        entree["analyse"] = None
        entree["incomplet"] = False
        entree["lecture"] = "illisible"
        return []
    entree["analyse"] = classer_motif(texte)
    entree["incomplet"] = False
    entree["lecture"] = "pdf"
    return []


# ===========================================================================
# EXECUTION (coeur testable, sans ecriture)
# ===========================================================================
def executer(aujourd=None, deja_vus=None, get=None, lire_pdf=None, pause=PAUSE,
             fenetre_mois=None):
    aujourd = aujourd or datetime.date.today()
    get = get or _get
    deja_vus = deja_vus or set()
    fenetre_mois = fenetre_mois or FENETRE_MOIS
    try:
        xml = get(SITEMAP)
    except Exception as exc:  # noqa: BLE001
        return [], {"erreurs": ["sitemap AMF : {}".format(exc)], "lues": 0,
                    "deja_vues": 0, "dans_fenetre": 0, "hors_cible": 0}
    limite = D.ajouter_mois(aujourd, -fenetre_mois)
    urls = decisions_du_sitemap(xml)
    erreurs, nouvelles = [], []
    dans_fenetre = 0
    hors_fenetre_suite = 0
    for url in reversed(urls):                 # du plus recent au plus ancien
        if hors_fenetre_suite > 8:
            break
        try:
            page = get(url)
        except Exception as exc:  # noqa: BLE001
            erreurs.append("page : {}".format(str(exc)[:100]))
            continue
        titre = titre_og(page)
        date = date_decision(titre)
        if not date or date < limite:
            hors_fenetre_suite += 1
            continue
        hors_fenetre_suite = 0
        dans_fenetre += 1
        if url in deja_vus:
            continue
        pdf = lien_pdf(page)
        soc = nom_societe(titre) if societe_nommee(titre) else ""
        entree = {"cle": url, "societe": soc, "validation": date,
                  "titre": titre, "lien_communique": url, "lien_convention": pdf,
                  "analyse": None, "incomplet": False, "lecture": "illisible"}
        if pdf:
            erreurs += lire_decision(entree, get=get, lire_pdf=lire_pdf, pause=pause)
        else:
            entree["incomplet"] = True
        entree["statut"] = statut(entree)
        nouvelles.append(entree)
    info = {"erreurs": erreurs, "lues": len(nouvelles), "dans_fenetre": dans_fenetre,
            "deja_vues": dans_fenetre - len(nouvelles),
            "hors_cible": sum(1 for e in nouvelles if e["statut"] == "hors_cible")}
    return nouvelles, info


# ===========================================================================
# LIGNES DE SORTIE (type = "sanction AMF", schema partage)
# ===========================================================================
_LABEL = {"sanction_conformite": "sanction AMF conformite/LCB-FT",
          "illisible": "PDF a verifier", "incomplet": "lecture incomplete",
          "abus_marche": "abus de marche", "hors_cible": "hors cible"}


def ligne_sheet(entree, aujourd):
    v = {"detecte_le": aujourd.isoformat(), "type": "sanction AMF",
         "statut": _LABEL.get(entree["statut"], entree["statut"]),
         "societe": entree["societe"], "validation": entree["validation"].isoformat(),
         "duree_mois": "", "fin_suivi_afa": "", "juridiction": "AMF",
         "lien_communique": entree["lien_communique"],
         "lien_convention": entree["lien_convention"], "cle": entree["cle"],
         "statut_arise": "", "commentaire_arise": ""}
    return [v[c] for c in D.COLONNES_SHEET]


def valeurs_pg(entree):
    a = entree.get("analyse") or {}
    return [entree["cle"], "sanction AMF", entree["societe"], entree["validation"],
            "AMF", entree["statut"], a.get("conformite"), a.get("abus"), None, None,
            entree.get("lecture"), entree["lien_communique"], entree["lien_convention"]]


# ===========================================================================
# STOCKAGE (best-effort, ne leve jamais ; schema et table partages)
# ===========================================================================
def memoire_pg(url):
    return D.memoire_pg(url)


def ecrire_pg(url, entrees):
    """Ajout seul, SAUF statut provisoire (illisible/incomplet) mis a jour quand
    la lecture aboutit. Table partagee `radar_ie_declencheurs`."""
    if not url:
        return "Postgres : DATABASE_URL absent."
    if not entrees:
        return "Postgres : rien a ecrire."
    try:
        import psycopg
        maj = ", ".join("{0} = EXCLUDED.{0}".format(c) for c in D.COLONNES_PG[1:])
        sql = ("INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (cle) DO UPDATE SET {m} "
               "WHERE {t}.statut IN {r}").format(
                   t=D.TABLE, c=", ".join(D.COLONNES_PG),
                   v=", ".join(["%s"] * len(D.COLONNES_PG)), m=maj,
                   r=D.STATUTS_A_RELIRE_SQL)
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(D.DDL)
                for e in entrees:
                    cur.execute(sql, valeurs_pg(e))
        return "Postgres : {} sanction(s) AMF ecrite(s) dans {}.".format(
            len(entrees), D.TABLE)
    except Exception as exc:  # noqa: BLE001
        return "Postgres : ECHEC d'ecriture ({}).".format(str(exc)[:160])


def rapport_markdown(nouvelles, info, ecritures, debug):
    montrees = [e for e in nouvelles if visible_arise(e)]
    lignes = ["# Declencheurs IE - sanctions AMF", "",
              "Mode : {}. Decisions dans la fenetre {} mois : {} "
              "(deja connues : {}, lues ce run : {}, montrees : {}).".format(
                  "DEBUG (aucune ecriture)" if debug else "production", FENETRE_MOIS,
                  info["dans_fenetre"], info["deja_vues"], info["lues"], len(montrees)),
              "",
              "| Validee le | Societe | Statut | Documents |",
              "|---|---|---|---|"]
    for e in sorted(nouvelles, key=lambda x: x["validation"], reverse=True):
        lignes.append("| {} | {} | {} | {} |".format(
            e["validation"], e["societe"][:50] or "(personnes)",
            e["statut"], e["lien_convention"] or e["lien_communique"] or "-"))
    if not nouvelles:
        lignes.append("| (aucune nouvelle decision) | | | |")
    lignes += ["", "## Ecritures", ""] + ["- " + x for x in ecritures]
    if info["erreurs"]:
        lignes += ["", "## Erreurs", ""] + ["- " + x for x in info["erreurs"][:20]]
    return "\n".join(lignes) + "\n"


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
            feuille = D.ouvrir_onglet(sheet_id, fichier)
        except Exception as exc:  # noqa: BLE001
            print("  (info) classeur ARISE inaccessible ({}).".format(str(exc)[:120]))
    deja = memoire_pg(url)
    if deja is None:
        deja = D.cles_onglet(feuille) if feuille is not None else set()
    nouvelles, info = executer(aujourd, deja_vus=deja)
    if debug:
        ecritures = ["aucune (mode debug)"]
    else:
        presentes = D.cles_onglet(feuille) if feuille is not None else set()
        lignes = [ligne_sheet(e, aujourd) for e in nouvelles
                  if visible_arise(e) and e["cle"] not in presentes]
        ecritures = [ecrire_pg(url, nouvelles), D.ecrire_sheet(feuille, lignes)]
    md = rapport_markdown(nouvelles, info, ecritures, debug)
    print(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
