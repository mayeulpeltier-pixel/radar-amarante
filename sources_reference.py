# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- REGISTRE DES SOURCES ET FIABILITE (P1, P2, P5).
===============================================================================

LE PROBLEME QU'IL RESOUT
------------------------
Jusqu'ici la decouverte comptait les sources comme des unites interchangeables :
la regle de promotion etait "3 signaux ET 2 sources". Consequence absurde :
  - un communique de la Banque Mondiale annoncant l'approbation d'un pret de
    500 M$ ne suffisait PAS a creer un candidat (1 source) ;
  - deux reprises d'une meme depeche par deux blogs suffisaient.

Ce module donne un POIDS a chaque source. Une source officielle (DFI,
ministere, agence publique) porte une preuve ; un agregateur porte une rumeur.

TROIS NIVEAUX D'INFORMATION
---------------------------
  1. TYPE de source      -> fiabilite de base (TYPES).
  2. DOMAINE connu       -> surcharge precise (SOURCES), ex. worldbank.org.
  3. Collecteur interne  -> les collecteurs DFI du depot (BM, IFC, MIGA, AfDB,
     EBRD, Proparco/AFD) declarent leur propre fiabilite via COLLECTEURS.

AJOUTER UNE SOURCE = AJOUTER UNE LIGNE dans SOURCES. Aucun code moteur a
modifier : c'est l'exigence "ajouter une source sans modifier le coeur".

ECHELLE DE FIABILITE (0 a 1)
-----------------------------
  0.95  emetteur institutionnel du projet (DFI, banque de developpement)
  0.90  gouvernement : ministere, presidence, conseil des ministres
  0.85  agence publique specialisee (energie, mines, port, investissement)
  0.80  entreprise partie prenante (communique officiel de l'operateur)
  0.65  presse economique de reference
  0.55  presse sectorielle specialisee
  0.50  presse nationale generaliste
  0.40  presse locale / agregateur / source inconnue

Ces valeurs sont des POIDS DE DECISION, pas des verites. Elles sont
surchargables par variable d'environnement (RADAR_FIABILITE_<TYPE>) pour etre
ajustees sans redeploiement.
"""

import os
import re


# ===========================================================================
# 1. TYPES DE SOURCE ET FIABILITE DE BASE
# ===========================================================================
TYPES_DEFAUT = {
    "dfi": 0.95,                 # Banque Mondiale, IFC, MIGA, AfDB, EBRD, AFD
    "gouvernement": 0.90,        # ministere, presidence, conseil des ministres
    "agence_publique": 0.85,     # agence energie/mines/port/investissement
    "entreprise": 0.80,          # communique de l'operateur du projet
    "presse_economique": 0.65,   # presse economique de reference
    "presse_sectorielle": 0.55,  # presse specialisee (mines, energie, shipping)
    "presse_generaliste": 0.50,  # quotidien national
    "presse_locale": 0.40,       # presse locale, agregateur, inconnu
}

LIBELLE_TYPE = {
    "dfi": "Banque de developpement",
    "gouvernement": "Gouvernement",
    "agence_publique": "Agence publique",
    "entreprise": "Entreprise partie prenante",
    "presse_economique": "Presse economique",
    "presse_sectorielle": "Presse sectorielle",
    "presse_generaliste": "Presse generaliste",
    "presse_locale": "Presse locale / inconnue",
}

TYPE_PAR_DEFAUT = "presse_locale"


def fiabilite_du_type(type_source):
    """Fiabilite d'un type, surchargable par env (RADAR_FIABILITE_DFI=0.9)."""
    cle = "RADAR_FIABILITE_" + str(type_source or "").upper()
    brut = os.environ.get(cle)
    if brut:
        try:
            return max(0.0, min(float(brut), 1.0))
        except ValueError:
            pass
    return TYPES_DEFAUT.get(type_source, TYPES_DEFAUT[TYPE_PAR_DEFAUT])


# ===========================================================================
# 2. REGISTRE DES SOURCES NOMMEES
# ===========================================================================
def _s(domaine, type_source, libelle, pays="", langue="", secteur=""):
    return {"domaine": domaine, "type": type_source, "libelle": libelle,
            "pays": pays, "langue": langue, "secteur": secteur}


SOURCES = [
    # ---------------------------------------------------------------- DFI
    _s("worldbank.org", "dfi", "Banque Mondiale"),
    _s("projects.worldbank.org", "dfi", "Banque Mondiale (projets)"),
    _s("ifc.org", "dfi", "IFC"),
    _s("miga.org", "dfi", "MIGA"),
    _s("afdb.org", "dfi", "Banque africaine de developpement"),
    _s("ebrd.com", "dfi", "BERD"),
    _s("afd.fr", "dfi", "Agence francaise de developpement"),
    _s("proparco.fr", "dfi", "Proparco"),
    _s("adb.org", "dfi", "Banque asiatique de developpement"),
    _s("isdb.org", "dfi", "Banque islamique de developpement"),
    _s("iadb.org", "dfi", "Banque interamericaine de developpement"),
    _s("dfc.gov", "dfi", "US DFC"),
    _s("eib.org", "dfi", "Banque europeenne d'investissement"),

    # -------------------------------------------------- AGENCES ET GOUVERNEMENTS
    # Afrique de l'Est / australe
    _s("tpdc.co.tz", "agence_publique", "TPDC (petrole/gaz Tanzanie)", "TZA", "en", "energie"),
    _s("tanzaniainvest.com", "presse_economique", "TanzaniaInvest", "TZA", "en"),
    _s("nemc.or.tz", "agence_publique", "NEMC Tanzanie", "TZA", "en"),
    _s("energy.go.tz", "gouvernement", "Ministere de l'Energie Tanzanie", "TZA", "en", "energie"),
    _s("enh.co.mz", "agence_publique", "ENH Mozambique", "MOZ", "pt", "energie"),
    _s("inp.gov.mz", "agence_publique", "INP Mozambique", "MOZ", "pt", "energie"),
    _s("sonangol.co.ao", "entreprise", "Sonangol", "AGO", "pt", "energie"),
    _s("anpg.co.ao", "agence_publique", "ANPG Angola", "AGO", "pt", "energie"),
    # Afrique centrale / de l'Ouest
    _s("presidence.cd", "gouvernement", "Presidence RDC", "COD", "fr"),
    _s("mines-rdc.cd", "gouvernement", "Ministere des Mines RDC", "COD", "fr", "mines"),
    _s("adpi-rdc.cd", "agence_publique", "ADPI-RDC", "COD", "fr"),
    _s("nnpcgroup.com", "entreprise", "NNPC", "NGA", "en", "energie"),
    _s("ncdmb.gov.ng", "agence_publique", "NCDMB Nigeria", "NGA", "en", "energie"),
    _s("nipc.gov.ng", "agence_publique", "NIPC (investissement Nigeria)", "NGA", "en"),
    _s("mines.gov.gn", "gouvernement", "Ministere des Mines Guinee", "GIN", "fr", "mines"),
    _s("gouvernement.gouv.ci", "gouvernement", "Gouvernement Cote d'Ivoire", "CIV", "fr"),
    _s("gouv.sn", "gouvernement", "Gouvernement Senegal", "SEN", "fr"),
    _s("petrosen.sn", "entreprise", "Petrosen", "SEN", "fr", "energie"),
    _s("snim.com", "entreprise", "SNIM", "MRT", "fr", "mines"),
    # MENA
    _s("oil.gov.iq", "gouvernement", "Ministere du Petrole Irak", "IRQ", "ar", "energie"),
    _s("pmo.iq", "gouvernement", "Premier ministre Irak", "IRQ", "ar"),
    _s("noc.ly", "entreprise", "National Oil Corporation Libye", "LBY", "ar", "energie"),
    _s("aramco.com", "entreprise", "Saudi Aramco", "SAU", "en", "energie"),
    _s("pif.gov.sa", "agence_publique", "PIF Arabie Saoudite", "SAU", "en"),
    _s("adnoc.ae", "entreprise", "ADNOC", "ARE", "en", "energie"),
    # Europe de l'Est / Asie centrale
    _s("kmg.kz", "entreprise", "KazMunayGas", "KAZ", "ru", "energie"),
    _s("kmu.gov.ua", "gouvernement", "Gouvernement Ukraine", "UKR", "uk"),
    _s("me.gov.ua", "gouvernement", "Ministere de l'Economie Ukraine", "UKR", "uk"),
    # Amerique latine
    _s("pancanal.com", "agence_publique", "Autorite du Canal de Panama", "PAN", "es", "transport"),
    _s("ani.gov.co", "agence_publique", "ANI Colombie", "COL", "es", "transport"),
    _s("bndes.gov.br", "dfi", "BNDES", "BRA", "pt"),

    # ------------------------------------------------------ PRESSE ECONOMIQUE
    _s("reuters.com", "presse_economique", "Reuters"),
    _s("bloomberg.com", "presse_economique", "Bloomberg"),
    _s("ft.com", "presse_economique", "Financial Times"),
    _s("jeuneafrique.com", "presse_economique", "Jeune Afrique"),
    _s("agenceecofin.com", "presse_economique", "Agence Ecofin"),
    _s("africaintelligence.fr", "presse_economique", "Africa Intelligence"),
    _s("theafricareport.com", "presse_economique", "The Africa Report"),
    _s("businessday.ng", "presse_economique", "BusinessDay Nigeria", "NGA", "en"),
    _s("theeastafrican.co.ke", "presse_economique", "The EastAfrican", "", "en"),
    _s("zawya.com", "presse_economique", "Zawya", "", "en"),
    _s("meed.com", "presse_economique", "MEED", "", "en"),
    _s("valor.globo.com", "presse_economique", "Valor Economico", "BRA", "pt"),

    # ----------------------------------------------------- PRESSE SECTORIELLE
    _s("mining.com", "presse_sectorielle", "Mining.com", "", "en", "mines"),
    _s("miningweekly.com", "presse_sectorielle", "Mining Weekly", "", "en", "mines"),
    _s("upstreamonline.com", "presse_sectorielle", "Upstream", "", "en", "energie"),
    _s("lngprime.com", "presse_sectorielle", "LNG Prime", "", "en", "energie"),
    _s("offshore-energy.biz", "presse_sectorielle", "Offshore Energy", "", "en", "energie"),
    _s("porttechnology.org", "presse_sectorielle", "Port Technology", "", "en", "transport"),
    _s("railwaygazette.com", "presse_sectorielle", "Railway Gazette", "", "en", "transport"),
    _s("globalconstructionreview.com", "presse_sectorielle", "GCR", "", "en"),

    # ----------------------------------------------------- PRESSE GENERALISTE
    _s("bbc.com", "presse_generaliste", "BBC"),
    _s("rfi.fr", "presse_generaliste", "RFI"),
    _s("lemonde.fr", "presse_generaliste", "Le Monde"),
    _s("aljazeera.com", "presse_generaliste", "Al Jazeera"),
    _s("thecitizen.co.tz", "presse_generaliste", "The Citizen", "TZA", "en"),
    _s("guineenews.org", "presse_generaliste", "Guineenews", "GIN", "fr"),
]


# ===========================================================================
# 3. FIABILITE DES COLLECTEURS INTERNES (P1)
# ===========================================================================
# Les collecteurs DFI du depot produisent des signaux dont l'origine est
# certaine : pas de scraping de presse, c'est la publication de l'institution.
# On leur donne donc la fiabilite DFI, sans passer par la resolution d'URL.
COLLECTEURS = {
    "BM": {"type": "dfi", "libelle": "Banque Mondiale (collecteur)"},
    "BMP": {"type": "dfi", "libelle": "Banque Mondiale projets (collecteur)"},
    "IFC": {"type": "dfi", "libelle": "IFC (collecteur)"},
    "MIGA": {"type": "dfi", "libelle": "MIGA (collecteur)"},
    "AFDB": {"type": "dfi", "libelle": "AfDB (collecteur)"},
    "EBRD": {"type": "dfi", "libelle": "BERD (collecteur)"},
    "ADB": {"type": "dfi", "libelle": "ADB (collecteur)"},
    "ISDB": {"type": "dfi", "libelle": "IsDB (collecteur)"},
    "IDB": {"type": "dfi", "libelle": "IDB (collecteur)"},
    "PROPARCO": {"type": "dfi", "libelle": "Proparco / AFD (collecteur)"},
    "DFC": {"type": "dfi", "libelle": "DFC (collecteur)"},
    "UNGM": {"type": "agence_publique", "libelle": "UNGM (collecteur)"},
    "TED": {"type": "gouvernement", "libelle": "TED / UE (collecteur)"},
    "news": {"type": None, "libelle": "Presse (resolution par domaine)"},
}


# ===========================================================================
# 4. RESOLUTION
# ===========================================================================
_INDEX = None


def _index():
    """Index domaine -> source, construit une fois."""
    global _INDEX
    if _INDEX is None:
        _INDEX = {s["domaine"].lower(): s for s in SOURCES}
    return _INDEX


# Agregateurs : leur domaine ne dit RIEN de la source reelle. Un flux Google
# News renvoie tous ses liens sur news.google.com, si bien que dix medias
# distincts comptaient pour UNE SEULE source. Mesure du shadow run du
# 24/08/2026 : le projet Tanga Refinery reunissait 10 articles de 10 redactions
# differentes (TanzaniaInvest, CNBC Africa, The EastAfrican, African Energy,
# TRT Afrika, dailynews, thecitizen, ippmedia...) et restait plafonne a
# nb_sources=1, poids=0.40 -- donc INPROMOUVABLE quoi qu'il arrive.
AGREGATEURS = {"news.google.com", "google.com", "news.yahoo.com", "flipboard.com",
               "msn.com", "allafrica.com", "linkedin.com"}


def editeur_du_titre(titre):
    """Editeur reel d'un article Google News, extrait du suffixe du titre.
    Google formate ses titres 'Titre de l'article - Editeur'. Fonction PURE.
    Retourne '' si aucun suffixe plausible."""
    t = str(titre or "").strip()
    if " - " not in t:
        return ""
    editeur = t.rsplit(" - ", 1)[1].strip()
    # Un suffixe trop long ou ponctue n'est pas un nom de media.
    if not editeur or len(editeur) > 40 or editeur.endswith((".", "?", "!")):
        return ""
    return editeur


def _norm_editeur(nom):
    import unicodedata
    t = unicodedata.normalize("NFD", str(nom or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", t)


_INDEX_EDITEURS = None


def _index_editeurs():
    """Index nom d'editeur normalise -> source connue (libelle ou domaine)."""
    global _INDEX_EDITEURS
    if _INDEX_EDITEURS is None:
        _INDEX_EDITEURS = {}
        for s in SOURCES:
            _INDEX_EDITEURS.setdefault(_norm_editeur(s["libelle"]), s)
            racine = s["domaine"].split(".")[0]
            _INDEX_EDITEURS.setdefault(_norm_editeur(racine), s)
    return _INDEX_EDITEURS


def source_de_l_editeur(titre):
    """Source connue derriere le suffixe d'un titre agrege, ou None."""
    nom = editeur_du_titre(titre)
    if not nom:
        return None
    return _index_editeurs().get(_norm_editeur(nom))


def cle_source(signal):
    """Identite de la SOURCE d'un signal, pour le comptage des sources
    distinctes. Derriere un agregateur, c'est l'EDITEUR qui compte, pas
    l'agregateur. Fonction PURE."""
    origine = str(signal.get("source") or "").strip()
    if origine and origine.upper() in COLLECTEURS:
        return origine.upper()
    dom = domaine_du_lien(signal.get("lien", ""))
    if dom and dom not in AGREGATEURS:
        return dom
    editeur = editeur_du_titre(signal.get("titre", ""))
    return _norm_editeur(editeur) or dom


def domaine_du_lien(lien):
    """Domaine d'une URL, sans 'www.'. Fonction PURE."""
    m = re.search(r"https?://([^/]+)", str(lien or ""))
    if not m:
        return ""
    return m.group(1).lower().split(":")[0].replace("www.", "")


def source_du_lien(lien):
    """Source connue correspondant a une URL, ou None. Reconnait aussi les
    sous-domaines (data.worldbank.org -> worldbank.org). Fonction PURE."""
    dom = domaine_du_lien(lien)
    if not dom:
        return None
    idx = _index()
    if dom in idx:
        return idx[dom]
    parties = dom.split(".")
    for i in range(1, len(parties) - 1):
        candidat = ".".join(parties[i:])
        if candidat in idx:
            return idx[candidat]
    return None


def type_du_signal(signal):
    """Type de source d'un signal. Priorite au COLLECTEUR d'origine (certain),
    puis au domaine connu, puis defaut prudent. Fonction PURE."""
    origine = str(signal.get("source") or "").strip()
    info = COLLECTEURS.get(origine.upper()) or COLLECTEURS.get(origine)
    if info and info.get("type"):
        return info["type"]
    src = source_du_lien(signal.get("lien", ""))
    if src and domaine_du_lien(signal.get("lien", "")) not in AGREGATEURS:
        return src["type"]
    # Lien d'agregateur : c'est le suffixe du titre qui porte l'editeur reel.
    par_editeur = source_de_l_editeur(signal.get("titre", ""))
    if par_editeur:
        return par_editeur["type"]
    # Editeur inconnu mais IDENTIFIE : une redaction nommee vaut mieux qu'un
    # lien anonyme, sans pour autant valoir une source qualifiee.
    if editeur_du_titre(signal.get("titre", "")):
        return "presse_generaliste"
    return TYPE_PAR_DEFAUT


def fiabilite_du_signal(signal):
    """Poids de preuve d'un signal, entre 0 et 1. Fonction PURE."""
    return fiabilite_du_type(type_du_signal(signal))


def decrire_source(signal):
    """Description lisible de la source d'un signal (affichage, motifs)."""
    origine = str(signal.get("source") or "").strip()
    info = COLLECTEURS.get(origine.upper()) or COLLECTEURS.get(origine)
    if info and info.get("type"):
        return "{} ({})".format(info["libelle"], LIBELLE_TYPE[info["type"]])
    src = source_du_lien(signal.get("lien", ""))
    if src:
        return "{} ({})".format(src["libelle"], LIBELLE_TYPE[src["type"]])
    dom = domaine_du_lien(signal.get("lien", ""))
    return "{} ({})".format(dom or "source inconnue",
                            LIBELLE_TYPE[TYPE_PAR_DEFAUT])


def sources_du_pays(iso3, secteur=None):
    """Sources connues pour un pays (et un secteur). Sert a construire des
    requetes ciblees 'site:' et a documenter la couverture."""
    iso3 = str(iso3 or "").upper()
    out = [s for s in SOURCES if s["pays"] == iso3]
    if secteur:
        out = [s for s in out if not s["secteur"] or s["secteur"] == secteur]
    return out


def est_officielle(signal):
    """Source faisant AUTORITE : DFI, gouvernement, agence publique.
    C'est le critere qui autorise une promotion sur une source unique."""
    return type_du_signal(signal) in ("dfi", "gouvernement", "agence_publique")


def statistiques():
    """Couverture du registre (diagnostic)."""
    import collections
    par_type = collections.Counter(s["type"] for s in SOURCES)
    par_pays = collections.Counter(s["pays"] for s in SOURCES if s["pays"])
    return {"total": len(SOURCES), "par_type": dict(par_type),
            "pays_couverts": len(par_pays),
            "collecteurs": len([c for c in COLLECTEURS.values() if c.get("type")])}
