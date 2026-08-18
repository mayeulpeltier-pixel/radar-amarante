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
    import radar_resilience
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce "
        "collecteur (il en reutilise la session, le nettoyage HTML, le Sheet)."
    )
try:
    import cpv_reference          # divisions CPV officielles (eForms-SDK)
except Exception:                 # module absent : on garde la table metier seule
    cpv_reference = None
try:
    import sparql_titulaires      # titulaires via SPARQL (TED Open Data)
except Exception:                 # module absent : parsing PDF seul
    sparql_titulaires = None


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_ATTRIBUTIONS", "1") != "0"
NOM_ONGLET = "attributions_radar"

# Types de notice = avis d'attribution (resultats). Valeurs VALIDES de
# `notice-type` dans l'expert search TED, confirmees par eForms-SDK :
#   can-standard (subtypes 29-32, E4 : marche/concession, regime ordinaire,
#                 y compris directive SECTORIELLE 30 qui couvre le transport)
#   can-social   (subtypes 33-35 : regime allege)
# NB : `can-tport` n'existe PAS (rejete par l'API + absent du SDK) ; l'employer
# faisait echouer la requete filtree en 400 et forcait la degradation a chaque
# run. Retire le 17/08/2026 (sonde winner-status v2).
NOTICE_TYPES_ATTRIB = ["can-standard", "can-social"]

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


def _noms_uniques(v):
    """Noms de titulaires distincts d'un champ `winner-name` (souvent un dict
    multilingue de listes redondantes, ex {"deu": ["X", "X", ...]}). Aplati et
    dedup en preservant l'ordre. Sert de FILET quand le PDF/SPARQL ne donne
    aucun gagnant."""
    vus, out = set(), []
    for nom in _all_str(v):
        n = nom.strip()
        cle = n.lower()
        if n and cle not in vus:
            vus.add(cle)
            out.append(n)
    return out


def _titulaire_etranger(pays_titulaire, codes_execution):
    """'oui' si un pays de titulaire (ISO3, adresse enregistree) est HORS des
    pays d'execution, 'non' si tous y sont, '' si le pays du titulaire est
    inconnu. DETERMINISTE (sans LLM), complementaire de l'inference d'origine
    faite par attributions_analyse (adresse enregistree != origine du groupe)."""
    pays = [p.strip() for p in str(pays_titulaire or "").split(";") if p.strip()]
    if not pays:
        return ""
    execution = set(codes_execution or [])
    return "oui" if any(p not in execution for p in pays) else "non"


def statut_selection(notice):
    """Statut de selection du titulaire, AGREGE au niveau notice depuis
    `winner-selection-status` (codelist eForms winner-selection-status) :
        selec-w = un titulaire choisi | clos-nw = clos sans titulaire
        open-nw = en cours, pas encore de titulaire

    L'API search aplatit ces statuts en liste plate SANS cle de jointure vers
    winner-name (cardinalites differentes observees : 6 statuts vs 25 noms sur
    la notice 10759-2026). On ne peut donc PAS apparier statut <-> titulaire
    par index, seulement agreger. Renvoie :
        "attribuee"    au moins un lot attribue, aucun infructueux
        "partielle"    au moins un lot attribue ET au moins un infructueux
        "infructueuse" tous les lots renseignes sont clos sans titulaire
        "en_cours"     aucun titulaire encore, au moins un lot ouvert
        ""             statut absent (notice sans ce champ)
    """
    statuts = [s.lower() for s in _all_str(notice.get("winner-selection-status"))]
    if not statuts:
        return ""
    a_gagnant = "selec-w" in statuts
    a_clos = "clos-nw" in statuts
    a_ouvert = "open-nw" in statuts
    if a_gagnant and a_clos:
        return "partielle"
    if a_gagnant:
        return "attribuee"
    if a_clos:
        return "infructueuse"
    if a_ouvert:
        return "en_cours"
    return ""


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
            # Titulaire et statut de selection recuperes DES la collecte
            # (sonde v2, 17/08/2026) : `winner-name` fiabilise le nom sans
            # telecharger le PDF ; `winner-selection-status` distingue lot
            # attribue (selec-w) / infructueux (clos-nw) / en cours (open-nw).
            "winner-name", "winner-selection-status",
            # Montant total de l'attribution (sonde v3, 17/08/2026) : rempli
            # ~13/15 sur echantillon reel. FILET quand le PDF/SPARQL n'a pas de
            # total. Sentinelle -1 = non publie (filtree dans _montant_api).
            "total-value", "total-value-cur",
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
    def appeler(payload):
        if fetch is not None:
            return fetch(payload)
        # Failover primaire -> secondaire mutualise avec le collecteur d'appels
        # (ted.poster_ted). `session` (None en prod) est resolue par poster_ted.
        rep = ted.poster_ted(payload, timeout=45, session=session)
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


def _montant_api(notice):
    """Montant total de l'attribution depuis `total-value` (API search), au
    format 'montant DEVISE' attendu par le dashboard (radar_dashboard.
    _valeur_en_millions applique alors le correctif de devise, dont CFA).

    FILET : n'est utilise que si le PDF/SPARQL n'a pas fourni de total. Filtre
    la sentinelle -1 (= montant non publie, vue sur 10759-2026), les valeurs
    nulles ou non numeriques. La devise vient de `total-value-cur`."""
    brut = _val(notice.get("total-value")).strip()
    if not brut:
        return ""
    try:
        montant = float(brut.replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except ValueError:
        return ""
    if montant <= 0:                 # -1 (sentinelle TED) ou 0 : non exploitable
        return ""
    devise = _val(notice.get("total-value-cur")).strip()
    return _nettoyer_montant(brut, devise) if devise else brut


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


def obtenir_gagnants(pub, fetch=None, session=None, fetch_sparql=None, fetch_renouv=None):
    """Titulaires d'une attribution, au format de parser_gagnants, enrichis du
    RENOUVELLEMENT (date de fin de contrat) quand SPARQL est actif.

    SPARQL PRIORITAIRE (structure, fiable) : si le flag RADAR_SPARQL_TITULAIRES
    est actif ET que le triplestore repond avec au moins un titulaire, on
    utilise ce resultat. SINON on retombe sur le PDF (parsing regex), qui reste
    le filet -- notamment pour un avis trop frais pour etre deja dans l'ODS.
    Le renouvellement (conclusion + duree) est ajoute dans parse["renouvellement"]
    s'il est disponible. `fetch` (PDF), `fetch_sparql` et `fetch_renouv` (JSON
    SPARQL) sont injectables pour tests.
    """
    parse = None
    renouv = {}
    if sparql_titulaires is not None and getattr(sparql_titulaires, "ACTIF", False):
        try:
            parse = sparql_titulaires.parse_depuis_sparql(
                pub, fetch=fetch_sparql, session=session)
        except Exception:
            parse = None
        try:
            renouv = sparql_titulaires.renouvellement_par_pn(
                pub, fetch=fetch_renouv, session=session) or {}
        except Exception:
            renouv = {}
    if not parse:
        texte = lire_notice(pub, fetch=fetch, session=session)
        parse = parser_gagnants(texte)
    if renouv:
        parse["renouvellement"] = renouv
    return parse


# ===========================================================================
# PARTIE 5 -- NORMALISATION
# ===========================================================================

def secteur_lisible(codes_cpv):
    # 1) Table metier Amarante (libelles courts, prioritaire).
    for c in codes_cpv:
        lib = SECTEUR_PAR_DIVISION.get(c[:2])
        if lib:
            return lib
    # 2) Fallback : division officielle CPV (eForms-SDK) pour les divisions
    #    hors table metier -> evite un "Autre" quand un secteur existe.
    if cpv_reference is not None:
        for c in codes_cpv:
            lib = cpv_reference.division_lisible(c)
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
    statut_sel = statut_selection(notice)
    # FILET titulaire (sonde v2) : si ni SPARQL ni PDF n'ont donne de gagnant,
    # on retombe sur `winner-name` recupere a la collecte. Gratuit (deja dans
    # la notice), sans telechargement supplementaire, souvent suffisant. En cas
    # d'attribution 100% infructueuse, winner-name est vide -> rien a ajouter.
    if not parse["gagnants"]:
        for nom in _noms_uniques(notice.get("winner-name")):
            parse["gagnants"].append({"nom": nom, "valeur": ""})
    noms_gagnants = "; ".join(g["nom"] for g in parse["gagnants"]) or "(gagnant non publie)"
    valeurs = "; ".join(g["valeur"] for g in parse["gagnants"] if g["valeur"])
    tier = max([ted.MULTIPLICATEUR_ZONE.get(c, 0.2) for c in codes_iso] or [0.2])
    renouv = parse.get("renouvellement", {})
    # Socle DETERMINISTE du titulaire (SPARQL) : pays d'adresse enregistree et
    # deduction "etranger vs pays d'execution". Vide si SPARQL inactif/muet.
    pays_tit = parse.get("pays_titulaire", "")
    etranger = _titulaire_etranger(pays_tit, codes_iso)
    return {
        "publication_number": pub,
        "gagnant": noms_gagnants,
        "valeur_attribuee": parse["total"] or _montant_api(notice) or valeurs,
        "acheteur": _val(notice.get("buyer-name")),
        "pays_execution": pays_lisible(codes_iso) or _val(notice.get("place-of-performance")),
        "secteur": secteur_lisible(codes_cpv),
        "cpv": ", ".join(dict.fromkeys(codes_cpv)),
        "sous_traitance": "oui" if parse["sous_traitance"] else "non",
        "titre": _val(notice.get("notice-title"))[:300],
        "date_publication": _val(notice.get("publication-date"))[:10],
        "lien": LIEN_NOTICE_HTML.format(pub),
        # Renouvellement (SPARQL) : date de fin de contrat estimee et alerte.
        "fin_contrat": renouv.get("fin", ""),
        "mois_avant_fin": renouv.get("mois_avant", ""),
        "statut_renouv": renouv.get("statut", ""),
        # Socle deterministe titulaire (colonnes partagees deja au schema).
        "pays_titulaire": pays_tit,
        "titulaire_etranger": etranger,
        "_tier": tier,
        "_nb_gagnants": len(parse["gagnants"]),
        # Statut de selection agrege (clef prefixee _ : HORS schema Sheet,
        # supprimee par la lecture positionnelle de COLONNES). Sert a `ligne()`
        # pour le signal re-tender et au bilan console.
        "_statut_selection": statut_sel,
    }


# ===========================================================================
# PARTIE 6 -- SORTIE GOOGLE SHEET
# ===========================================================================
COLONNES = [
    "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
    "acheteur", "titre", "cpv", "sous_traitance",
    "date_publication", "publication_number", "lien", "a_demarcher",
    # Socle DETERMINISTE du titulaire, calcule a la collecte sans LLM (23/07/2026).
    # Ajoute en FIN de schema, AVANT les colonnes humaines : l'ordre des colonnes
    # existantes ne bouge pas, donc aucune ligne deja ecrite n'est desalignee.
    # attributions_analyse.py affinera l'origine ; ces deux champs donnent une
    # reponse immediate meme quand l'analyse LLM n'a pas encore tourne.
    "pays_titulaire", "titulaire_etranger",
]
COL_STATUT = "statut_prospection"     # zone preservee (saisie humaine)
COL_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COL_STATUT, COL_DETECTION]


def ouvrir_feuille(sheet_id, fichier):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = radar_resilience.avec_retry(lambda: gspread.authorize(creds).open_by_key(sheet_id), "ouverture classeur")
    try:
        f = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000, cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f
    entete_actuel = f.row_values(1)
    # MIGRATION MANUELLE (chantier B, 23/07/2026) : deux colonnes ajoutees au
    # schema partage (`pays_titulaire`, `titulaire_etranger`). On NE reecrit
    # PAS l'en-tete et on ne decale AUCUNE ligne existante -- la migration se
    # fait a la main, une fois (voir bm_attributions.ouvrir_feuille pour le
    # detail). On avertit tant que ce n'est pas fait.
    if entete_actuel and "pays_titulaire" not in entete_actuel:
        print("  (!) MIGRATION REQUISE sur l'onglet '{}' : inserer deux "
              "colonnes vides 'pays_titulaire' et 'titulaire_etranger' entre "
              "'a_demarcher' et 'statut_prospection'.".format(NOM_ONGLET))
    elif not entete_actuel:
        f.update(values=[TOUTES_COLONNES], range_name="A1")
    return f


def ligne(a):
    # Signal de prospection porte par la colonne EXISTANTE `a_demarcher`
    # (aucune colonne ajoutee au schema partage) : une attribution infructueuse
    # (tous lots clos-nw) n'a pas de titulaire mais annonce une RE-PUBLICATION
    # -> opportunite directe, marquee "re-tender".
    if a.get("_statut_selection") == "infructueuse":
        demarche = "re-tender"
    elif a["_nb_gagnants"]:
        demarche = "oui"
    else:
        demarche = "verifier"
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
        "a_demarcher": demarche,
        "pays_titulaire": a.get("pays_titulaire", ""),
        "titulaire_etranger": a.get("titulaire_etranger", ""),
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
        radar_resilience.avec_retry(lambda: feuille.batch_update(maj), "ecriture batch_update")
    if nouvelles:
        radar_resilience.avec_retry(lambda: feuille.append_rows(nouvelles, value_input_option="RAW"), "ecriture append_rows")
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
            parse = obtenir_gagnants(pub)
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
    infructueuses = sum(1 for a in attributions
                        if a.get("_statut_selection") == "infructueuse")
    print("\n" + "=" * 68)
    print("REGISTRE DES ATTRIBUTIONS (qui a gagne quoi en zone a risque)")
    print("{} avis traites | {} avec titulaire identifie | {} infructueuse(s) "
          "-> re-tender".format(len(attributions), avec, infructueuses))
    print("=" * 68)
    for a in sorted(attributions, key=lambda x: x["_tier"], reverse=True)[:25]:
        print("\n[{}] {}".format(a["secteur"], a["gagnant"]))
        print("  Pays : {} | Valeur : {} | Acheteur : {}".format(
            a["pays_execution"], a["valeur_attribuee"] or "n.c.", a["acheteur"][:50]))
        print("  {}".format(a["lien"]))


if __name__ == "__main__":
    main()
