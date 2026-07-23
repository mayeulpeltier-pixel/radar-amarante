# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur ATTRIBUTIONS TED (Levier 1 : "qui a gagne").
========================================================================

Objectif business : combler l'angle mort de la prospection privee. Au lieu
d'attendre un appel d'offres, on recolte les AVIS D'ATTRIBUTION (contract
award notices) publies sur TED pour les pays a risque suivis, et on en
extrait le TITULAIRE (le gagnant). Chaque gagnant est une entreprise qui
va deployer du personnel dans une zone a risque : c'est une cible de
prospection directe, et la liste se met a jour toute seule a chaque run.

Ce module NE fait AUCUN appel LLM : c'est de la recolte + du parsing
deterministe. Cout d'exploitation = 0 (juste des requetes HTTP publiques).

SOURCES ET FORMATS (verifies sur donnees reelles, juillet 2026) :
- API v3 de recherche TED (meme endpoint que le collecteur d'appels
  d'offres), acces anonyme. Filtre : notice-type IN (can-standard ...),
  scope ALL (les attributions ne sont pas des marches "actifs").
  can-standard = "contract award" (confirme : atelier OP-TED + spec TED).
- Le NOM DU GAGNANT n'est pas un champ plat de l'API : on lit la notice
  publiee (format HTML) et on parse la section normalisee
  "Information about winners" -> "Official name". Valide sur la notice
  reelle 302871-2026 (gagnant : Badenelektra GmbH).

REUTILISATION : session resiliente, nettoyage HTML, ecriture Sheet groupee
et memoire inter-runs proviennent du coeur ted_complet_v14 (aucune
modification de ce fichier). Ecrit dans l'onglet SEPARE "attributions_radar".

Interrupteur : RADAR_ATTRIBUTIONS=0 desactive le collecteur.
Mode reglage : ATTRIB_DRY_RUN=1 montre l'entonnoir SANS lire les notices.
"""

import os
import re
import time
from datetime import date, datetime, timedelta

import requests

try:
    import ted_complet_v14 as ted
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce "
        "collecteur (il en reutilise la session, le nettoyage HTML, le Sheet)."
    )


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_ATTRIBUTIONS", "1") != "0"
NOM_ONGLET = "attributions_radar"

# Types de notice = avis d'attribution (resultats). can-standard couvre
# l'immense majorite ; can-social (regime allege) et can-tport (transport
# public de voyageurs) sont ajoutes par securite.
NOTICE_TYPES_ATTRIB = ["can-standard", "can-social", "can-tport"]

# On reutilise EXACTEMENT l'univers CPV et pays du radar (memes cibles).
CODES_CPV = list(ted.CODES_CPV)
CODES_PAYS = list(ted.CODES_PAYS_SUIVIS)

LIEN_NOTICE_HTML = "https://ted.europa.eu/en/notice/{}/html"
# La page /html est rendue par JavaScript (coquille vide en telechargement
# brut). On LIT donc la notice au format PDF, rendu cote serveur (texte
# propre et complet). Le lien /html reste affiche a l'humain (lisible dans
# le navigateur).
LIEN_NOTICE_PDF = "https://ted.europa.eu/en/notice/{}/pdf"
LIMITE = getattr(ted, "LIMITE_RESULTATS", 100)
MAX_PAGES = 20                # plafond de pages (attributions zones a risque = volume modere)
MAX_NOTICES_LUES = int(os.environ.get("ATTRIB_BUDGET", "120"))  # lectures de notices max / run
# On ignore les attributions trop anciennes (titulaire peu pertinent
# commercialement aujourd'hui). Reglable ; 0 = pas de limite d'age.
ANNEES_MAX = int(os.environ.get("ATTRIB_ANNEES", "3"))

# Divisions CPV = secteur lisible (code, pas de LLM). Reutilise la logique
# infrastructure critique du coeur pour marquer les gagnats "deployeurs".
SECTEUR_PAR_DIVISION = {
    "45": "BTP / construction", "71": "Ingenierie / etudes",
    "09": "Energie / petrole-gaz", "65": "Energie / eau / utilities",
    "76": "Petrole & gaz (services)", "14": "Mines / materiaux",
    "32": "Telecom / equipements", "90": "Environnement / traitement",
    "72": "IT / numerique", "79": "Conseil / services aux entreprises",
    "75": "Administration / defense", "80": "Formation / education",
    "85": "Sante", "34": "Transport / vehicules", "50": "Maintenance",
}


# ===========================================================================
# PARTIE 2 -- EXTRACTION DEFENSIVE DES CHAMPS DE RECHERCHE
# ===========================================================================
# Les champs de l'API v3 arrivent sous des formes variables (str, liste,
# dict multilingue). On lit de facon tolerante, comme le reste du radar.

def _val(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        # champ multilingue : privilegier l'anglais, sinon 1re valeur
        for cle in ("eng", "en", "ENG", "EN"):
            if v.get(cle):
                return _val(v[cle])
        for val in v.values():
            t = _val(val)
            if t:
                return t
        return ""
    if isinstance(v, list):
        for x in v:
            t = _val(x)
            if t:
                return t
        return ""
    return str(v)


def _codes_iso3(v):
    """Codes pays a risque presents dans un champ place-of-performance."""
    txt = " ".join(_all_str(v)).upper()
    return [c for c in CODES_PAYS if re.search(r"\b" + c + r"\b", txt)]


def _codes_cpv(v):
    txt = " ".join(_all_str(v))
    return re.findall(r"\b(\d{8})\b", txt)


def _all_str(v):
    """Aplati recursivement toute valeur en liste de chaines."""
    out = []
    if v is None:
        return out
    if isinstance(v, str):
        return [v]
    if isinstance(v, (int, float)):
        return [str(v)]
    if isinstance(v, dict):
        for x in v.values():
            out.extend(_all_str(x))
    elif isinstance(v, list):
        for x in v:
            out.extend(_all_str(x))
    else:
        out.append(str(v))
    return out


# ===========================================================================
# PARTIE 3 -- COLLECTE DES AVIS D'ATTRIBUTION (API v3)
# ===========================================================================

def _query(include_type=True):
    q = "classification-cpv IN ({}) AND place-of-performance IN ({})".format(
        " ".join(CODES_CPV), " ".join(CODES_PAYS))
    if include_type:
        q += " AND notice-type IN ({})".format(" ".join(NOTICE_TYPES_ATTRIB))
    return q


def _corps(page, include_type):
    return {
        "query": _query(include_type),
        "fields": [
            "publication-number", "notice-title", "buyer-name",
            "buyer-country", "place-of-performance", "classification-cpv",
            "publication-date", "notice-type",
        ],
        "page": page,
        "limit": LIMITE,
        "scope": "ALL",           # les attributions ne sont pas "ACTIVE"
        "checkQuerySyntax": False,
        "paginationMode": "PAGE_NUMBER",
    }


def _est_attribution(notice):
    code = _val(notice.get("notice-type")).lower()
    return code.startswith("can")


def collecte_attributions(fetch=None, session=None):
    """Pagine l'API v3 et renvoie la liste des avis d'attribution bruts.
    Degradation : si le filtre notice-type est rejete (400), on relance sans
    lui et on garde les attributions cote client (notice-type commence par
    'can'). `fetch` injectable pour tests : callable(payload)->dict JSON."""
    session = session or ted.session_robuste()
    url = ted.TED_ENDPOINT

    def appeler(payload):
        if fetch is not None:
            return fetch(payload)
        rep = session.post(url, json=payload, timeout=45)
        rep.raise_for_status()
        return rep.json()

    include_type = True
    bruts = []
    page = 1
    while page <= MAX_PAGES:
        try:
            data = appeler(_corps(page, include_type))
        except requests.HTTPError as e:
            if include_type and getattr(e.response, "status_code", None) == 400:
                # Le filtre notice-type est peut-etre refuse : on degrade.
                print("  (info) filtre notice-type refuse, repli sur tri cote client.")
                include_type = False
                bruts = []
                page = 1
                continue
            print("  (info) API TED indisponible (page {}) : {}.".format(page, e))
            break
        except Exception as e:
            print("  (info) API TED indisponible (page {}) : {}.".format(page, e))
            break
        lot = data.get("notices") or data.get("results") or data.get("items") or []
        if not lot:
            break
        for n in lot:
            if include_type or _est_attribution(n):
                bruts.append(n)
        if len(lot) < LIMITE:
            break
        page += 1
        time.sleep(0.3)
    return bruts


# ===========================================================================
# PARTIE 4 -- LECTURE DE LA NOTICE + PARSING DU GAGNANT (deterministe)
# ===========================================================================
# Valide sur la notice reelle 302871-2026. Les LIBELLES de section sont en
# anglais sur la page /en/ (structure normalisee eForms), meme quand le
# texte libre est dans la langue du pays.

_RE_TOTAL_EFORMS = re.compile(
    r"Value of all contracts awarded in this notice\s*:\s*"
    r"([0-9][0-9\s.,\u00a0\u202f]*)\s*([A-Z]{3})", re.I)
_RE_TOTAL_ANCIEN = re.compile(
    r"Total value of the (?:contract/lot|procurement)[^0-9]{0,40}?"
    r"([0-9][0-9\s.,\u00a0\u202f]*)\s*([A-Z]{3})", re.I)
_RE_TENDER_VAL = re.compile(
    r"Value of the tender\s*:\s*([0-9][0-9\s.,\u00a0\u202f]*)\s*([A-Z]{3})", re.I)

# Libelles marquant la FIN d'un nom d'organisation (coupe propre).
# Libelles de champ (eForms/F03) qui suivent un nom d'organisation. On coupe
# le nom au PROCHAIN "Libelle :". Exiger les deux-points distingue un vrai
# libelle d'un mot present dans un nom (ex: "GROUP", "Value" sans deux-points
# ne coupent pas "NIRAS GROUP (UK) LTD").
_LABELS = (
    r"Subcontractor|Official name|Registration number|Registration|"
    r"Postal address|Post ?code|Town|City|Country subdivision|Country|"
    r"NUTS(?: code)?|Telephone|Phone|Fax|E-?mail|Internet address|Internet|"
    r"Website|Tender identifier|Tender|Value of the tender|Value|Contract|"
    r"Roles of|Winner of|Winner selection|The tenderer|The contractor|"
    r"This organisation|Size of|Contact|Identifier of|Group Lead|Section|VAT|"
    r"Type of|Lot"
)
_RE_STOP = re.compile(r"\s+(?:" + _LABELS + r")\s*:", re.I)
_RE_STREET = re.compile(
    r"\s+(?:Avenue|Rue|Boulevard|Bd|Street|Stra(?:ss|\u00df)e|Via|Calle|"
    r"Piazza|Platz|Route|Road|Rond-point|Am)\b", re.I)
_MOTS_VIDES = {"the", "a", "an", "le", "la", "les", "el", "der", "die", "das", "l"}


def _nettoyer_montant(num, devise):
    return "{} {}".format(re.sub(r"[\u00a0\u202f]", " ", num).strip(), devise)


def _nom_apres_official(bloc):
    """Nom qui suit le 1er 'Official name:' d'un bloc, nettoye :
    coupe au prochain libelle, coupe une adresse, ecarte les debris."""
    m = re.search(r"Official name\s*:\s*(.+)", bloc)
    if not m:
        return ""
    seg = m.group(1)
    seg = _RE_STOP.split(seg)[0]              # coupe au prochain "Libelle :"
    seg = _RE_STREET.split(seg)[0]            # coupe une adresse sans libelle
    seg = re.split(r",\s*\d", seg)[0]         # coupe "..., 4, 1040 ..."
    seg = re.sub(r"\s*\([A-Z]{1,3}$", "", seg)  # fragment pays pendouillant "(B"
    nom = seg.strip(" .,;:-\u2013")
    if len(nom) < 3 or nom.lower() in _MOTS_VIDES or nom.lower() in ("not applicable", "n/a"):
        return ""
    return nom


def parser_gagnants(texte):
    """Extrait gagnants [{nom, valeur}], montant total, flag sous-traitance.
    Gere les DEUX formats TED, valides sur notices reelles :
      - eForms (fin 2022+) : 'Information about winners' -> 'Official name'.
        (ex: 302871-2026 -> Badenelektra GmbH)
      - ancien schema F03  : 'Name and address of the contractor'
        -> 'Official name'. (ex: 704485-2022 -> PROATEC SRL)
    Tout l'espace (y compris retours a la ligne) est normalise en espaces
    simples pour etre robuste aux differences PDF/HTML."""
    t = re.sub(r"\s+", " ", texte)

    gagnants = []
    # --- Format eForms : 1 bloc par lot gagne ---
    for bloc in re.split(r"Information about winners", t)[1:]:
        bloc = re.split(r"\b8\.\s|Notice information|Organisations\b", bloc)[0]
        nom = _nom_apres_official(bloc)
        if not nom:
            continue
        mv = _RE_TENDER_VAL.search(bloc)
        gagnants.append({"nom": nom,
                         "valeur": _nettoyer_montant(mv.group(1), mv.group(2)) if mv else ""})

    # --- Format ancien (F03) : 1 ou plusieurs contractants ---
    if not gagnants:
        for m in re.finditer(r"[Nn]ame and address of the contractor(.{0,300})", t):
            nom = _nom_apres_official(m.group(1))
            if nom:
                gagnants.append({"nom": nom, "valeur": ""})

    # Dedup en preservant l'ordre.
    vus, uniques = set(), []
    for g in gagnants:
        cle = g["nom"].lower()
        if cle not in vus:
            vus.add(cle)
            uniques.append(g)

    total = ""
    m = _RE_TOTAL_EFORMS.search(t) or _RE_TOTAL_ANCIEN.search(t)
    if m:
        total = _nettoyer_montant(m.group(1), m.group(2))

    sous_traitance = bool(re.search(r"Subcontracting\s*:\s*yes", t, re.I))
    return {"gagnants": uniques, "total": total, "sous_traitance": sous_traitance}


def _pdf_en_texte(octets):
    """Extrait le texte d'un PDF (octets) via pypdf. Renvoie '' si pypdf est
    absent ou si le PDF est illisible (jamais bloquant)."""
    try:
        import io
        from pypdf import PdfReader
    except Exception:
        print("  (info) pypdf absent : ajoute 'pypdf' aux dependances pour "
              "extraire les gagnants. Etape gagnant ignoree ce run.")
        return ""
    try:
        lecteur = PdfReader(io.BytesIO(octets))
        return "\n".join((p.extract_text() or "") for p in lecteur.pages)
    except Exception:
        return ""


def lire_notice(pub, fetch=None, session=None):
    """Renvoie le texte lisible d'une notice. On telecharge le PDF (rendu
    cote serveur, contrairement a /html qui est du JavaScript) et on en
    extrait le texte. `fetch` injectable pour tests : callable(pub)->texte."""
    if fetch is not None:
        return fetch(pub)          # les tests injectent directement du texte
    session = session or ted.session_robuste()
    rep = session.get(LIEN_NOTICE_PDF.format(pub), timeout=60)
    rep.raise_for_status()
    return _pdf_en_texte(rep.content)


# ===========================================================================
# PARTIE 5 -- NORMALISATION
# ===========================================================================

def secteur_lisible(codes_cpv):
    for c in codes_cpv:
        lib = SECTEUR_PAR_DIVISION.get(c[:2])
        if lib:
            return lib
    return "Autre"


def pays_lisible(codes_iso3):
    noms = []
    for code in codes_iso3:
        for nom, c in ted.PAYS_ROUGE.items():
            if c == code:
                noms.append(nom)
                break
        else:
            noms.append(code)
    return ", ".join(dict.fromkeys(noms))


def normaliser(notice, parse):
    codes_iso = _codes_iso3(notice.get("place-of-performance"))
    codes_cpv = _codes_cpv(notice.get("classification-cpv"))
    pub = _val(notice.get("publication-number"))
    noms_gagnants = "; ".join(g["nom"] for g in parse["gagnants"]) or "(gagnant non publie)"
    valeurs = "; ".join(g["valeur"] for g in parse["gagnants"] if g["valeur"])
    tier = max([ted.MULTIPLICATEUR_ZONE.get(c, 0.2) for c in codes_iso] or [0.2])
    return {
        "publication_number": pub,
        "gagnant": noms_gagnants,
        "valeur_attribuee": parse["total"] or valeurs,
        "acheteur": _val(notice.get("buyer-name")),
        "pays_execution": pays_lisible(codes_iso) or _val(notice.get("place-of-performance")),
        "secteur": secteur_lisible(codes_cpv),
        "cpv": ", ".join(dict.fromkeys(codes_cpv)),
        "sous_traitance": "oui" if parse["sous_traitance"] else "non",
        "titre": _val(notice.get("notice-title"))[:300],
        "date_publication": _val(notice.get("publication-date"))[:10],
        "lien": LIEN_NOTICE_HTML.format(pub),
        "_tier": tier,
        "_nb_gagnants": len(parse["gagnants"]),
    }


# ===========================================================================
# PARTIE 6 -- SORTIE GOOGLE SHEET
# ===========================================================================
COLONNES = [
    "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
    "acheteur", "titre", "cpv", "sous_traitance",
    "date_publication", "publication_number", "lien", "a_demarcher",
]
COL_STATUT = "statut_prospection"     # zone preservee (saisie humaine)
COL_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COL_STATUT, COL_DETECTION]


def ouvrir_feuille(sheet_id, fichier):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        f = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000, cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f
    if COL_DETECTION not in f.row_values(1):
        f.update(values=[TOUTES_COLONNES], range_name="A1")
    return f


def ligne(a):
    valeurs = {
        "date_maj": date.today().isoformat(),
        "gagnant": a["gagnant"],
        "secteur": a["secteur"],
        "pays_execution": a["pays_execution"],
        "valeur_attribuee": a["valeur_attribuee"],
        "acheteur": a["acheteur"],
        "titre": a["titre"],
        "cpv": a["cpv"],
        "sous_traitance": a["sous_traitance"],
        "date_publication": a["date_publication"],
        "publication_number": a["publication_number"],
        "lien": a["lien"],
        "a_demarcher": "oui" if a["_nb_gagnants"] else "verifier",
    }
    return [str(valeurs.get(c, "")) for c in COLONNES]


def ecrire(feuille, attributions):
    # Index construit en LECTURE POSITIONNELLE depuis le SCHEMA (regle 4) :
    # la position de `publication_number` vient de COLONNES, jamais de
    # l'en-tete de la feuille. Immunise contre un en-tete desaligne, un en-tete
    # duplique et la numerisation des identifiants. Voir ted.index_publications.
    index = ted.charger_index_publication(feuille, COLONNES)
    derniere = ted.lettre_colonne(len(COLONNES))
    maj, nouvelles, nb_n, nb_m = [], [], 0, 0
    for a in attributions:
        pub = a.get("publication_number", "")
        vals = ligne(a)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere), "values": [vals]})
            nb_m += 1
        else:
            nouvelles.append(vals + ["nouveau", date.today().isoformat()])
            nb_n += 1
    if maj:
        feuille.batch_update(maj)
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    # Double ecriture (etape 2 du cap produit, 21/07/2026) : miroir Postgres
    # best-effort. On passe TOUTES les attributions, pas seulement les
    # nouvelles : le miroir a sa propre memoire (ON CONFLICT DO NOTHING) et se
    # remplit ainsi retroactivement. Ne peut JAMAIS faire echouer le run.
    try:
        import radar_stockage
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, attributions))
    except Exception as e:                     # module absent : run intact
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_n, nb_m


# ===========================================================================
# PARTIE 7 -- POINT D'ENTREE
# ===========================================================================

def main():
    if not ACTIVER:
        print("(info) Collecteur attributions desactive (RADAR_ATTRIBUTIONS=0).")
        return

    print("Etape 1/3 -- Collecte des avis d'attribution TED (zones a risque)...")
    bruts = collecte_attributions()
    if not bruts:
        print("\n/!\\ 0 avis d'attribution renvoye. Causes possibles : rien de "
              "nouveau, ou changement d'API TED. Verifier avant de conclure.")
        return

    # Dedup par numero de publication + tri par risque decroissant.
    vus, uniques = set(), []
    for n in bruts:
        pub = _val(n.get("publication-number"))
        if pub and pub not in vus:
            vus.add(pub)
            uniques.append(n)
    uniques.sort(key=lambda n: max(
        [ted.MULTIPLICATEUR_ZONE.get(c, 0.2) for c in _codes_iso3(n.get("place-of-performance"))] or [0.2]),
        reverse=True)
    print("Attributions brutes : {} | uniques : {}".format(len(bruts), len(uniques)))

    # Garde-fou d'anciennete : on ecarte les attributions trop vieilles.
    if ANNEES_MAX > 0:
        annee_min = date.today().year - ANNEES_MAX
        def _assez_recent(n):
            a = _val(n.get("publication-date"))[:4]
            return (not a.isdigit()) or int(a) >= annee_min
        avant = len(uniques)
        uniques = [n for n in uniques if _assez_recent(n)]
        if avant != len(uniques):
            print("Anciennete : {} attribution(s) anterieure(s) a {} ecartee(s).".format(
                avant - len(uniques), annee_min))

    # Memoire inter-runs : ne pas relire une notice deja traitee.
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    deja = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES)
    if deja:
        avant = len(uniques)
        uniques = [n for n in uniques if _val(n.get("publication-number")).strip() not in deja]
        print("Memoire : {} deja traitee(s) ignoree(s), {} nouvelle(s).".format(
            avant - len(uniques), len(uniques)))
    if not uniques:
        print("Aucune NOUVELLE attribution a traiter.")
        return

    plafond = len(uniques) > MAX_NOTICES_LUES
    if plafond:
        uniques = uniques[:MAX_NOTICES_LUES]
        print("(plafond de {} lectures de notices ce run ; zones les plus a "
              "risque d'abord.)".format(MAX_NOTICES_LUES))

    if os.environ.get("ATTRIB_DRY_RUN"):
        print("\n=== DRY-RUN : pas de lecture de notice ===")
        for i, n in enumerate(uniques, 1):
            print("  {:3}. {} | {}".format(
                i, _val(n.get("publication-number")),
                _val(n.get("notice-title"))[:70]))
        return

    print("\nEtape 2/3 -- Lecture des notices et extraction des gagnants...")
    attributions = []
    for i, n in enumerate(uniques, 1):
        pub = _val(n.get("publication-number"))
        try:
            texte = lire_notice(pub)
            parse = parser_gagnants(texte)
        except Exception as e:
            print("  [{}/{}] {} : lecture impossible ({}).".format(i, len(uniques), pub, e))
            parse = {"gagnants": [], "total": "", "sous_traitance": False}
        a = normaliser(n, parse)
        attributions.append(a)
        noms = a["gagnant"][:60]
        print("  [{}/{}] {} -> {}".format(i, len(uniques), pub, noms))
        time.sleep(0.2)

    print("\nEtape 3/3 -- Ecriture Sheet...")
    if sheet_id and fichier:
        try:
            feuille = ouvrir_feuille(sheet_id, fichier)
            nb_n, nb_m = ecrire(feuille, attributions)
            print("-> {} nouveau(x) titulaire(s), {} mise(s) a jour "
                  "(statut_prospection preserve).".format(nb_n, nb_m))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("(Pas de Sheet configure : TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE.)")

    # Bilan console
    avec = sum(1 for a in attributions if a["_nb_gagnants"])
    print("\n" + "=" * 68)
    print("REGISTRE DES ATTRIBUTIONS (qui a gagne quoi en zone a risque)")
    print("{} avis traites | {} avec titulaire identifie".format(len(attributions), avec))
    print("=" * 68)
    for a in sorted(attributions, key=lambda x: x["_tier"], reverse=True)[:25]:
        print("\n[{}] {}".format(a["secteur"], a["gagnant"]))
        print("  Pays : {} | Valeur : {} | Acheteur : {}".format(
            a["pays_execution"], a["valeur_attribuee"] or "n.c.", a["acheteur"][:50]))
        print("  {}".format(a["lien"]))


if __name__ == "__main__":
    main()
