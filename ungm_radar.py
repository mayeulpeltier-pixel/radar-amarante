# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COLLECTEUR UNGM (UN Global Marketplace).
===========================================================

CE QU'IL FAIT
-------------
Recupere les avis de marches publies par les agences des Nations Unies sur le
portail public UNGM, les filtre sur l'univers de risque, les fait analyser par
le meme moteur que TED / BM / AfDB / EBRD, et ecrit dans l'onglet `ungm_radar`.

POURQUOI CETTE SOURCE
---------------------
90+ organisations (HCR, PAM, UNICEF, PNUD, OIM, UNOPS, UNRWA, OSCE...) qui
operent precisement la ou Amarante travaille : Syrie, Afghanistan, Yemen, Mali,
Somalie, Soudan du Sud, RDC, Libye, Irak, Haiti. UNGM publie meme un type
d'avis dedie "Public Order, Security and Safety Services".

ACCESSIBILITE (verifiee par sonde depuis GitHub Actions, 18/07/2026)
---------------------------------------------------------------------
La page publique n'affiche rien en HTML brut : le tableau est rendu par un
appel AJAX. L'endpoint POST /Public/Notice/Search repond HTTP 200 avec ~95 Ko
de HTML exploitable. Attention, la sonde v1 avait conclu a tort a un echec :
elle cherchait des <tr>, or UNGM rend ses lignes en <div role="row">.

PARSING : RECONNAISSANCE DE CONTENU, PAS POSITION DE COLONNE
-------------------------------------------------------------
L'ordre des colonnes du portail n'est pas garanti et n'a pas pu etre observe en
entier. Plutot que de figer des index fragiles, on IDENTIFIE chaque cellule par
son contenu : une date par son motif, un pays par la table de reference, la
reference par sa forme, le titre par ce qui reste de plus long. Un changement
d'ordre des colonnes cote UNGM ne casse alors rien.

MODE VERIFICATION (a utiliser au premier run)
---------------------------------------------
    RADAR_UNGM_DEBUG=1  -> n'ecrit rien, imprime les avis interpretes ET la
    structure BRUTE des premieres lignes. C'est ce second point qui permet de
    corriger le parseur sur donnees reelles si l'interpretation deraille.

Interrupteur : RADAR_UNGM=0 desactive la collecte.
Fenetre      : RADAR_UNGM_JOURS (defaut 45 jours de publication).

LANCEMENT :  python ungm_radar.py
"""

import html as _html
import os
import re
from datetime import date, datetime, timedelta

import bm_attributions as _pays   # resolveur de pays bilingue, deja teste
import ted_complet_v14 as ted


# ===========================================================================
# CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_UNGM", "1") != "0"
DEBUG = os.environ.get("RADAR_UNGM_DEBUG", "0") == "1"

PAGE_PUBLIQUE = "https://www.ungm.org/Public/Notice"
ENDPOINT_RECHERCHE = "https://www.ungm.org/Public/Notice/Search"
LIEN_AVIS = "https://www.ungm.org/Public/Notice/{}"

JOURS_FENETRE = int(os.environ.get("RADAR_UNGM_JOURS", "45"))
PAGES_MAX = int(os.environ.get("RADAR_UNGM_PAGES", "8"))
TAILLE_PAGE = int(os.environ.get("RADAR_UNGM_TAILLE", "50"))
BUDGET_LLM = int(os.environ.get("RADAR_UNGM_BUDGET", "60"))
# Collecte pays par pays : nombre de pays interroges et pages par pays.
PAYS_MAX = int(os.environ.get("RADAR_UNGM_PAYS_MAX", "60"))
PAGES_PAR_PAYS = int(os.environ.get("RADAR_UNGM_PAGES_PAYS", "3"))
# Garde-temps : cette boucle fait une requete par pays. Sans borne, elle
# pourrait manger le budget du job (45 min) et tuer dashboard et digest.
MINUTES_MAX = float(os.environ.get("RADAR_UNGM_MINUTES", "12"))

NOM_ONGLET = "ungm_radar"
# Schema IDENTIQUE a celui des autres bailleurs : le dashboard peut alors lire
# cet onglet avec le meme helper generique, sans code specifique.
COLONNES_UNGM = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "pays_acheteur",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance", "modele", "raffine", "divergence",
    "type_notice", "phase", "publication_number", "lien_avis",
    "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_UNGM = COLONNES_UNGM + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]

ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": PAGE_PUBLIQUE,
}

# Charge utile validee par la sonde (HTTP 200, ~95 Ko de lignes).
def charge_recherche(page=0, taille=None):
    return {
        "PageIndex": page,
        "PageSize": taille or TAILLE_PAGE,
        "Title": "", "Description": "", "Reference": "",
        "NoticeTypes": [], "Agencies": [], "Countries": [], "UNSPSCs": [],
        "SortField": "DatePublished", "SortAscending": False,
    }


# ===========================================================================
# PARSEUR (fonctions PURES : testables sans reseau)
# ===========================================================================

RE_LIGNE = re.compile(r'<div[^>]*\brole="row"[^>]*>', re.I)
RE_ID = re.compile(r'data-noticeid="(\d+)"', re.I)
RE_CELLULE = re.compile(r'<div[^>]*\brole="cell"[^>]*>(.*?)</div>\s*(?=<div[^>]*\brole="cell"|$)',
                        re.I | re.S)
# Dates rencontrees sur les portails de ce type.
RE_DATE = re.compile(
    r"\b(\d{1,2})[-/\s]([A-Za-z]{3,9}|\d{1,2})[-/\s](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
RE_REFERENCE = re.compile(r"^[A-Z0-9][A-Z0-9/_.\-]{5,}$")

# Types d'avis courants sur UNGM. La cellule doit correspondre ENTIEREMENT au
# libelle : un titre comme "ITB for Supply of veterinary drugs" commence par un
# type mais n'en est pas un (constate sur donnees reelles le 20/07/2026, il
# etait pris pour le type et le vrai type devenait le titre).
RE_TYPE_AVIS = re.compile(
    r"(?i)^(request for (proposals?|quotations?|information|expressions? of interest)"
    r"|invitation to bids?|invitation for bids?|expressions? of interest"
    r"|calls? for (proposals?|expressions?( of interest)?)"
    r"|pre[- ]?qualification( notice)?|general procurement notice"
    r"|advance procurement notice|procurement notice|notice of award"
    r"|tender( notice)?|rfp|rfq|rfi|itb|eoi)$")

# Agences ONU et apparentees. Sert a reconnaitre l'emetteur quelle que soit la
# position de la colonne. La liste n'a pas besoin d'etre exhaustive : un nom
# inconnu retombe sur l'heuristique de longueur.
AGENCES_ONU = (
    "UNHCR", "WFP", "UNICEF", "UNDP", "IOM", "UNOPS", "UNRWA", "WHO", "FAO",
    "UNFPA", "UNEP", "UN WOMEN", "UNESCO", "ILO", "UNIDO", "IFAD", "OCHA",
    "UNAIDS", "ITU", "IAEA", "UNODC", "OSCE", "ICAO", "IMO", "WMO", "UPU",
    "WIPO", "UNMISS", "MONUSCO", "MINUSMA", "MINUSCA", "UNSOS", "UNSMIL",
    "UNAMA", "UNIFIL", "UNDSS", "ITC", "UNCDF", "UNV", "PAHO", "UNITAR",
    "NATIONS UNIES", "UNITED NATIONS", "WORLD FOOD", "REFUGEE AGENCY",
)


def _est_agence(txt):
    """Vrai si la cellule DESIGNE une agence ONU, et non un intitule qui en
    mentionne une. Le garde-fou de taille est essentiel : sans lui, un titre
    comme "Provision of Security Guard Services for UNHCR Offices" etait pris
    pour l'emetteur (constate a l'ecriture du parseur)."""
    t = re.sub(r"\s+", " ", str(txt or "")).strip()
    if not t or len(t) > 50 or len(t.split()) > 7:
        return False
    # Une cellule d'agence ne contient jamais d'annee ni de numero d'ordre.
    # Sans ce garde-fou, un titre comme "UNDP-IC-2026-177: Legal expert..."
    # etait pris pour l'emetteur (constate au run du 20/07/2026).
    if re.search(r"\d{3,}", t):
        return False
    h = re.sub(r"[^A-Z ]", " ", t.upper())
    jetons = set(h.split())
    for a in AGENCES_ONU:
        if " " in a:
            if a in h:
                return True
        elif a in jetons:
            return True
    return False

MOIS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _texte(html_brut):
    """HTML -> texte propre sur une ligne."""
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", " ", str(html_brut or ""))
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def extraire_lignes(html_brut):
    """HTML de reponse -> [{'id':..., 'cellules':[texte, ...]}, ...].

    On decoupe sur les <div role="row"> (UNGM n'utilise PAS <tr>) puis on lit
    les <div role="cell"> de chaque bloc."""
    src = str(html_brut or "")
    bornes = [m.start() for m in RE_LIGNE.finditer(src)]
    lignes = []
    for i, deb in enumerate(bornes):
        fin = bornes[i + 1] if i + 1 < len(bornes) else len(src)
        bloc = src[deb:fin]
        m = RE_ID.search(bloc)
        if not m:
            continue                      # ligne d'en-tete ou de mise en forme
        cellules = [nettoyer_cellule(_texte(c)) for c in RE_CELLULE.findall(bloc)]
        cellules = [c for c in cellules if c]
        if cellules:
            lignes.append({"id": m.group(1), "cellules": cellules})
    return lignes


def lire_date(texte):
    """Date ISO depuis les formats courants, '' sinon."""
    m = RE_DATE.search(str(texte or ""))
    if not m:
        return ""
    try:
        if m.group(4):                                  # AAAA-MM-JJ
            return date(int(m.group(4)), int(m.group(5)), int(m.group(6))).isoformat()
        jour, mois_brut, annee = m.group(1), m.group(2), m.group(3)
        mois = (MOIS.get(mois_brut[:3].lower()) if not mois_brut.isdigit()
                else int(mois_brut))
        if not mois:
            return ""
        return date(int(annee), mois, int(jour)).isoformat()
    except (ValueError, TypeError):
        return ""


def _iso3_depuis_texte(txt):
    """ISO3 si la cellule EST un nom de pays (comparaison sur la cellule
    ENTIERE, pour ne pas prendre un pays cite dans un intitule pour le pays
    d'execution).

    UNGM publie en ANGLAIS : la table francaise seule ne reconnaissait ni
    "Somalia", ni "South Sudan", ni "Iraq". On reutilise donc le resolveur
    bilingue ecrit pour les attributions BM, deja couvert par des tests
    (Niger/Nigeria distingues, les deux Congo, accents perdus)."""
    cle = re.sub(r"\s+", " ", str(txt or "")).strip()
    if not cle or len(cle) > 60:
        return ""
    return _pays.iso3_pays_libre(cle) or ""


# Cellule de service presente sur CHAQUE ligne (boutons UNGM Pro) : aucun
# contenu metier, on l'ecarte avant toute interpretation.
RE_BRUIT = re.compile(r"(?i)(unsave|save) this procurement opportunity|subscribe to ungm pro")
# Suffixes d'accessibilite colles aux libelles cliquables.
RE_SUFFIXE = re.compile(r"(?i)\s*open in a new window\s*$")
# Les cellules de date portent l'heure, le fuseau, et un flottant technique
# ("20-Aug-2026 04:00 (GMT 3.00) 30.6971513338947") : on ne garde que la date.
RE_RESIDU_DATE = re.compile(r"(?i)\s*\d{1,2}:\d{2}.*$")


def nettoyer_cellule(txt):
    """Retire le bruit d'interface d'une cellule. '' si la cellule est
    purement technique."""
    t = re.sub(r"\s+", " ", str(txt or "")).strip()
    if not t or RE_BRUIT.search(t):
        return ""
    return RE_SUFFIXE.sub("", t).strip()


# --- Detection du pays : UNGM N'A PAS DE COLONNE PAYS ----------------------
# Constate sur donnees reelles le 20/07/2026 : le tableau de resultats expose
# titre, echeance, publication, agence, type et reference, mais AUCUN pays.
# Le pays se trouve en revanche dans le titre ("...for the WHO Djibouti") et
# souvent code dans la reference ("EM/ACO/DJI/P/0009332" -> DJI). On tente donc
# ces deux pistes gratuites avant d'envisager la page de detail.

def _table_pays():
    """Noms de pays -> ISO3, anglais ET francais, les plus longs d'abord.
    L'ordre est essentiel : sans lui, "South Sudan" serait reconnu comme
    "Sudan" et le lead atterrirait dans le mauvais pays."""
    table = {}
    try:
        import ted_complet_bm as _bm
        table.update({k.lower(): v for k, v in
                      getattr(_bm, "PAYS_NOM_VERS_ISO3", {}).items()})
    except Exception:
        pass
    try:
        import bitd_signaux as _bitd
        for k, v in getattr(_bitd, "NOM_VERS_ISO3", {}).items():
            table.setdefault(k.lower(), v)
    except Exception:
        pass
    return sorted(table.items(), key=lambda kv: -len(kv[0]))


_TABLE_PAYS = None


def pays_depuis_texte(txt):
    """ISO3 d'un pays CITE dans un texte libre (titre d'avis). '' si aucun.
    Recherche par mot entier pour eviter qu'un fragment ne declenche une
    fausse detection."""
    global _TABLE_PAYS
    if _TABLE_PAYS is None:
        _TABLE_PAYS = _table_pays()
    bas = " " + re.sub(r"[^a-zA-ZÀ-ÿ ]", " ", str(txt or "")).lower() + " "
    bas = re.sub(r"\s+", " ", bas)
    for nom, iso in _TABLE_PAYS:
        if len(nom) < 4:
            continue                      # trop court : trop de faux positifs
        if " " + nom + " " in bas:
            return iso
    return ""


def pays_depuis_reference(ref):
    """ISO3 code dans une reference d'avis ("EM/ACO/DJI/P/0009332" -> DJI).
    On n'accepte qu'un segment de 3 lettres qui EST un code pays connu ; les
    segments internes comme "ACO" ou "ROAS" ne matchent rien et sont ignores."""
    segments = re.split(r"[^A-Za-z]+", str(ref or ""))
    for seg in segments:
        if len(seg) != 3:
            continue
        iso = seg.upper()
        if iso in ted.MULTIPLICATEUR_ZONE:
            return iso
    return ""


def detecter_pays(champs):
    """Cascade de detection, de la piste la plus sure a la plus indirecte."""
    if champs.get("pays"):
        return champs["pays"], "cellule"
    iso = pays_depuis_texte(champs.get("titre", ""))
    if iso:
        return iso, "titre"
    iso = pays_depuis_reference(champs.get("reference", ""))
    if iso:
        return iso, "reference"
    for autre in champs.get("autres", []):
        iso = pays_depuis_texte(autre)
        if iso:
            return iso, "autre cellule"
    return "", ""


RE_OPTION = re.compile(
    r'<option[^>]*value="([^"]+)"[^>]*>\s*([^<]{3,60}?)\s*</option>', re.I | re.S)


def charger_pays_ungm(session=None, fetch=None):
    """{ISO3: identifiant UNGM} depuis le formulaire de filtres de la page
    publique.

    POURQUOI C'EST LA CLE DE CE COLLECTEUR : le tableau de resultats d'UNGM
    n'expose AUCUNE colonne pays (verifie le 20/07/2026). Deviner le pays
    depuis le titre produit des faux positifs graves : un billet d'avion "from
    Lusaka, Zambia" ressortait en Malawi, et un avis pour l'Inde en Sierra
    Leone. En interrogeant UNGM PAYS PAR PAYS, le pays est connu avec certitude
    puisque c'est nous qui le demandons.

    Renvoie {} en cas d'echec : le collecteur retombe alors sur la detection
    par titre/reference, degradee mais non nulle."""
    try:
        if fetch is not None:
            html = fetch()
        else:
            session = session or ted.session_robuste()
            rep = session.get(PAGE_PUBLIQUE, headers=ENTETES, timeout=45)
            if rep.status_code >= 400:
                print("(ungm) page publique HTTP {} : filtre pays indisponible.".format(
                    rep.status_code))
                return {}
            html = rep.text
    except Exception as e:
        print("(ungm) liste des pays illisible ({}).".format(e))
        return {}

    table = {}
    for valeur, libelle in RE_OPTION.findall(html or ""):
        iso = _iso3_depuis_texte(libelle.strip())
        if not iso or iso in table:
            continue
        val = valeur.strip()
        if val and val.isdigit():
            table[iso] = val
    return table


def pays_a_interroger(table_pays):
    """Pays a interroger ce run, LES PLUS RISQUES D'ABORD.

    On se limite a l'univers de risque : interroger la Suisse ou le Danemark
    consommerait des requetes pour des avis sans interet commercial."""
    candidats = [(iso, ident) for iso, ident in (table_pays or {}).items()
                 if iso in ted.MULTIPLICATEUR_ZONE]
    candidats.sort(key=lambda c: (-ted.MULTIPLICATEUR_ZONE.get(c[0], 0), c[0]))
    return candidats[:PAYS_MAX]


def diagnostic_pays(session=None, ident_exemple=""):
    """DIAGNOSTIC (mode verification seulement) : cherche par ou obtenir le
    pays, puisque le tableau de resultats ne l'expose pas.

    Deux pistes imprimees pour arbitrage :
      A. le formulaire de filtres de la page publique, qui contient la liste
         des pays et leurs identifiants internes. Si on les recupere, on peut
         interroger UNGM PAYS PAR PAYS et connaitre le pays avec certitude,
         sans plus dependre du titre.
      B. la page de detail d'un avis, ou le pays est peut-etre affiche.
    N'ecrit rien, n'echoue jamais."""
    session = session or ted.session_robuste()

    print("\n[3] Piste A : liste des pays dans le formulaire de filtres")
    try:
        rep = session.get(PAGE_PUBLIQUE, headers=ENTETES, timeout=45)
        html = rep.text or ""
        print("    GET {} -> HTTP {} ({} octets)".format(
            PAGE_PUBLIQUE, rep.status_code, len(rep.content)))
        motifs = [
            (r'<option[^>]*value="([^"]+)"[^>]*>\s*([^<]{3,40})\s*</option>', "option"),
            (r'data-value="([^"]+)"[^>]*>\s*([^<]{3,40})\s*<', "data-value"),
            (r'<li[^>]*data-id="([^"]+)"[^>]*>\s*([^<]{3,40})\s*<', "li data-id"),
        ]
        # On ne retient que les entrees dont le LIBELLE est un pays connu :
        # c'est ce qui distingue la liste des pays des autres filtres.
        for motif, nom in motifs:
            paires = re.findall(motif, html, re.I | re.S)
            trouves = []
            for val, lib in paires:
                iso = _iso3_depuis_texte(lib.strip())
                if iso:
                    trouves.append((iso, val.strip(), lib.strip()))
            if trouves:
                print("    via {} : {} pays reconnus. Exemples :".format(nom, len(trouves)))
                for iso, val, lib in trouves[:12]:
                    print("        {} -> identifiant {!r} ({})".format(iso, val, lib))
                break
        else:
            print("    aucune liste de pays reconnue dans la page. "
                  "Piste A a abandonner, voir piste B.")
    except Exception as e:
        print("    echec piste A : {}".format(e))

    print("\n[4] Piste B : le pays figure-t-il sur la page de detail d'un avis ?")
    if not ident_exemple:
        print("    (aucun avis a tester)")
        return
    url = LIEN_AVIS.format(ident_exemple)
    try:
        rep = session.get(url, headers=ENTETES, timeout=45)
        html = rep.text or ""
        print("    GET {} -> HTTP {} ({} octets)".format(url, rep.status_code, len(rep.content)))
        texte = _texte(html)
        # On cherche les libelles qui precedent habituellement un pays.
        for etiquette in ("Beneficiary countries", "Beneficiary country",
                          "Country", "Duty station", "Place of delivery",
                          "Pays", "Location"):
            m = re.search(r"(?i)" + re.escape(etiquette) + r"\s*:?\s*(.{0,90})", texte)
            if m:
                print("    {!r} -> {!r}".format(etiquette, m.group(1).strip()[:90]))
        iso = pays_depuis_texte(texte[:6000])
        print("    pays reconnu dans le debut de page : {}".format(iso or "AUCUN"))
    except Exception as e:
        print("    echec piste B : {}".format(e))


def interpreter_ligne(cellules):
    """Cellules brutes -> champs identifies PAR LEUR CONTENU.

    Aucune position n'est supposee : UNGM peut reordonner ses colonnes sans
    rien casser ici. Renvoie un dict, meme partiellement vide."""
    dates, pays, reference, agence, type_avis, restes = [], "", "", "", "", []
    for c in cellules:
        iso = _iso3_depuis_texte(c)
        if iso and not pays:
            pays = iso
            continue
        d = lire_date(c)
        if d and RE_DATE.match(RE_RESIDU_DATE.sub("", c).strip()):
            # La cellule EST une date (eventuellement suivie d'une heure, d'un
            # fuseau et d'un flottant technique), et non un texte qui en cite une.
            dates.append(d)
            continue
        if not reference and RE_REFERENCE.match(c) and any(ch.isdigit() for ch in c):
            reference = c
            continue
        if not agence and _est_agence(c) and len(c) <= 90:
            agence = c
            continue
        if not type_avis and RE_TYPE_AVIS.match(c) and len(c) <= 60:
            type_avis = c
            continue
        restes.append(c)
    dates = sorted(set(dates))
    titre = max(restes, key=len) if restes else ""
    autres = [r for r in restes if r is not titre]
    return {
        "titre": titre,
        "pays": pays,
        "reference": reference,
        "agence": agence,
        "type_avis": type_avis,
        # La date la plus ancienne est la publication, la plus tardive
        # l'echeance. Avec une seule date on ne devine pas : on la met en
        # publication, ce qui est le choix prudent (pas de fausse urgence).
        "date_publication": dates[0] if dates else "",
        "deadline": dates[-1] if len(dates) > 1 else "",
        "autres": autres,
    }


def agence_probable(champs):
    """Agence ONU emettrice. La reconnaissance explicite prime ; a defaut on
    prend une cellule courte restante, et en dernier recours un libelle neutre."""
    if champs.get("agence"):
        return champs["agence"]
    for c in champs.get("autres", []):
        if 2 < len(c) <= 80 and not c.isdigit() and not RE_TYPE_AVIS.match(c):
            return c
    return "Nations Unies"


def dans_la_fenetre(iso_date, aujourdhui=None, jours=None):
    """Avis assez recent pour valoir un appel au modele."""
    if not iso_date:
        return True                       # sans date, on ne jette pas
    jours = JOURS_FENETRE if jours is None else jours
    aujourdhui = aujourdhui or date.today()
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return True
    return (aujourdhui - d) <= timedelta(days=jours)


def normaliser(ligne):
    """Ligne UNGM -> avis dict compatible ted.appeler_llm / ted.calculer_scores.
    None si hors perimetre (pays non suivi, avis trop ancien, titre vide)."""
    champs = interpreter_ligne(ligne.get("cellules") or [])
    # Si la ligne vient d'une requete ciblee, le pays est CERTAIN : on ne
    # redevine rien. La detection par titre/reference n'est qu'un repli.
    if ligne.get("pays_iso3"):
        iso3, origine_pays = ligne["pays_iso3"], "requete"
    else:
        iso3, origine_pays = detecter_pays(champs)
    if not iso3 or iso3 not in ted.MULTIPLICATEUR_ZONE:
        return None
    if not champs["titre"]:
        return None
    if not dans_la_fenetre(champs["date_publication"]):
        return None
    return {
        "acheteur": agence_probable(champs),
        "pays_acheteur": "",                        # organisation multilaterale
        "pays_execution": iso3,
        "titre": champs["titre"][:300],
        "cpv": "",
        "description": " · ".join(champs["autres"])[:ted.MAX_CARACTERES_DESCRIPTION],
        "type_notice": champs.get("type_avis") or "Avis de marche",
        "phase": "avis",
        "lien_avis": LIEN_AVIS.format(ligne.get("id", "")),
        "publication_number": "UNGM-{}".format(ligne.get("id", "")),
        "deadline": champs["deadline"],
        "date_publication": champs["date_publication"],
        "pays_execution_incertitude": False,
        "_reference": champs["reference"],
        "_origine_pays": origine_pays,
    }


# ===========================================================================
# COLLECTE
# ===========================================================================

def collecte(session=None, fetch=None):
    """Pagine la recherche publique UNGM. Best-effort : une page en echec
    interrompt la pagination sans faire echouer le run.

    `fetch` injectable pour tests : callable(page) -> texte HTML."""
    import json as _json
    session = session or ted.session_robuste()
    lignes, stats = [], {"pages": 0, "lignes": 0, "arret": "plafond de pages"}
    vus = set()
    for page in range(PAGES_MAX):
        try:
            if fetch is not None:
                texte = fetch(page)
            else:
                rep = session.post(
                    ENDPOINT_RECHERCHE,
                    data=_json.dumps(charge_recherche(page)).encode("utf-8"),
                    headers=dict(ENTETES, **{"Content-Type": "application/json"}),
                    timeout=45)
                if rep.status_code >= 400:
                    print("(ungm) page {} : HTTP {}, arret.".format(page, rep.status_code))
                    stats["arret"] = "erreur HTTP {}".format(rep.status_code)
                    break
                texte = rep.text
        except Exception as e:
            print("(ungm) page {} illisible ({}), arret.".format(page, e))
            stats["arret"] = "reponse illisible"
            break
        lot = extraire_lignes(texte)
        if not lot:
            stats["arret"] = "fin des donnees"
            break
        nouvelles = [l for l in lot if l["id"] not in vus]
        for l in nouvelles:
            vus.add(l["id"])
        lignes.extend(nouvelles)
        stats["pages"] += 1
        stats["lignes"] += len(nouvelles)
        if not nouvelles:
            stats["arret"] = "pagination bouclee"
            break
    return lignes, stats


def collecte_par_pays(session=None, fetch=None, table_pays=None):
    """Interroge UNGM PAYS PAR PAYS. Chaque ligne obtenue est etiquetee avec le
    pays demande : l'attribution est certaine, plus aucune devinette.

    Renvoie (lignes, stats). Chaque ligne porte une cle 'pays_iso3'.
    Best-effort : un pays en echec est saute, le reste continue."""
    import json as _json
    import time as _time
    session = session or ted.session_robuste()
    table = table_pays if table_pays is not None else charger_pays_ungm(session)
    cibles = pays_a_interroger(table)
    lignes, vus = [], set()
    stats = {"pays_interroges": 0, "pays_avec_avis": 0, "lignes": 0,
             "requetes": 0, "arret": "termine"}
    if not cibles:
        stats["arret"] = "aucun pays exploitable"
        return lignes, stats

    debut = _time.time()
    for iso, ident in cibles:
        if (_time.time() - debut) / 60.0 >= MINUTES_MAX:
            stats["arret"] = "garde-temps"
            break
        stats["pays_interroges"] += 1
        avant = len(lignes)
        for page in range(PAGES_PAR_PAYS):
            charge = charge_recherche(page)
            charge["Countries"] = [ident]
            try:
                if fetch is not None:
                    texte = fetch(iso, page)
                else:
                    rep = session.post(
                        ENDPOINT_RECHERCHE,
                        data=_json.dumps(charge).encode("utf-8"),
                        headers=dict(ENTETES, **{"Content-Type": "application/json"}),
                        timeout=45)
                    stats["requetes"] += 1
                    if rep.status_code >= 400:
                        break
                    texte = rep.text
            except Exception:
                break                      # pays saute, on continue les autres
            lot = extraire_lignes(texte)
            nouvelles = [l for l in lot if l["id"] not in vus]
            for l in nouvelles:
                vus.add(l["id"])
                l["pays_iso3"] = iso       # attribution CERTAINE
            lignes.extend(nouvelles)
            if len(lot) < TAILLE_PAGE or not nouvelles:
                break                      # derniere page pour ce pays
        if len(lignes) > avant:
            stats["pays_avec_avis"] += 1
    stats["lignes"] = len(lignes)
    return lignes, stats


# --- Priorisation de la file d'analyse -------------------------------------
# Un run reel ramene ~240 avis pour un budget de 60 appels au modele. Sans
# tri, l'ordre de collecte etant alphabetique par pays, l'Afghanistan
# consommait tout le budget et le Mali, l'Ukraine ou la Somalie n'avaient
# AUCUNE analyse (constate le 20/07/2026). On classe donc la file.
#
# UNGM n'a pas de CPV : ce filtre lexical en tient lieu. Il ne SUPPRIME rien,
# il ordonne seulement, pour que le budget aille d'abord aux avis susceptibles
# d'impliquer une presence de terrain.
MOTS_FORTS = (
    "construction", "travaux", "works", "infrastructure", "rehabilitation",
    "réhabilitation", "borehole", "forage", "drilling", "road", "route",
    "bridge", "pont", "camp", "shelter", "abri", "warehouse", "entrepot",
    "entrepôt", "logistics", "logistique", "transport", "convoy", "convoi",
    "escort", "escorte", "fleet", "vehicle", "vehicule", "véhicule",
    "security", "securite", "sécurité", "guard", "gardiennage", "safety",
    "protection", "surveillance", "demining", "deminage", "déminage",
    "evacuation", "field", "terrain", "deployment", "installation",
    "maintenance", "generator", "solar", "electrical", "energy", "engineering",
    "supervision", "building", "clinic", "hospital", "health facility",
    "water", "sanitation", "pipeline", "drilling", "excavation", "mission",
)
MOTS_FAIBLES = (
    "translation", "traduction", "interpretation", "interprétation",
    "catering", "restauration", "coffee", "lunch", "meeting venue",
    "workshop venue", "printing", "impression", "stationery",
    "office supplies", "fourniture de bureau", "software", "logiciel",
    "application development", "website", "site web", "graphic", "design of a",
    "communication campaign", "air ticket", "billet", "webinar", "e-learning",
)


def interet_lexical(avis):
    """Score d'interet grossier (0.2 a 3.0) d'un avis, sans appel au modele.
    Sert UNIQUEMENT a ordonner la file : rien n'est jete."""
    texte = "{} {}".format(avis.get("titre", ""), avis.get("description", "")).lower()
    score = 1.0
    for mot in MOTS_FORTS:
        if mot in texte:
            score += 0.5
            if score >= 3.0:
                break
    for mot in MOTS_FAIBLES:
        if mot in texte:
            score -= 0.6
    return max(0.2, min(3.0, score))


def prioriser(avis_liste):
    """Ordonne la file : risque du pays x interet lexical, du plus fort au
    plus faible. Garantit que les zones rouges passent avant les autres."""
    def cle(a):
        risque = ted.MULTIPLICATEUR_ZONE.get(a.get("pays_execution", ""), 0.2)
        return -(risque * interet_lexical(a))
    return sorted(avis_liste, key=cle)


def construire(lignes):
    """Lignes brutes -> avis normalises, avec le detail des rejets."""
    avis, motifs = [], {"sans_pays": 0, "sans_titre": 0, "hors_fenetre": 0}
    for ligne in lignes:
        a = normaliser(ligne)
        if a is not None:
            avis.append(a)
            continue
        champs = interpreter_ligne(ligne.get("cellules") or [])
        iso = ligne.get("pays_iso3") or detecter_pays(champs)[0]
        if not iso or iso not in ted.MULTIPLICATEUR_ZONE:
            motifs["sans_pays"] += 1
        elif not champs["titre"]:
            motifs["sans_titre"] += 1
        else:
            motifs["hors_fenetre"] += 1
    return avis, motifs


# ===========================================================================
# ANALYSE ET ECRITURE
# ===========================================================================

def analyser(avis_liste, budget=None):
    """Analyse LLM + scoring, dans la limite d'un budget d'appels."""
    budget = BUDGET_LLM if budget is None else budget
    resultats = []
    for avis in avis_liste:
        if budget <= 0:
            print("  (budget d'analyses epuise, arret propre)")
            break
        extraction = ted.appeler_llm(avis)
        budget -= 1
        if not extraction:
            continue
        extraction = ted.normaliser_securite(extraction)
        surete, commercial, final = ted.calculer_scores(avis, extraction)
        resultats.append({"avis": avis, "extraction": extraction,
                          "surete": surete, "commercial": commercial,
                          "final": final, "raffine": False})
    return resultats


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier_cs, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        return classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=2000,
                                   cols=len(TOUTES_COLONNES_UNGM))
        f.append_row(TOUTES_COLONNES_UNGM)
        return f


def ligne_depuis_resultat(r):
    a, e = r["avis"], r["extraction"]
    valeurs = {
        "date_maj": date.today().isoformat(),
        "score_final": r["final"], "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.action_recommandee(r["final"]),
        "fenetre_action": ted.fenetre_action(a.get("deadline", "")),
        "niveau_opportunite_amarante": e.get("niveau_opportunite_amarante", ""),
        "titre": a.get("titre", ""), "acheteur": a.get("acheteur", ""),
        "pays_execution": a.get("pays_execution", ""),
        "pays_acheteur": a.get("pays_acheteur", ""),
        "type_client": e.get("type_client", ""),
        "type_mobilite": e.get("type_mobilite", ""),
        "profil_personnes_exposees": e.get("profil_personnes_exposees", ""),
        "duree_estimee": e.get("duree_estimee", ""),
        "accessibilite_commerciale": e.get("accessibilite_commerciale", ""),
        "securite_existante_detectee": e.get("securite_existante", ""),
        "profils_acteurs_probables": ", ".join(e.get("profils_acteurs_probables", []) or []),
        "cible_commerciale_reelle": e.get("cible_commerciale_reelle", ""),
        "justification": e.get("justification", ""),
        "confiance": e.get("confiance", ""),
        "modele": ted.MODELE, "raffine": r.get("raffine", False),
        "divergence": "", "type_notice": a.get("type_notice", ""),
        "phase": a.get("phase", ""),
        "publication_number": a.get("publication_number", ""),
        "lien_avis": a.get("lien_avis", ""),
        "deadline": a.get("deadline", ""),
        "date_publication": a.get("date_publication", ""),
    }
    return [str(valeurs.get(c, "")) for c in COLONNES_UNGM]


def ecrire(feuille, resultats):
    """N'ajoute que les avis inconnus. Ne reecrit jamais une ligne existante :
    `statut_suivi` est une zone de saisie humaine."""
    index = ted.charger_index_publication(feuille)
    nouvelles, deja = [], 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        if pub and pub in index:
            deja += 1
            continue
        nouvelles.append(ligne_depuis_resultat(r) + ["", date.today().isoformat()])
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    return len(nouvelles), deja


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    if not ACTIVER:
        print("(info) Collecteur UNGM desactive (RADAR_UNGM=0).")
        return

    print("Collecte UNGM (fenetre {} jours)...".format(JOURS_FENETRE))
    table = charger_pays_ungm()
    if table:
        cibles = pays_a_interroger(table)
        print("  {} pays connus d'UNGM, {} dans l'univers de risque a interroger.".format(
            len(table), len(cibles)))
        lignes, stats = collecte_par_pays(table_pays=table)
        print("  {} ligne(s) | {} pays interroges dont {} avec des avis | "
              "{} requetes (arret : {}).".format(
                  stats["lignes"], stats["pays_interroges"],
                  stats["pays_avec_avis"], stats["requetes"], stats["arret"]))
    else:
        # Repli : le formulaire a change. On collecte globalement et on devine
        # le pays, ce qui est moins fiable mais vaut mieux que rien.
        print("  (!) liste des pays indisponible : repli sur la collecte globale "
              "avec detection du pays par titre/reference (moins fiable).")
        lignes, stats = collecte()
        print("  {} ligne(s) sur {} page(s) (arret : {}).".format(
            stats["lignes"], stats["pages"], stats["arret"]))
    if not lignes:
        print("  Aucune ligne : structure du portail a revoir "
              "(relancer avec RADAR_UNGM_DEBUG=1).")
        return

    avis, motifs = construire(lignes)
    print("  ecartes -> pays hors perimetre : {sans_pays} | sans titre : "
          "{sans_titre} | hors fenetre : {hors_fenetre}".format(**motifs))
    voies = {}
    for a_ in avis:
        v = a_.get("_origine_pays") or "?"
        voies[v] = voies.get(v, 0) + 1
    print("  {} avis exploitable(s){}.".format(
        len(avis), " (pays via {})".format(voies) if voies else ""))

    if DEBUG:
        print("\n--- MODE VERIFICATION (RADAR_UNGM_DEBUG=1) : AUCUNE ECRITURE ---")
        print("\n[1] Tete de file APRES priorisation (ce que le budget "
              "analyserait en premier) :")
        for a in prioriser(avis)[:20]:
            print("  {} (via {}) | {} | {} | pub {} | fin {}".format(
                a["pays_execution"], a.get("_origine_pays") or "?",
                a["acheteur"][:20], a["titre"][:52],
                a["date_publication"] or "n.c.", a["deadline"] or "n.c."))
        print("\n[2] Structure BRUTE des 3 premieres lignes (pour corriger le")
        print("    parseur si l'interpretation ci-dessus est fausse) :")
        for ligne in lignes[:3]:
            print("  --- avis {} : {} cellule(s) ---".format(
                ligne["id"], len(ligne["cellules"])))
            for i, c in enumerate(ligne["cellules"]):
                print("      [{}] {}".format(i, c[:110]))
        # Le pays n'est PAS dans le tableau : on diagnostique par ou l'obtenir.
        diagnostic_pays(ident_exemple=lignes[0]["id"] if lignes else "")
        print("\n--- Verifie que titre, agence et dates sont au bon endroit,")
        print("    puis lis les pistes [3] et [4] pour la question du pays. ---")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not (sheet_id and fichier_cs):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    # DEDUPLICATION AVANT ANALYSE (comme le collecteur AfDB) : analyser un avis
    # deja present dans le Sheet pour le jeter ensuite a l'ecriture gaspillerait
    # tout le budget des le second run.
    try:
        deja_vus = ted.numeros_publication_existants(
            sheet_id, fichier_cs, NOM_ONGLET, COLONNES_UNGM)
    except Exception as e:
        print("(ungm) memoire illisible ({}), on analyse tout.".format(e))
        deja_vus = set()
    nouveaux = [a for a in avis if a.get("publication_number") not in deja_vus]
    print("  memoire : {} deja connu(s) ignore(s), {} nouveau(x).".format(
        len(avis) - len(nouveaux), len(nouveaux)))
    if not nouveaux:
        print("  Rien de nouveau ce run.")
        return

    # Le budget d'appels est inferieur au volume collecte : on classe la file
    # pour que les zones rouges et les marches de terrain passent en premier.
    nouveaux = prioriser(nouveaux)
    resultats = analyser(nouveaux)
    print("  {} avis analyse(s) par le modele (budget {}).".format(
        len(resultats), BUDGET_LLM))
    try:
        feuille = ouvrir_feuille(sheet_id, fichier_cs)
        ajoutes, deja = ecrire(feuille, resultats)
        print("  {} nouvelle(s) ligne(s) dans '{}' ({} deja connue(s)).".format(
            ajoutes, NOM_ONGLET, deja))
    except Exception as e:
        print("(ungm) ecriture impossible ({}). Le run continue.".format(e))


if __name__ == "__main__":
    main()
