# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- ATTRIBUTIONS IsDB (Banque islamique de developpement).
=========================================================================

CE QU'IL FAIT
-------------
Recupere les avis d'attribution ("Contract Award") du portail IsDB, en extrait
le TITULAIRE, SON PAYS D'ORIGINE et LE PAYS D'EXECUTION, et ecrit dans
l'onglet `attributions_radar` -- celui des attributions TED, BM et UNGM.

Consequence voulue : AUCUN cablage dashboard. Les lignes remontent seules dans
la lentille "Titulaires - attributions" et dans la fiche entreprise 360.

LECON DU RUN DU 21/07/2026 : LE FILTRE PAYS EST UN LEURRE
----------------------------------------------------------
Le formulaire expose un <select> dont le parametre reel est `locality`. Ce
parametre est SILENCIEUSEMENT IGNORE par le portail : chaque requete renvoie
la meme liste globale non filtree. Preuves relevees sur donnees reelles :
  - les 40 pays interroges ont tous renvoye le meme jeu de 62 liens ;
  - une fiche indonesienne (project code IDN1031) a ete servie sous la
    requete Afghanistan, alors que l'Indonesie n'etait meme pas interrogee ;
  - un projet kirghize (Issyk-Kul Ring Road) etait etiquete AFG.
Resultat : 142 requetes, un seul jeu de resultats, et un pays d'execution
FAUX sur 100% des lignes.

CORRECTION : on n'interroge plus par pays. Un seul balayage global, et le
pays d'execution est derive du PROJECT CODE de la fiche (IDN1031 -> IDN),
qui est une donnee de la fiche elle-meme, pas une supposition de la requete.

GARDE-FOU : un prefixe n'est accepte que s'il correspond a un ISO3 connu du
radar. Les prefixes non resolus (codes internes IsDB, projets regionaux) sont
comptes et affiches en mode verification, jamais devines.

STRUCTURE
---------
  - Liste  : chaque avis est un lien contenant /contract-award/.
  - Fiche  : "Contract Award Company Name", "Contract Award Company Country",
             "Project title", "Issue Date", "Project code".

MODE VERIFICATION
-----------------
    RADAR_ISDB_DEBUG=1  -> n'ecrit rien. Imprime, pour CHAQUE fiche lue, le
    project code, l'ISO3 derive, le motif de retenue ou de rejet, puis un
    releve de tous les prefixes rencontres. C'est ce qui valide ou invalide
    l'hypothese du prefixe sur donnees reelles.

Interrupteur : RADAR_ISDB=0 desactive la collecte.

LANCEMENT :  python isdb_radar.py
"""

import os
import re
from datetime import date, datetime, timedelta

import bm_attributions as bma      # resolveur de pays bilingue + conversion USD
import ted_complet_v14 as ted


ACTIVER = os.environ.get("RADAR_ISDB", "1") != "0"
DEBUG = os.environ.get("RADAR_ISDB_DEBUG", "0") == "1"

BASE = "https://www.isdb.org"
PAGE_TENDERS = BASE + "/project-procurement/tenders"

JOURS_FENETRE = int(os.environ.get("RADAR_ISDB_JOURS", "365"))
# Balayage GLOBAL : le filtre pays du portail ne filtre rien (voir en-tete).
PAGES_MAX = int(os.environ.get("RADAR_ISDB_PAGES", "12"))
# Une fiche = une requete. On borne, et on ne lit QUE les fiches inconnues.
FICHES_MAX = int(os.environ.get("RADAR_ISDB_FICHES_MAX", "60"))
MINUTES_MAX = float(os.environ.get("RADAR_ISDB_MINUTES", "12"))

# Onglet PARTAGE avec les attributions TED, BM et UNGM.
NOM_ONGLET = "attributions_radar"
COLONNES = [
    "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
    "acheteur", "titre", "cpv", "sous_traitance",
    "date_publication", "publication_number", "lien", "a_demarcher",
]
COL_STATUT = "statut_prospection"
COL_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COL_STATUT, COL_DETECTION]

ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

RE_LIEN_DETAIL = re.compile(
    r'href="(/project-procurement/tenders/[^"#?]{6,180})"', re.I)
# "IDN1031", "MLI-0123", "SDN 0456" : trois lettres puis un chiffre.
RE_CODE_PROJET = re.compile(r"^\s*([A-Za-z]{3})\s*[-/]?\s*\d")

# Prefixes internes IsDB qui NE SONT PAS des ISO3. A remplir uniquement sur
# preuve issue du mode verification, jamais par supposition.
ALIAS_CODE_ISDB = {}

# Etiquettes de la fiche, relevees sur donnees reelles le 21/07/2026.
LABEL_SOCIETE = "contract award company name"
LABEL_PAYS_SOCIETE = "contract award company country"
LABEL_ADRESSE = "contract award company address"
LABEL_PROJET = "project title"
LABEL_DATE = "issue date"
LABEL_CODE = "project code"


# ===========================================================================
# OUTILS (fonctions PURES : testables sans reseau)
# ===========================================================================

def _plat(t):
    return re.sub(r"\s+", " ", str(t or "")).strip()


def _texte(html):
    """HTML -> lignes de texte, navigation et scripts retires."""
    t = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", str(html or ""))
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?i)</\s*(div|p|tr|td|th|li|h\d|span|dt|dd)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    import html as _h
    return [_plat(l) for l in _h.unescape(t).split("\n") if _plat(l)]


_CACHE_ISO3 = {}


def iso3_connus():
    """Ensemble des ISO3 que le radar sait situer. Sert de garde-fou : un
    prefixe de code projet n'est accepte que s'il est la-dedans."""
    if _CACHE_ISO3.get("set") is None:
        codes = set(ted.MULTIPLICATEUR_ZONE or {})
        try:
            import radar_dashboard as dash
            codes |= set(dash.ZONE_PAR_ISO3 or {})
        except Exception:
            pass
        _CACHE_ISO3["set"] = codes
    return _CACHE_ISO3["set"]


def prefixe_code_projet(code):
    """'IDN1031' -> 'IDN'. '' si le code n'a pas la forme attendue.
    Renvoie le prefixe BRUT, resolu ou non : le mode verification en a besoin
    pour montrer ce qui n'est pas reconnu."""
    m = RE_CODE_PROJET.match(str(code or ""))
    return m.group(1).upper() if m else ""


def iso3_depuis_code_projet(code):
    """'IDN1031' -> 'IDN' si l'ISO3 est connu du radar, sinon ''.

    C'est la SEULE source fiable du pays d'execution : le filtre pays du
    portail ne filtre rien (voir en-tete du module)."""
    prefixe = prefixe_code_projet(code)
    if not prefixe:
        return ""
    prefixe = ALIAS_CODE_ISDB.get(prefixe, prefixe)
    return prefixe if prefixe in iso3_connus() else ""


def liens_attributions(html):
    """URL de detail des avis d'ATTRIBUTION presents dans une page de liste.
    Le segment /contract-award/ de l'URL est le marqueur le plus sur."""
    vus, sorties = set(), []
    for chemin in RE_LIEN_DETAIL.findall(str(html or "")):
        if "/contract-award/" not in chemin.lower():
            continue
        if chemin in vus:
            continue
        vus.add(chemin)
        sorties.append(chemin)
    return sorties


def identifiant_depuis_lien(chemin):
    """Cle de deduplication stable, derivee du dernier segment d'URL."""
    slug = [p for p in str(chemin or "").split("/") if p]
    return "ISDB-{}".format(slug[-1][:80]) if slug else ""


def paires_fiche(html):
    """{etiquette minuscule: valeur} d'une fiche d'attribution.

    La fiche presente ses champs en paires ETIQUETTE puis VALEUR sur deux
    lignes successives (structure relevee le 21/07/2026) :
        Contract Award Company Name
        PT. Lista Fariska Putra JO PT. Shima Bahtera Nusantara
    On accepte aussi la forme "Etiquette : valeur" par prudence."""
    lignes = _texte(html)
    etiquettes = {LABEL_SOCIETE, LABEL_PAYS_SOCIETE, LABEL_ADRESSE, LABEL_PROJET,
                  LABEL_DATE, LABEL_CODE, "notice type", "tender type",
                  "last date of submission", "email", "status"}
    paires = {}
    for i, ligne in enumerate(lignes):
        cle = ligne.rstrip(":").strip().lower()
        if cle in etiquettes and i + 1 < len(lignes):
            valeur = lignes[i + 1].strip()
            if valeur.rstrip(":").lower() not in etiquettes and cle not in paires:
                paires[cle] = valeur[:200]
            continue
        if ":" in ligne:
            g, _, d = ligne.partition(":")
            g = g.strip().lower()
            if g in etiquettes and d.strip() and g not in paires:
                paires[g] = d.strip()[:200]
    return paires


def lire_date_isdb(txt):
    """'1 October 2024' ou '01/10/2024' -> ISO. '' si illisible."""
    s = _plat(txt)
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,12})\s+(\d{4})", s)
    if m:
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(
                    "{} {} {}".format(*m.groups()), fmt).date().isoformat()
            except ValueError:
                continue
    return ""


def dans_la_fenetre(iso_date, aujourdhui=None, jours=None):
    """Attribution assez recente pour que la mobilisation soit en cours."""
    if not iso_date:
        return True                       # sans date, on ne jette pas
    jours = JOURS_FENETRE if jours is None else jours
    aujourdhui = aujourdhui or date.today()
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return True
    return timedelta(0) <= (aujourdhui - d) <= timedelta(days=jours)


def _nom_pays(iso3):
    """Nom du pays depuis son ISO3, pour comparer avec l'origine du titulaire."""
    try:
        import radar_dashboard as dash
        entree = dash.ZONE_PAR_ISO3.get(iso3)
        if entree:
            return entree[0]
    except Exception:
        pass
    return iso3


# ===========================================================================
# INTERPRETATION D'UNE FICHE
# ===========================================================================

def examiner(chemin, html, iso3=None):
    """(ligne ou None, diagnostic).

    Le diagnostic porte le motif de rejet : c'est lui qui alimente le mode
    verification, sans jamais assouplir le filtre de production."""
    paires = paires_fiche(html)
    code = _plat(paires.get(LABEL_CODE))
    societe = _plat(paires.get(LABEL_SOCIETE))
    pays_societe = _plat(paires.get(LABEL_PAYS_SOCIETE))
    titre = _plat(paires.get(LABEL_PROJET)) or "Marche IsDB"
    d_attrib = lire_date_isdb(paires.get(LABEL_DATE, ""))
    derive = iso3 or iso3_depuis_code_projet(code)

    diag = {
        "code": code,
        "prefixe": prefixe_code_projet(code),
        "iso3": derive,
        "societe": societe,
        "pays_societe": pays_societe,
        "titre": titre,
        "date": d_attrib,
        "motif": "",
    }

    if not societe or len(societe) < 3:
        diag["motif"] = "sans titulaire"
        return None, diag
    if not dans_la_fenetre(d_attrib):
        diag["motif"] = "hors fenetre"
        return None, diag
    if not derive:
        diag["motif"] = "prefixe non resolu"
        return None, diag
    if derive not in ted.MULTIPLICATEUR_ZONE:
        diag["motif"] = "hors univers de risque"
        return None, diag

    # Comme pour la Banque Mondiale : une entreprise etrangere expatrie du
    # personnel, un entrepreneur local non.
    etranger = bma.titulaire_etranger(pays_societe, _nom_pays(derive))
    diag["motif"] = "retenu"
    ligne = {
        "date_maj": date.today().isoformat(),
        "gagnant": societe[:160],
        "secteur": "Marche IsDB",
        "pays_execution": derive,         # ISO3 : le dashboard resout en mode ISO
        "valeur_attribuee": "",
        "acheteur": "Banque islamique de developpement",
        "titre": titre[:300],
        "cpv": code[:40],
        "sous_traitance": "",
        "date_publication": d_attrib,
        "publication_number": identifiant_depuis_lien(chemin),
        "lien": BASE + chemin,
        "a_demarcher": "oui",
        "_pays_titulaire": pays_societe,
        "_etranger": etranger,
        "_code_projet": code,
    }
    return ligne, diag


def normaliser(chemin, html, iso3=None):
    """Fiche d'attribution -> ligne de l'onglet `attributions_radar`.
    None si le titulaire n'est pas nommable, l'avis hors fenetre, ou le pays
    d'execution non derivable. `iso3` peut etre force (tests)."""
    ligne, _diag = examiner(chemin, html, iso3=iso3)
    return ligne


# ===========================================================================
# COLLECTE
# ===========================================================================

def collecte(session=None, fetch_liste=None, fetch_fiche=None, deja_vus=None):
    """Balayage GLOBAL des attributions, sans filtre pays.

    Le filtre pays du portail est un leurre (voir en-tete). Le pays
    d'execution est derive de la fiche. Deduplique AVANT de lire les fiches :
    une fiche coute une requete (lecon du collecteur UNGM)."""
    import time as _time
    session = session or ted.session_robuste()
    deja_vus = deja_vus or set()
    stats = {"pages": 0, "liens": 0, "fiches": 0, "requetes": 0,
             "deja_connus": 0, "arret": "termine", "exemple": "",
             "journal": [], "prefixes": {}}

    attributions, vus_liens = [], set()
    debut = _time.time()
    plafond = False

    # Une attribution est un marche conclu : les deux statuts sont utiles.
    for statut in ("active", "closed"):
        if plafond:
            break
        for page in range(PAGES_MAX):
            if (_time.time() - debut) / 60.0 >= MINUTES_MAX:
                stats["arret"] = "garde-temps"
                plafond = True
                break
            params = {"tender_type": "contract-award", "status": statut}
            if page:
                params["page"] = page
            try:
                if fetch_liste:
                    page_html = fetch_liste(statut, page)
                else:
                    r = session.get(PAGE_TENDERS, params=params,
                                    headers=ENTETES, timeout=45)
                    stats["requetes"] += 1
                    if r.status_code >= 400:
                        break
                    page_html = r.text
            except Exception:
                break
            stats["pages"] += 1

            liens = [l for l in liens_attributions(page_html) if l not in vus_liens]
            if not liens:
                break                     # plus rien de neuf : page suivante inutile

            for chemin in liens:
                vus_liens.add(chemin)
                stats["liens"] += 1
                ident = identifiant_depuis_lien(chemin)
                if ident in deja_vus:
                    stats["deja_connus"] += 1
                    continue               # deja dans le Sheet : pas de requete
                if stats["fiches"] >= FICHES_MAX:
                    stats["arret"] = "plafond de fiches"
                    plafond = True
                    break
                try:
                    if fetch_fiche:
                        fiche = fetch_fiche(chemin)
                    else:
                        rf = session.get(BASE + chemin, headers=ENTETES, timeout=45)
                        stats["requetes"] += 1
                        if rf.status_code >= 400:
                            continue
                        fiche = rf.text
                except Exception:
                    continue
                stats["fiches"] += 1
                if not stats["exemple"]:
                    stats["exemple"] = fiche

                a, diag = examiner(chemin, fiche)
                prefixe = diag["prefixe"] or "(aucun)"
                casier = stats["prefixes"].setdefault(
                    prefixe, {"n": 0, "resolu": bool(diag["iso3"])})
                casier["n"] += 1
                stats["journal"].append(diag)
                if a:
                    attributions.append(a)
            if plafond:
                break
    return attributions, stats


def dedupliquer(attributions):
    """Une meme attribution peut apparaitre sous deux statuts."""
    sorties, vus = [], set()
    for a in attributions:
        cle = (a["gagnant"].lower(), a["pays_execution"], a["date_publication"])
        if cle in vus or a["publication_number"] in vus:
            continue
        vus.add(cle)
        vus.add(a["publication_number"])
        sorties.append(a)
    return sorties


# ===========================================================================
# ECRITURE ET MAIN
# ===========================================================================

def ouvrir_feuille(sheet_id, fichier):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        return classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000,
                                   cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f


def ecrire(feuille, attributions):
    index = ted.charger_index_publication(feuille)
    nouvelles, deja = [], 0
    for a in attributions:
        pub = a.get("publication_number", "")
        if pub and pub in index:
            deja += 1
            continue
        nouvelles.append([str(a.get(c, "")) for c in COLONNES] +
                         ["", date.today().isoformat()])
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    return len(nouvelles), deja


def _imprimer_verification(attributions, stats):
    """Ce que le run de verification doit prouver : le prefixe du project code
    donne-t-il un pays d'execution fiable ?"""
    print("\n--- MODE VERIFICATION (RADAR_ISDB_DEBUG=1) : AUCUNE ECRITURE ---")

    print("\n[A] Fiches lues (code | ISO3 | motif | titulaire <- pays | titre) :")
    for d in stats["journal"][:80]:
        print("  {:12} | {:5} | {:22} | {:28} <- {:14} | {}".format(
            (d["code"] or "-")[:12], d["iso3"] or "-", d["motif"],
            (d["societe"] or "-")[:28], (d["pays_societe"] or "-")[:14],
            (d["titre"] or "-")[:40]))

    print("\n[B] Prefixes rencontres (c'est ici que se joue la validation) :")
    ordonnes = sorted(stats["prefixes"].items(),
                      key=lambda kv: (-kv[1]["n"], kv[0]))
    for prefixe, info in ordonnes:
        print("  {:8} x{:<3} {}".format(
            prefixe, info["n"],
            "resolu en ISO3" if info["resolu"] else "NON RESOLU (a arbitrer)"))
    non_resolus = sum(i["n"] for _p, i in ordonnes if not i["resolu"])
    print("  -> {} fiche(s) sur {} avec un prefixe non resolu.".format(
        non_resolus, len(stats["journal"])))

    motifs = {}
    for d in stats["journal"]:
        motifs[d["motif"]] = motifs.get(d["motif"], 0) + 1
    print("\n[C] Motifs :")
    for motif, n in sorted(motifs.items(), key=lambda kv: -kv[1]):
        print("  {:24} {}".format(motif, n))

    if stats.get("exemple"):
        print("\n[D] Etiquettes trouvees dans une fiche reelle :")
        for cle, val in paires_fiche(stats["exemple"]).items():
            print("      {:34} = {}".format(cle[:34], str(val)[:70]))


def main():
    if not ACTIVER:
        print("(info) Collecteur IsDB desactive (RADAR_ISDB=0).")
        return

    print("Collecte des attributions IsDB (balayage global, fenetre {} jours)...".format(
        JOURS_FENETRE))
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    # Memoire AVANT collecte : evite de lire des fiches deja connues.
    deja_vus = set()
    if sheet_id and fichier and not DEBUG:
        try:
            deja_vus = ted.numeros_publication_existants(
                sheet_id, fichier, NOM_ONGLET, COLONNES)
        except Exception as e:
            print("(isdb) memoire illisible ({}), on lira toutes les fiches.".format(e))

    attributions, stats = collecte(deja_vus=deja_vus)
    print("  {} page(s) | {} lien(s) d'attribution | {} fiche(s) lue(s) | "
          "{} deja connu(s) | {} requetes (arret : {}).".format(
              stats["pages"], stats["liens"], stats["fiches"],
              stats["deja_connus"], stats["requetes"], stats["arret"]))

    attributions = dedupliquer(attributions)
    etrangers = [a for a in attributions if a.get("_etranger")]
    print("  {} attribution(s) exploitable(s), dont {} titulaire(s) etranger(s).".format(
        len(attributions), len(etrangers)))

    if DEBUG:
        _imprimer_verification(attributions, stats)
        return

    if not (sheet_id and fichier):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    try:
        feuille = ouvrir_feuille(sheet_id, fichier)
        ajoutees, deja = ecrire(feuille, attributions)
        print("  {} nouvelle(s) ligne(s) dans '{}' ({} deja connue(s)).".format(
            ajoutees, NOM_ONGLET, deja))
    except Exception as e:
        print("(isdb) ecriture impossible ({}). Le run continue.".format(e))


if __name__ == "__main__":
    main()
