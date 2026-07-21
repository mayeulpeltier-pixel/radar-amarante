# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- ATTRIBUTIONS IsDB (Banque islamique de developpement).
=========================================================================

CE QU'IL FAIT
-------------
Recupere les avis d'attribution ("Contract Award") du portail IsDB, en extrait
le TITULAIRE et SON PAYS D'ORIGINE, et ecrit dans l'onglet
`attributions_radar` -- celui des attributions TED, BM et UNGM.

Consequence voulue : AUCUN cablage dashboard. Les lignes remontent seules dans
la lentille "Titulaires - attributions" et dans la fiche entreprise 360.

POURQUOI CETTE SOURCE
---------------------
IsDB finance exactement dans la zone d'Amarante : Mali, Mauritanie, Niger,
Somalie, Soudan, Libye, Yemen, Togo, Sierra Leone, Mozambique, Tadjikistan,
Palestine, Ouganda, Senegal. Et surtout, sa fiche d'attribution donne le
"Contract Award Company Country", ce que UNGM ne fournit pas : le filtre
local/etranger redevient donc possible, comme pour la Banque Mondiale.

ACCESSIBILITE (verifiee par sonde depuis GitHub Actions, 21/07/2026)
---------------------------------------------------------------------
Contenu RENDU COTE SERVEUR : pas de piege JavaScript (contrairement a ADB et
UNGM). Le formulaire de filtrage accepte trois parametres, releves dans le
HTML de la page :
    country=<ISO2>   tender_type=contract-award   status=active|closed
Une attribution est un marche conclu : on interroge donc les deux statuts.

STRUCTURE
---------
  - Liste  : chaque avis est un bloc `views-row` contenant un lien de detail.
  - Fiche  : "Contract Award Company Name", "Contract Award Company Country",
             "Project title", "Issue Date", "Project code".

MODE VERIFICATION (a utiliser au premier run)
---------------------------------------------
    RADAR_ISDB_DEBUG=1  -> n'ecrit rien, imprime les titulaires interpretes et
    la structure brute d'une fiche.

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
PAYS_MAX = int(os.environ.get("RADAR_ISDB_PAYS_MAX", "40"))
PAGES_PAR_PAYS = int(os.environ.get("RADAR_ISDB_PAGES", "2"))
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

RE_SELECT = re.compile(r'<select([^>]*)>(.*?)</select>', re.I | re.S)
RE_NOM_SELECT = re.compile(r'name\s*=\s*["\']([^"\']+)["\']', re.I)
RE_OPTION = re.compile(
    r'<option[^>]*\bvalue\s*=\s*["\']([A-Za-z]{2})["\'][^>]*>\s*([^<]{2,60}?)\s*</option>',
    re.I)
RE_LIEN_DETAIL = re.compile(
    r'href="(/project-procurement/tenders/[^"#?]{6,180})"', re.I)

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


def charger_pays_isdb(html):
    """({ISO3: ISO2}, nom_du_parametre) depuis le formulaire de filtrage.

    DETECTION PAR LE CONTENU, pas par le nom de l'attribut : on retient le
    <select> dont les options sont des codes a deux lettres correspondant a de
    vrais pays. Supposer `name="country"` avait produit zero resultat au
    premier run (21/07/2026) : le tag d'ouverture n'etait pas visible dans le
    dump de la sonde, je l'avais devine.

    Le nom reel du parametre est renvoye avec la table, pour construire les
    requetes sans nouvelle supposition. Les ISO2 sont convertis en ISO3 via le
    resolveur bilingue deja teste (Niger/Nigeria distingues)."""
    meilleur, meilleur_nom = {}, ""
    for attributs, contenu in RE_SELECT.findall(str(html or "")):
        table = {}
        for code, libelle in RE_OPTION.findall(contenu):
            iso3 = bma.iso3_pays_libre(libelle)
            if iso3 and iso3 not in table:
                table[iso3] = code.upper()
        # Un select de pays en compte des dizaines ; les autres, aucun ou un.
        if len(table) > len(meilleur):
            meilleur = table
            m = RE_NOM_SELECT.search(attributs or "")
            meilleur_nom = m.group(1) if m else ""
    return meilleur, meilleur_nom


def pays_a_interroger(table):
    """Pays du formulaire qui sont dans l'univers de risque, les plus exposes
    d'abord. Interroger la Turquie ou le Royaume-Uni serait du gaspillage."""
    candidats = [(iso3, iso2) for iso3, iso2 in (table or {}).items()
                 if iso3 in ted.MULTIPLICATEUR_ZONE]
    candidats.sort(key=lambda c: (-ted.MULTIPLICATEUR_ZONE.get(c[0], 0), c[0]))
    return candidats[:PAYS_MAX]


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


def normaliser(chemin, html, iso3):
    """Fiche d'attribution -> ligne de l'onglet `attributions_radar`.
    None si le titulaire n'est pas nommable ou l'avis hors fenetre.
    `iso3` vient de la requete : le pays d'execution est certain."""
    paires = paires_fiche(html)
    societe = _plat(paires.get(LABEL_SOCIETE))
    if not societe or len(societe) < 3:
        return None
    d_attrib = lire_date_isdb(paires.get(LABEL_DATE, ""))
    if not dans_la_fenetre(d_attrib):
        return None
    if not iso3 or iso3 not in ted.MULTIPLICATEUR_ZONE:
        return None

    pays_societe = _plat(paires.get(LABEL_PAYS_SOCIETE))
    titre = _plat(paires.get(LABEL_PROJET)) or "Marche IsDB"
    # Comme pour la Banque Mondiale : une entreprise etrangere expatrie du
    # personnel, un entrepreneur local non.
    etranger = bma.titulaire_etranger(pays_societe, _nom_pays(iso3))
    return {
        "date_maj": date.today().isoformat(),
        "gagnant": societe[:160],
        "secteur": "Marche IsDB",
        "pays_execution": iso3,           # ISO3 : le dashboard resout en mode ISO
        "valeur_attribuee": "",
        "acheteur": "Banque islamique de developpement",
        "titre": titre[:300],
        "cpv": _plat(paires.get(LABEL_CODE))[:40],
        "sous_traitance": "",
        "date_publication": d_attrib,
        "publication_number": identifiant_depuis_lien(chemin),
        "lien": BASE + chemin,
        "a_demarcher": "oui",
        "_pays_titulaire": pays_societe,
        "_etranger": etranger,
    }


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
# COLLECTE
# ===========================================================================

def collecte(session=None, fetch_liste=None, fetch_fiche=None, deja_vus=None):
    """Parcourt les attributions pays par pays.

    Deduplique AVANT de lire les fiches : une fiche coute une requete, et
    relire ce qui est deja dans le Sheet serait du gaspillage (lecon du
    collecteur UNGM)."""
    import time as _time
    session = session or ted.session_robuste()
    deja_vus = deja_vus or set()
    stats = {"pays": 0, "liens": 0, "fiches": 0, "requetes": 0,
             "deja_connus": 0, "arret": "termine", "exemple": "",
             "param_pays": "", "html_base": ""}

    # Table des pays, depuis le formulaire de la page.
    try:
        html_base = fetch_liste("", "", 0) if fetch_liste else session.get(
            PAGE_TENDERS, headers=ENTETES, timeout=45).text
    except Exception as e:
        stats["arret"] = "page de base illisible ({})".format(e)
        return [], stats
    if DEBUG:
        stats["html_base"] = html_base
    table, param_pays = charger_pays_isdb(html_base)
    param_pays = param_pays or "country"
    stats["param_pays"] = param_pays
    cibles = pays_a_interroger(table)
    if DEBUG:
        print("  formulaire : parametre pays = {!r}, {} pays reconnus, "
              "{} dans l'univers de risque.".format(
                  param_pays, len(table), len(cibles)))
    if not cibles:
        stats["arret"] = "aucun pays exploitable"
        return [], stats

    attributions, vus_liens = [], set()
    debut = _time.time()
    for iso3, iso2 in cibles:
        if (_time.time() - debut) / 60.0 >= MINUTES_MAX:
            stats["arret"] = "garde-temps"
            break
        stats["pays"] += 1
        # Une attribution est un marche conclu : les deux statuts sont utiles.
        for statut in ("active", "closed"):
            for page in range(PAGES_PAR_PAYS):
                params = {param_pays: iso2, "tender_type": "contract-award",
                          "status": statut}
                if page:
                    params["page"] = page
                try:
                    if fetch_liste:
                        page_html = fetch_liste(iso2, statut, page)
                    else:
                        r = session.get(PAGE_TENDERS, params=params,
                                        headers=ENTETES, timeout=45)
                        stats["requetes"] += 1
                        if r.status_code >= 400:
                            break
                        page_html = r.text
                except Exception:
                    break
                liens = [l for l in liens_attributions(page_html) if l not in vus_liens]
                if not liens:
                    break
                for chemin in liens:
                    vus_liens.add(chemin)
                    stats["liens"] += 1
                    ident = identifiant_depuis_lien(chemin)
                    if ident in deja_vus:
                        stats["deja_connus"] += 1
                        continue           # deja dans le Sheet : pas de requete
                    if stats["fiches"] >= FICHES_MAX:
                        stats["arret"] = "plafond de fiches"
                        continue
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
                    if DEBUG and not stats["exemple"]:
                        stats["exemple"] = fiche
                    a = normaliser(chemin, fiche, iso3)
                    if a:
                        attributions.append(a)
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


def main():
    if not ACTIVER:
        print("(info) Collecteur IsDB desactive (RADAR_ISDB=0).")
        return

    print("Collecte des attributions IsDB (fenetre {} jours)...".format(JOURS_FENETRE))
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
    print("  {} pays | {} lien(s) d'attribution | {} fiche(s) lue(s) | "
          "{} deja connu(s) | {} requetes (arret : {}).".format(
              stats["pays"], stats["liens"], stats["fiches"],
              stats["deja_connus"], stats["requetes"], stats["arret"]))

    attributions = dedupliquer(attributions)
    etrangers = [a for a in attributions if a.get("_etranger")]
    print("  {} attribution(s) exploitable(s), dont {} titulaire(s) etranger(s).".format(
        len(attributions), len(etrangers)))

    if DEBUG:
        print("\n--- MODE VERIFICATION (RADAR_ISDB_DEBUG=1) : AUCUNE ECRITURE ---")
        if stats.get("arret") == "aucun pays exploitable" and stats.get("html_base"):
            print("\n[!] Aucun pays reconnu. Selects reellement presents "
                  "dans la page (pour corriger la detection) :")
            for attributs, contenu in RE_SELECT.findall(stats["html_base"]):
                m = RE_NOM_SELECT.search(attributs or "")
                options = RE_OPTION.findall(contenu)
                echantillon = ", ".join(
                    "{}={}".format(c, _plat(l)[:22]) for c, l in options[:6])
                print("      name={!r} | {} option(s) a 2 lettres | {}".format(
                    m.group(1) if m else "?", len(options), echantillon or "-"))
            return
        print("\n[A] Attributions interpretees :")
        for a in attributions[:25]:
            print("  [{}] {} | {:34} <- {:16}{} | {}".format(
                a["date_publication"] or "n.c.", a["pays_execution"],
                a["gagnant"][:34], (a.get("_pays_titulaire") or "n.c.")[:16],
                "" if a.get("_etranger") else " (LOCAL)", a["titre"][:44]))
        if stats.get("exemple"):
            print("\n[B] Etiquettes trouvees dans une fiche reelle :")
            for cle, val in paires_fiche(stats["exemple"]).items():
                print("      {:34} = {}".format(cle[:34], str(val)[:70]))
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
