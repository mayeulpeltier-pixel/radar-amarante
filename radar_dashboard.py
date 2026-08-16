# -*- coding: utf-8 -*-
"""
Radar Amarante -- Generateur de tableau de bord
================================================

Lit les deux onglets du Google Sheet (ted_radar, bm_radar) et produit une page
web autonome (public/index.html), le "tableau de situation", destinee a etre
publiee sur Cloudflare Pages apres chaque run du radar.

PRINCIPE : ce script ne fait que LIRE le Sheet et ECRIRE un fichier HTML. Il ne
touche ni aux collecteurs, ni au Sheet. Il lit par NOM de colonne (get_all_records),
donc il reste correct meme si l'ordre des colonnes change.

ENV attendues (fournies par GitHub Actions ou une cellule Colab) :
  - TED_SHEET_ID                 (obligatoire)
  - GOOGLE_SERVICE_ACCOUNT_FILE  (obligatoire, meme compte de service que les collecteurs)
  - DASHBOARD_OUTPUT             (optionnel, defaut "public/index.html")

LANCEMENT :  python radar_dashboard.py
"""

import json
import os
import re
import sys
import unicodedata
import radar_retroaction
from datetime import date
try:
    import pays_reference          # referentiel pays officiel (eForms-SDK)
except Exception:
    pays_reference = None
try:
    import bm_attributions as _bm_dev   # table de change TAUX_USD (conversion valeur)
except Exception:
    _bm_dev = None
_RE_DEVISE = re.compile(r"\b([A-Z]{3})\b")

NOM_ONGLET_TED = "ted_radar"
NOM_ONGLET_BM = "bm_radar"

# ===========================================================================
# TRI "IMPORTANCE" : SCORE DOMINANT, FRAICHEUR EN NUANCE (02/08/2026)
# ===========================================================================
# Le tri par defaut ("Importance") classait par score final PUR : un lead
# detecte il y a 40 jours passait devant un lead d'hier a peine moins bien note.
# Or "en amont" veut dire frais. On introduit un RANG = score attenue par l'age,
# applique UNIQUEMENT au tri -- le score AFFICHE reste le score reel, et les
# onglets "Urgence" et "Recents" ne changent pas.
#
# DOCTRINE (identique au collecteur TED, cf. CLAUDE.md §7) : le SCORE domine, la
# fraicheur DEPARTAGE. Un lead frais mais faible ne double jamais un lead fort
# ancien ; un lead fort ancien perd juste assez de rang pour qu'un lead RECENT
# ET COMPARABLE repasse devant.
#
#   facteur(age) = PLANCHER + (1 - PLANCHER) * 0.5 ** (age / DEMIVIE)
#     age 0 j        -> 1.00           (frais : score plein)
#     age = DEMIVIE  -> a mi-chemin entre 1.00 et PLANCHER
#     age -> +inf    -> PLANCHER       (jamais 0 : un vieux lead fort reste fort)
#
# Defauts CONSERVATEURS (score tres dominant) : DEMIVIE=45 j, PLANCHER=0.85.
# Exemple : un 7.0 vaut 7.0 frais, ~6.5 a 40 j, ~6.4 a 60 j. Un 6.8 frais
# (rang 6.8) repasse donc devant un 7.0 vieux de 40 j (rang ~6.5), ce qui
# corrige le cas signale ("un lead ancien un peu mieux note enterrait un lead
# frais"). Mais un 5.8 frais (rang 5.8) reste DERRIERE ce meme 7.0 ancien, et un
# 9.0 ancien (rang ~8.4 a 60 j) reste tout en haut. Score dominant, fraicheur en
# nuance -- pas une bascule vers le tri par date (qui reste l'onglet "Recents").
#
# Reglable / desactivable sans toucher au code (motif ADB) :
#   RADAR_TRI_FRAICHEUR=0     -> desactive (tri "Importance" = score pur, comme avant)
#   RADAR_TRI_DEMIVIE_JOURS   -> vitesse d'attenuation (defaut 45 j ; plus petit = plus agressif)
#   RADAR_TRI_PLANCHER        -> part du score jamais perdue (defaut 0.85 ; plus bas = plus agressif)
RADAR_TRI_FRAICHEUR = os.environ.get("RADAR_TRI_FRAICHEUR", "1") != "0"
RADAR_TRI_DEMIVIE_JOURS = float(os.environ.get("RADAR_TRI_DEMIVIE_JOURS") or "45")
RADAR_TRI_PLANCHER = float(os.environ.get("RADAR_TRI_PLANCHER") or "0.85")

# ===========================================================================
# RESOLUTION PAYS -> (nom affiche, zone)
# BM stocke le NOM du pays, TED stocke un CODE ISO. On gere les deux.
# Les zones sont volontairement larges (lecture operationnelle, pas geographie fine).
# ===========================================================================
ZONE_PAR_NOM = {
    # Sahel
    "mali": ("Mali", "Sahel"), "niger": ("Niger", "Sahel"),
    "burkina faso": ("Burkina Faso", "Sahel"), "chad": ("Tchad", "Sahel"),
    "mauritania": ("Mauritanie", "Sahel"),
    # Afrique de l'Ouest (hors Sahel)
    "cote d'ivoire": ("Côte d'Ivoire", "Afrique de l'Ouest"),
    "côte d'ivoire": ("Côte d'Ivoire", "Afrique de l'Ouest"),
    "nigeria": ("Nigeria", "Afrique de l'Ouest"),
    "senegal": ("Sénégal", "Afrique de l'Ouest"), "ghana": ("Ghana", "Afrique de l'Ouest"),
    "benin": ("Bénin", "Afrique de l'Ouest"), "togo": ("Togo", "Afrique de l'Ouest"),
    "guinea": ("Guinée", "Afrique de l'Ouest"), "liberia": ("Libéria", "Afrique de l'Ouest"),
    # Afrique centrale
    "congo, democratic republic of": ("RDC", "Afrique centrale"),
    "democratic republic of congo": ("RDC", "Afrique centrale"),
    "congo, republic of": ("Congo-Brazzaville", "Afrique centrale"),
    "cameroon": ("Cameroun", "Afrique centrale"),
    "central african republic": ("Centrafrique", "Afrique centrale"),
    "chad ": ("Tchad", "Afrique centrale"), "gabon": ("Gabon", "Afrique centrale"),
    # Afrique de l'Est / Corne
    "ethiopia": ("Éthiopie", "Afrique de l'Est"), "kenya": ("Kenya", "Afrique de l'Est"),
    "uganda": ("Ouganda", "Afrique de l'Est"), "tanzania": ("Tanzanie", "Afrique de l'Est"),
    "somalia": ("Somalie", "Afrique de l'Est"),
    "somalia, federal republic of": ("Somalie", "Afrique de l'Est"),
    "south sudan": ("Soudan du Sud", "Afrique de l'Est"),
    "rwanda": ("Rwanda", "Afrique de l'Est"), "djibouti": ("Djibouti", "Afrique de l'Est"),
    # Afrique australe / Ocean Indien
    "mozambique": ("Mozambique", "Afrique australe"),
    "madagascar": ("Madagascar", "Afrique australe"),
    "south africa": ("Afrique du Sud", "Afrique australe"),
    "zambia": ("Zambie", "Afrique australe"), "zimbabwe": ("Zimbabwe", "Afrique australe"),
    "malawi": ("Malawi", "Afrique australe"), "angola": ("Angola", "Afrique australe"),
    "botswana": ("Botswana", "Afrique australe"),
    # Afrique du Nord
    "egypt, arab republic of": ("Égypte", "Afrique du Nord"),
    "egypt": ("Égypte", "Afrique du Nord"), "morocco": ("Maroc", "Afrique du Nord"),
    "tunisia": ("Tunisie", "Afrique du Nord"), "algeria": ("Algérie", "Afrique du Nord"),
    "libya": ("Libye", "Afrique du Nord"),
    # Proche / Moyen-Orient
    "west bank and gaza": ("Cisjordanie et Gaza", "Proche-Orient"),
    "jordan": ("Jordanie", "Proche-Orient"), "lebanon": ("Liban", "Proche-Orient"),
    "iraq": ("Irak", "Proche-Orient"), "yemen, republic of": ("Yémen", "Proche-Orient"),
    "yemen": ("Yémen", "Proche-Orient"),
    "turkiye": ("Turquie", "Proche-Orient"), "türkiye": ("Turquie", "Proche-Orient"),
    "turkey": ("Turquie", "Proche-Orient"),
    # Asie centrale
    "uzbekistan": ("Ouzbékistan", "Asie centrale"),
    "tajikistan": ("Tadjikistan", "Asie centrale"),
    "kyrgyz republic": ("Kirghizistan", "Asie centrale"),
    "kazakhstan": ("Kazakhstan", "Asie centrale"),
    # Asie du Sud / Sud-Est
    "bangladesh": ("Bangladesh", "Asie du Sud"), "pakistan": ("Pakistan", "Asie du Sud"),
    "india": ("Inde", "Asie du Sud"), "nepal": ("Népal", "Asie du Sud"),
    "indonesia": ("Indonésie", "Asie du Sud-Est"),
    "philippines": ("Philippines", "Asie du Sud-Est"),
    # Europe de l'Est / Balkans / Caucase
    "ukraine": ("Ukraine", "Europe de l'Est"), "moldova": ("Moldavie", "Europe de l'Est"),
    "albania": ("Albanie", "Balkans"), "north macedonia": ("Macédoine du Nord", "Balkans"),
    "serbia": ("Serbie", "Balkans"), "georgia": ("Géorgie", "Caucase"),
    "armenia": ("Arménie", "Caucase"), "azerbaijan": ("Azerbaïdjan", "Caucase"),
    # Amerique latine / Caraibes
    "haiti": ("Haïti", "Caraïbes"), "jamaica": ("Jamaïque", "Caraïbes"),
    "mexico": ("Mexique", "Amérique latine"), "ecuador": ("Équateur", "Amérique latine"),
    "brazil": ("Brésil", "Amérique latine"), "colombia": ("Colombie", "Amérique latine"),
    "el salvador": ("El Salvador", "Amérique latine"), "salvador": ("El Salvador", "Amérique latine"),
    "panama": ("Panama", "Amérique latine"), "nicaragua": ("Nicaragua", "Amérique latine"),
    # Europe de l'Ouest (TED, faible interet operationnel)
    "france": ("France", "Europe de l'Ouest"), "germany": ("Allemagne", "Europe de l'Ouest"),
    "denmark": ("Danemark", "Europe de l'Ouest"), "new caledonia": ("Nouvelle-Calédonie", "Outre-mer"),
    # -- Complement (audit juillet 2026) : mêmes pays que côté ISO3, mais par
    # NOM (la Banque Mondiale et ReliefWeb stockent le nom du pays, pas le code).
    "syria": ("Syrie", "Proche-Orient"), "syrian arab republic": ("Syrie", "Proche-Orient"),
    "iran": ("Iran", "Proche-Orient"), "iran, islamic republic of": ("Iran", "Proche-Orient"),
    "israel": ("Israël", "Proche-Orient"),
    "saudi arabia": ("Arabie Saoudite", "Péninsule arabique"),
    "united arab emirates": ("Émirats Arabes Unis", "Péninsule arabique"),
    "qatar": ("Qatar", "Péninsule arabique"), "kuwait": ("Koweït", "Péninsule arabique"),
    "bahrain": ("Bahreïn", "Péninsule arabique"), "oman": ("Oman", "Péninsule arabique"),
    "russia": ("Russie", "Europe de l'Est"), "russian federation": ("Russie", "Europe de l'Est"),
    "belarus": ("Biélorussie", "Europe de l'Est"),
    "argentina": ("Argentine", "Amérique latine"), "bolivia": ("Bolivie", "Amérique latine"),
    "chile": ("Chili", "Amérique latine"), "peru": ("Pérou", "Amérique latine"),
    "paraguay": ("Paraguay", "Amérique latine"), "uruguay": ("Uruguay", "Amérique latine"),
    "venezuela": ("Venezuela", "Amérique latine"),
    "venezuela, republica bolivariana de": ("Venezuela", "Amérique latine"),
    "guyana": ("Guyana", "Amérique latine"), "suriname": ("Suriname", "Amérique latine"),
    "trinidad and tobago": ("Trinité-et-Tobago", "Caraïbes"),
}

# TED stocke des codes ISO. Table code -> (nom FR, zone).
ZONE_PAR_ISO3 = {
    "MLI": ("Mali", "Sahel"), "NER": ("Niger", "Sahel"), "BFA": ("Burkina Faso", "Sahel"),
    "TCD": ("Tchad", "Sahel"), "MRT": ("Mauritanie", "Sahel"),
    "CIV": ("Côte d'Ivoire", "Afrique de l'Ouest"), "NGA": ("Nigeria", "Afrique de l'Ouest"),
    "SEN": ("Sénégal", "Afrique de l'Ouest"), "GHA": ("Ghana", "Afrique de l'Ouest"),
    "TGO": ("Togo", "Afrique de l'Ouest"), "BEN": ("Bénin", "Afrique de l'Ouest"),
    "COD": ("RDC", "Afrique centrale"), "COG": ("Congo-Brazzaville", "Afrique centrale"),
    "CMR": ("Cameroun", "Afrique centrale"), "CAF": ("Centrafrique", "Afrique centrale"),
    "GAB": ("Gabon", "Afrique centrale"),
    "ETH": ("Éthiopie", "Afrique de l'Est"), "KEN": ("Kenya", "Afrique de l'Est"),
    "UGA": ("Ouganda", "Afrique de l'Est"), "TZA": ("Tanzanie", "Afrique de l'Est"),
    "SOM": ("Somalie", "Afrique de l'Est"), "SSD": ("Soudan du Sud", "Afrique de l'Est"),
    "MOZ": ("Mozambique", "Afrique australe"), "MDG": ("Madagascar", "Afrique australe"),
    "ZAF": ("Afrique du Sud", "Afrique australe"), "AGO": ("Angola", "Afrique australe"),
    "ZMB": ("Zambie", "Afrique australe"), "BWA": ("Botswana", "Afrique australe"),
    "EGY": ("Égypte", "Afrique du Nord"), "MAR": ("Maroc", "Afrique du Nord"),
    "TUN": ("Tunisie", "Afrique du Nord"), "DZA": ("Algérie", "Afrique du Nord"),
    "LBY": ("Libye", "Afrique du Nord"),
    "PSE": ("Cisjordanie et Gaza", "Proche-Orient"), "JOR": ("Jordanie", "Proche-Orient"),
    "LBN": ("Liban", "Proche-Orient"), "IRQ": ("Irak", "Proche-Orient"),
    "YEM": ("Yémen", "Proche-Orient"), "TUR": ("Turquie", "Proche-Orient"),
    "UZB": ("Ouzbékistan", "Asie centrale"), "TJK": ("Tadjikistan", "Asie centrale"),
    "KGZ": ("Kirghizistan", "Asie centrale"), "KAZ": ("Kazakhstan", "Asie centrale"),
    "BGD": ("Bangladesh", "Asie du Sud"), "PAK": ("Pakistan", "Asie du Sud"),
    "IND": ("Inde", "Asie du Sud"), "IDN": ("Indonésie", "Asie du Sud-Est"),
    "UKR": ("Ukraine", "Europe de l'Est"), "MDA": ("Moldavie", "Europe de l'Est"),
    "ALB": ("Albanie", "Balkans"), "MKD": ("Macédoine du Nord", "Balkans"),
    "SRB": ("Serbie", "Balkans"), "GEO": ("Géorgie", "Caucase"),
    "HTI": ("Haïti", "Caraïbes"), "JAM": ("Jamaïque", "Caraïbes"),
    "MEX": ("Mexique", "Amérique latine"), "ECU": ("Équateur", "Amérique latine"),
    # Perimetre commercial elargi le 22/07/2026 (Amerique centrale + Mongolie).
    "HND": ("Honduras", "Amérique latine"), "GTM": ("Guatemala", "Amérique latine"),
    "SLV": ("El Salvador", "Amérique latine"), "PAN": ("Panama", "Amérique latine"),
    "NIC": ("Nicaragua", "Amérique latine"),
    "MNG": ("Mongolie", "Asie centrale"),
    "BRA": ("Brésil", "Amérique latine"), "OMN": ("Oman", "Péninsule arabique"),
    "FRA": ("France", "Europe de l'Ouest"), "DEU": ("Allemagne", "Europe de l'Ouest"),
    "DNK": ("Danemark", "Europe de l'Ouest"), "NCL": ("Nouvelle-Calédonie", "Outre-mer"),
    # -- Complement bailleurs (AfDB, ADB, EBRD) : couverture des pays suivis --
    "RWA": ("Rwanda", "Afrique de l'Est"), "BDI": ("Burundi", "Afrique de l'Est"),
    "DJI": ("Djibouti", "Afrique de l'Est"), "ERI": ("Érythrée", "Afrique de l'Est"),
    "SDN": ("Soudan", "Afrique de l'Est"), "COM": ("Comores", "Afrique de l'Est"),
    "SYC": ("Seychelles", "Afrique de l'Est"),
    "GIN": ("Guinée", "Afrique de l'Ouest"), "GMB": ("Gambie", "Afrique de l'Ouest"),
    "GNB": ("Guinée-Bissau", "Afrique de l'Ouest"), "LBR": ("Liberia", "Afrique de l'Ouest"),
    "SLE": ("Sierra Leone", "Afrique de l'Ouest"), "CPV": ("Cap-Vert", "Afrique de l'Ouest"),
    "GNQ": ("Guinée équatoriale", "Afrique centrale"), "STP": ("Sao Tomé-et-Principe", "Afrique centrale"),
    "MWI": ("Malawi", "Afrique australe"), "NAM": ("Namibie", "Afrique australe"),
    "LSO": ("Lesotho", "Afrique australe"), "SWZ": ("Eswatini", "Afrique australe"),
    "ZWE": ("Zimbabwe", "Afrique australe"), "MUS": ("Maurice", "Afrique australe"),
    "AFG": ("Afghanistan", "Asie du Sud"), "NPL": ("Népal", "Asie du Sud"),
    "LKA": ("Sri Lanka", "Asie du Sud"),
    "MMR": ("Myanmar", "Asie du Sud-Est"), "KHM": ("Cambodge", "Asie du Sud-Est"),
    "LAO": ("Laos", "Asie du Sud-Est"), "PHL": ("Philippines", "Asie du Sud-Est"),
    "TKM": ("Turkménistan", "Asie centrale"),
    "ARM": ("Arménie", "Caucase"), "AZE": ("Azerbaïdjan", "Caucase"),
    "BIH": ("Bosnie-Herzégovine", "Balkans"), "MNE": ("Monténégro", "Balkans"),
    "XKX": ("Kosovo", "Balkans"),
    "PNG": ("Papouasie-Nouvelle-Guinée", "Pacifique"), "FJI": ("Fidji", "Pacifique"),
    "SLB": ("Îles Salomon", "Pacifique"), "VUT": ("Vanuatu", "Pacifique"),
    # -- Complement (audit juillet 2026) : pays suivis par le coeur TED mais
    # absents de cette carte, qui tombaient donc en "Non classe" au dashboard
    # (zone faible, pas de point carte, mauvais tri). On les rattache a leur
    # zone reelle. Golfe -> "Peninsule arabique" (comme Oman, deja present).
    "SYR": ("Syrie", "Proche-Orient"), "IRN": ("Iran", "Proche-Orient"),
    "ISR": ("Israël", "Proche-Orient"),
    "SAU": ("Arabie Saoudite", "Péninsule arabique"),
    "ARE": ("Émirats Arabes Unis", "Péninsule arabique"),
    "QAT": ("Qatar", "Péninsule arabique"), "KWT": ("Koweït", "Péninsule arabique"),
    "BHR": ("Bahreïn", "Péninsule arabique"),
    "RUS": ("Russie", "Europe de l'Est"), "BLR": ("Biélorussie", "Europe de l'Est"),
    "GUF": ("Guyane", "Outre-mer"), "MYT": ("Mayotte", "Outre-mer"),
    "ARG": ("Argentine", "Amérique latine"), "BOL": ("Bolivie", "Amérique latine"),
    "CHL": ("Chili", "Amérique latine"), "COL": ("Colombie", "Amérique latine"),
    "PER": ("Pérou", "Amérique latine"), "PRY": ("Paraguay", "Amérique latine"),
    "URY": ("Uruguay", "Amérique latine"), "VEN": ("Venezuela", "Amérique latine"),
    "GUY": ("Guyana", "Amérique latine"), "SUR": ("Suriname", "Amérique latine"),
    "TTO": ("Trinité-et-Tobago", "Caraïbes"),
}

# Ordre d'affichage des zones (les autres suivent, "Non classé" en dernier)
ORDRE_ZONES = [
    "Afrique de l'Ouest", "Sahel", "Afrique centrale", "Afrique de l'Est",
    "Afrique australe", "Afrique du Nord", "Proche-Orient", "Péninsule arabique",
    "Asie centrale", "Asie du Sud", "Asie du Sud-Est", "Pacifique", "Caucase", "Balkans",
    "Europe de l'Est", "Caraïbes", "Amérique latine", "Europe de l'Ouest",
    "Outre-mer", "Non classé",
]


def resoudre_pays(brut, source):
    """Renvoie (nom_affiche, zone) a partir du champ pays_execution."""
    brut = _txt(brut)
    if not brut:
        return ("Pays non précisé", "Non classé")
    if source in ("TED", "AFDB", "ADB", "EBRD", "UNGM", "IDB", "BMP", "PROPARCO", "DFC"):
        # Sources ISO : pays_execution stocke en code ISO3 (scoring direct).
        code = brut.split(",")[0].strip().upper()
        if code in ZONE_PAR_ISO3:
            return ZONE_PAR_ISO3[code]
        return (code or "Pays non précisé", "Non classé")
    # BM / RW : nom lisible
    nom = brut.split(",")[0].strip() if brut.lower() not in ZONE_PAR_NOM else brut
    cle = brut.lower().strip()
    if cle in ZONE_PAR_NOM:
        return ZONE_PAR_NOM[cle]
    cle2 = nom.lower().strip()
    if cle2 in ZONE_PAR_NOM:
        return ZONE_PAR_NOM[cle2]
    # Fallback referentiel officiel (eForms-SDK) : un nom mal orthographie
    # (accents, "Iraq" vs "Irak"...) est resolu en ISO3, puis rattache a sa
    # zone si elle est connue. Ne s'active QUE sur les noms non deja mappes,
    # donc n'altere aucun cas existant.
    if pays_reference is not None:
        try:
            iso3 = pays_reference.resoudre(brut)
        except Exception:
            iso3 = None
        if iso3 and iso3 in ZONE_PAR_ISO3:
            return ZONE_PAR_ISO3[iso3]
    return (nom or brut, "Non classé")


def _txt(v):
    """Cellule -> texte propre. gspread peut renvoyer des int/float (ex. un
    numero de telephone ou un score), donc on force en str avant tout .strip()."""
    if v is None:
        return ""
    return str(v).strip()


def _vrai(v):
    """Interprete une valeur de cellule comme booleen (divergence, securite)."""
    return _txt(v).lower() in ("true", "vrai", "1", "oui", "yes")


def _num(v, defaut=0.0):
    try:
        return float(str(v).replace(",", ".").strip())
    except (ValueError, AttributeError):
        return defaut


_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def _mois_depuis_date(s):
    """'2026-06-28' -> ('2026-06', 'juin 2026'). Gere aussi JJ/MM/AAAA.
    Renvoie ('', 'Sans date') si illisible (l'avis ira dans un groupe a part)."""
    import re
    s = _txt(s)
    m = re.match(r"(\d{4})-(\d{2})", s)
    if m:
        annee, mois = m.group(1), int(m.group(2))
    else:
        m2 = re.match(r"\d{2}/(\d{2})/(\d{4})", s)
        if m2:
            annee, mois = m2.group(2), int(m2.group(1))
        else:
            return ("", "Sans date")
    if 1 <= mois <= 12:
        return ("{}-{:02d}".format(annee, mois), "{} {}".format(_MOIS_FR[mois], annee))
    return ("", "Sans date")


def _age_jours(date_str, aujourdhui=None):
    """Age en jours d'une date de detection (ISO AAAA-MM-JJ ou JJ/MM/AAAA).
    None si illisible. Jamais negatif (une date future compte comme age 0)."""
    from datetime import date as _date
    import re
    s = _txt(date_str)
    an = mo = jo = None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        an, mo, jo = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m2 = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
        if m2:
            jo, mo, an = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
    if an is None:
        return None
    try:
        d = _date(an, mo, jo)
    except ValueError:
        return None
    ref = aujourdhui or _date.today()
    return max(0, (ref - d).days)


def rang_tri(final, date_det, aujourdhui=None):
    """Cle de tri "Importance" : le score attenue par l'age de detection.

    N'affecte QUE l'ordre d'affichage, JAMAIS le score montre a l'ecran (qui
    reste `final`). Le facteur est borne par PLANCHER : un lead ancien mais fort
    ne peut pas tomber sous PLANCHER x son score, donc il ne se fait doubler que
    par un lead RECENT ET COMPARABLE, jamais par un lead frais mais faible.

    Robuste : fraicheur desactivee (RADAR_TRI_FRAICHEUR=0) ou date illisible ->
    renvoie le score brut, aucune penalite injustifiee. Score non numerique ->
    0.0 (le lead ira en fin de liste, comme un score nul)."""
    try:
        base = float(final)
    except (TypeError, ValueError):
        return 0.0
    if not RADAR_TRI_FRAICHEUR:
        return base
    age = _age_jours(date_det, aujourdhui)
    if age is None:
        return base
    demivie = RADAR_TRI_DEMIVIE_JOURS if RADAR_TRI_DEMIVIE_JOURS > 0 else 30.0
    facteur = RADAR_TRI_PLANCHER + (1.0 - RADAR_TRI_PLANCHER) * (0.5 ** (age / demivie))
    return round(base * facteur, 3)


# ===========================================================================
# OBSERVABILITE DE RUN : ETAT DU DERNIER RUN PAR SOURCE (02/08/2026)
# ===========================================================================
# Quand le digest est tombe en silence (Niveau 0), rien dans la surface ne le
# montrait. Ce bandeau derive, POUR CHAQUE SOURCE, son volume et la fraicheur de
# son plus recent lead -- a partir des leads DEJA construits (aucune lecture ni
# stockage supplementaire). Une source qui se tait (0 lead, ou plus rien de
# recent) devient visible d'un coup d'oeil. Ce n'est pas un diagnostic de panne,
# c'est un "coup d'oeil sante" : mieux vaut un "a verifier" injustifie qu'une
# mort silencieuse.
#
# Seuils reglables (motif ADB) : au-dela de CALME_JOURS sans rien de neuf, la
# source est marquee "ancien" (a regarder). FRAIS_JOURS = produit ce cycle-ci.
SANTE_FRAIS_JOURS = int(os.environ.get("RADAR_SANTE_FRAIS_JOURS") or "4")
SANTE_CALME_JOURS = int(os.environ.get("RADAR_SANTE_CALME_JOURS") or "14")

# Sources attendues, dans l'ordre d'affichage. Une source de cette liste ABSENTE
# des leads s'affiche a 0 (c'est ainsi qu'une source morte se voit).
CATALOGUE_SOURCES = ("TED", "BM", "AFDB", "ADB", "EBRD", "UNGM", "RW",
                     "MIGA", "IFC", "PROPARCO", "DFC", "IDB", "BMP", "ATTRIB", "PRIVÉ")


def sante_run(leads, aujourdhui=None):
    """Etat du dernier run par source, derive des leads deja construits.

    Pour chaque source : volume (n) et age du plus RECENT lead (freshest). Etat :
      - "frais"   : quelque chose de neuf ce cycle (age <= FRAIS_JOURS)
      - "calme"   : rien de tres recent mais raisonnable (<= CALME_JOURS)
      - "ancien"  : plus rien de recent (> CALME_JOURS) -> a verifier
      - "absent"  : aucun lead (source desactivee, ou tombee en panne)
    Robuste aux formats de date mixtes (age calcule lead par lead, pas de
    comparaison de chaines). Ne leve jamais."""
    from datetime import date as _date
    ref = aujourdhui or _date.today()
    par_src = {}
    for l in (leads or []):
        s = l.get("src") or "?"
        d = par_src.setdefault(s, {"n": 0, "age": None})
        d["n"] += 1
        a = _age_jours(l.get("date_det") or "", ref)
        if a is not None and (d["age"] is None or a < d["age"]):
            d["age"] = a
    ordre = list(CATALOGUE_SOURCES) + [s for s in par_src if s not in CATALOGUE_SOURCES]
    lignes = []
    for s in ordre:
        d = par_src.get(s)
        if not d or d["n"] == 0:
            lignes.append({"src": s, "n": 0, "age": None, "etat": "absent"})
            continue
        age = d["age"]
        if age is None:
            etat = "calme"                 # des leads, mais aucune date lisible
        elif age <= SANTE_FRAIS_JOURS:
            etat = "frais"
        elif age <= SANTE_CALME_JOURS:
            etat = "calme"
        else:
            etat = "ancien"
        lignes.append({"src": s, "n": d["n"], "age": age, "etat": etat})
    a_verifier = sum(1 for x in lignes if x["etat"] in ("ancien", "absent"))
    actives = sum(1 for x in lignes if x["n"] > 0)
    return {"date": ref.strftime("%d/%m/%Y"), "sources": lignes,
            "actives": actives, "a_verifier": a_verifier}


# ===========================================================================
# TAXONOMIE SECTEUR CANONIQUE (10/08/2026)
# ===========================================================================
# Le systeme portait DEUX vocabulaires secteur incompatibles : les libelles CPV
# des attributions (« BTP / construction », « Energie / petrole-gaz »...) et la
# watchlist en texte libre (« luxe », « oil & gas »...). Sans normalisation, le
# filtre secteur affichait vingt libelles disjoints et ne regroupait pas la meme
# activite. On replie tout sur 10 secteurs canoniques via des mots-cles. C'est la
# SOURCE DE VERITE : chaque lead porte `sect` (precalcule ici) et le JS ne
# reclassifie pas -- il lit la valeur. « Secteur estime » cote avis : le libelle
# est deduit du titre, moins fiable qu'un code CPV, l'UI l'indique.
SECTEURS_CANONIQUES = [
    "Défense", "BTP / Construction", "Énergie & Oil-Gas", "Mines / Matériaux",
    "Ingénierie / Études", "Transport / Logistique", "Télécom / IT",
    "Luxe", "Agro", "Autre",
]
# Ordre = PRIORITE de classement : le premier secteur dont un mot-cle est present
# gagne. « Défense » d'abord (signal metier fort), « Autre » implicite en repli.
_SECTEUR_MOTS = [
    ("Défense", ("defense", "defence", "armement", "arme", "military",
                 "militaire", "naval", "armee", "gendarmerie", "dga", "otan",
                 "nato", "securite", "security")),
    ("BTP / Construction", ("btp", "construction", "travaux", "works", "genie",
                            "batiment", "building", "route", "road", "barrage",
                            "dam", "pont", "bridge", "infrastructure", "chantier",
                            "rehabilitation", "voirie")),
    ("Énergie & Oil-Gas", ("energie", "energy", "petrol", "petrole", "oil",
                           "gas", "gaz", "utilities", "power", "electr",
                           "hydro", "solaire", "solar", "eolien", "eau",
                           "water", "pipeline", "raffinerie")),
    ("Mines / Matériaux", ("mine", "mines", "mining", "materiaux", "materials",
                           "extract", "carriere", "metal", "cuivre", "or ",
                           "lithium", "cobalt", "ciment")),
    ("Ingénierie / Études", ("ingenierie", "ingenieur", "engineering", "etudes",
                             "study", "conseil", "consulting", "assistance technique",
                             "maitrise d")),
    ("Transport / Logistique", ("transport", "logistique", "logistics",
                                "vehicule", "fleet", "port ", "aeroport",
                                "airport", "rail", "ferroviaire", "supply chain",
                                "fret", "cargo")),
    ("Télécom / IT", ("telecom", "telecommunication", "reseau", "network",
                      "numerique", "digital", "informatique", "logiciel",
                      "software", "data", "fibre", "satellite", "it ")),
    ("Luxe", ("luxe", "luxury", "mode", "cosmetique", "joaillerie",
              "maroquinerie", "parfum", "vin", "spiritueux", "hotellerie",
              "haute couture")),
    ("Agro", ("agro", "agri", "agricole", "food", "alimentaire", "farming",
              "plantation", "elevage", "peche", "cacao", "coton")),
]


def secteur_canonique(*textes):
    """Replie un ou plusieurs textes libres (libelle CPV, secteur watchlist,
    titre d'avis, activite) sur UN secteur canonique. Repli « Autre ».
    Deterministe, sans LLM : mots-cles, premier match dans l'ordre de priorite."""
    t = " " + " ".join(
        unicodedata.normalize("NFD", str(x or "").lower())
        .encode("ascii", "ignore").decode("ascii")
        for x in textes if x) + " "
    for sec, mots in _SECTEUR_MOTS:
        if any(m in t for m in mots):
            return sec
    return "Autre"


def _secteur_du_lead(row, source, groupe):
    """Secteur canonique d'un lead, selon sa source (fiabilite decroissante) :
      - ATTRIB : libelle CPV (`secteur`), fiable ;
      - IDB    : `secteur_idb` fourni par le collecteur, fiable ;
      - PRIVÉ  : secteur de l'entreprise si present, sinon activite ;
      - avis   : deduit du titre + groupe (ESTIME, l'UI l'indique)."""
    if source == "ATTRIB":
        return secteur_canonique(_txt(row.get("secteur")) or groupe)
    if source == "IDB":
        return secteur_canonique(_txt(row.get("secteur_idb")),
                                 _txt(row.get("titre")), groupe)
    if source == "PRIVÉ":
        return secteur_canonique(_txt(row.get("secteur")),
                                 _txt(row.get("type_activite")),
                                 _txt(row.get("titre")))
    return secteur_canonique(_txt(row.get("titre")), groupe)


def ligne_vers_lead(row, source):
    """Transforme une ligne de Sheet (dict par nom de colonne) en lead unifie.
    Toutes les cellules passent par _txt() : gspread peut renvoyer des nombres
    (telephone, score, identifiant) la ou on attend du texte."""
    pays_brut = _txt(row.get("pays_execution"))
    nom_pays, zone = resoudre_pays(pays_brut, source)
    if source == "PRIVÉ":
        zone = _txt(row.get("zone")) or zone
    action = _txt(row.get("action_recommandee")).lower()

    if source == "BM":
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Titulaire du marché qui déploie les équipes, pas l'agence acheteuse."
        groupe = _txt(row.get("procurement_group")) or "n.c."
    elif source == "PRIVÉ":
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Contact via réseau : direction sûreté / export / MCO."
        groupe = _txt(row.get("type_activite")) or "signal"
    elif source == "RW":
        # ReliefWeb : l'organisation qui recrute EST le déployeur (cible directe).
        # cible_commerciale_reelle est déjà nuancée côté collecteur (marché ONU
        # souvent verrouillé). Groupe = catégorie de poste (field, logistics...).
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Organisation qui recrute et déploie : viser direction sûreté / logistique / RH terrain."
        groupe = _txt(row.get("categorie")) or "humanitaire"
    elif source in ("MIGA", "IFC", "PROPARCO", "DFC"):
        # Vague 2 : investisseur prive / entreprise projet qui deploie en
        # zone a risque (deja formule par le collecteur). Groupe = type de
        # document (SPI/ESRS = pre-board, signal precoce).
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Investisseur prive / entreprise projet qui deploie cadres et actifs en zone a risque."
        groupe = _txt(row.get("type_document")) or "divulgation"
    elif source in ("AFDB", "ADB", "EBRD", "UNGM", "IDB", "BMP"):
        # Bailleurs multilatéraux (Afrique / Asie / Ukraine-Caucase /
        # Amérique latine pour IDB). Le
        # collecteur remplit déjà cible_commerciale_reelle (pour EBRD, le client
        # maître d'ouvrage). Groupe = type de notice (GPN, prequalif, tender...).
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Bureau ou consortium titulaire qui déploiera en zone à risque, pas le bailleur."
        groupe = _txt(row.get("type_notice")) or _txt(row.get("phase")) or "avis"
    else:
        cible = "Bureau ou consortium titulaire du marché, pas le bailleur."
        groupe = "AT"

    # Periode : mois de PREMIERE detection (date_detection ne change jamais ;
    # a defaut, date de derniere maj). Permet de classer les leads par mois.
    date_det = _txt(row.get("date_detection")) or _txt(row.get("date_maj"))
    mois_cle, mois_label = _mois_depuis_date(date_det)
    # Statut de suivi (CRM) : ce que tu ecris a la main dans le Sheet.
    statut = _txt(row.get("statut_suivi")) or "nouveau"

    return {
        "src": source,
        "pays": nom_pays,
        "zone": zone,
        "titre": _txt(row.get("titre")),
        "agence": _txt(row.get("acheteur")) or "n.c.",
        "final": round(_num(row.get("score_final")), 1),
        "surete": round(_num(row.get("score_surete")), 1),
        "comm": round(_num(row.get("score_commercial")), 1),
        "action": action or "n.c.",
        "win": _txt(row.get("fenetre_action")) or "indetermine",
        "nom": _txt(row.get("contact_name")) or "n.c.",
        "email": _txt(row.get("contact_email")) or "n.c.",
        "tel": _txt(row.get("contact_phone")) or "n.c.",
        "cible": cible,
        "justif": _txt(row.get("justification")),
        "grp": groupe,
        "lien": _txt(row.get("lien_avis")),
        "ecart": _vrai(row.get("divergence")),
        "secu": _vrai(row.get("securite_existante_detectee")),
        "mois": mois_cle,
        "mois_label": mois_label,
        "date_det": date_det,
        "statut": statut,
        "motif_ecart": _txt(row.get("motif_ecart")),
        "deadline": _txt(row.get("deadline")),
        "conf": _txt(row.get("confiance")),
        "modele": _txt(row.get("modele")),
        "pub": _txt(row.get("publication_number")),
        # Nom de l'entreprise cible (pour la lentille Entreprises). Cote PRIVÉ,
        # c'est l'entreprise surveillee ; ailleurs le gagnant est inconnu.
        "entreprise": (_txt(row.get("acheteur")) or "n.c.") if source in ("PRIVÉ", "IFC", "MIGA", "PROPARCO", "DFC") else "",
        # Secteur canonique (filtre + regroupement). Estime cote avis.
        "sect": _secteur_du_lead(row, source, groupe),
    }


RISQUE_ZONE = {
    "Sahel": 5.0, "Corne de l'Afrique": 5.0, "Afrique de l'Est": 3.5,
    "Afrique de l'Ouest": 4.0, "Afrique centrale": 4.5, "Afrique australe": 2.5,
    "Afrique du Nord": 4.0, "Proche-Orient": 5.0, "Moyen-Orient": 5.0,
    "Péninsule arabique": 3.0,
    "Asie centrale": 4.0, "Asie du Sud": 4.0, "Asie du Sud-Est": 3.0,
    "Caucase": 4.0, "Balkans": 3.0, "Europe de l'Est": 4.0, "Pacifique": 2.5,
    "Amérique latine": 2.5, "Caraïbes": 2.5, "Europe de l'Ouest": 1.0,
    "Outre-mer": 1.5, "Non classé": 1.5,
}
_MOTS_SECTEUR_FORT = ("construction", "travaux", "works", "génie", "route",
    "road", "bâtiment", "infrastructure", "énergie", "energy", "power",
    "électr", "pétrol", "petrol", "oil", "gas", "gaz", "mines", "mining",
    "extract", "défense", "defence", "defense", "sécurit", "security",
    "aéroport", "airport", "port", "barrage", "dam", "pipeline")


def _facteur_eur(txt):
    """Facteur de conversion vers EUR de la devise detectee dans le texte.

    Derive de bm_attributions.TAUX_USD (unites par USD) :
        taux_eur(devise) = TAUX_USD["EUR"] / TAUX_USD[devise].
    Renvoie 1.0 si EUR, si aucune devise connue n'est reperee, ou si la table
    est indisponible -> le montant est laisse inchange (comportement d'avant).

    POURQUOI : le mini-score pondere par la valeur. Les attributions TED
    restaient en devise LOCALE (seule la Banque Mondiale convertissait deja),
    si bien qu'un marche de 50 000 000 XOF (~76 000 EUR, franc CFA du Sahel)
    etait pese comme un marche de 50 M. On normalise donc tout en EUR ici, au
    point de scoring commun. Pivot EUR : les marches TED en euros (la majorite)
    restent inchanges, seules les devises etrangeres sont converties.
    """
    if _bm_dev is None:
        return 1.0
    taux = getattr(_bm_dev, "TAUX_USD", None) or {}
    ref = taux.get("EUR")
    if not ref:
        return 1.0
    for m in _RE_DEVISE.finditer(str(txt or "").upper()):
        d = taux.get(m.group(1))
        if d:
            return ref / d
    return 1.0


def _valeur_en_millions(txt):
    """Extrait un montant en millions depuis un texte libre (best-effort),
    NORMALISE EN EUR selon la devise detectee (voir _facteur_eur)."""
    if not txt:
        return 0.0
    t = txt.replace("\u202f", "").replace("\u00a0", "").replace(" ", "")
    t = t.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return 0.0
    val = float(m.group(1))
    bas = txt.lower()
    if "milliard" in bas or "billion" in bas or "bn" in bas:
        val *= 1000.0
    elif "million" in bas or "mn" in bas or (val > 100000):
        val = val / 1_000_000.0 if val > 100000 else val
    return val * _facteur_eur(txt)


def score_attribution(zone, secteur, marche, valeur):
    """Mini-score DETERMINISTE (sans LLM) d'une attribution, sur 10.
    = risque de la zone (0-5) + pertinence du secteur (0-3) + poids de la
    valeur du marché (0-2). Donne un vrai tri : gros marché de travaux en
    zone rouge devant petites fournitures en zone stable."""
    base = RISQUE_ZONE.get(zone, 1.5)
    texte = (secteur + " " + marche).lower()
    sect = 3.0 if any(m in texte for m in _MOTS_SECTEUR_FORT) else 1.0
    v = _valeur_en_millions(valeur)
    if v >= 20:
        poids_v = 2.0
    elif v >= 5:
        poids_v = 1.5
    elif v >= 1:
        poids_v = 1.0
    else:
        poids_v = 0.5
    return round(min(10.0, base + sect + poids_v), 1)


def attribution_vers_lead(row):
    """Transforme une ligne 'attributions_radar' (titulaire d'un marche gagne)
    en lead ATTRIB. Score DETERMINISTE (zone x secteur x valeur), PAS une
    analyse surete LLM. Filtrable via l'onglet 'Titulaires'."""
    gagnant = _txt(row.get("gagnant"))
    secteur = _txt(row.get("secteur")) or "Attribution"
    # Attributions issues de TED : pays_execution en code ISO3 -> mode ISO.
    nom_pays, zone = resoudre_pays(_txt(row.get("pays_execution")), "TED")
    valeur = _txt(row.get("valeur_attribuee"))
    marche = _txt(row.get("titre"))
    date_det = _txt(row.get("date_detection")) or _txt(row.get("date_maj"))
    mois_cle, mois_label = _mois_depuis_date(date_det)
    score = score_attribution(zone, secteur, marche, valeur)
    justif = ("Titulaire d'un marché gagné ({}){}. Marché : {}. "
              "Prospect à démarcher (déploie du personnel). "
              "Score = risque zone + secteur + valeur (indicatif, pas une analyse sûreté).").format(
        nom_pays, " · " + valeur if valeur else "", marche or "n.c.")
    # Socle DETERMINISTE du titulaire, calcule a la collecte (chantier B,
    # 23/07/2026). Present meme quand l'analyse LLM n'a pas encore tourne :
    # une attribution non analysee affiche deja son origine et le drapeau
    # etranger. superposer_analyse_attribution ecrasera par la valeur du
    # modele quand elle existe.
    origine_det = _txt(row.get("pays_titulaire"))
    etranger_det = _txt(row.get("titulaire_etranger")).lower() in ("oui", "true", "1", "vrai")
    justif_socle = justif
    if origine_det:
        justif_socle = "Titulaire {} ({}). {}".format(
            origine_det,
            "ETRANGER au pays d'exécution" if etranger_det else "local",
            justif)
    return {
        "src": "ATTRIB", "pays": nom_pays, "zone": zone,
        "titre": (gagnant or "Titulaire") + (" · " + secteur if secteur else ""),
        "agence": _txt(row.get("acheteur")) or "n.c.",
        "final": score, "surete": score, "comm": score,
        "action": "surveiller", "win": "indetermine",
        "nom": "n.c.", "email": "n.c.", "tel": "n.c.",
        "cible": ("Titulaire du marché : entreprise qui déploie du personnel en "
                  "zone à risque. À démarcher (direction sûreté / opérations)."),
        "justif": justif_socle, "grp": secteur, "lien": _txt(row.get("lien")),
        "valeur": valeur,
        # Renouvellement (chantier renouvellement) : fin de contrat estimee +
        # alerte, calcules a la collecte via SPARQL. Vides si non disponibles.
        "fin_contrat": _txt(row.get("fin_contrat")),
        "mois_avant_fin": row.get("mois_avant_fin", ""),
        "statut_renouv": _txt(row.get("statut_renouv")),
        # Champs exposes a la lentille Titulaires (badge etranger, filtre),
        # renseignes des la collecte. L'analyse LLM les affinera si elle existe.
        "origine": origine_det, "etranger_titulaire": etranger_det,
        "nature_deploiement": "", "besoin_surete": "", "interlocuteur": "",
        "analysee": False,
        "ecart": False, "secu": False, "mois": mois_cle, "mois_label": mois_label,
        "date_det": date_det, "statut": _txt(row.get("statut_prospection")) or "nouveau",
        "motif_ecart": _txt(row.get("motif_ecart")),
        "deadline": "", "conf": "", "modele": "",
        "pub": _txt(row.get("publication_number")),
        "entreprise": gagnant or "Titulaire",
        "sect": secteur_canonique(secteur, marche),
    }


def _norm_ent(s):
    """Cle canonique d'un nom d'entreprise -- SOURCE DE VERITE UNIQUE de la
    resolution d'entite : fusion transverse (watchlist / signaux prives /
    titulaires dans la fiche 360) ET rattachement de l'enrichissement passent par
    elle. Minuscules, sans accents, separateurs unifies, connecteurs et formes
    juridiques (abregees OU en toutes lettres : ltd = limited, corp = corporation)
    retires. Le JS ne recalcule plus cette cle : il lit `ent_cle`, precalcule ici
    (voir construire_leads) ; `normEntreprise` cote JS n'est qu'un repli pour
    d'anciennes pages en cache et reste le miroir exact de cette fonction."""
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("&", " ")
    s = s.replace(".", "")                       # S.A. -> SA (forme pointee captee)
    s = re.sub(r"[,'()\-/]", " ", s)
    s = re.sub(r"^\s*the\s+", " ", s)
    s = re.sub(r"\b(sa|sas|sarl|sasu|eurl|spa|srl|gmbh|ltd|ltda|limited|llc|llp|inc|"
               r"incorporated|plc|pvt|bv|nv|ag|co|company|companies|corp|corporation|"
               r"group|groupe|holding|international|intl|and|et)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _nom_entreprise(lead):
    """Nom retenu pour l'identite d'un lead : l'entreprise si connue, sinon
    l'acheteur/agence. MIROIR EXACT de la selection cote JS, pour que la cle
    Python (ent_cle) et le regroupement des fiches coincident au caractere pres."""
    e = _txt(lead.get("entreprise"))
    if e and e != "n.c.":
        return e
    return _txt(lead.get("agence"))


def _attacher_enrichissement(lead, enrichissement, cle_entreprise):
    """Rattache dirigeant / email pro / SIREN / CA a un lead, depuis la table
    d'enrichissement. Essaie la clef exacte (minuscules), puis une clef
    normalisee (formes juridiques retirees). No-op si rien ne matche."""
    info = enrichissement.get((cle_entreprise or "").lower()) \
        or enrichissement.get(_norm_ent(cle_entreprise))
    if not info:
        return
    dirigeant = _txt(info.get("dirigeant_principal"))
    if dirigeant and lead["nom"] in ("", "n.c."):
        lead["nom"] = dirigeant
    email = _txt(info.get("email_pro"))
    if email:
        lead["email"] = email           # contact Hunter -> pre-remplit le mailto
    lead["siren"] = _txt(info.get("siren"))
    lead["ca"] = _txt(info.get("chiffre_affaires"))


def superposer_analyse_attribution(lead, analyse):
    """Remplace le score DETERMINISTE d'un lead ATTRIB par la vraie analyse
    du modele (attributions_analyse.py), quand elle existe pour ce
    publication_number.

    POURQUOI (23/07/2026)
    ---------------------
    `attribution_vers_lead` fabrique un score deterministe zone x secteur x
    valeur, recopie a l'identique dans surete, commercial et final, avec une
    justification en gabarit fixe. C'etait la seule information disponible :
    aucun collecteur d'attributions n'appelait le modele. Depuis
    `attributions_analyse.py`, une table `attributions_analyse` porte une vraie
    analyse (origine du titulaire, nature du deploiement, interlocuteur a
    viser). On la JOINT ici sur publication_number, exactement comme le
    dashboard joint deja l'enrichissement firmographique.

    Superposition NON DESTRUCTIVE : si l'analyse manque (pas encore produite,
    solde LLM epuise, publication_number absent), le lead garde son score
    deterministe. La page reste donc complete, jamais trouee -- le meme
    principe que l'enrichissement, qui est un no-op quand rien ne matche."""
    if not analyse:
        return lead
    def num(cle, defaut):
        try:
            return float(analyse.get(cle))
        except (TypeError, ValueError):
            return defaut
    lead["final"] = num("score_final", lead["final"])
    lead["surete"] = num("score_surete", lead["surete"])
    lead["comm"] = num("score_commercial", lead["comm"])
    action = _txt(analyse.get("action_recommandee"))
    if action:
        lead["action"] = action
    justif = _txt(analyse.get("justification"))
    if justif:
        # On PREFIXE le pays d'origine : c'est le signal que le score
        # deterministe ne pouvait pas porter, et le premier que l'oeil doit
        # voir sur la fiche.
        origine = _txt(analyse.get("pays_origine_titulaire"))
        etranger = _txt(analyse.get("titulaire_etranger")).lower() in ("true", "1", "vrai")
        prefixe = ""
        if origine:
            prefixe = "Titulaire {} ({}). ".format(
                origine, "ETRANGER au pays d'execution" if etranger else "local")
        lead["justif"] = prefixe + justif
    interlocuteur = _txt(analyse.get("interlocuteur_vise"))
    if interlocuteur:
        lead["cible"] = "Contacter : {}. {}".format(interlocuteur, lead["cible"])
    # Champs exposes a la lentille Titulaires (badges, filtres).
    lead["origine"] = _txt(analyse.get("pays_origine_titulaire"))
    lead["etranger_titulaire"] = _txt(analyse.get("titulaire_etranger")).lower() \
        in ("true", "1", "vrai")
    lead["nature_deploiement"] = _txt(analyse.get("nature_deploiement"))
    lead["besoin_surete"] = _txt(analyse.get("besoin_surete_probable"))
    lead["interlocuteur"] = interlocuteur
    lead["analysee"] = True
    return lead


def construire_leads(lignes_ted, lignes_bm, lignes_prive=None,
                     enrichissement=None, lignes_attrib=None, lignes_rw=None,
                     lignes_afdb=None, lignes_adb=None, lignes_ebrd=None,
                     lignes_ungm=None, analyses_attrib=None,
                     lignes_miga=None, lignes_ifc=None, lignes_idb=None,
                     lignes_bmp=None, lignes_proparco=None, lignes_dfc=None):
    """Fusionne les onglets (TED, Banque Mondiale, AfDB, ADB, EBRD, ReliefWeb,
    PRIVÉ/BITD), deduplique, trie. Pour les leads PRIVÉ, remonte le dirigeant
    (enrichissement) comme contact.

    AfDB / ADB / EBRD : sources d'AVIS (lentille Opportunités, comme TED/BM).
    Même moteur de score additif (ted.calculer_scores), donc même échelle
    « avis ». Pays en code ISO3 (comme TED). ReliefWeb : pays par NOM (comme BM)."""
    leads = [ligne_vers_lead(r, "TED") for r in lignes_ted]
    leads += [ligne_vers_lead(r, "BM") for r in lignes_bm]
    leads += [ligne_vers_lead(r, "AFDB") for r in (lignes_afdb or [])]
    leads += [ligne_vers_lead(r, "UNGM") for r in (lignes_ungm or [])]
    leads += [ligne_vers_lead(r, "ADB") for r in (lignes_adb or [])]
    leads += [ligne_vers_lead(r, "EBRD") for r in (lignes_ebrd or [])]
    leads += [ligne_vers_lead(r, "RW") for r in (lignes_rw or [])]
    leads += [ligne_vers_lead(r, "MIGA") for r in (lignes_miga or [])]
    leads += [ligne_vers_lead(r, "IFC") for r in (lignes_ifc or [])]
    leads += [ligne_vers_lead(r, "IDB") for r in (lignes_idb or [])]
    leads += [ligne_vers_lead(r, "BMP") for r in (lignes_bmp or [])]
    leads += [ligne_vers_lead(r, "PROPARCO") for r in (lignes_proparco or [])]
    leads += [ligne_vers_lead(r, "DFC") for r in (lignes_dfc or [])]
    leads_prive = [ligne_vers_lead(r, "PRIVÉ") for r in (lignes_prive or [])]

    # Index d'enrichissement : clefs brutes (minuscules) + alias normalises,
    # pour matcher malgre les variantes de nom (« SA », casse, accents...).
    enrichissement = dict(enrichissement or {})
    for k in list(enrichissement.keys()):
        nk = _norm_ent(k)
        if nk and nk not in enrichissement:
            enrichissement[nk] = enrichissement[k]

    for l in leads_prive:
        _attacher_enrichissement(l, enrichissement, l["entreprise"])
    leads += leads_prive

    # Attributions (titulaires de marches gagnes) : registre de prospects.
    # Enrichis AUSSI (dirigeant / email / SIREN) quand l'entreprise est connue,
    # pour que la fiche entreprise ait un contact si le titulaire est francais.
    attribs = [attribution_vers_lead(r) for r in (lignes_attrib or [])
               if _txt(r.get("gagnant")) and "non publie" not in _txt(r.get("gagnant")).lower()]
    # Index des analyses LLM par publication_number (attributions_analyse.py).
    # Absent = pas encore analyse : le lead garde alors son score deterministe.
    analyses = {}
    for a in (analyses_attrib or []):
        pub = _txt(a.get("publication_number"))
        if pub:
            analyses[pub] = a
    for l in attribs:
        superposer_analyse_attribution(l, analyses.get(_txt(l.get("pub"))))
        _attacher_enrichissement(l, enrichissement, l["entreprise"])
    leads += attribs

    leads = [l for l in leads if l["titre"]]  # avis exploitables seulement

    # Deduplication : un meme avis (meme lien, ou meme pays+titre) ne doit
    # apparaitre qu'une fois, meme si la feuille contient des lignes en double.
    # On garde l'occurrence au meilleur score.
    uniques = {}
    for l in leads:
        cle = l["lien"] or (l["src"] + "|" + l["pays"] + "|" + l["agence"] + "|" + l["titre"])
        if cle not in uniques or l["final"] > uniques[cle]["final"]:
            uniques[cle] = l
    leads = list(uniques.values())

    # RANG DE TRI : score attenue par l'age (cf. rang_tri). Calcule APRES la
    # dedup (qui, elle, garde l'occurrence au meilleur score, independamment de
    # l'age) et expose au JS via le champ `rang` (leads serialises tels quels).
    # Le score AFFICHE (`final`) n'est pas touche. Departage a rang egal : le
    # score brut, pour que deux leads de meme rang gardent l'ordre par valeur.
    for l in leads:
        l["rang"] = rang_tri(l.get("final", 0), l.get("date_det", ""))
        # ent_cle : cle canonique d'entite, SOURCE DE VERITE UNIQUE. Precalculee
        # ici pour que les fiches 360 (JS) regroupent sur EXACTEMENT la meme cle
        # que le rattachement d'enrichissement (Python), sans re-normaliser cote
        # client (fin de la double implementation qui pouvait deriver).
        l["ent_cle"] = _norm_ent(_nom_entreprise(l))

    leads.sort(key=lambda l: (l["rang"], l["final"]), reverse=True)
    return leads


def _lignes_vers_dicts(valeurs, colonnes):
    """Transforme une grille brute (get_all_values) en liste de dicts, en
    mappant PAR POSITION sur l'ordre officiel `colonnes` (source de verite =
    les collecteurs). On ignore la ligne d'en-tete de la feuille, qui peut etre
    restee sur un ancien schema et provoquer un decalage. Robuste."""
    if not valeurs:
        return []
    debut = 0
    premiere = [str(c).strip() for c in valeurs[0]]
    # Detecte une ligne d'en-tete : mots-cles connus OU 1re cellule = 1re colonne.
    if ("score_final" in premiere or "titre" in premiere
            or premiere[:1] == ["date_maj"] or (colonnes and premiere[:1] == [colonnes[0]])):
        debut = 1
    dicts = []
    for row in valeurs[debut:]:
        if not any(str(c).strip() for c in row):
            continue  # ligne vide
        d = {nom: (row[i] if i < len(row) else "") for i, nom in enumerate(colonnes)}
        dicts.append(d)
    return dicts


def lire_onglets(sheet_id, fichier_cs):
    """Lit les deux onglets via gspread, en lecture POSITIONNELLE (get_all_values
    + ordre officiel des colonnes), pas get_all_records (sensible aux en-tetes).
    Import paresseux : pas requis pour les tests de generation HTML."""
    import gspread
    from google.oauth2.service_account import Credentials
    import ted_complet_v14 as ted
    import ted_complet_bm as bm

    portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    client = gspread.authorize(creds)
    classeur = client.open_by_key(sheet_id)

    def valeurs(nom):
        try:
            return classeur.worksheet(nom).get_all_values()
        except gspread.WorksheetNotFound:
            print("  (info) onglet '{}' introuvable, ignore.".format(nom))
            return []

    # On lit TOUTES les colonnes (y compris statut_suivi et date_detection,
    # situees apres la zone preservee) pour le CRM et le tri par mois.
    lignes_ted = _lignes_vers_dicts(valeurs(NOM_ONGLET_TED), ted.TOUTES_COLONNES_SHEET)
    lignes_bm = _lignes_vers_dicts(valeurs(NOM_ONGLET_BM), bm.TOUTES_COLONNES_BM)

    # Onglet des signaux prives (BITD), s'il existe. Schema fourni par le moteur.
    try:
        import bitd_signaux as bitd
        colonnes_prive = bitd.TOUTES_COLONNES_PRIVE
        onglet_prive = bitd.NOM_ONGLET_PRIVE
    except Exception:
        onglet_prive = "prive_radar"
        colonnes_prive = [
            "date_maj", "score_final", "score_surete", "score_commercial",
            "action_recommandee", "fenetre_action", "titre", "acheteur",
            "pays_execution", "zone", "type_activite", "confiance", "modele",
            "cible_commerciale_reelle", "justification", "entreprise",
            "priorite_compte", "publication_number", "lien_avis",
            "date_publication", "statut_suivi", "date_detection"]
    lignes_prive = _lignes_vers_dicts(valeurs(onglet_prive), colonnes_prive)

    # Registre des attributions (titulaires de marches gagnes). Schema fourni
    # par le collecteur d'attributions.
    try:
        import ted_complet_attributions as att
        colonnes_attrib = att.TOUTES_COLONNES
        onglet_attrib = att.NOM_ONGLET
    except Exception:
        onglet_attrib = "attributions_radar"
        colonnes_attrib = [
            "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
            "acheteur", "titre", "cpv", "sous_traitance", "date_publication",
            "publication_number", "lien", "a_demarcher",
            "pays_titulaire", "titulaire_etranger",       # socle B (23/07/2026)
            "statut_prospection", "date_detection"]
    lignes_attrib = _lignes_vers_dicts(valeurs(onglet_attrib), colonnes_attrib)

    # Onglet ReliefWeb (offres terrain = signaux de déploiement humanitaire).
    # Schéma fourni par le collecteur ; repli sur un schéma figé si le module
    # est absent (le dashboard reste fonctionnel).
    try:
        import ted_complet_reliefweb as rw
        colonnes_rw = rw.TOUTES_COLONNES_RW
        onglet_rw = rw.NOM_ONGLET_RW
    except Exception:
        onglet_rw = "reliefweb_radar"
        colonnes_rw = [
            "date_maj", "score_final", "score_surete", "score_commercial",
            "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
            "titre", "acheteur", "pays_execution",
            "type_client", "type_mobilite", "profil_personnes_exposees",
            "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
            "profils_acteurs_probables", "cible_commerciale_reelle",
            "justification", "confiance", "modele", "raffine", "divergence",
            "organisation", "type_contrat", "categorie", "ville", "how_to_apply",
            "publication_number", "lien_avis", "deadline", "date_publication",
            "statut_suivi", "date_detection"]
    lignes_rw = _lignes_vers_dicts(valeurs(onglet_rw), colonnes_rw)

    # Onglets des bailleurs multilateraux (AfDB, ADB, EBRD). Schema fourni par
    # chaque collecteur ; repli sur l'onglet nomme si le module est absent (le
    # dashboard reste fonctionnel meme sans les collecteurs installes).
    def _lire_bailleur(nom_module, onglet_defaut, attr_colonnes, attr_onglet):
        try:
            mod = __import__(nom_module)
            colonnes = getattr(mod, attr_colonnes)
            onglet = getattr(mod, attr_onglet)
        except Exception:
            colonnes, onglet = None, onglet_defaut
        if colonnes is None:   # repli : schema minimal commun (avis ISO)
            colonnes = [
                "date_maj", "score_final", "score_surete", "score_commercial",
                "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
                "titre", "acheteur", "pays_execution", "pays_acheteur",
                "type_client", "type_mobilite", "profil_personnes_exposees",
                "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
                "profils_acteurs_probables", "cible_commerciale_reelle",
                "justification", "confiance", "modele", "raffine", "divergence",
                "type_notice", "phase", "publication_number", "lien_avis",
                "deadline", "date_publication", "statut_suivi", "date_detection"]
        return _lignes_vers_dicts(valeurs(onglet), colonnes)

    lignes_afdb = _lire_bailleur("afdb_radar", "afdb_radar", "TOUTES_COLONNES_AFDB", "NOM_ONGLET")
    lignes_ungm = _lire_bailleur("ungm_radar", "ungm_radar", "TOUTES_COLONNES_UNGM", "NOM_ONGLET")
    lignes_adb = _lire_bailleur("adb_radar", "adb_radar", "TOUTES_COLONNES_ADB", "NOM_ONGLET")
    lignes_ebrd = _lire_bailleur("ebrd_radar", "ebrd_radar", "TOUTES_COLONNES_EBRD", "NOM_ONGLET")
    # Vague 2 : MIGA (garanties risque politique) et IFC (investissements
    # prives). Sources d'AVIS, pays par NOM (comme BM/RW). Onglets dedies.
    lignes_miga = _lire_bailleur("miga_radar", "miga_radar", "TOUTES_COLONNES", "NOM_ONGLET")
    lignes_ifc = _lire_bailleur("ifc_radar", "ifc_radar", "TOUTES_COLONNES", "NOM_ONGLET")
    # IDB (Banque interaméricaine, Amérique latine) : source d'AVIS ISO3 comme
    # AfDB/ADB. Son onglet `idb_radar` était collecté mais jamais lu (orphelin,
    # 10/08/2026). Branché ici. Les attributions IDB restent dans attributions_radar.
    lignes_idb = _lire_bailleur("idb_radar", "idb_radar", "TOUTES_COLONNES_IDB", "NOM_ONGLET")
    # Vague 3 : Proparco (DFI FR) et DFC (DFI US) -- investissements prives
    # nominatifs. Pays en ISO3 (comme AfDB/IDB). Onglets dedies.
    lignes_proparco = _lire_bailleur("proparco_radar", "proparco_radar", "TOUTES_COLONNES", "NOM_ONGLET")
    lignes_dfc = _lire_bailleur("dfc_radar", "dfc_radar", "TOUTES_COLONNES", "NOM_ONGLET")

    # Watchlist des cibles privees (comptes_cibles_bitd) : liste curee a la main
    # (oil & gas, BTP, luxe...). Lue telle quelle (colonnes libres), pour la
    # lentille "Cibles privees". Repli silencieux si l'onglet/module manque.
    lignes_watchlist = []
    try:
        import bitd_signaux as _bitd
        onglet_wl = getattr(_bitd, "NOM_ONGLET_WHITELIST", "comptes_cibles_bitd")
    except Exception:
        onglet_wl = "comptes_cibles_bitd"
    try:
        vals_wl = valeurs(onglet_wl)
        if vals_wl and len(vals_wl) >= 2:
            entetes_wl = [str(c).strip() for c in vals_wl[0]]
            for r in vals_wl[1:]:
                d = {entetes_wl[i]: (r[i] if i < len(r) else "")
                     for i in range(len(entetes_wl))}
                if _txt(d.get("entreprise")):
                    lignes_watchlist.append(d)
    except Exception:
        pass

    # Enrichissement firmographique (dirigeants), pour remonter le contact.
    try:
        import enrichir_entreprises as ee
        colonnes_enr = ee.COLONNES_ENRICHIES
        onglet_enr = ee.NOM_ONGLET_ENRICHIES
    except Exception:
        onglet_enr = "entreprises_enrichies"
        colonnes_enr = ["entreprise", "siren", "nom_officiel", "dirigeant_principal",
                        "autres_dirigeants", "activite_naf", "effectif", "ville",
                        "chiffre_affaires", "source", "date_enrichissement"]
    enrichissement = {}
    for d in _lignes_vers_dicts(valeurs(onglet_enr), colonnes_enr):
        nom = _txt(d.get("entreprise")).lower()
        if nom:
            enrichissement[nom] = d

    # Contacts (Hunter) : email pro par entreprise, injecte dans l'enrichissement.
    try:
        import enrichir_entreprises as ee
        onglet_contacts = ee.NOM_ONGLET_CONTACTS
        colonnes_contacts = ee.COLONNES_CONTACTS
    except Exception:
        onglet_contacts = "contacts_bitd"
        colonnes_contacts = ["entreprise", "email_pro", "confiance", "source", "date_contact"]
    for d in _lignes_vers_dicts(valeurs(onglet_contacts), colonnes_contacts):
        nom = _txt(d.get("entreprise")).lower()
        email = _txt(d.get("email_pro"))
        if nom and email:
            enrichissement.setdefault(nom, {})["email_pro"] = email

    # Analyse LLM des attributions (attributions_analyse.py), table SEPAREE
    # jointe sur publication_number dans construire_leads. Repli silencieux si
    # le module ou l'onglet manque : les leads ATTRIB gardent alors leur score
    # deterministe, la page reste complete.
    try:
        import attributions_analyse as aa
        colonnes_aa = aa.COLONNES
        onglet_aa = aa.NOM_ONGLET
    except Exception:
        onglet_aa = "attributions_analyse"
        colonnes_aa = [
            "publication_number", "date_analyse", "score_final", "score_surete",
            "score_commercial", "action_recommandee", "pays_origine_titulaire",
            "titulaire_etranger", "nature_deploiement", "profils_deployes",
            "duree_chantier", "exposition_terrain", "besoin_surete_probable",
            "interlocuteur_vise", "justification", "confiance", "modele"]
    analyses_attrib = _lignes_vers_dicts(valeurs(onglet_aa), colonnes_aa)

    # Alertes voyageurs (alertes_voyageurs.py). Bandeau de contexte, option A.
    try:
        import alertes_voyageurs as av
        colonnes_al = av.COLONNES
        onglet_al = av.NOM_ONGLET
    except Exception:
        onglet_al = "alertes_radar"
        colonnes_al = ["date_maj", "pays_execution", "pays_nom", "zone",
                       "niveau_avant", "niveau_apres", "sens", "severite",
                       "motif", "publication_number", "lien"]
    lignes_alertes = _lignes_vers_dicts(valeurs(onglet_al), colonnes_al)

    # BM Projects (AMONT) : projets approuves, en amont des appels d'offres BM.
    # Source d'AVIS ISO3, score deterministe (pas de LLM). Onglet dedie.
    lignes_bmp = _lire_bailleur("bm_projets", "bm_projets_radar", "TOUTES_COLONNES_BMP", "NOM_ONGLET")

    return (lignes_ted, lignes_bm, lignes_prive, lignes_attrib, enrichissement,
            lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_watchlist,
            lignes_ungm, analyses_attrib, lignes_alertes, lignes_miga, lignes_ifc,
            lignes_idb, lignes_bmp, lignes_proparco, lignes_dfc)


def charger_leads(sheet_id, fichier_cs):
    """POINT D'ENTREE UNIQUE DU CHEMIN SHEET : lit les onglets puis construit les
    leads avec le cablage COMPLET (toutes les sources, MIGA/IFC/UNGM inclus).

    POURQUOI (02/08/2026)
    ---------------------
    `lire_onglets` renvoie un tuple qui a grossi au fil des sources (10 -> 15).
    Chaque appelant qui le deballait a la main puis rappelait `construire_leads`
    devait rester synchronise. Le digest ne l'etait pas : il deballait 10 valeurs
    sur 15 et levait `ValueError` a chaque run, avale en "lecture du Sheet
    impossible", canal PUSH mort en silence.

    Desormais un SEUL endroit deballe `lire_onglets` et cable `construire_leads` :
    cette fonction. `main` et le digest passent par ici, ne recablent plus rien,
    et ne peuvent plus deriver. Le contrat d'arite est verrouille par
    `test_cablage_lecture.py`.

    Renvoie (leads, onglets) : `onglets` est le tuple brut de `lire_onglets`, pour
    que l'appelant qui a besoin de la watchlist, des alertes ou des comptes par
    source (le dashboard statique) le reutilise SANS relire le Sheet."""
    onglets = lire_onglets(sheet_id, fichier_cs)
    (lignes_ted, lignes_bm, lignes_prive, lignes_attrib, enrichissement,
     lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_watchlist,
     lignes_ungm, analyses_attrib, lignes_alertes, lignes_miga, lignes_ifc,
     lignes_idb, lignes_bmp, lignes_proparco, lignes_dfc) = onglets
    leads = construire_leads(
        lignes_ted, lignes_bm, lignes_prive, enrichissement, lignes_attrib,
        lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_ungm,
        analyses_attrib, lignes_miga=lignes_miga, lignes_ifc=lignes_ifc,
        lignes_idb=lignes_idb, lignes_bmp=lignes_bmp,
        lignes_proparco=lignes_proparco, lignes_dfc=lignes_dfc)
    return leads, onglets


# Fenetre de fraicheur de l'onglet Geopolitique : la SEMAINE EN COURS.
# Les evenements pays ne sont un contexte utile que tres frais. Surchargeable
# (ex. RADAR_GEO_JOURS=14) sans toucher au code.
GEO_JOURS = int(os.environ.get("RADAR_GEO_JOURS", "7"))


def preparer_geo(lignes_alertes, jours=None):
    """Lignes brutes de `alertes_radar` -> flux GEOPOLITIQUE de l'onglet dedie.

    Ne garde que les signaux de la SEMAINE EN COURS (7 jours par defaut) : au
    dela, un evenement pays n'est plus un contexte operationnel pertinent, juste
    du bruit qui encombre. Un evenement geo (FCDO ou presse) reste un CONTEXTE
    pays : aucun score, aucune action « je contacte ». Fonction pure."""
    from datetime import date, timedelta
    jours = GEO_JOURS if jours is None else jours
    limite = (date.today() - timedelta(days=jours)).isoformat()
    prets = []
    for l in (lignes_alertes or []):
        maj = _txt(l.get("date_maj"))
        if maj and maj < limite:
            continue
        try:
            sev = int(float(l.get("severite") or 0))
        except (TypeError, ValueError):
            sev = 0
        nom_pays = _txt(l.get("pays_nom")) or _txt(l.get("pays_execution"))
        _, zone_calc = resoudre_pays(_txt(l.get("pays_execution")), "TED")
        prets.append({
            "pays": nom_pays,
            "iso3": _txt(l.get("pays_execution")),
            "zone": _txt(l.get("zone")) or zone_calc,
            "sens": _txt(l.get("sens")),
            "avant": _txt(l.get("niveau_avant")),
            "apres": _txt(l.get("niveau_apres")),
            "motif": _txt(l.get("motif")),
            "severite": sev,
            "date": maj,
            "lien": _txt(l.get("lien")),
        })
    # Tri par severite puis date (recent d'abord) : le JS regroupe ensuite par
    # zone en conservant cet ordre intra-zone.
    prets.sort(key=lambda a: (a["severite"], a["date"]), reverse=True)
    return prets


# ===========================================================================
# COUPLAGE GEO -> SCORE (12/08/2026) -- « board vivant »
# ===========================================================================
# Le signal geopolitique cessait d'etre decoratif : un pays qui vient de virer
# au rouge (aggravation FCDO/presse recente) doit rendre ses AVIS plus chauds.
# Applique en DISPLAY-TIME dans le dashboard (non destructif, dynamique) : le
# score de base est conserve, le boost est borne, trace, et recalcule le rang.
# On ne touche PAS aux collecteurs (le score fige a la collecte ne verrait pas
# un pays basculer apres coup).
BOOST_GEO_JOURS = int(os.environ.get("RADAR_BOOST_GEO_JOURS", "14"))
BOOST_GEO_MAX = float(os.environ.get("RADAR_BOOST_GEO_MAX", "1.5"))   # sur sûreté
# Familles boostables : les AVIS uniquement. PRIVÉ/ATTRIB ont d'autres baremes,
# non comparables, et ne sont pas des « avis dans un pays qui bascule ».
SRC_BOOSTABLES = {"TED", "BM", "AFDB", "ADB", "EBRD", "UNGM", "RW", "MIGA", "IFC", "IDB", "PROPARCO", "DFC"}


def _boost_par_pays(alertes, aujourdhui=None):
    """Nom de pays (resolu) -> (boost, motif, date) depuis les AGGRAVATIONS
    recentes (< BOOST_GEO_JOURS). Un allegement ne baisse jamais un lead : on ne
    veut pas masquer une opportunite reelle parce que le FCDO s'ameliore. Boost =
    severite (0-4) atenuee lineairement par l'age, bornee a BOOST_GEO_MAX. Par
    pays, la plus forte aggravation gagne (pas d'empilement). Fonction pure."""
    from datetime import date, timedelta
    auj = aujourdhui or date.today()
    limite = (auj - timedelta(days=BOOST_GEO_JOURS)).isoformat()
    par_pays = {}
    for a in (alertes or []):
        if _txt(a.get("sens")) != "aggravation":
            continue
        maj = _txt(a.get("date_maj"))
        if maj and maj < limite:
            continue
        try:
            sev = int(float(a.get("severite") or 0))
        except (TypeError, ValueError):
            sev = 0
        try:
            age = (auj - date.fromisoformat(maj)).days if maj else 0
        except ValueError:
            age = 0
        decay = max(0.0, 1.0 - age / float(max(1, BOOST_GEO_JOURS)))
        boost = round(min(BOOST_GEO_MAX, (sev / 4.0) * BOOST_GEO_MAX * decay), 2)
        if boost <= 0:
            continue
        # Cle = NOM resolu (comme le champ `pays` des leads), via le meme
        # resolveur ISO3 -> nom. Repli sur pays_nom brut.
        nom_resolu, _ = resoudre_pays(_txt(a.get("pays_execution")), "TED")
        for cle in (nom_resolu, _txt(a.get("pays_nom"))):
            cle = _txt(cle)
            if not cle:
                continue
            cur = par_pays.get(cle)
            if not cur or boost > cur[0]:
                par_pays[cle] = (boost, _txt(a.get("motif")), maj)
    return par_pays


def appliquer_boost_geo(leads, alertes):
    """Rehausse les AVIS d'un pays en aggravation recente, in place. Non
    destructif : `final_base`/`surete_base` conservent le score d'origine, le
    resultat est borne a 10, le lead est marque (`geo_boost`/`geo_motif`) pour
    l'UI, et le rang de tri est recalcule pour que le boost remonte le lead dans
    la vue Importance. PRIVÉ/ATTRIB intacts. Retourne `leads` (re-trie)."""
    par_pays = _boost_par_pays(alertes)
    if not par_pays:
        return leads
    touche = False
    for l in leads:
        if l.get("src") not in SRC_BOOSTABLES:
            continue
        info = par_pays.get(_txt(l.get("pays")))
        if not info:
            continue
        boost, motif, dmaj = info
        l["surete_base"] = l.get("surete_base", l.get("surete", 0))
        l["final_base"] = l.get("final_base", l.get("final", 0))
        l["surete"] = round(min(10.0, l.get("surete", 0) + boost), 1)
        l["final"] = round(min(10.0, l.get("final", 0) + 0.5 * boost), 1)
        l["geo_boost"] = boost
        l["geo_motif"] = motif
        l["geo_date"] = dmaj
        touche = True
    if touche:
        for l in leads:
            l["rang"] = rang_tri(l.get("final", 0), l.get("date_det", ""))
        leads.sort(key=lambda l: (l["rang"], l["final"]), reverse=True)
    return leads


def generer_html(leads, watchlist=None, api_statut=False, alertes=None):
    """Produit la page HTML autonome (situation board) a partir des leads.

    api_statut : True uniquement quand la page est servie par l'APPLICATION
    (radar_app). Le bouton « Je contacte » ecrit alors AUSSI dans la table
    radar_statuts via POST /api/statut, en plus de l'Apps Script. False par
    defaut : la page statique Cloudflare garde EXACTEMENT le comportement
    d'avant (l'appel /api/statut n'existe pas sur un hebergement statique)."""
    watchlist = watchlist or []
    # Couplage géo -> score : un pays en aggravation récente rend ses avis plus
    # chauds. Appliqué AVANT meta/serialisation pour que le boost pèse sur les
    # KPI, le rang (Importance) et le score affiché. Non destructif.
    appliquer_boost_geo(leads, alertes or [])
    # Les titulaires (ATTRIB) sont un REGISTRE DE PROSPECTS, pas des avis
    # analyses : on les EXCLUT des KPI d'action pour ne pas gonfler
    # artificiellement "a surveiller" ni le total. Ils ont leur propre
    # compteur "titulaires" et leur propre tuile dans le dashboard.
    analyses = [l for l in leads if l["src"] != "ATTRIB"]
    meta = {
        "date": date.today().strftime("%d/%m/%Y"),
        "total": len(analyses),
        "contacter": sum(1 for l in analyses if l["action"] == "contacter"),
        "surveiller": sum(1 for l in analyses if l["action"] == "surveiller"),
        "ignorer": sum(1 for l in analyses if l["action"] == "ignorer"),
        "titulaires": sum(1 for l in leads if l["src"] == "ATTRIB"),
    }
    # Mois presents, du plus recent au plus ancien (pour les onglets periode).
    labels = {}
    for l in leads:
        if l["mois"]:
            labels[l["mois"]] = l["mois_label"]
    meta["mois"] = [{"cle": c, "label": labels[c]} for c in sorted(labels, reverse=True)]
    leads_json = json.dumps(leads, ensure_ascii=False)
    # Section "Alertes pays" (option A) : bandeau de contexte en tete, SEPARE
    # des leads. Une alerte n'est pas un prospect a contacter mais une info qui
    # rend les autres leads de ce pays plus chauds. On ne la melange donc pas au
    # scoring des leads (ce serait le defaut qu'on a corrige au chantier C).
    # Onglet Géopolitique : signaux de la semaine en cours, groupés par zone.
    geo_json = json.dumps(preparer_geo(alertes or []), ensure_ascii=False)
    # Taxonomie secteur exposée au JS (filtre + regroupement). Ordre = affichage.
    secteurs_json = json.dumps(SECTEURS_CANONIQUES, ensure_ascii=False)
    meta_json = json.dumps(meta, ensure_ascii=False)
    # Watchlist normalisee pour le JS : nom + secteur (detection dynamique de la
    # colonne secteur, quel que soit son intitule exact).
    def _secteur_wl(d):
        for k in d:
            if str(k).strip().lower() in ('secteur','secteurs','sector','categorie','cat\u00e9gorie','domaine','activite','activit\u00e9'):
                return _txt(d.get(k))
        return ''
    watchlist_norm = [{'entreprise': _txt(w.get('entreprise')), 'secteur': _secteur_wl(w),
                       'ent_cle': _norm_ent(_txt(w.get('entreprise'))),
                       'sect': secteur_canonique(_secteur_wl(w))}
                      for w in watchlist if _txt(w.get('entreprise'))]
    watchlist_json = json.dumps(watchlist_norm, ensure_ascii=False)
    # Boucle de retroaction (item 7), volet VISIBILITE : taux de conversion
    # gagne/perdu par secteur et par zone, calcule sur toutes les sources. La
    # zone est comparable partout ; le secteur melange les taxonomies (avis vs
    # signaux) mais reste informatif.
    outcomes = [{"secteur": (l.get("grp") or ""), "zone": (l.get("zone") or ""),
                 "statut": (l.get("statut") or "")} for l in leads]
    conversion_json = json.dumps(radar_retroaction.table_conversion(outcomes), ensure_ascii=False)
    # Observabilite de run : etat par source, derive des leads (aucune lecture
    # supplementaire). Rend visible une source qui s'est tue.
    sante_json = json.dumps(sante_run(leads), ensure_ascii=False)
    # Config du bouton "Je contacte". PRIORITE aux variables d'environnement
    # (secrets GitHub Actions), pour ne plus stocker AUCUN secret dans le
    # depot. Repli sur suivi_config.py uniquement pour un usage local. Si les
    # deux restent vides, le bouton ne s'affiche pas (dashboard intact).
    surl = os.environ.get("SUIVI_WEBAPP_URL", "") or ""
    stok = os.environ.get("SUIVI_TOKEN", "") or ""
    if not (surl and stok):
        try:
            import suivi_config
            surl = surl or (getattr(suivi_config, "SUIVI_WEBAPP_URL", "") or "")
            stok = stok or (getattr(suivi_config, "SUIVI_TOKEN", "") or "")
        except Exception:
            pass
    return (GABARIT_HTML
            .replace("__LEADS_JSON__", leads_json)
            .replace("__GEO_JSON__", geo_json)
            .replace("__SECTEURS_JSON__", secteurs_json)
            .replace("__META_JSON__", meta_json)
            .replace("__WATCHLIST_JSON__", watchlist_json)
            .replace("__CONVERSION_JSON__", conversion_json)
            .replace("__SANTE_JSON__", sante_json)
            .replace("__SUIVI_URL__", json.dumps(surl))
            .replace("__SUIVI_TOKEN__", json.dumps(stok))
            .replace("__API_STATUT__", "true" if api_statut else "false"))


def main():
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sortie = os.environ.get("DASHBOARD_OUTPUT", "public/index.html")

    if not sheet_id or not fichier_cs:
        print("ERREUR : TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE sont requis.")
        sys.exit(1)

    print("Lecture des onglets du Sheet...")
    # Cablage UNIQUE via charger_leads : main ne deballe plus lire_onglets ni ne
    # rappelle construire_leads a la main (c'etait la source des regressions du
    # 02/08). On recupere aussi le tuple brut `onglets` pour les comptes par
    # source et pour le rendu (watchlist + alertes), sans relire le Sheet.
    leads, onglets = charger_leads(sheet_id, fichier_cs)
    (lignes_ted, lignes_bm, lignes_prive, lignes_attrib, enrichissement,
     lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_watchlist,
     lignes_ungm, analyses_attrib, lignes_alertes, lignes_miga, lignes_ifc,
     lignes_idb, lignes_bmp, lignes_proparco, lignes_dfc) = onglets

    # Persistance de la tendance (best-effort) : le snapshot sante par source
    # s'accumule dans 'runs_radar' une fois par run (etape "Generer le tableau
    # de bord"), pour tracer la tendance. Ne casse jamais la generation.
    try:
        import radar_runs
        print("(pg) " + radar_runs.enregistrer("sante", radar_runs.charge_sante(sante_run(leads))))
        # Alerte proactive : source en regression silencieuse (muette N runs).
        radar_runs.alerter_sources_muettes()
    except Exception as e:
        print("(pg) stat de run sante non persistee ({}) -- generation non affectee".format(
            str(e)[:100]))
    print("  TED : {} | BM : {} | AfDB : {} | ADB : {} | EBRD : {} | UNGM : {} | "
          "ReliefWeb : {} | MIGA : {} | IFC : {} | IDB : {} | BMP : {} | total exploitable : {}".format(
              len(lignes_ted), len(lignes_bm), len(lignes_afdb), len(lignes_adb),
              len(lignes_ebrd), len(lignes_ungm), len(lignes_rw),
              len(lignes_miga), len(lignes_ifc), len(lignes_idb), len(lignes_bmp), len(leads)))

    html = generer_html(leads, lignes_watchlist, alertes=lignes_alertes)
    dossier = os.path.dirname(sortie)
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    with open(sortie, "w", encoding="utf-8") as f:
        f.write(html)
    print("Tableau de bord ecrit dans : {} ({} octets)".format(sortie, len(html)))


# ===========================================================================
# GABARIT HTML (situation board). __LEADS_JSON__ et __META_JSON__ injectes.
# ===========================================================================
GABARIT_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar Amarante, tableau de situation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@300;400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  :root{
    --ink:#16100F; --ink-2:#1F1715; --ink-3:#281D1B;
    --bone:#ECE4DA; --bone-dim:#A99E92; --bone-faint:#6E645C;
    --oxblood:#6F0E27; --oxblood-2:#8C1D2C;
    --fort:#C0273A; --fort-soft:rgba(192,39,58,0.14);
    --watch:#C8893B; --watch-soft:rgba(200,137,59,0.13);
    --low:#5C6670; --low-soft:rgba(92,102,112,0.12);
    --line:rgba(236,228,218,0.12); --line-2:rgba(236,228,218,0.06);
    --display:'Jost',-apple-system,system-ui,sans-serif;
    --body:'Inter',-apple-system,system-ui,sans-serif;
    --mono:'IBM Plex Mono',ui-monospace,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{-webkit-text-size-adjust:100%}
  body{
    font-family:var(--body); color:var(--bone); background:var(--ink);
    line-height:1.5; font-size:15px; padding-bottom:4rem;
    background-image:
      radial-gradient(1200px 600px at 78% -8%, rgba(140,29,44,0.18), transparent 60%),
      repeating-radial-gradient(circle at 82% 4%, transparent 0 34px, rgba(236,228,218,0.018) 34px 35px);
    min-height:100vh;
  }
  .wrap{max-width:1180px;margin:0 auto;padding:0 18px}
  header.mast{border-bottom:1px solid var(--line);padding:26px 0 20px;margin-bottom:22px}
  .mast-top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
  .brand{display:flex;align-items:center;gap:13px}
  .brand .mark{width:11px;height:34px;background:linear-gradient(var(--oxblood),var(--oxblood-2));border-radius:1px;box-shadow:0 0 18px rgba(140,29,44,0.5)}
  .brand h1{font-family:var(--display);font-weight:600;font-size:1.18rem;letter-spacing:0.14em;text-transform:uppercase}
  .brand .sub{font-family:var(--mono);font-size:0.66rem;letter-spacing:0.26em;color:var(--bone-dim);text-transform:uppercase;margin-top:2px}
  .runmeta{font-family:var(--mono);font-size:0.7rem;color:var(--bone-dim);text-align:right;letter-spacing:0.04em;line-height:1.7}
  .runmeta b{color:var(--bone);font-weight:600}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:22px}
  .tile{background:var(--ink-2);border:1px solid var(--line);border-radius:7px;padding:13px 14px;position:relative;overflow:hidden;cursor:pointer;text-align:left;color:inherit;font-family:inherit}
  .tile .n{font-family:var(--display);font-weight:600;font-size:1.85rem;line-height:1}
  .tile .l{font-family:var(--mono);font-size:0.6rem;letter-spacing:0.18em;text-transform:uppercase;color:var(--bone-dim);margin-top:7px}
  .tile.act{border-color:rgba(192,39,58,0.45)} .tile.act .n{color:var(--fort)}
  .tile.act::after{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--fort)}
  .tile.wat .n{color:var(--watch)} .tile.low .n{color:var(--bone-dim)}
  .tile.att .n{color:var(--bone-dim)}
  .tile.att::after{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--bone-faint)}
  .tile[aria-pressed="true"]{outline:2px solid var(--bone-dim);outline-offset:-1px}
  section.zones{margin-top:26px}
  .eyebrow{font-family:var(--mono);font-size:0.64rem;letter-spacing:0.24em;text-transform:uppercase;color:var(--bone-dim);margin-bottom:12px;display:flex;align-items:center;gap:10px}
  .eyebrow::after{content:"";flex:1;height:1px;background:var(--line-2)}
  .zonegrid{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}
  .zone{background:var(--ink-2);border:1px solid var(--line);border-radius:7px;padding:12px 11px;cursor:pointer;transition:border-color .15s,background .15s;text-align:left;color:inherit;font-family:inherit}
  .zone:hover{border-color:rgba(236,228,218,0.3)}
  .zone[aria-pressed="true"]{border-color:var(--fort);background:var(--fort-soft)}
  .zone .zn{font-family:var(--display);font-weight:500;font-size:0.82rem;line-height:1.2;min-height:2.1em}
  .zone .zc{font-family:var(--mono);font-size:1.5rem;font-weight:600;margin-top:8px;line-height:1}
  .zone .zl{font-family:var(--mono);font-size:0.56rem;letter-spacing:0.14em;text-transform:uppercase;color:var(--bone-dim);margin-top:3px}
  .zbar{height:3px;border-radius:2px;margin-top:9px;background:var(--low)}
  .zone[data-int="3"] .zbar{background:var(--fort)}
  .zone[data-int="2"] .zbar{background:var(--oxblood-2)}
  .zone[data-int="1"] .zbar{background:var(--watch)}
  .controls{display:flex;gap:10px;align-items:center;margin:26px 0 16px;flex-wrap:wrap}
  .search{flex:1;min-width:200px;display:flex;align-items:center;gap:9px;background:var(--ink-2);border:1px solid var(--line);border-radius:7px;padding:9px 13px}
  .search input{flex:1;background:none;border:none;color:var(--bone);font-family:var(--body);font-size:0.9rem;outline:none}
  .search input::placeholder{color:var(--bone-faint)}
  .search svg{flex-shrink:0;stroke:var(--bone-dim)}
  .seg{display:flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}
  .seg button{background:var(--ink-2);border:none;color:var(--bone-dim);font-family:var(--mono);font-size:0.66rem;letter-spacing:0.1em;text-transform:uppercase;padding:9px 13px;cursor:pointer;transition:.15s}
  .seg button+button{border-left:1px solid var(--line)}
  .seg button[aria-pressed="true"]{background:var(--oxblood);color:var(--bone)}
  .clearz{font-family:var(--mono);font-size:0.66rem;letter-spacing:0.08em;color:var(--bone-dim);background:none;border:none;cursor:pointer;text-decoration:underline;text-underline-offset:3px;display:none}
  .clearz.on{display:inline}
  .export{font-family:var(--mono);font-size:0.66rem;letter-spacing:0.08em;color:var(--bone-dim);background:none;border:1px solid var(--line);border-radius:3px;padding:4px 9px;cursor:pointer;margin-left:auto}
  .export:hover{color:var(--bone);border-color:var(--bone-dim)}
  .count{font-family:var(--mono);font-size:0.7rem;color:var(--bone-dim);letter-spacing:0.06em;margin-bottom:14px}
  .sante-run{margin-bottom:16px;border:1px solid var(--line);border-radius:8px;background:var(--ink-2);padding:10px 12px}
  .sante-run:empty{display:none}
  .sante-tete{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:9px}
  .sante-titre{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.16em;text-transform:uppercase;color:var(--bone-dim)}
  .sante-sub{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.05em;color:var(--bone-faint)}
  .sante-sub .warn{color:#dcb079}
  .sante-grid{display:flex;flex-wrap:wrap;gap:6px}
  .sante-chip{display:inline-flex;align-items:center;gap:7px;padding:4px 9px;border-radius:6px;border:1px solid var(--line);background:rgba(255,255,255,0.02);font-family:var(--mono);font-size:0.66rem;letter-spacing:0.03em;color:var(--bone-dim)}
  .sante-chip .src{color:var(--bone);font-weight:600}
  .sante-chip .n{color:var(--bone)}
  .sante-chip .ag{color:var(--bone-faint)}
  .sante-chip.frais{border-color:rgba(95,160,110,0.5)} .sante-chip.frais .dot{background:#5fa06e}
  .sante-chip.calme{border-color:var(--line)} .sante-chip.calme .dot{background:var(--bone-faint)}
  .sante-chip.ancien{border-color:rgba(200,137,59,0.55)} .sante-chip.ancien .dot{background:#c8893b}
  .sante-chip.absent{opacity:0.55} .sante-chip.absent .dot{background:#7a3b41}
  .sante-chip .dot{width:7px;height:7px;border-radius:50%;flex:none}
  /* Onglets periode (mois) */
  .period{display:flex;gap:7px;flex-wrap:wrap;margin:22px 0 4px;align-items:center}
  .period .chip{background:var(--ink-2);border:1px solid var(--line);border-radius:20px;padding:7px 14px;cursor:pointer;color:var(--bone-dim);font-family:var(--mono);font-size:0.66rem;letter-spacing:0.06em;transition:.15s;white-space:nowrap}
  .period .chip:hover{border-color:rgba(236,228,218,0.3);color:var(--bone)}
  .period .chip[aria-pressed="true"]{background:var(--oxblood);border-color:var(--oxblood);color:var(--bone)}
  /* Badge statut de suivi (CRM) */
  .statut{font-family:var(--mono);font-size:0.56rem;letter-spacing:0.1em;text-transform:uppercase;padding:3px 7px;border-radius:4px;border:1px solid var(--line);color:var(--bone-dim)}
  .statut.contacte{border-color:rgba(200,137,59,0.5);color:#dcb079}
  .statut.gagne{border-color:rgba(95,160,110,0.6);color:#86c596}
  .statut.perdu{border-color:rgba(150,150,150,0.4);color:var(--bone-faint)}
  .statut.relance{border-color:rgba(192,39,58,0.5);color:#e08e98}
  .datedet{font-family:var(--mono);font-size:0.58rem;color:var(--bone-faint);letter-spacing:0.04em}
  .jx{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.08em;font-weight:600;padding:3px 8px;border-radius:4px;border:1px solid var(--line)}
  .jx.urgent{background:rgba(192,39,58,0.18);border-color:rgba(192,39,58,0.6);color:#e88b96}
  .jx.proche{background:var(--watch-soft);border-color:rgba(200,137,59,0.5);color:#dcb079}
  .jx.large{border-color:var(--line-2);color:var(--bone-dim)}
  .jx.clos{border-color:rgba(150,150,150,0.35);color:var(--bone-faint)}
  .leads{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}
  .lead{background:var(--ink-2);border:1px solid var(--line);border-radius:9px;overflow:hidden;position:relative;display:flex;flex-direction:column}
  .lead .spine{position:absolute;left:0;top:0;bottom:0;width:4px}
  .lead[data-tier="contacter"] .spine{background:var(--fort)}
  .lead[data-tier="surveiller"] .spine{background:var(--watch)}
  .lead[data-tier="ignorer"] .spine{background:var(--low)}
  .lead .body{padding:15px 16px 14px 19px}
  .lhead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
  .lmeta{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--bone-dim);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .src{padding:2px 6px;border-radius:3px;border:1px solid var(--line);color:var(--bone-dim)}
  .src.bm{border-color:rgba(192,39,58,0.4);color:#d98a96}
  .src.ted{border-color:rgba(200,137,59,0.4);color:#dcb079}
  .src.attrib{border-color:rgba(120,190,150,0.45);color:#7fae8f}
  .src.rw{border-color:rgba(90,150,210,0.45);color:#8fb8de}
  .src.afdb{border-color:rgba(210,150,70,0.45);color:#d9b483}
  .src.ungm{border-color:rgba(90,150,210,0.45);color:#8fb8dd}
  .src.adb{border-color:rgba(120,170,120,0.45);color:#9ec49e}
  .src.ebrd{border-color:rgba(150,120,200,0.45);color:#b39ad6}
  .src.privé,.src.prive{border-color:rgba(150,150,200,0.4);color:#a9a9d9}
  .src.miga{border-color:rgba(200,120,90,0.45);color:#d69a80}
  .src.ifc{border-color:rgba(120,170,190,0.45);color:#8fbccf}
  .src.idb{border-color:rgba(210,180,90,0.45);color:#d9c383}
  .src.bmp{border-color:rgba(120,190,150,0.5);color:#8fce9f}
  /* Sélecteurs secteur / grouper */
  .pick{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:7px;
    background:var(--ink-2);padding:0 4px 0 11px;height:35px}
  .pick>span{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.11em;text-transform:uppercase;color:var(--bone-dim)}
  .pick select{background:transparent;border:none;color:var(--bone);font-family:var(--mono);font-size:0.68rem;
    letter-spacing:0.04em;padding:8px 6px;cursor:pointer;outline:none;max-width:190px}
  .pick select option{background:var(--ink-2);color:var(--bone)}
  /* En-têtes de groupe (« Grouper par ») */
  .grpsec{grid-column:1/-1;display:grid;grid-template-columns:repeat(2,1fr);gap:13px;margin-bottom:2px}
  .grphead{grid-column:1/-1;display:flex;align-items:baseline;gap:10px;margin:14px 0 2px;
    font-family:var(--mono);font-size:0.72rem;letter-spacing:0.14em;text-transform:uppercase;color:var(--fort);
    border-bottom:1px solid var(--line);padding-bottom:7px}
  .grphead span{color:var(--bone-dim);font-size:0.66rem;letter-spacing:0.08em}
  /* Lentille géopolitique */
  .geosec .grphead{color:var(--bone)}
  /* Cockpit « À faire » : accents d'urgence par bucket */
  .todosec .grphead{align-items:center}
  .grpsub{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.08em;text-transform:none;color:var(--bone-faint);font-style:italic;margin-left:4px}
  .todosec.b-retard .grphead{color:#e5544b;border-bottom-color:rgba(192,57,43,0.5)}
  .todosec.b-echeance .grphead{color:#d9a54a;border-bottom-color:rgba(210,150,60,0.4)}
  .todosec.b-contacter .grphead{color:var(--fort)}
  .todosec.b-suivre .grphead{color:var(--bone-dim)}
  .geocard{grid-column:1/-1;display:grid;grid-template-columns:26px 1fr auto;gap:12px;align-items:center;
    text-decoration:none;background:var(--ink-2);border:1px solid var(--line);border-left-width:3px;
    border-radius:8px;padding:11px 15px;transition:.15s}
  .geocard:hover{border-color:var(--oxblood);transform:translateX(2px)}
  .geocard.g-agg{border-left-color:#c0392b}
  .geocard.g-alleg{border-left-color:#27812f}
  .geocard.g-lat{border-left-color:#8a7f78}
  .gc-sens{font-size:1.1rem;text-align:center}
  .geocard.g-agg .gc-sens{color:#e07a6b}
  .geocard.g-alleg .gc-sens{color:#6fbf77}
  .geocard.g-lat .gc-sens{color:#b7aca3}
  .gc-main{display:flex;flex-direction:column;gap:3px;min-width:0}
  .gc-pays{color:var(--bone);font-weight:600;font-size:0.95rem}
  .gc-motif{color:var(--bone-dim);font-size:0.8rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .gc-meta{display:flex;flex-direction:column;align-items:flex-end;gap:3px;font-family:var(--mono);font-size:0.66rem;color:var(--bone-dim)}
  .gc-niv{color:var(--bone)}
  .gc-sev{letter-spacing:2px;color:var(--fort)}
  @media(max-width:860px){.gc-motif{white-space:normal}}
  .pays{color:var(--bone);font-weight:600}
  .scorebox{text-align:right;flex-shrink:0}
  .scorebox .sf{font-family:var(--display);font-weight:700;font-size:1.5rem;line-height:1}
  .lead[data-tier="contacter"] .sf{color:var(--fort)}
  .lead[data-tier="surveiller"] .sf{color:var(--watch)}
  .lead[data-tier="ignorer"] .sf{color:var(--bone-dim)}
  .scorebox .sd{font-family:var(--mono);font-size:0.58rem;color:var(--bone-dim);letter-spacing:0.06em;margin-top:3px;white-space:nowrap}
  .scorebox .se{font-family:var(--mono);font-size:0.5rem;color:var(--bone-faint);letter-spacing:0.12em;text-transform:uppercase;margin-top:2px;white-space:nowrap}
  .ltitle{font-family:var(--display);font-weight:500;font-size:1.02rem;line-height:1.3;margin:11px 0 12px;color:var(--bone)}
  .badges{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:13px}
  .badge{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.1em;text-transform:uppercase;padding:4px 8px;border-radius:4px;display:inline-flex;align-items:center;gap:5px}
  .badge.win-immediate{background:var(--fort-soft);color:#e08e98}
  .badge.win-court_terme{background:var(--watch-soft);color:#dcb079}
  .badge.win-indetermine{background:var(--low-soft);color:var(--bone-dim)}
  .badge.ecart{background:transparent;border:1px solid rgba(200,137,59,0.5);color:#dcb079}
  .badge.deplacement{background:rgba(224,142,152,0.16);color:#e08e98;border:1px solid rgba(224,142,152,0.55);font-weight:600}
  .badge.geoboost{background:rgba(192,57,43,0.20);color:#f0a090;border:1px solid rgba(192,57,43,0.6);font-weight:600}
  .lead.boosted{box-shadow:inset 0 0 0 1px rgba(192,57,43,0.35)}
  .scorebox .sfbase{font-size:0.9rem;color:var(--bone-faint);text-decoration:line-through;font-weight:400;font-family:var(--display)}
  .contact{border-top:1px solid var(--line-2);padding-top:12px;margin-top:2px}
  .contact .row{display:flex;gap:8px;font-size:0.8rem;margin-bottom:5px;align-items:baseline}
  .contact .k{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--bone-faint);min-width:62px;flex-shrink:0;padding-top:2px}
  .contact .v{color:var(--bone);word-break:break-word}
  .contact .v a{color:#d98a96;text-decoration:none}
  .contact .v a:hover{text-decoration:underline}
  .cible{background:rgba(111,14,39,0.16);border-left:2px solid var(--oxblood-2);padding:9px 11px;border-radius:0 5px 5px 0;font-size:0.78rem;color:var(--bone-dim);margin-top:11px;line-height:1.45}
  .cible b{color:var(--bone);font-weight:600}
  details.just{margin-top:11px;border-top:1px solid var(--line-2);padding-top:10px}
  details.just summary{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--bone-dim);cursor:pointer;list-style:none;display:flex;align-items:center;gap:7px;user-select:none}
  details.just summary::-webkit-details-marker{display:none}
  details.just summary .chev{transition:transform .2s;display:inline-block}
  details.just[open] summary .chev{transform:rotate(90deg)}
  details.just p{font-size:0.83rem;color:var(--bone-dim);line-height:1.55;margin-top:10px}
  .lead .foot{margin-top:auto;border-top:1px solid var(--line-2);padding:10px 16px 11px 19px;display:flex;justify-content:space-between;align-items:center;gap:10px}
  .lead .foot a.av{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.08em;color:var(--bone-dim);text-decoration:none}
  .lead .foot a.av:hover{color:var(--bone)}
  .lead .foot .grp{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.12em;color:var(--bone-faint)}
  .empty{grid-column:1/-1;text-align:center;padding:50px 20px;color:var(--bone-dim);font-family:var(--mono);font-size:0.8rem;letter-spacing:0.06em}
  footer{margin-top:34px;padding-top:18px;border-top:1px solid var(--line);font-family:var(--mono);font-size:0.64rem;color:var(--bone-faint);letter-spacing:0.06em;line-height:1.8}
  @media(max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}.zonegrid{grid-template-columns:repeat(2,1fr)}.leads{grid-template-columns:1fr}.runmeta{text-align:left}}
  @media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
  :focus-visible{outline:2px solid var(--watch);outline-offset:2px}

  /* Synthese executive */
  .exec{margin-top:18px;padding:15px 18px;background:linear-gradient(100deg,rgba(111,14,39,0.22),rgba(31,23,21,0.4));border:1px solid rgba(140,29,44,0.35);border-radius:9px;font-size:0.96rem;line-height:1.55;color:var(--bone)}
  .exec b{color:#e8a0ab;font-weight:600}
  .exec .lead-strong{font-family:var(--display);font-weight:500}
  /* Bloc carte + graphique cote a cote */
  .geo{display:grid;grid-template-columns:1.7fr 1fr;gap:13px;margin-top:24px}
  .panel{background:var(--ink-2);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  .panel .phead{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.22em;text-transform:uppercase;color:var(--bone-dim);padding:13px 16px 0}
  #map{height:340px;width:100%;margin-top:10px;background:#11100f;border-bottom-left-radius:10px;border-bottom-right-radius:10px}
  .leaflet-container{background:#13110f!important;font-family:var(--mono)!important}
  .leaflet-popup-content-wrapper{background:var(--ink-3);color:var(--bone);border-radius:7px;box-shadow:0 6px 24px rgba(0,0,0,0.5)}
  .leaflet-popup-tip{background:var(--ink-3)}
  .leaflet-popup-content{margin:11px 13px;font-family:var(--body)}
  .leaflet-popup-content b{font-family:var(--display)}
  .leaflet-control-attribution{background:rgba(20,16,15,0.7)!important;color:var(--bone-faint)!important}
  .leaflet-control-attribution a{color:var(--bone-dim)!important}
  .leaflet-bar a{background:var(--ink-3)!important;color:var(--bone)!important;border-color:var(--line)!important}
  /* Graphique repartition par zone */
  .zonechart{padding:14px 16px 16px;display:flex;flex-direction:column;gap:9px}
  .zrow{cursor:pointer}
  .zrow .zlab{display:flex;justify-content:space-between;font-family:var(--mono);font-size:0.66rem;letter-spacing:0.04em;color:var(--bone-dim);margin-bottom:4px}
  .zrow:hover .zlab{color:var(--bone)}
  .zrow[aria-pressed="true"] .zlab{color:var(--fort)}
  .ztrack{height:8px;background:var(--ink-3);border-radius:5px;overflow:hidden}
  .zfill{height:100%;border-radius:5px;background:linear-gradient(90deg,var(--oxblood),var(--fort));transition:width .4s ease}
  .zrow[aria-pressed="true"] .zfill{background:var(--fort)}
  /* Entree progressive */
  @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .lead{animation:rise .35s ease both}
  .panel,.exec,.stats .tile{animation:rise .4s ease both}
  @media(max-width:860px){.geo{grid-template-columns:1fr}#map{height:280px}}
  /* --- Fiche lead (modale) + actions --- */
  .foot .footacts{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .foot .act,.facts .act{cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--bone-dim);
    font:inherit;font-size:12px;padding:4px 10px;border-radius:6px;text-decoration:none;transition:.15s}
  .foot .act:hover,.facts .act:hover{border-color:var(--oxblood);color:var(--bone)}
  .foot .act.mail,.facts .act.mail{border-color:var(--oxblood);color:var(--fort)}
  .foot .act.mail:hover,.facts .act.mail:hover{background:var(--oxblood);color:#fff}
  .foot .act.contact{border-color:var(--oxblood);color:var(--fort)}
  .foot .act.contact:hover{background:var(--oxblood);color:#fff}
  .foot .act.contact.done{border-color:rgba(120,190,150,0.5);color:#7fae8f;background:transparent;cursor:default}
  .foot .act.contact.done:hover{background:transparent;color:#7fae8f}
  /* Écarter « pas pertinent » */
  .foot .act.ecart{border-color:transparent;color:var(--bone-faint);margin-left:auto}
  .foot .act.ecart:hover{border-color:rgba(150,150,150,0.4);color:var(--bone-dim)}
  .motifmenu{display:inline-flex;flex-wrap:wrap;gap:5px;align-items:center}
  .motifchip{font-size:11px}
  .motifchip:hover{border-color:var(--oxblood);color:var(--bone)}
  .motifannule{color:var(--bone-faint);border-color:transparent}
  .ecartes .ec-learn{font-family:var(--mono);font-size:0.66rem;color:var(--bone-dim);letter-spacing:0.05em;margin-bottom:9px}
  .ecrow{display:grid;grid-template-columns:auto 1fr auto auto;gap:10px;align-items:center;padding:6px 0;border-top:1px solid var(--line)}
  .ec-titre{color:var(--bone-dim);font-size:0.82rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ec-motif{font-family:var(--mono);font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;color:var(--fort)}
  .ecrow .act{font-size:11px}
  /* Surveiller un projet amont */
  .foot .act.surv{border-color:transparent;color:var(--bone-faint)}
  .foot .act.surv:hover{border-color:rgba(120,170,190,0.5);color:#8fbccf}
  .badge.surveille{background:rgba(120,170,190,0.14);color:#8fbccf;border:1px solid rgba(120,170,190,0.45)}
  .badge.attribok{background:rgba(95,160,110,0.18);color:#86c596;border:1px solid rgba(95,160,110,0.55);font-weight:600}
  .badge.renouv-imminent{background:rgba(224,142,152,0.18);color:#e08e98;border:1px solid rgba(224,142,152,0.55);font-weight:600}
  .badge.renouv-a_venir{background:rgba(120,170,190,0.14);color:#8fbccf;border:1px solid rgba(120,170,190,0.45)}
  .surv-head{font-family:var(--mono);font-size:0.6rem;letter-spacing:0.14em;text-transform:uppercase;color:var(--bone-dim);margin:10px 0 4px}
  .surv-gagnant{font-family:var(--mono);font-size:0.66rem;color:#86c596}
  .surv-attente{font-family:var(--mono);font-size:0.6rem;letter-spacing:0.08em;text-transform:uppercase;color:var(--bone-faint)}
  .modal-ov{position:fixed;inset:0;background:rgba(10,6,8,.72);backdrop-filter:blur(3px);
    display:none;align-items:flex-start;justify-content:center;z-index:1000;padding:40px 16px;overflow:auto}
  .modal-ov.open{display:flex}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:12px;max-width:640px;width:100%;
    box-shadow:0 24px 80px rgba(0,0,0,.6);animation:pop .18s ease}
  @keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .modal .mhead{padding:20px 22px 14px;border-bottom:1px solid var(--line);position:relative}
  .modal .mclose{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--bone-dim);
    font-size:22px;cursor:pointer;line-height:1}
  .modal .mclose:hover{color:var(--bone)}
  .modal .msrc{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--fort)}
  .modal h2{font-size:17px;margin:6px 0 0;color:var(--bone);padding-right:24px}
  .modal .mscore{display:flex;gap:18px;margin-top:12px;align-items:baseline}
  .modal .mscore .big{font-size:30px;font-weight:700;color:var(--bone)}
  .modal .mscore .sub{font-size:12px;color:var(--bone-dim)}
  .modal .mscore .sub-echelle{font-family:var(--mono);font-size:0.56rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--bone-faint);margin-top:2px}
  .modal .tlrow .tlech{display:block;font-family:var(--mono);font-size:0.5rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--bone-faint);font-weight:400}
  .modal .mbody{padding:16px 22px}
  .modal .frow{display:flex;gap:12px;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.05);font-size:13px}
  .modal .fk{flex:0 0 130px;color:var(--bone-dim)}
  .modal .fv{flex:1;color:var(--bone)}
  .modal .fv a{color:var(--fort)}
  .modal .mactions{display:flex;gap:10px;padding:16px 22px 22px;flex-wrap:wrap}
  .modal .mbtn{flex:1;min-width:160px;text-align:center;padding:11px 14px;border-radius:8px;font:inherit;
    font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;border:1px solid var(--line);
    background:transparent;color:var(--bone)}
  .modal .mbtn.primary{background:var(--oxblood);border-color:var(--oxblood);color:#fff}
  .modal .mbtn.primary:hover{background:var(--fort)}
  .modal .mbtn.ghost:hover{border-color:var(--oxblood);color:var(--fort)}
  /* --- Comptes chauds (BITD) + timeline --- */
  #comptes{margin:0 0 18px}
  .chead{display:flex;align-items:baseline;gap:10px;margin-bottom:10px;flex-wrap:wrap}
  .chead b{color:var(--bone);font-size:14px}
  .csub{color:var(--bone-dim);font-size:11px}
  .cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
  .ccard{text-align:left;cursor:pointer;background:var(--panel);border:1px solid var(--line);
    border-radius:9px;padding:11px 12px;color:var(--bone);font:inherit;transition:.15s}
  .ccard:hover{border-color:var(--oxblood);transform:translateY(-1px)}
  .ccard.t-chaud{border-left:3px solid var(--fort)}
  .ccard.t-tiede{border-left:3px solid var(--watch)}
  .ccard.t-froid{border-left:3px solid var(--line)}
  .ccard .cn{font-weight:600;font-size:13px;display:flex;gap:6px;align-items:center}
  .ccard .cmeta{color:var(--bone-dim);font-size:11px;margin-top:4px}
  .ccard .cbar{height:4px;background:rgba(255,255,255,.06);border-radius:3px;margin-top:8px;overflow:hidden}
  .ccard .cbar span{display:block;height:100%;background:var(--oxblood)}
  .modal .tlhead{color:var(--bone-dim);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px}
  .modal .timeline{display:flex;flex-direction:column;gap:2px}
  .modal .tlrow{display:grid;grid-template-columns:96px 1fr auto 42px;gap:8px;padding:6px 0;
    border-bottom:1px solid rgba(255,255,255,.05);font-size:12px}
  .modal .tlrow .tld{color:var(--bone-dim)}
  .modal .tlrow .tls{color:var(--fort);text-align:right;font-weight:600}
  .lensseg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-top:20px}
  .lensseg button{background:var(--ink-2);border:none;color:var(--bone-dim);font-family:var(--mono);font-size:0.72rem;letter-spacing:0.06em;padding:11px 18px;cursor:pointer;transition:.15s}
  .lensseg button span{opacity:.55;font-size:0.64rem}
  .lensseg button+button{border-left:1px solid var(--line)}
  .lensseg button[aria-pressed="true"]{background:var(--oxblood);color:var(--bone)}
  .fiche{background:var(--ink-2);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:12px}
  .fiche .fhead{display:flex;align-items:flex-start;gap:12px}
  .fiche .fav{width:38px;height:38px;border-radius:50%;background:var(--oxblood);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;color:var(--bone);flex-shrink:0}
  .fiche .fnom{font-weight:600;font-size:15px;color:var(--bone)}
  .fiche .fmeta{color:var(--bone-dim);font-size:12px;margin-top:2px;font-family:var(--mono);letter-spacing:.03em}
  .fiche .fprio{font-family:var(--mono);font-size:0.56rem;letter-spacing:.1em;text-transform:uppercase;padding:3px 8px;border-radius:4px;white-space:nowrap}
  .fiche .fprio.contacter{background:var(--fort-soft);color:var(--fort);border:1px solid rgba(192,39,58,.4)}
  .fiche .fprio.surveiller{background:var(--watch-soft);color:#dcb079;border:1px solid rgba(200,137,59,.4)}
  .fiche .fprio.ignorer{border:1px solid var(--line);color:var(--bone-dim)}
  .fiche .fsrc{font-family:var(--mono);font-size:0.5rem;letter-spacing:.08em;text-transform:uppercase;padding:2px 6px;border-radius:3px;white-space:nowrap;border:1px solid var(--line-2);color:var(--bone-dim)}
  .fiche .fsrc.titulaire{color:#e08e98;border-color:rgba(224,142,152,.45)}
  .fiche .fsrc.signal{color:#dcb079;border-color:rgba(200,137,59,.4)}
  .fiche .fsrc.dfi{color:#8fb9d9;border-color:rgba(143,185,217,.4)}
  .fiche .fsig{border-top:1px solid var(--line);margin-top:12px;padding-top:10px;display:flex;flex-direction:column;gap:7px}
  .fiche .fsr{display:flex;align-items:center;gap:10px;font-size:12.5px}
  .fiche .fsr .fsi{color:var(--bone);flex:1;min-width:0}
  .fiche .fsr .fsd{color:var(--bone-faint);font-family:var(--mono);font-size:11px;white-space:nowrap}
  .fiche .fenr{border-top:1px solid var(--line);margin-top:10px;padding-top:10px;display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:5px 16px;font-size:12.5px}
  .fiche .fenr .er{display:flex;justify-content:space-between;gap:8px;color:var(--bone)}
  .fiche .fenr .er .ek{color:var(--bone-dim)}
  .fiche .fmiss{border-top:1px solid var(--line);margin-top:10px;padding-top:10px;color:var(--watch);font-size:12px}
  .fsl{font-family:var(--mono);font-size:10.5px;color:var(--bone-dim);text-decoration:none;border-bottom:1px dotted var(--line);white-space:nowrap}
  .fsl:hover{color:var(--bone)}
  .fiche .fnosig{border-top:1px solid var(--line);margin-top:8px;padding-top:10px;color:var(--bone-faint);font-size:12px;font-style:italic}
  .fiche .facts{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .conv{margin:14px 0 4px;border:1px solid var(--line);border-radius:8px;background:var(--ink-2);font-size:0.8rem}
  .conv>summary{cursor:pointer;padding:10px 14px;font-family:var(--mono);font-size:0.62rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--bone-dim)}
  .conv[open]>summary{border-bottom:1px solid var(--line);color:var(--bone)}
  .conv .convmeta,.conv .convempty{padding:10px 14px;color:var(--bone-dim);line-height:1.5}
  .conv .convtitre{padding:8px 14px 4px;font-family:var(--mono);font-size:0.56rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--bone-faint)}
  .conv .convtab{width:100%;border-collapse:collapse;font-size:0.78rem}
  .conv .convtab th,.conv .convtab td{text-align:right;padding:5px 14px;border-top:1px solid var(--line)}
  .conv .convtab th:first-child,.conv .convtab td:first-child{text-align:left}
  .conv .convtab thead th{color:var(--bone-faint);font-weight:400;font-family:var(--mono);font-size:0.56rem;letter-spacing:0.08em;text-transform:uppercase}
  .conv .convneutre td{color:var(--bone-dim)}
  .conv .convactif td{color:var(--bone)}
  .conv .convmute{font-family:var(--mono);font-size:0.5rem;color:var(--bone-faint);letter-spacing:0.05em}
</style>
</head>
<body>
<div class="wrap">
  <header class="mast">
    <div class="mast-top">
      <div class="brand">
        <div class="mark"></div>
        <div><h1>Radar Amarante</h1><div class="sub">Tableau de situation, BU Escorte</div></div>
      </div>
      <div class="runmeta" id="runmeta"></div>
    </div>
    <div class="lensseg" id="lensseg" role="group" aria-label="Vue">
      <button data-lens="avis" aria-pressed="true">Opportunités <span>· avis</span></button>
      <button data-lens="todo" aria-pressed="false">À faire <span>· cette semaine</span></button>
      <button data-lens="entreprises" aria-pressed="false">Entreprises <span>· 360°</span></button>
      <button data-lens="cibles" aria-pressed="false">Cibles privées <span>· prospects</span></button>
      <button data-lens="titulaires" aria-pressed="false">Titulaires <span>· attributions</span></button>
      <button data-lens="geo" aria-pressed="false">Géopolitique <span>· alertes</span></button>
    </div>
    <div class="stats" id="stats"></div>
    <div class="exec" id="exec"></div>
    <details class="conv" id="conv"><summary>Conversion observée (gagné / perdu)</summary><div id="convbody"></div></details>
    <details class="conv ecartes" id="ecartes"><summary id="ecartes-sum">Écartés</summary><div id="ecartes-body"></div></details>
    <details class="conv surveillance" id="surveillance"><summary id="surv-sum">Surveillance</summary><div id="surv-body"></div></details>
  </header>
  <section class="geo">
    <div class="panel">
      <div class="phead">Carte des opportunités</div>
      <div id="map"></div>
    </div>
    <div class="panel">
      <div class="phead">Répartition par zone</div>
      <div class="zonechart" id="zonechart"></div>
    </div>
  </section>
  <div class="period" id="period"></div>
  <div id="comptes"></div>
  <div class="controls">
    <label class="search">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      <input type="text" id="search" placeholder="Filtrer par pays, agence, mot-clé..." autocomplete="off">
    </label>
    <div class="seg" id="srcseg" role="group" aria-label="Source"></div>
    <label class="pick" id="secteurPick" title="Filtrer par secteur">
      <span>Secteur</span>
      <select id="secteurSel"></select>
    </label>
    <label class="pick" id="groupPick" title="Regrouper la liste par">
      <span>Grouper</span>
      <select id="groupSel">
        <option value="aucun">Aucun</option>
        <option value="zone">Zone</option>
        <option value="secteur">Secteur</option>
        <option value="action">Priorité</option>
      </select>
    </label>
    <div class="seg" id="triseg" role="group" aria-label="Tri">
      <button data-tri="score" aria-pressed="true">Importance</button>
      <button data-tri="urgence" aria-pressed="false">Urgence</button>
      <button data-tri="date" aria-pressed="false">Récents</button>
    </div>
    <button class="clearz" id="clearz">Réinitialiser</button>
    <button class="export" id="export" title="Exporter la sélection courante en CSV (ouvrable dans Excel)">Exporter</button>
  </div>
  <div class="count" id="count"></div>
  <div class="sante-run" id="santeRun"></div>
  <div class="leads" id="leads"></div>
  <footer id="foot"></footer>
</div>
<div class="modal-ov" id="modal" role="dialog" aria-modal="true">
  <div class="modal" id="modalcard"></div>
</div>
<script>
const LEADS = __LEADS_JSON__;
const GEO = __GEO_JSON__;
const SECTEURS = __SECTEURS_JSON__;
const SANTE = __SANTE_JSON__;
const META = __META_JSON__;
const WATCHLIST = __WATCHLIST_JSON__;
const CONVERSION = __CONVERSION_JSON__;
const SUIVI_URL = __SUIVI_URL__;
const SUIVI_TOKEN = __SUIVI_TOKEN__;
// Servi par l'application (radar_app) : le bouton ecrit AUSSI en base.
const API_STATUT = __API_STATUT__;
// Le bouton s'affiche des qu'UNE destination existe : Apps Script (page
// statique) ou l'API (application). Sans quoi l'app, qui n'a pas de secret
// Apps Script, n'afficherait aucun bouton.
const SUIVI_ON = !!SUIVI_URL || API_STATUT;
// Correspondance source -> onglet, cle d'ecriture dans radar_statuts.
const ONGLET_SRC = {TED:'ted_radar',BM:'bm_radar',AFDB:'afdb_radar',ADB:'adb_radar',
  EBRD:'ebrd_radar',UNGM:'ungm_radar',RW:'reliefweb_radar','PRIVÉ':'prive_radar',
  ATTRIB:'attributions_radar',MIGA:'miga_radar',IFC:'ifc_radar',IDB:'idb_radar',BMP:'bm_projets_radar',PROPARCO:'proparco_radar',DFC:'dfc_radar'};
// Statut CRM deja pose (serveur) : survit au changement de navigateur, ce que
// le localStorage seul ne permettait pas.
function dejaContacte(l){const s=String(l.statut||'').toLowerCase();
  return s.indexOf('contact')>=0||s.indexOf('gagn')>=0||s.indexOf('perd')>=0||s.indexOf('relanc')>=0;}
const CONTACTES = new Set((()=>{try{return JSON.parse(localStorage.getItem('suivi_contactes')||'[]')}catch(e){return[]}})());
const SRC_SUIVI = {TED:'TED',BM:'Banque Mondiale','PRIVÉ':'Privé BITD',RW:'ReliefWeb',ONG:'ReliefWeb',PROPARCO:'Proparco',DFC:'DFC'};
function leadId(l){return l.pub||l.lien||(l.src+'|'+l.pays+'|'+l.agence+'|'+l.titre);}
// --- Ecarter une opportunite « pas pertinente » (12/08/2026) ---
// Bouton -> statut 'non_pertinent' + RAISON. Le lead disparait des vues (masque)
// et part dans la section « Ecartes » (reversible). Persistance : /api/statut
// (Postgres) + Apps Script + localStorage (affichage immediat, meme hors ligne).
const MOTIFS_ECART=[
  {k:'hors_zone',l:'Hors zone'},{k:'secteur',l:'Secteur non pertinent'},
  {k:'trop_petit',l:'Trop petit'},{k:'pas_terrain',l:'Pas de déploiement terrain'},
  {k:'doublon',l:'Doublon'},{k:'autre',l:'Autre'}];
const MOTIF_LABEL=Object.fromEntries(MOTIFS_ECART.map(m=>[m.k,m.l]));
let ECARTES=new Set(); let MOTIFS_LOCAUX={};
try{ECARTES=new Set(JSON.parse(localStorage.getItem('ecartes')||'[]'));}catch(e){}
try{MOTIFS_LOCAUX=JSON.parse(localStorage.getItem('ecartes_motifs')||'{}');}catch(e){}
function estEcarte(l){return (l&&l.statut==='non_pertinent')||ECARTES.has(leadId(l));}
function motifDe(l){return MOTIFS_LOCAUX[leadId(l)]||(l&&l.motif_ecart)||'';}
function _sauverEcartes(){
  try{localStorage.setItem('ecartes',JSON.stringify([...ECARTES]));}catch(e){}
  try{localStorage.setItem('ecartes_motifs',JSON.stringify(MOTIFS_LOCAUX));}catch(e){}
}
function _envoyerStatut(l,statut,motif){
  const cid=leadId(l);
  if(SUIVI_URL){const p={token:SUIVI_TOKEN,id:cid,source:SRC_SUIVI[l.src]||l.src,
    statut:statut,motif:motif||'',pays:l.pays||'',zone:l.zone||'',agence:l.agence||'',
    titre:l.titre||'',lien:l.lien||'',score:l.final};
    fetch(SUIVI_URL,{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain;charset=utf-8'},body:JSON.stringify(p)}).catch(function(){});}
  if(API_STATUT&&l.pub&&ONGLET_SRC[l.src]){
    fetch('/api/statut',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({onglet:ONGLET_SRC[l.src],publication_number:l.pub,statut:statut,motif:motif||''})}).catch(function(){});}
}
function marquerNonPertinent(idx,motif){
  const l=AFFICHES[idx]; if(!l)return;
  const cid=leadId(l); ECARTES.add(cid); MOTIFS_LOCAUX[cid]=motif||'autre'; _sauverEcartes();
  _envoyerStatut(l,'non_pertinent',motif||'autre');
  render();
}
function restaurerEcarte(cid){
  // Retrouver le lead par son id pour renvoyer le statut au serveur.
  const l=LEADS.find(x=>leadId(x)===cid);
  ECARTES.delete(cid); delete MOTIFS_LOCAUX[cid]; _sauverEcartes();
  if(l) _envoyerStatut(l,'nouveau','');
  render();
}
function menuRaisons(idx){
  return '<span class="motifmenu">'+MOTIFS_ECART.map(m=>
    '<button class="act motifchip" type="button" data-ecartmotif="'+idx+'|'+m.k+'">'+esc(m.l)+'</button>').join('')+
    '<button class="act motifannule" type="button" data-ecartannule="1">annuler</button></span>';
}
function renderEcartes(){
  const box=document.getElementById('ecartes-body');
  const sum=document.getElementById('ecartes-sum');
  const dets=document.getElementById('ecartes');
  if(!box||!dets)return;
  const ecs=LEADS.filter(l=>estEcarte(l));
  sum.textContent = ecs.length ? ('Écartés · '+ecs.length) : 'Écartés';
  if(!ecs.length){ dets.style.display='none'; box.innerHTML=''; return; }
  dets.style.display='';
  const parMotif={};
  ecs.forEach(l=>{ const m=motifDe(l)||'autre'; parMotif[m]=(parMotif[m]||0)+1; });
  const top=Object.entries(parMotif).sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>(MOTIF_LABEL[k]||k)+' ×'+v).join(' · ');
  const lignes=ecs.map(l=>{
    const cid=leadId(l), m=motifDe(l);
    return '<div class="ecrow"><span class="src '+l.src.toLowerCase()+'">'+esc(SRC_LABEL[l.src]||l.src)+'</span>'+
      '<span class="ec-titre">'+esc(l.titre)+'</span>'+
      '<span class="ec-motif">'+(m?esc(MOTIF_LABEL[m]||m):'—')+'</span>'+
      '<button class="act" type="button" data-restaurer="'+esc(cid)+'">↺ Restaurer</button></div>';
  }).join('');
  box.innerHTML='<div class="ec-learn">Raisons : '+(top||'—')+'</div>'+lignes;
}
// --- Surveiller un projet amont : le run verifie si l'attribution a paru ---
let SURVEILLES=new Set();
try{SURVEILLES=new Set(JSON.parse(localStorage.getItem('surveilles')||'[]'));}catch(e){}
function estSurveille(l){return (l&&(l.statut==='surveille'||l.statut==='attribution_publiee'))||SURVEILLES.has(leadId(l));}
function attribPubliee(l){return l&&l.statut==='attribution_publiee';}
function gagnantSurveille(l){return attribPubliee(l)?(l.motif_ecart||''):'';}
function marquerSurveille(idx){
  const l=AFFICHES[idx]; if(!l)return;
  SURVEILLES.add(leadId(l));
  try{localStorage.setItem('surveilles',JSON.stringify([...SURVEILLES]));}catch(e){}
  _envoyerStatut(l,'surveille','');
  render();
}
function arreterSurveille(cid){
  const l=LEADS.find(x=>leadId(x)===cid);
  SURVEILLES.delete(cid);
  try{localStorage.setItem('surveilles',JSON.stringify([...SURVEILLES]));}catch(e){}
  if(l)_envoyerStatut(l,'nouveau','');
  render();
}
function renderSurveillance(){
  const box=document.getElementById('surv-body');
  const sum=document.getElementById('surv-sum');
  const dets=document.getElementById('surveillance');
  if(!box||!dets)return;
  const surv=LEADS.filter(l=>estSurveille(l));
  const publies=surv.filter(l=>attribPubliee(l));
  sum.textContent = surv.length ? ('Surveillance · '+surv.length+(publies.length?' · '+publies.length+' attribution(s)':'')) : 'Surveillance';
  if(!surv.length){ dets.style.display='none'; box.innerHTML=''; return; }
  dets.style.display='';
  const ligne=(l,gagnant)=>{
    const cid=leadId(l);
    return '<div class="ecrow"><span class="src '+l.src.toLowerCase()+'">'+esc(SRC_LABEL[l.src]||l.src)+'</span>'+
      '<span class="ec-titre">'+esc(l.pays)+' · '+esc(l.titre)+'</span>'+
      (gagnant?'<span class="surv-gagnant">✓ '+esc(gagnant)+'</span>':'<span class="surv-attente">en attente</span>')+
      '<button class="act" type="button" data-arreter-surv="'+esc(cid)+'">arrêter</button></div>';
  };
  const secPub = publies.length ? '<div class="surv-head">Attribution publiée</div>'+publies.map(l=>ligne(l,gagnantSurveille(l))).join('') : '';
  const attente = surv.filter(l=>!attribPubliee(l));
  const secAtt = attente.length ? '<div class="surv-head">En attente d\'attribution</div>'+attente.map(l=>ligne(l,'')).join('') : '';
  box.innerHTML=secPub+secAtt;
}
function marquerContacte(idx,btn){
  const l=AFFICHES[idx]; if(!l||!SUIVI_ON)return;
  const cid=leadId(l);
  btn.disabled=true; btn.classList.add('done'); btn.textContent='✓ Contacté';
  CONTACTES.add(cid); try{localStorage.setItem('suivi_contactes',JSON.stringify([...CONTACTES]));}catch(e){}
  // Date de contact (locale) : alimente le bucket « à relancer » du cockpit.
  try{const m=JSON.parse(localStorage.getItem('suivi_dates')||'{}');m[cid]=new Date().toISOString().slice(0,10);localStorage.setItem('suivi_dates',JSON.stringify(m));}catch(e){}
  const p={token:SUIVI_TOKEN,id:cid,source:SRC_SUIVI[l.src]||l.src,date_det:l.date_det||'',
    pays:l.pays||'',zone:l.zone||'',agence:l.agence||'',titre:l.titre||'',lien:l.lien||'',
    score:l.final,surete:l.surete,comm:l.comm,action:l.action||'',fenetre:l.win||'',
    contact:(l.nom&&l.nom!=='n.c.')?l.nom:'',email:(l.email&&l.email!=='n.c.')?l.email:''};
  // 1) Sheet via Apps Script, tant qu'il reste la reference CRM.
  if(SUIVI_URL){fetch(SUIVI_URL,{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain;charset=utf-8'},body:JSON.stringify(p)}).catch(function(){});}
  // 2) Base, quand la page est servie par l'application. Best-effort : un
  //    echec ici ne doit jamais bloquer l'interface (le bouton est deja passe
  //    en « Contacte » et l'ecriture Sheet a eu lieu).
  if(API_STATUT&&l.pub&&ONGLET_SRC[l.src]){
    fetch('/api/statut',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({onglet:ONGLET_SRC[l.src],publication_number:l.pub,statut:'contacte'})}).catch(function(){});
  }
}
const ORDRE_ZONES = ["Afrique de l'Ouest","Sahel","Afrique centrale","Afrique de l'Est","Afrique australe","Afrique du Nord","Proche-Orient","Péninsule arabique","Asie centrale","Asie du Sud","Asie du Sud-Est","Caucase","Balkans","Europe de l'Est","Caraïbes","Amérique latine","Europe de l'Ouest","Outre-mer","Non classé"];
const winLabel={immediate:'Fenêtre immédiate',court_terme:'Court terme',indetermine:'Fenêtre indéterminée'};
const SRC_LABEL={BM:'Banque Mondiale',TED:'TED',AFDB:'AfDB',ADB:'ADB',EBRD:'EBRD',UNGM:'UNGM · ONU',RW:'ReliefWeb',MIGA:'MIGA',IFC:'IFC',IDB:'IDB · Amérique latine',BMP:'BM Projet · amont','PRIVÉ':'Privé · BITD',ATTRIB:'Titulaire'};
// Etiquette d'echelle de score. TROIS familles NON comparables entre elles :
//   - AVIS (TED/BM/bailleurs, bareme additif) : « echelle avis » ;
//   - SIGNAL PRIVE (bareme multiplicatif)      : « echelle signal » ;
//   - TITULAIRE (ATTRIB)                        : DEUX cas distincts.
// Un 6 avis ne vaut pas un 6 signal ni un 6 titulaire : l'etiquette empeche la
// comparaison directe trompeuse, source de la confusion « c'est mal trie ».
//
// Pour un TITULAIRE, on distingue en plus l'origine du score, sinon un
// titulaire analyse par le modele (vrais scores surete/commercial) porterait
// la meme etiquette « indicatif » qu'un titulaire jamais analyse (score
// deterministe zone+secteur+valeur recopie). C'est precisement le cas qui
// donnait l'impression que les attributions « ne veulent rien dire ».
function echelleLabel(l){
  const src=(typeof l==='string')?l:(l&&l.src);   // tolere l'ancien appel(src)
  if(src==='PRIVÉ') return 'échelle signal';
  if(src==='ATTRIB') return (l&&l.analysee)?'sûreté analysée':'indice titulaire';
  return 'échelle avis';
}
let AFFICHES=[];

// Pays francophones (choix de la langue du mail). Les entreprises BITD sont
// francophones par nature.
const FRANCO=['france','mali','niger','tchad','senegal','ivoire','burkina','benin','togo',
'guinee','cameroun','gabon','congo','rdc','centrafrique','djibouti','madagascar','maroc','algerie',
'tunisie','mauritanie','liban','haiti','belgique','suisse','luxembourg','monaco','comores','burundi',
'rwanda','seychelles','vanuatu'];
function sansAccent(s){return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');}
function langue(l){
  if(l.src==='PRIVÉ')return 'fr';
  // Match par MOT entier (évite "niger" ⊂ "nigeria").
  const p=' '+sansAccent(l.pays).replace(/[^a-z ]/g,' ').replace(/\s+/g,' ').trim()+' ';
  return FRANCO.some(f=>p.includes(' '+f+' '))?'fr':'en';
}

// Brouillon d'email contextualise (pre-rempli, a ajuster avant envoi).
function buildEmail(l){
  const fr=langue(l)==='fr';
  const pays=l.pays||(fr?'la zone concernée':'the area');
  const zone=l.zone&&l.zone!=='Non classé'?l.zone:(fr?'les zones sensibles':'high-risk areas');
  if(l.src==='PRIVÉ'){
    const act=l.grp&&l.grp!=='signal'?l.grp.replace(/_/g,' '):(fr?'déploiement':'deployment');
    if(fr)return{subject:`Sûreté de vos équipes déployées — ${pays}`,
      body:`Madame, Monsieur,\n\nNous suivons le développement de ${l.agence} à l'international. Un déploiement de vos équipes en ${pays} (${act}) peut impliquer des enjeux de sûreté pour les personnels sur place.\n\nAmarante International sécurise les collaborateurs de la BITD en environnement sensible : protection rapprochée, escorte, chauffeurs de sécurité, évaluation de sites et conseil sûreté.\n\nSi le sujet est d'actualité pour vos prochaines missions, je serais ravi d'en échanger quelques minutes.\n\nBien cordialement,\n[Votre nom]\nAmarante International`};
    return{subject:`Security for your deployed teams — ${pays}`,
      body:`Dear Sir or Madam,\n\nWe are following ${l.agence}'s international development. Deploying your teams to ${pays} (${act}) may raise security considerations for your personnel on the ground.\n\nAmarante International protects defence-industry staff in complex environments: close protection, secure transport, site assessment and security advisory.\n\nIf this is relevant to your upcoming missions, I would welcome a brief call.\n\nKind regards,\n[Your name]\nAmarante International`};
  }
  // Marches publics (TED / BM)
  const titre=l.titre||(fr?'votre projet':'your project');
  if(fr)return{subject:`Sûreté des équipes — ${pays}`,
    body:`Madame, Monsieur,\n\nJe me permets de vous contacter au sujet de « ${titre} », en ${pays}.\n\nAmarante International accompagne les organisations déployant du personnel en environnement complexe : protection rapprochée, escorte sécurisée, chauffeurs de sécurité et conseil sûreté. Nous intervenons régulièrement en ${zone}.\n\nSi la sûreté des équipes mobilisées sur ce projet est un sujet, je serais ravi d'échanger quelques minutes.\n\nBien cordialement,\n[Votre nom]\nAmarante International`};
  return{subject:`Security for deployed teams — ${pays}`,
    body:`Dear Sir or Madam,\n\nI am reaching out regarding "${titre}", in ${pays}.\n\nAmarante International supports organisations deploying staff in complex environments: close protection, secure transport and security advisory. We operate regularly across ${zone}.\n\nIf the safety of the teams involved in this project is a consideration, I would welcome a brief call.\n\nKind regards,\n[Your name]\nAmarante International`};
}
function mailtoHref(l){
  const e=buildEmail(l);
  const to=(l.email&&l.email!=='n.c.')?encodeURIComponent(l.email):'';
  return `mailto:${to}?subject=${encodeURIComponent(e.subject)}&body=${encodeURIComponent(e.body)}`;
}
let state={zone:null,src:'all',q:'',action:'contacter',mois:null,tri:'score',lens:'avis',secteur:'all',group:'aucun'};

// Filtre de la VUE AVIS (appels d'offres). Les entreprises (PRIVÉ, ATTRIB)
// vivent dans la lentille Entreprises et sont exclues ici.
function match(l, ignore){
  ignore = ignore || {};
  if(l.src==='PRIVÉ' || l.src==='ATTRIB') return false;
  if(!ignore.ecarte && estEcarte(l)) return false;
  if(!ignore.action && state.action!=='all' && l.action!==state.action) return false;
  if(!ignore.secteur && state.secteur!=='all' && (l.sect||'Autre')!==state.secteur) return false;
  if(!ignore.mois && state.mois && l.mois!==state.mois) return false;
  if(!ignore.zone && state.zone && l.zone!==state.zone) return false;
  if(!ignore.src && state.src!=='all' && l.src!==state.src) return false;
  if(!ignore.q && state.q){const hay=(l.pays+' '+l.agence+' '+l.titre+' '+l.zone+' '+l.nom).toLowerCase(); if(!hay.includes(state.q)) return false;}
  return true;
}

// --- Lentille Entreprises : regroupe PRIVÉ + ATTRIB par entreprise ---
// Normalisation PRUDENTE (decision validee) : minuscules, ponctuation, formes
// juridiques. On prefere deux fiches a fusionner a la main qu'une fusion abusive.
function normEntreprise(s){
  // Repli pour anciennes pages en cache ; MIROIR EXACT de _norm_ent (Python),
  // qui reste la source de verite (ent_cle est precalcule cote serveur).
  return sansAccent(s)
    .replace(/&/g,' ')
    .replace(/\./g,'')
    .replace(/[,'()\-/]/g,' ')
    .replace(/^\s*the\s+/,' ')
    .replace(/\b(sa|sas|sarl|sasu|eurl|spa|srl|gmbh|ltd|ltda|limited|llc|llp|inc|incorporated|plc|pvt|bv|nv|ag|co|company|companies|corp|corporation|group|groupe|holding|international|intl|and|et)\b/g,' ')
    .replace(/[^a-z0-9 ]/g,' ').replace(/\s+/g,' ').trim();
}
const RANG_ACTION={contacter:3,surveiller:2,ignorer:1,aucun:0};
// Choix du libelle de fiche : on prefere une forme en casse mixte (plus
// lisible qu'un nom tout en majuscules), puis la plus complete.
function meilleurNom(a,b){
  if(!a)return b; if(!b)return a;
  const au=a===a.toUpperCase(), bu=b===b.toUpperCase();
  if(au!==bu) return au?b:a;
  return b.length>a.length?b:a;
}
// --- Finalisation commune d'une fiche entreprise (gere le cas sans signal) ---
function finaliserFiche(f){
  let prio='ignorer';
  f.signaux.forEach(s=>{ if((RANG_ACTION[s.action]||0)>(RANG_ACTION[prio]||0)) prio=s.action; });
  if(!f.signaux.length) prio='aucun';                 // cible surveillee, sans signal recent
  else if(!(prio in RANG_ACTION)) prio='surveiller';
  let enr={nom:'',email:'',siren:'',ca:''};
  f.signaux.forEach(s=>{
    if(!enr.nom && s.nom && s.nom!=='n.c.') enr.nom=s.nom;
    if(!enr.email && s.email && s.email!=='n.c.') enr.email=s.email;
    if(!enr.siren && s.siren) enr.siren=s.siren;
    if(!enr.ca && s.ca) enr.ca=s.ca;
  });
  const repr=f.signaux.find(s=>s.email&&s.email!=='n.c.')||f.signaux[0]||null;
  const meilleur=f.signaux.length?f.signaux.reduce((a,b)=>b.final>a.final?b:a):null;
  const dernier=f.signaux.length?(f.signaux.reduce((a,b)=>((b.date_det||'')>(a.date_det||'')?b:a)).date_det||''):'';
  // analysee : au moins un signal de la fiche porte une vraie analyse LLM
  // (attribution passee par attributions_analyse). Sert a faire remonter les
  // titulaires qualifies devant ceux encore sur le score deterministe.
  const analysee=f.signaux.some(s=>s.analysee);
  const sectClasses=[...(f.sectSet||new Set())];
  return Object.assign(f,{zones:[...f.zones],secteurs:[...f.secteurs],
    sectClasses,sectPrimary:sectClasses[0]||'Autre',
    prio,enr,repr,meilleur,dernier,analysee,n:f.signaux.length});
}
function _triFiches(a,b){
  return (RANG_ACTION[b.prio]-RANG_ACTION[a.prio])
    || ((b.meilleur?b.meilleur.final:0)-(a.meilleur?a.meilleur.final:0))
    || ((b.dernier||'').localeCompare(a.dernier||''));
}
// --- Lentille CIBLES PRIVEES : ta watchlist (toutes) + signaux PRIVE croises ---
function agregerCibles(){
  const parCle={};
  WATCHLIST.forEach(w=>{
    const nom=w.entreprise; if(!nom) return;
    const cle=w.ent_cle||normEntreprise(nom)||sansAccent(nom);
    if(!parCle[cle]) parCle[cle]={cle,nom,signaux:[],zones:new Set(),secteurs:new Set(),sectSet:new Set(),cible:true};
    if(w.secteur) parCle[cle].secteurs.add(w.secteur);
    if(w.sect) parCle[cle].sectSet.add(w.sect);
  });
  LEADS.filter(l=>l.src==='PRIVÉ').forEach(l=>{
    const nom=(l.entreprise&&l.entreprise!=='n.c.')?l.entreprise:(l.agence||'?');
    const cle=l.ent_cle||normEntreprise(nom)||sansAccent(nom);
    if(!parCle[cle]) parCle[cle]={cle,nom,signaux:[],zones:new Set(),secteurs:new Set(),sectSet:new Set(),cible:false};
    const f=parCle[cle]; f.signaux.push(l); f.nom=meilleurNom(f.nom,nom);
    if(l.zone&&l.zone!=='Non classé') f.zones.add(l.zone);
    if(l.grp&&l.grp!=='signal'&&l.grp!=='AT') f.secteurs.add(l.grp.replace(/_/g,' '));
    if(l.sect) f.sectSet.add(l.sect);
  });
  return Object.values(parCle).map(finaliserFiche).sort(_triFiches);
}
// --- Lentille TITULAIRES : attributions (ATTRIB) uniquement ---
function agregerTitulaires(){
  const parCle={};
  LEADS.filter(l=>l.src==='ATTRIB').forEach(l=>{
    const nom=(l.entreprise&&l.entreprise!=='n.c.')?l.entreprise:(l.agence||'?');
    const cle=l.ent_cle||normEntreprise(nom)||sansAccent(nom);
    if(!parCle[cle]) parCle[cle]={cle,nom,signaux:[],zones:new Set(),secteurs:new Set(),sectSet:new Set(),cible:false};
    const f=parCle[cle]; f.signaux.push(l); f.nom=meilleurNom(f.nom,nom);
    if(l.zone&&l.zone!=='Non classé') f.zones.add(l.zone);
    if(l.grp&&l.grp!=='signal'&&l.grp!=='AT') f.secteurs.add(l.grp.replace(/_/g,' '));
    if(l.sect) f.sectSet.add(l.sect);
  });
  return Object.values(parCle).map(finaliserFiche)
    // Les titulaires ANALYSES (vrais scores surete/commercial) remontent
    // devant ceux encore sur l'indice deterministe : sinon un indice gonfle
    // par la zone et le montant pouvait passer devant un prospect qualifie.
    // A analyse egale, tri par score. C'est la reponse au « mal trie ».
    .sort((a,b)=>((b.analysee?1:0)-(a.analysee?1:0))
      || ((b.meilleur?b.meilleur.final:0)-(a.meilleur?a.meilleur.final:0)));
}
// --- Lentille ENTREPRISES (360) : fiche UNIFIEE par entreprise ---------------
// Fusionne les trois univers sur la meme cle normalisee : la watchlist curee,
// les signaux PRIVE (deploiement amont) et les titulaires ATTRIB (marches
// gagnes). Une societe a la fois surveillee, active dans la presse ET gagnante
// d'un marche apparait UNE SEULE FOIS, avec tous ses signaux et des facettes de
// provenance (Watchlist / Signal / Titulaire). Repond a "tout ce que je sais
// sur X" au lieu de fragmenter la meme entreprise en deux lentilles.
// Sources DFI a sponsor prive nomme : elles nourrissent les fiches (une
// entreprise financee par IFC/MIGA/Proparco/DFC est un prospect, pas une
// agence). Les avis-tender (TED/BM/AfDB...) nomment l'acheteur -> exclus.
const SRC_DFI=new Set(['IFC','MIGA','PROPARCO','DFC']);
function agregerEntreprises(){
  const parCle={};
  function fiche(cle,nom){
    if(!parCle[cle]) parCle[cle]={cle,nom,signaux:[],zones:new Set(),secteurs:new Set(),sectSet:new Set(),cible:false,srcSet:new Set()};
    return parCle[cle];
  }
  WATCHLIST.forEach(w=>{
    const nom=w.entreprise; if(!nom) return;
    const cle=w.ent_cle||normEntreprise(nom)||sansAccent(nom);
    const f=fiche(cle,nom); f.cible=true; f.srcSet.add('watchlist');
    if(w.secteur) f.secteurs.add(w.secteur);
    if(w.sect) f.sectSet.add(w.sect);
  });
  LEADS.filter(l=>l.src==='PRIVÉ'||l.src==='ATTRIB'||SRC_DFI.has(l.src)).forEach(l=>{
    const nom=(l.entreprise&&l.entreprise!=='n.c.')?l.entreprise:(l.agence||'?');
    const cle=l.ent_cle||normEntreprise(nom)||sansAccent(nom);
    const f=fiche(cle,nom); f.nom=meilleurNom(f.nom,nom);
    f.signaux.push(l); f.srcSet.add(l.src==='PRIVÉ'?'signal':(l.src==='ATTRIB'?'titulaire':'dfi'));
    if(l.zone&&l.zone!=='Non classé') f.zones.add(l.zone);
    if(l.grp&&l.grp!=='signal'&&l.grp!=='AT') f.secteurs.add(l.grp.replace(/_/g,' '));
    if(l.sect) f.sectSet.add(l.sect);
  });
  return Object.values(parCle).map(f=>{
    const x=finaliserFiche(f);
    // Ordre lisible des facettes : watchlist, signal, titulaire.
    x.srcs=['watchlist','dfi','signal','titulaire'].filter(s=>f.srcSet.has(s));
    return x;
  }).sort(_triFiches);
}
function fichesCourantes(){
  if(state.lens==='entreprises') return agregerEntreprises();
  if(state.lens==='cibles') return agregerCibles();
  if(state.lens==='titulaires') return agregerTitulaires();
  return [];
}
function vueFiches(){ return state.lens==='cibles'||state.lens==='titulaires'||state.lens==='entreprises'; }
// Un signal passe les filtres transverses (zone / mois / recherche) ?
function signalOK(s, ignore){
  ignore = ignore || {};
  if(!ignore.zone && state.zone && s.zone!==state.zone) return false;
  if(!ignore.mois && state.mois && s.mois!==state.mois) return false;
  if(!ignore.q && state.q){const hay=(s.pays+' '+(s.entreprise||s.agence)+' '+s.titre+' '+s.zone+' '+s.grp).toLowerCase(); if(!hay.includes(state.q)) return false;}
  return true;
}
// Filtre d'action selon la lentille : par priorite (cibles) ou par tranche de
// score (titulaires). En vue avis on n'utilise pas cette fonction.
function ficheMatchAction(f){
  if(state.lens==='titulaires'){
    const sc=f.meilleur?f.meilleur.final:0;
    if(state.action==='fort') return sc>=6;
    if(state.action==='moyen') return sc>=4&&sc<6;
    if(state.action==='faible') return sc<4;
    return true;                         // 'all' (ou defaut) : tout
  }
  if(state.lens==='entreprises'){
    if(state.action==='all') return true;
    if(state.action==='titulaire') return (f.srcs||[]).includes('titulaire');
    return f.prio===state.action;        // contacter / surveiller / aucun
  }
  return state.action==='all' || f.prio===state.action;
}
// Une fiche est visible si son action/tranche matche ET, si elle a des signaux,
// qu'au moins un passe les filtres transverses. Une cible SANS signal (watchlist
// pure) reste visible tant qu'aucun filtre zone/mois/recherche n'est actif.
function ficheOK(f, ignore){
  ignore = ignore || {};
  if(!ignore.action && !ficheMatchAction(f)) return false;
  if(!ignore.secteur && state.secteur!=='all' && !(f.sectSet&&f.sectSet.has(state.secteur))) return false;
  if(!f.signaux.length){
    if((!ignore.zone&&state.zone)||(!ignore.mois&&state.mois)||(!ignore.q&&state.q)) return false;
    return true;
  }
  return f.signaux.some(s=>signalOK(s, ignore));
}
// Liste des unites courantes (avis en vue avis, signaux d'entreprises en vue
// entreprises) pour alimenter la carte, le graphe de zones et les periodes.
function signauxCourants(ignore){
  ignore = ignore || {};
  if(vueFiches()){
    const out=[];
    fichesCourantes().forEach(f=>{
      if(!ignore.action && !ficheMatchAction(f)) return;
      f.signaux.forEach(s=>{ if(signalOK(s, ignore)) out.push(s); });
    });
    return out;
  }
  return LEADS.filter(l=>match(l, ignore));
}

// Onglets periode (mois). Construits depuis META.mois (du plus recent au plus ancien).
function buildPeriod(){
  const box=document.getElementById('period');
  const chips=[{cle:null,label:'Toute la période'}].concat(META.mois);
  const base=signauxCourants({mois:true});
  box.innerHTML=chips.map(m=>{
    const c=base.filter(l=>(m.cle===null||l.mois===m.cle)).length;
    const pressed=(state.mois===m.cle)?'true':'false';
    return `<button class="chip" data-mois="${m.cle===null?'':m.cle}" aria-pressed="${pressed}">${m.label} · ${c}</button>`;
  }).join('');
  box.querySelectorAll('.chip').forEach(ch=>ch.addEventListener('click',()=>{
    const v=ch.dataset.mois;
    state.mois = v===''? null : v;
    box.querySelectorAll('.chip').forEach(x=>x.setAttribute('aria-pressed',(x===ch)?'true':'false'));
    render();
  }));
}

const SRC_NOMS_META={TED:'TED',BM:'Banque Mondiale',AFDB:'AfDB',ADB:'ADB',EBRD:'EBRD',UNGM:'UNGM (agences ONU)',RW:'ReliefWeb','PRIVÉ':'Privé (BITD)',ATTRIB:'Titulaires',MIGA:'MIGA (garanties)',IFC:'IFC (invest. privé)',IDB:'IDB (Amérique latine)',BMP:'BM Projets (amont)',PROPARCO:'Proparco (invest. privé FR)',DFC:'DFC (invest. privé US)'};
const SRC_PRESENTES=[...new Set(LEADS.map(l=>l.src))].map(s=>SRC_NOMS_META[s]||s);
document.getElementById('runmeta').innerHTML =
  'Run du <b>'+META.date+'</b><br>'+META.total+' avis analysés<br>Sources : '+(SRC_PRESENTES.join(', ')||'aucune');

// Stat tiles (cliquables = filtre par action / priorite). Reconstruites au
// changement de lentille : en vue avis on compte les appels d'offres par
// action ; en vue entreprises on compte les fiches par priorite.
function buildStats(){
  const box=document.getElementById('stats');
  let defs;
  if(state.lens==='cibles'){
    const fiches=agregerCibles();
    const c=a=>fiches.filter(f=>f.prio===a).length;
    defs=[
      {k:'contacter',cls:'act',n:c('contacter'),l:'À contacter'},
      {k:'surveiller',cls:'wat',n:c('surveiller'),l:'Signal récent'},
      {k:'aucun',cls:'low',n:c('aucun'),l:'Sans signal'},
      {k:'all',cls:'',n:fiches.length,l:'Toutes mes cibles'}
    ];
  }else if(state.lens==='entreprises'){
    const fiches=agregerEntreprises();
    const c=a=>fiches.filter(f=>f.prio===a).length;
    const nt=fiches.filter(f=>(f.srcs||[]).includes('titulaire')).length;
    defs=[
      {k:'contacter',cls:'act',n:c('contacter'),l:'À contacter'},
      {k:'surveiller',cls:'wat',n:c('surveiller'),l:'Signal récent'},
      {k:'titulaire',cls:'low',n:nt,l:'Titulaire de marché'},
      {k:'all',cls:'',n:fiches.length,l:'Toutes'}
    ];
  }else if(state.lens==='titulaires'){
    const fiches=agregerTitulaires();
    const sc=f=>f.meilleur?f.meilleur.final:0;
    defs=[
      {k:'fort',cls:'act',n:fiches.filter(f=>sc(f)>=6).length,l:'Fort (\u22656)'},
      {k:'moyen',cls:'wat',n:fiches.filter(f=>sc(f)>=4&&sc(f)<6).length,l:'Moyen'},
      {k:'faible',cls:'low',n:fiches.filter(f=>sc(f)<4).length,l:'Faible'},
      {k:'all',cls:'',n:fiches.length,l:'Tous les titulaires'}
    ];
  }else if(state.lens==='geo'){
    const c=s=>GEO.filter(a=>a.sens===s).length;
    defs=[
      {k:'aggravation',cls:'act',n:c('aggravation'),l:'Aggravations'},
      {k:'allegement',cls:'low',n:c('allegement'),l:'Allègements'},
      {k:'lateral',cls:'wat',n:c('lateral'),l:'Latéraux'},
      {k:'all',cls:'',n:GEO.length,l:'Tous les signaux'}
    ];
  }else if(state.lens==='todo'){
    const items=todoListe();
    const c=b=>items.filter(o=>o.b===b).length;
    defs=[
      {k:'retard',cls:'act',n:c('retard'),l:'En retard'},
      {k:'echeance',cls:'wat',n:c('echeance'),l:'Échéance ≤ 7 j'},
      {k:'contacter',cls:'',n:c('contacter'),l:'À contacter'},
      {k:'suivre',cls:'low',n:c('suivre'),l:'À suivre'}
    ];
  }else{
    const av=LEADS.filter(l=>l.src==='TED'||l.src==='BM'||l.src==='AFDB'||l.src==='ADB'||l.src==='EBRD'||l.src==='UNGM'||l.src==='RW'||l.src==='MIGA'||l.src==='IFC'||l.src==='IDB'||l.src==='BMP');
    const c=a=>av.filter(l=>l.action===a).length;
    defs=[
      {k:'contacter',cls:'act',n:c('contacter'),l:'À contacter'},
      {k:'surveiller',cls:'wat',n:c('surveiller'),l:'À surveiller'},
      {k:'ignorer',cls:'low',n:c('ignorer'),l:'Faibles'},
      {k:'all',cls:'',n:av.length,l:'Tous les avis'}
    ];
  }
  box.innerHTML=defs.map(s=>
    `<button class="tile ${s.cls}" data-action="${s.k}" aria-pressed="${s.k===state.action}"><div class="n">${s.n}</div><div class="l">${s.l}</div></button>`).join('');
  box.querySelectorAll('.tile').forEach(t=>t.addEventListener('click',()=>{
    state.action=(state.action===t.dataset.action)?'all':t.dataset.action;
    box.querySelectorAll('.tile').forEach(x=>x.setAttribute('aria-pressed',x.dataset.action===state.action?'true':'false'));
    render();
  }));
}

// Coordonnees (lat,lng) par pays affiche, pour la carte mondiale.
const COORDS={
 "Mali":[17.6,-3.5],"Niger":[17.6,9.4],"Burkina Faso":[12.2,-1.6],"Tchad":[15.5,18.7],"Mauritanie":[20.3,-10.9],
 "Côte d'Ivoire":[7.5,-5.5],"Nigeria":[9.1,8.7],"Sénégal":[14.5,-14.5],"Ghana":[7.9,-1.0],"Togo":[8.6,0.8],"Bénin":[9.3,2.3],"Guinée":[9.9,-9.7],"Libéria":[6.4,-9.4],
 "RDC":[-4.0,21.8],"Congo-Brazzaville":[-0.7,15.8],"Cameroun":[5.6,12.4],"Centrafrique":[6.6,20.9],"Gabon":[-0.8,11.6],
 "Éthiopie":[9.1,40.5],"Kenya":[0.0,37.9],"Ouganda":[1.4,32.3],"Tanzanie":[-6.4,34.9],"Somalie":[5.2,46.2],"Soudan du Sud":[7.3,30.3],"Rwanda":[-1.9,29.9],"Djibouti":[11.8,42.6],
 "Mozambique":[-18.7,35.5],"Madagascar":[-18.8,46.9],"Afrique du Sud":[-30.6,22.9],"Zambie":[-13.1,27.8],"Zimbabwe":[-19.0,29.2],"Malawi":[-13.3,34.3],"Angola":[-11.2,17.9],"Botswana":[-22.3,24.7],
 "Égypte":[26.8,30.8],"Maroc":[31.8,-7.1],"Tunisie":[33.9,9.6],"Algérie":[28.0,1.7],"Libye":[26.3,17.2],
 "Cisjordanie et Gaza":[31.9,35.2],"Jordanie":[30.6,36.2],"Liban":[33.9,35.9],"Irak":[33.2,43.7],"Yémen":[15.6,48.0],"Turquie":[39.0,35.2],"Oman":[21.5,55.9],
 "Ouzbékistan":[41.4,64.6],"Tadjikistan":[38.9,71.3],"Kirghizistan":[41.2,74.8],"Kazakhstan":[48.0,66.9],
 "Bangladesh":[23.7,90.4],"Pakistan":[30.4,69.3],"Inde":[22.4,78.9],"Népal":[28.4,84.1],"Indonésie":[-2.5,118.0],"Philippines":[12.9,121.8],
 "Ukraine":[48.4,31.2],"Moldavie":[47.2,28.5],"Albanie":[41.2,20.0],"Macédoine du Nord":[41.6,21.7],"Serbie":[44.0,21.0],"Géorgie":[42.3,43.4],"Arménie":[40.1,45.0],"Azerbaïdjan":[40.1,47.6],
 "Haïti":[19.0,-72.3],"Jamaïque":[18.1,-77.3],"Mexique":[23.6,-102.6],"Honduras":[14.8,-86.2],"Guatemala":[15.5,-90.2],"El Salvador":[13.8,-88.9],"Nicaragua":[12.9,-85.2],"Panama":[8.5,-80.1],"Mongolie":[46.9,103.8],"Équateur":[-1.8,-78.2],"Brésil":[-10.3,-53.2],"Colombie":[4.6,-74.3],
 "France":[46.6,2.2],"Allemagne":[51.2,10.4],"Danemark":[56.0,9.5],"Nouvelle-Calédonie":[-21.3,165.5],
 // -- Complement carte (audit juillet 2026) : coordonnees des pays qui
 // etaient mappes a une zone mais sans point sur la carte (Afghanistan,
 // Soudan, Syrie, Russie...), plus les nouveaux pays ajoutes a ZONE_PAR_ISO3.
 "Afghanistan":[33.9,67.7],"Arabie Saoudite":[24.0,45.1],"Argentine":[-38.4,-63.6],"Bahreïn":[26.1,50.6],
 "Biélorussie":[53.7,27.9],"Bolivie":[-16.3,-63.6],"Bosnie-Herzégovine":[43.9,17.7],"Burundi":[-3.4,29.9],
 "Cambodge":[12.6,104.9],"Cap-Vert":[16.0,-24.0],"Chili":[-35.7,-71.5],"Comores":[-11.6,43.3],
 "Eswatini":[-26.5,31.5],"Fidji":[-17.7,178.0],"Gambie":[13.4,-15.3],"Guinée équatoriale":[1.6,10.3],
 "Guinée-Bissau":[12.0,-15.0],"Guyana":[4.9,-58.9],"Guyane":[4.0,-53.0],"Iran":[32.4,53.7],
 "Israël":[31.4,35.0],"Kosovo":[42.6,20.9],"Koweït":[29.3,47.6],"Laos":[19.9,102.5],
 "Lesotho":[-29.6,28.2],"Liberia":[6.4,-9.4],"Maurice":[-20.3,57.6],"Mayotte":[-12.8,45.2],
 "Monténégro":[42.7,19.4],"Myanmar":[21.9,95.9],"Namibie":[-22.6,17.1],"Papouasie-Nouvelle-Guinée":[-6.3,143.9],
 "Paraguay":[-23.4,-58.4],"Pérou":[-9.2,-75.0],"Qatar":[25.3,51.2],"Russie":[61.5,90.0],
 "Sao Tomé-et-Principe":[0.2,6.6],"Seychelles":[-4.7,55.5],"Sierra Leone":[8.5,-11.8],"Soudan":[15.5,30.2],
 "Sri Lanka":[7.9,80.8],"Suriname":[4.0,-56.0],"Syrie":[35.0,38.5],"Trinité-et-Tobago":[10.5,-61.3],
 "Turkménistan":[38.9,59.6],"Uruguay":[-32.5,-55.8],"Vanuatu":[-16.3,167.7],"Venezuela":[6.4,-66.6],
 "Émirats Arabes Unis":[24.0,54.0],"Érythrée":[15.2,39.8],"Îles Salomon":[-9.6,160.2]
};
const TIER_COULEUR={contacter:'#C0273A',surveiller:'#C8893B',ignorer:'#5C6670'};
const TIER_RANG={contacter:3,surveiller:2,ignorer:1};

// Synthese executive
function buildExec(){
  const aContacter=LEADS.filter(l=>l.action==='contacter');
  const zonesRisque=new Set(aContacter.map(l=>l.zone)).size;
  const immediat=aContacter.filter(l=>l.win==='immediate').length;
  const avecContact=aContacter.filter(l=>l.email!=='n.c.').length;
  document.getElementById('exec').innerHTML=
    `<span class="lead-strong">${aContacter.length} opportunités prioritaires</span> détectées dans `+
    `<b>${zonesRisque} zones</b> à enjeu sûreté, dont <b>${immediat}</b> en fenêtre immédiate. `+
    `<b>${avecContact}</b> disposent d'un contact direct identifié, en amont de la concurrence.`;
}

// Retroaction (item 7), volet visibilite : tableau de conversion gagne/perdu
// par secteur et par zone. Rempli une seule fois (les issues bougent lentement).
function renderConversion(){
  const box=document.getElementById('convbody');
  if(!box) return;
  if(!CONVERSION || !CONVERSION.n){
    box.innerHTML='<div class="convempty">Renseigne des statuts <b>gagné</b> / <b>perdu</b> '+
      'dans le Sheet pour voir quels secteurs et zones convertissent le mieux.</div>';
    return;
  }
  const bloc=(titre,lignes)=>{
    if(!lignes.length) return '';
    const rows=lignes.map(l=>{
      const pct=Math.round(l.taux*100);
      const cls=l.actif?'convactif':'convneutre';
      const tag=l.actif?'':'<span class="convmute">n&lt;'+CONVERSION.n_min+'</span>';
      return `<tr class="${cls}"><td>${esc(l.val)}</td><td>${l.g}</td><td>${l.p}</td>`+
             `<td>${l.n}</td><td>${pct}% ${tag}</td></tr>`;
    }).join('');
    return `<div class="convtitre">${titre}</div><table class="convtab">`+
      `<thead><tr><th>${titre==='Par secteur'?'Secteur':'Zone'}</th><th>G</th><th>P</th><th>n</th><th>taux lissé</th></tr></thead>`+
      `<tbody>${rows}</tbody></table>`;
  };
  box.innerHTML=
    `<div class="convmeta">${CONVERSION.n} issue(s) renseignée(s), taux de base ${Math.round(CONVERSION.base*100)}%. `+
    `Une catégorie influe sur le scoring (si la rétroaction est activée) à partir de ${CONVERSION.n_min} issues.</div>`+
    bloc('Par secteur', CONVERSION.secteur)+bloc('Par zone', CONVERSION.zone);
}

// Graphique repartition par zone (respecte les filtres sauf la zone)
function buildZoneChart(){
  const counts={};
  signauxCourants({zone:true}).forEach(l=>{ counts[l.zone]=(counts[l.zone]||0)+1; });
  const zones=ORDRE_ZONES.filter(z=>counts[z]);
  const maxZ=Math.max(1,...zones.map(z=>counts[z]));
  const box=document.getElementById('zonechart');
  if(!zones.length){box.innerHTML='<div class="count">Aucune zone pour ce filtre.</div>';return;}
  box.innerHTML=zones.map(z=>{
    const c=counts[z], pct=Math.round(c/maxZ*100);
    const pressed=z===state.zone?'true':'false';
    return `<div class="zrow" data-zone="${z}" role="button" tabindex="0" aria-pressed="${pressed}"><div class="zlab"><span>${z}</span><span>${c}</span></div><div class="ztrack"><div class="zfill" style="width:${pct}%"></div></div></div>`;
  }).join('');
  box.querySelectorAll('.zrow').forEach(el=>{
    const choisir=()=>{ state.zone=state.zone===el.dataset.zone?null:el.dataset.zone;
      document.getElementById('clearz').classList.toggle('on',!!(state.zone||state.mois)); render(); };
    el.addEventListener('click',choisir);
    el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choisir();}});
  });
}

// Carte mondiale (Leaflet). Un marqueur agrege par pays.
let _map=null, _layer=null;
function initMap(){
  if(typeof L==='undefined'){document.getElementById('map').innerHTML='<div class="count" style="padding:20px">Carte indisponible (connexion requise).</div>';return;}
  _map=L.map('map',{worldCopyJump:true,minZoom:1,attributionControl:true}).setView([18,18],2);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
    attribution:'&copy; OpenStreetMap &copy; CARTO',subdomains:'abcd',maxZoom:8
  }).addTo(_map);
  _layer=L.layerGroup().addTo(_map);
}
function updateMap(filtered){
  if(!_map||!_layer)return;
  _layer.clearLayers();
  const parPays={};
  filtered.forEach(l=>{ if(!COORDS[l.pays])return; (parPays[l.pays]=parPays[l.pays]||[]).push(l); });
  Object.entries(parPays).forEach(([pays,arr])=>{
    const meilleur=arr.reduce((a,b)=>TIER_RANG[b.action]>TIER_RANG[a.action]?b:a);
    const couleur=TIER_COULEUR[meilleur.action]||'#5C6670';
    const r=6+Math.min(14,arr.length*2);
    const top=arr.slice().sort((a,b)=>b.final-a.final)[0];
    const m=L.circleMarker(COORDS[pays],{radius:r,color:couleur,weight:1.5,fillColor:couleur,fillOpacity:0.55});
    m.bindPopup(`<b>${pays}</b><br>${arr.length} avis · meilleur score ${top.final.toFixed(1)}<br><span style="color:#A99E92;font-size:0.85em">${top.titre.slice(0,70)}</span>`);
    m.on('click',()=>{ const inp=document.getElementById('search'); inp.value=pays; state.q=pays.toLowerCase(); document.getElementById('clearz').classList.add('on'); render(); });
    m.addTo(_layer);
  });
}

// --- EXPORT LISTE D'APPELS -------------------------------------------------
// Exporte EXACTEMENT la selection affichee (lentille + filtres + tri courants),
// pour prospecter hors ligne. Tout se fait dans le navigateur : aucune donnee
// ne sort vers un service tiers.
function csvChamp(v){
  const t=(v===null||v===undefined)?'':String(v);
  // Excel francais attend le point-virgule ; on protege les champs qui en
  // contiennent, ainsi que les guillemets et les sauts de ligne.
  return /[";\n\r]/.test(t) ? '"'+t.replace(/"/g,'""')+'"' : t;
}
function csvLignes(entetes,lignes){
  const tout=[entetes].concat(lignes);
  return tout.map(r=>r.map(csvChamp).join(';')).join('\r\n');
}
function nc(v){ return (v&&v!=='n.c.')?v:''; }

function exportAvis(liste){
  const LIB_WIN={immediate:'immédiate',court_terme:'court terme',indetermine:'indéterminée'};
  const entetes=['Score','Sûreté','Commercial','Action','Fenêtre','Échéance',
                 'Pays','Zone','Organisation','Intitulé','Contact','Email',
                 'Téléphone','Statut','Source','Date détection','Lien'];
  const lignes=liste.map(l=>[
    l.final,l.surete,l.comm,l.action,LIB_WIN[l.win]||l.win||'',l.deadline||'',
    l.pays||'',l.zone||'',l.agence||'',l.titre||'',
    nc(l.nom),nc(l.email),nc(l.tel),l.statut||'nouveau',l.src||'',
    l.date_det||'',l.lien||''
  ]);
  return csvLignes(entetes,lignes);
}

function exportFiches(liste){
  const entetes=['Entreprise','Priorité','Score max','Nb signaux','Zones',
                 'Secteurs','Contact','Email','SIREN','CA','Dernier signal',
                 'Meilleur signal','Lien'];
  const lignes=liste.map(f=>[
    f.nom||'',f.prio||'',f.meilleur?f.meilleur.final:'',f.n||0,
    (f.zones||[]).join(' / '),(f.secteurs||[]).join(' / '),
    nc(f.enr&&f.enr.nom),nc(f.enr&&f.enr.email),
    nc(f.enr&&f.enr.siren),nc(f.enr&&f.enr.ca),
    f.dernier||'',f.meilleur?(f.meilleur.titre||''):'',
    f.meilleur?(f.meilleur.lien||''):''
  ]);
  return csvLignes(entetes,lignes);
}

function exporterCSV(){
  const fiches=vueFiches();
  const liste=fiches?(FICHES||[]):(AFFICHES||[]);
  if(!liste.length){ alert("Rien à exporter : aucun élément ne correspond au filtre courant."); return; }
  const csv=fiches?exportFiches(liste):exportAvis(liste);
  // Le BOM UTF-8 est indispensable pour qu'Excel affiche correctement les
  // accents ; sans lui, "Sûreté" ressort illisible.
  const blob=new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8;'});
  const d=new Date().toISOString().slice(0,10);
  const nom='radar-amarante-'+state.lens+'-'+d+'.csv';
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url; a.download=nom; document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
document.getElementById('export').addEventListener('click',exporterCSV);

document.getElementById('clearz').addEventListener('click',()=>{
  state.zone=null; state.mois=null;
  document.getElementById('clearz').classList.remove('on');
  buildPeriod(); buildZoneChart(); render();
});
document.querySelectorAll('#srcseg button').forEach(b=>b.addEventListener('click',()=>{
  state.src=b.dataset.src;
  document.querySelectorAll('#srcseg button').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
  render();
}));
document.querySelectorAll('#triseg button').forEach(b=>b.addEventListener('click',()=>{
  state.tri=b.dataset.tri;
  document.querySelectorAll('#triseg button').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
  render();
}));
// Interrupteur de lentille : bascule toute la vue avis <-> entreprises.
document.querySelectorAll('#lensseg button').forEach(b=>b.addEventListener('click',()=>{
  if(state.lens===b.dataset.lens)return;
  state.lens=b.dataset.lens;
  document.querySelectorAll('#lensseg button').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
  // Reset du filtre : en avis on part de "à contacter" ; en cibles/titulaires
  // on part de "tout" pour voir l'ensemble (toute la watchlist, tous les titulaires).
  state.action = state.lens==='avis' ? 'contacter' : 'all';
  majControlesLentille();
  const titres={avis:'Carte des opportunités',entreprises:'Carte des entreprises',cibles:'Carte des cibles',titulaires:'Carte des titulaires',geo:'Carte des signaux géopolitiques',todo:'Carte des actions à mener'};
  document.querySelector('.geo .phead').textContent=titres[state.lens]||'Carte';
  buildStats(); buildPeriod(); render();
}));
document.getElementById('search').addEventListener('input',e=>{state.q=e.target.value.toLowerCase().trim();render();});

const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// Jours restants avant la date limite. Renvoie un entier (peut etre negatif si
// cloture) ou null si pas de date exploitable.
function joursRestants(s){
  s=(s||'').trim(); if(!s)return null;
  let d=null;
  let m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(m){ d=new Date(+m[1],+m[2]-1,+m[3]); }
  else{ let m2=s.match(/^(\d{2})\/(\d{2})\/(\d{4})/); if(m2){ d=new Date(+m2[3],+m2[2]-1,+m2[1]); } }
  if(!d||isNaN(d))return null;
  const auj=new Date(); auj.setHours(0,0,0,0);
  return Math.round((d-auj)/86400000);
}
function badgeDeadline(l){
  const jr=joursRestants(l.deadline);
  if(jr===null)return '';
  if(jr<0)return '<span class="jx clos">clôturé</span>';
  if(jr===0)return '<span class="jx urgent">clôt. aujourd\'hui</span>';
  const cls=jr<=7?'urgent':(jr<=30?'proche':'large');
  return `<span class="jx ${cls}">J-${jr}</span>`;
}

function badgeRenouv(l){
  // Attribution dont le contrat arrive a echeance : opportunite de
  // renouvellement a travailler en amont. Rempli par SPARQL a la collecte.
  if(!l.statut_renouv)return '';
  const m=l.mois_avant_fin;
  const txt=(m!==''&&m!=null)?`⏳ expire dans ${Math.round(m)} mois`:'⏳ renouvellement';
  return `<span class="badge renouv-${l.statut_renouv}" title="Contrat en cours arrivant a echeance (fin estimee ${esc(l.fin_contrat||'n.c.')}). Opportunite de renouvellement : approcher l'acheteur avant la relance.">${txt}</span>`;
}

// ===================== REGROUPEMENT (« Grouper par ») =====================
// Clé de groupe d'un avis / d'une fiche selon state.group. Retourne un libellé
// lisible ; l'ordre des groupes suit ORDRE_ZONES pour la zone, sinon la taille.
function grpKeyAvis(o){
  if(state.group==='zone') return o.l.zone||'Zone n.c.';
  if(state.group==='secteur') return o.l.sect||'Autre';
  if(state.group==='action') return ({contacter:'À contacter',surveiller:'À surveiller',ignorer:'Faibles'})[o.l.action]||o.l.action||'—';
  return '';
}
function grpKeyFiche(o){
  if(state.group==='zone') return (o.f.zones&&o.f.zones[0])||'Zone n.c.';
  if(state.group==='secteur') return o.f.sectPrimary||'Autre';
  if(state.group==='action') return ({contacter:'À contacter',surveiller:'À surveiller',ignorer:'Faibles',aucun:'Sans signal'})[o.f.prio]||o.f.prio||'—';
  return '';
}
function grouperListe(items, keyFn){
  const g={}, ordre=[];
  items.forEach(o=>{ const k=keyFn(o)||'—'; if(!g[k]){g[k]=[];ordre.push(k);} g[k].push(o); });
  ordre.sort((a,b)=>{
    const ia=ORDRE_ZONES.indexOf(a), ib=ORDRE_ZONES.indexOf(b);
    if(ia>=0||ib>=0){ if(ia<0)return 1; if(ib<0)return -1; return ia-ib; }
    return g[b].length-g[a].length;
  });
  return ordre.map(k=>({k,items:g[k]}));
}
function rendreGroupes(groupes, cardFn){
  return groupes.map(gr=>
    `<div class="grpsec"><div class="grphead">${esc(gr.k)}<span>${gr.items.length}</span></div>`+
    gr.items.map(cardFn).join('')+`</div>`).join('');
}

// ===================== LENTILLE GEOPOLITIQUE =====================
// Flux de contexte pays (alertes FCDO + evenements presse), historique 90 j,
// GROUPE PAR ZONE et trie par date. Aucun score, aucune action « je contacte ».
const GEO_COULEUR={aggravation:'#c0392b',allegement:'#27812f',lateral:'#8a7f78'};
function geoFiltres(){
  return GEO.filter(a=>{
    if(state.action!=='all' && a.sens!==state.action) return false;
    if(state.zone && a.zone!==state.zone) return false;
    if(state.q){const hay=(a.pays+' '+a.zone+' '+a.motif+' '+a.apres).toLowerCase(); if(!hay.includes(state.q)) return false;}
    return true;
  });
}
function geoCard(a){
  const cls=a.sens==='aggravation'?'g-agg':(a.sens==='allegement'?'g-alleg':'g-lat');
  const fleche=a.sens==='aggravation'?'▲':(a.sens==='allegement'?'▼':'≈');
  const niv=(a.avant||a.apres)?`${esc(a.avant)} → ${esc(a.apres)}`:'';
  const sev='●●●●'.slice(0,Math.max(0,Math.min(4,a.severite)));
  const lien=a.lien?`href="${esc(a.lien)}" target="_blank" rel="noopener"`:'';
  return `<a class="geocard ${cls}" ${lien}>
    <span class="gc-sens">${fleche}</span>
    <span class="gc-main"><span class="gc-pays">${esc(a.pays)}</span>${a.motif?`<span class="gc-motif">${esc(a.motif)}</span>`:''}</span>
    <span class="gc-meta">${niv?`<span class="gc-niv">${niv}</span>`:''}<span class="gc-date">${esc(a.date||'')}</span><span class="gc-sev" title="sévérité ${a.severite}">${sev}</span></span>
  </a>`;
}
function buildZoneChartGeo(){
  const counts={};
  GEO.filter(a=>(state.action==='all'||a.sens===state.action) &&
      (!state.q||(a.pays+' '+a.zone+' '+a.motif+' '+a.apres).toLowerCase().includes(state.q)))
     .forEach(a=>{ counts[a.zone]=(counts[a.zone]||0)+1; });
  const zones=ORDRE_ZONES.filter(z=>counts[z]);
  const maxZ=Math.max(1,...zones.map(z=>counts[z]));
  const box=document.getElementById('zonechart');
  if(!zones.length){box.innerHTML='<div class="count">Aucun signal pour ce filtre.</div>';return;}
  box.innerHTML=zones.map(z=>{
    const c=counts[z], pct=Math.round(c/maxZ*100), pressed=z===state.zone?'true':'false';
    return `<div class="zrow" data-zone="${z}" role="button" tabindex="0" aria-pressed="${pressed}"><div class="zlab"><span>${z}</span><span>${c}</span></div><div class="ztrack"><div class="zfill" style="width:${pct}%"></div></div></div>`;
  }).join('');
  box.querySelectorAll('.zrow').forEach(el=>{
    const choisir=()=>{ state.zone=state.zone===el.dataset.zone?null:el.dataset.zone;
      document.getElementById('clearz').classList.toggle('on',!!(state.zone||state.mois)); render(); };
    el.addEventListener('click',choisir);
    el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choisir();}});
  });
}
function updateMapGeo(list){
  if(!_map||!_layer)return; _layer.clearLayers();
  const parPays={};
  list.forEach(a=>{ if(!COORDS[a.pays])return; (parPays[a.pays]=parPays[a.pays]||[]).push(a); });
  Object.entries(parPays).forEach(([pays,arr])=>{
    const pire=arr.some(x=>x.sens==='aggravation')?'aggravation':(arr.some(x=>x.sens==='lateral')?'lateral':'allegement');
    const col=GEO_COULEUR[pire]||'#8a7f78';
    const sev=Math.max(0,...arr.map(x=>x.severite||0));
    const r=6+Math.min(12,sev*3);
    const m=L.circleMarker(COORDS[pays],{radius:r,color:col,weight:1.5,fillColor:col,fillOpacity:0.55});
    m.bindPopup(`<b>${pays}</b><br>${arr.length} signal(aux) géo<br><span style="color:#A99E92;font-size:0.85em">${esc((arr[0].motif||'').slice(0,70))}</span>`);
    m.on('click',()=>{ const inp=document.getElementById('search'); inp.value=pays; state.q=pays.toLowerCase(); render(); });
    m.addTo(_layer);
  });
}
function renderGeo(){
  buildZoneChartGeo();
  const box=document.getElementById('leads');
  const list=geoFiltres();
  updateMapGeo(list);
  document.getElementById('count').textContent=
    `${list.length} signal${list.length>1?'aux':''} géopolitique${list.length>1?'s':''} · 7 derniers jours`+
    (state.zone?`, zone ${state.zone}`:'')+(state.action!=='all'?`, ${state.action}`:'');
  if(!list.length){box.innerHTML='<div class="empty">Aucun signal géopolitique cette semaine.</div>';return;}
  const parZone={};
  list.forEach(a=>{ (parZone[a.zone]=parZone[a.zone]||[]).push(a); });
  const zones=ORDRE_ZONES.filter(z=>parZone[z]).concat(Object.keys(parZone).filter(z=>!ORDRE_ZONES.includes(z)));
  box.innerHTML=zones.map(z=>{
    const items=parZone[z].slice().sort((a,b)=>(b.date||'').localeCompare(a.date||''));
    return `<div class="grpsec geosec"><div class="grphead">${esc(z)}<span>${items.length}</span></div>`+
      items.map(geoCard).join('')+`</div>`;
  }).join('');
}

// ===================== BARRES DYNAMIQUES =====================
// Barre source : ne montre QUE les sources d'avis reellement presentes (une
// source vide -- ADB desactivee, IDB si non collectee -- disparait d'elle-meme,
// plus de bouton qui ouvre une liste vide). Ordre d'affichage stable.
const SRC_BTN={BM:'Banque Mondiale',TED:'TED',AFDB:'AfDB',ADB:'ADB',EBRD:'EBRD',UNGM:'UNGM',RW:'ReliefWeb',MIGA:'MIGA',IFC:'IFC',IDB:'IDB',BMP:'BM Projet'};
const SRC_ORDRE_AVIS=['BM','TED','AFDB','ADB','EBRD','UNGM','RW','MIGA','IFC','IDB','BMP'];
function buildSrcSeg(){
  const seg=document.getElementById('srcseg'); if(!seg)return;
  const presentes=new Set(LEADS.filter(l=>l.src!=='PRIVÉ'&&l.src!=='ATTRIB').map(l=>l.src));
  const html=[`<button data-src="all" aria-pressed="${state.src==='all'}">Toutes</button>`]
    .concat(SRC_ORDRE_AVIS.filter(s=>presentes.has(s)).map(s=>
      `<button data-src="${s}" aria-pressed="${state.src===s}">${esc(SRC_BTN[s]||s)}</button>`));
  seg.innerHTML=html.join('');
  seg.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{
    state.src=b.dataset.src;
    seg.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
    render();
  }));
}
// Selecteur secteur : ne liste que les secteurs presents (leads + watchlist).
function buildSecteurSel(){
  const sel=document.getElementById('secteurSel'); if(!sel)return;
  const present=new Set();
  LEADS.forEach(l=>{ if(l.sect) present.add(l.sect); });
  WATCHLIST.forEach(w=>{ if(w.sect) present.add(w.sect); });
  const ordre=SECTEURS.filter(s=>present.has(s));
  sel.innerHTML='<option value="all">Tous</option>'+ordre.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');
  sel.value=state.secteur;
}
// Visibilite des controles selon la lentille. Source = avis seulement ;
// secteur + grouper = partout sauf geo ; periodes + comptes = pas en geo.
function majControlesLentille(){
  const geo=state.lens==='geo', fiches=vueFiches(), todo=state.lens==='todo';
  const set=(id,on)=>{const e=document.getElementById(id); if(e) e.style.display=on?'':'none';};
  set('srcseg', !geo && !fiches && !todo);
  set('secteurPick', !geo && !todo);
  set('groupPick', !geo && !todo);
  set('triseg', !geo && !todo);
  set('period', !geo && !todo);
  set('comptes', !geo && !fiches && !todo);
}
document.getElementById('secteurSel').addEventListener('change',e=>{state.secteur=e.target.value;render();});
document.getElementById('groupSel').addEventListener('change',e=>{state.group=e.target.value;render();});

// ===================== COCKPIT « À FAIRE » (discipline de pipeline) =====================
// Transforme le radar de « liste d'opportunités » en « que dois-je faire cette
// semaine ». Buckets prioritaires (un lead n'apparait qu'une fois, au plus
// urgent). Tout est derive des champs existants (statut, date_det, deadline,
// fenetre) : aucune collecte, aucune ecriture serveur nouvelle.
const SLA_IMMEDIAT_JOURS=3, ECHEANCE_JOURS=7, RELANCE_JOURS=10;
const BUCKETS_TODO=[
  {k:'retard',   l:'En retard',      cls:'b-retard'},
  {k:'echeance', l:'Échéance ≤ 7 j', cls:'b-echeance'},
  {k:'contacter',l:'À contacter',    cls:'b-contacter'},
  {k:'suivre',   l:'À suivre',       cls:'b-suivre'},
];
const BUCKET_LABEL={retard:'En retard',echeance:'Échéance ≤ 7 j',contacter:'À contacter',suivre:'À suivre'};
function _joursDepuis(ds){ if(!ds)return null; const d=new Date(ds+'T00:00:00'); if(isNaN(d.getTime()))return null; return Math.floor((Date.now()-d.getTime())/86400000); }
function _joursAvant(ds){ if(!ds)return null; const d=new Date(ds+'T00:00:00'); if(isNaN(d.getTime()))return null; return Math.floor((d.getTime()-Date.now())/86400000); }
function _clos(l){ const s=(l.statut||'').toLowerCase(); return s.indexOf('gagn')>=0||s.indexOf('perd')>=0; }
function _engage(l){ const s=(l.statut||'').toLowerCase(); return s.indexOf('contact')>=0||s.indexOf('relanc')>=0; }
function _dateContactLocale(l){ try{ const m=JSON.parse(localStorage.getItem('suivi_dates')||'{}'); return m[leadId(l)]||null; }catch(e){ return null; } }
// Bucket le plus prioritaire d'un lead, ou null s'il n'est pas actionnable.
function bucketTodo(l){
  if(_clos(l)) return null;                       // gagné/perdu -> hors pipeline
  if(estEcarte(l)) return null;                   // écarté « pas pertinent »
  if(l.src==='ATTRIB'){                            // titulaires = prospects
    if(_engage(l)) return 'suivre';
    return (l.final||0)>=6 ? 'contacter' : null;   // seuls les indices forts
  }
  if(_engage(l)) return 'suivre';                  // déjà contacté, pas d'issue
  const estC=l.action==='contacter';
  const ageDet=_joursDepuis(l.date_det);
  const dDead=_joursAvant(l.deadline);
  const echeanceProche=(dDead!==null && dDead<=ECHEANCE_JOURS && dDead>=-3);
  const retard=estC && l.win==='immediate' && ageDet!==null && ageDet>SLA_IMMEDIAT_JOURS;
  if(retard) return 'retard';
  if(echeanceProche) return 'echeance';
  if(estC) return 'contacter';
  return null;
}
function todoListe(){
  const out=[];
  LEADS.forEach(l=>{ const b=bucketTodo(l); if(b) out.push({l,b}); });
  return out;
}
const _ORDRE_B={retard:0,echeance:1,contacter:2,suivre:3};
function _triTodo(a,b){
  if(_ORDRE_B[a.b]!==_ORDRE_B[b.b]) return _ORDRE_B[a.b]-_ORDRE_B[b.b];
  if(a.b==='echeance'){ const da=_joursAvant(a.l.deadline), db=_joursAvant(b.l.deadline); return (da==null?999:da)-(db==null?999:db); }
  if(a.b==='retard'){ const aa=_joursDepuis(a.l.date_det)||0, ab=_joursDepuis(b.l.date_det)||0; return ab-aa; }
  if(a.b==='suivre'){ // le plus ancien contact d'abord (à relancer en premier)
    const ca=_dateContactLocale(a.l)||'9999', cb=_dateContactLocale(b.l)||'9999'; if(ca!==cb) return ca<cb?-1:1;
  }
  return (b.l.rang||b.l.final||0)-(a.l.rang||a.l.final||0);
}
function buildZoneChartTodo(items){
  const counts={};
  items.forEach(o=>{ if(o.l.zone) counts[o.l.zone]=(counts[o.l.zone]||0)+1; });
  const zones=ORDRE_ZONES.filter(z=>counts[z]).concat(Object.keys(counts).filter(z=>!ORDRE_ZONES.includes(z)));
  const box=document.getElementById('zonechart');
  const maxZ=Math.max(1,...zones.map(z=>counts[z]));
  if(!zones.length){ box.innerHTML='<div class="count">Rien à faire pour ce filtre.</div>'; return; }
  box.innerHTML=zones.map(z=>{
    const c=counts[z], pct=Math.round(c/maxZ*100), pressed=z===state.zone?'true':'false';
    return `<div class="zrow" data-zone="${z}" role="button" tabindex="0" aria-pressed="${pressed}"><div class="zlab"><span>${z}</span><span>${c}</span></div><div class="ztrack"><div class="zfill" style="width:${pct}%"></div></div></div>`;
  }).join('');
  box.querySelectorAll('.zrow').forEach(el=>{
    const choisir=()=>{ state.zone=state.zone===el.dataset.zone?null:el.dataset.zone; render(); };
    el.addEventListener('click',choisir);
    el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choisir();}});
  });
}
function renderTodo(){
  const box=document.getElementById('leads');
  let items=todoListe().sort(_triTodo);
  if(state.action!=='all') items=items.filter(o=>o.b===state.action);
  if(state.zone) items=items.filter(o=>o.l.zone===state.zone);
  if(state.q){ const q=state.q; items=items.filter(o=>((o.l.titre||'')+' '+(o.l.pays||'')+' '+(o.l.agence||'')+' '+(o.l.entreprise||'')).toLowerCase().indexOf(q)>=0); }
  buildZoneChartTodo(items);
  updateMap(items.map(o=>o.l));
  const n=items.length;
  document.getElementById('count').textContent=`${n} action${n>1?'s':''} cette semaine`+(state.action!=='all'?` · ${BUCKET_LABEL[state.action]}`:'')+(state.zone?`, ${state.zone}`:'');
  if(!n){ box.innerHTML='<div class="empty">Rien à traiter ici. Pipeline à jour.</div>'; AFFICHES=[]; return; }
  AFFICHES=items.map(o=>o.l);               // data-idx pointe vers cet index
  const withIdx=items.map((o,gi)=>({o,gi}));
  box.innerHTML=BUCKETS_TODO.filter(b=>items.some(o=>o.b===b.k)).map(b=>{
    const sec=withIdx.filter(x=>x.o.b===b.k);
    const sous=b.k==='suivre'?' <em class="grpsub">les plus anciens à relancer d\'abord</em>':(b.k==='retard'?' <em class="grpsub">SLA fenêtre immédiate dépassé</em>':'');
    return `<div class="grpsec todosec ${b.cls}"><div class="grphead">${esc(b.l)}<span>${sec.length}</span>${sous}</div>`+
      sec.map(x=>leadCard(x.o.l,x.gi)).join('')+`</div>`;
  }).join('');
}

function render(){
  renderEcartes();
  renderSurveillance();
  if(state.lens==='todo'){ renderTodo(); return; }

  if(state.lens==='geo'){ renderGeo(); return; }
  buildZoneChart();
  if(vueFiches()){ renderFiches(fichesCourantes()); return; }
  const box=document.getElementById('leads');
  let filtered=LEADS.filter(l=>match(l));
  if(state.tri==='date'){
    filtered.sort((a,b)=> (b.date_det||'').localeCompare(a.date_det||'') || b.final-a.final);
  }else if(state.tri==='urgence'){
    filtered.sort((a,b)=>{
      const ja=joursRestants(a.deadline), jb=joursRestants(b.deadline);
      const oa=(ja!==null&&ja>=0), ob=(jb!==null&&jb>=0);
      if(oa&&ob)return ja-jb;            // deux ouverts : le plus proche d'abord
      if(oa!==ob)return oa?-1:1;         // un ouvert passe avant un fermé/sans date
      return b.final-a.final;            // sinon, par score
    });
  }else{
    // "Importance" : rang = score attenue par la fraicheur (calcule cote
    // Python). Repli sur le score brut si `rang` absent (page en cache d'avant
    // le chantier). Departage a rang egal par le score reel.
    filtered.sort((a,b)=> ((b.rang!=null?b.rang:b.final)-(a.rang!=null?a.rang:a.final)) || (b.final-a.final));
  }
  updateMap(filtered);
  const moisLabel = state.mois ? (META.mois.find(m=>m.cle===state.mois)||{}).label : null;
  document.getElementById('count').textContent=
    `${filtered.length} avis affiché${filtered.length>1?'s':''}`+
    (moisLabel?`, ${moisLabel}`:'')+
    (state.zone?`, zone ${state.zone}`:'')+(state.src!=='all'?`, source ${state.src}`:'');
  if(!filtered.length){box.innerHTML='<div class="empty">Aucun avis ne correspond à ce filtre.</div>';return;}
  AFFICHES=filtered;
  box.innerHTML = state.group==='aucun'
    ? filtered.map((l,i)=>leadCard(l,i)).join('')
    : rendreGroupes(grouperListe(filtered.map((l,i)=>({l,i})),grpKeyAvis),(o)=>leadCard(o.l,o.i));
}
function leadCard(l,i){
    const tier=l.action;
    const done=SUIVI_ON&&(CONTACTES.has(leadId(l))||dejaContacte(l));
    const win=(['immediate','court_terme','indetermine'].includes(l.win))?l.win:'indetermine';
    const mail=l.email!=='n.c.'?`<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>`:'n.c.';
    const tel=l.tel!=='n.c.'?`<a href="tel:${esc(l.tel.replace(/\s/g,''))}">${esc(l.tel)}</a>`:'n.c.';
    const ecart=l.ecart?`<span class="badge ecart" title="Écart d'évaluation entre les deux passes, lire la justification">⚠ écart</span>`:'';
    const stKey=(l.statut||'nouveau').toLowerCase();
    const stCls=stKey.includes('gagn')?'gagne':stKey.includes('perd')?'perdu':stKey.includes('contact')?'contacte':stKey.includes('relanc')?'relance':'';
    const stMasque=(stKey==='non_pertinent'||stKey==='surveille'||stKey==='attribution_publiee');
    const statut=(stKey!=='nouveau'&&!stMasque)?`<span class="statut ${stCls}">${esc(l.statut)}</span>`:'';
    const dateChip=l.mois_label&&l.mois_label!=='Sans date'?`<span class="datedet">détecté ${esc(l.mois_label)}</span>`:'';
    const geoBadge=l.geo_boost?`<span class="badge geoboost" title="${esc(l.geo_motif||'Pays en aggravation récente')} — score rehaussé de +${(l.geo_boost*0.5).toFixed(1)}">▲ pays en aggravation +${(l.geo_boost*0.5).toFixed(1)}</span>`:'';
    const survBadge=attribPubliee(l)?`<span class="badge attribok" title="Une attribution correspondante a été détectée">✓ Attribution publiée${l.motif_ecart?' : '+esc(l.motif_ecart):''}</span>`:(estSurveille(l)?`<span class="badge surveille">👁 Surveillé</span>`:'');
    const scoreBase=l.geo_boost&&l.final_base!=null?`<span class="sfbase" title="Score avant rehausse géopolitique">${l.final_base.toFixed(1)}</span> `:'';
    const hasContact=(l.nom&&l.nom!=='n.c.')||(l.email&&l.email!=='n.c.')||(l.tel&&l.tel!=='n.c.');
    const contactRows = hasContact ? `
          <div class="row"><span class="k">Contact</span><span class="v">${esc(l.nom)}</span></div>
          <div class="row"><span class="k">Email</span><span class="v">${mail}</span></div>
          <div class="row"><span class="k">Tél</span><span class="v">${tel}</span></div>` : '';
    return `<article class="lead${l.geo_boost?' boosted':''}" data-tier="${tier}" data-idx="${i}"><span class="spine"></span><div class="body">
      <div class="lhead"><div class="lmeta"><span class="src ${l.src.toLowerCase()}">${SRC_LABEL[l.src]||l.src}</span><span class="pays">${esc(l.pays)}</span><span>· ${esc(l.zone)}</span></div>
      <div class="scorebox"><div class="sf">${scoreBase}${l.final.toFixed(1)}</div><div class="sd">sûreté ${l.surete.toFixed(1)} · com ${l.comm.toFixed(1)}</div><div class="se">${echelleLabel(l)}</div></div></div>
      <h3 class="ltitle">${esc(l.titre)}</h3>
      <div class="badges">${geoBadge}${survBadge}${(l.justif||'').indexOf('[DÉPLACEMENT CONCURRENT]')===0?'<span class="badge deplacement">⚔ Déplacement concurrent</span>':''}<span class="badge win-${win}">${winLabel[win]}</span>${badgeDeadline(l)}${badgeRenouv(l)}${ecart}${statut}${dateChip}</div>
      <div class="contact"><div class="row"><span class="k">Agence</span><span class="v">${esc(l.agence)}</span></div>${contactRows}</div>
      <div class="cible"><b>Qui démarcher.</b> ${esc(l.cible)}</div>
      ${l.justif?`<details class="just"><summary><span class="chev">▸</span> Justification sûreté</summary><p>${esc((l.justif||'').replace('[DÉPLACEMENT CONCURRENT]','').trim())}</p></details>`:''}
      </div><div class="foot"><span class="grp">Groupe ${esc(l.grp)}</span><span class="footacts">${SUIVI_ON?`<button class="act contact${done?' done':''}" type="button" data-contact="${i}"${done?' disabled':''}>${done?'✓ Contacté':'☎ Je contacte'}</button>`:''}<button class="act" type="button" data-fiche="${i}">Fiche ↗</button><a class="act mail" href="${mailtoHref(l)}">✉ Rédiger email</a>${l.lien?`<a class="act" href="${esc(l.lien)}" target="_blank" rel="noopener">Voir l'avis ↗</a>`:''}${estSurveille(l)?'':`<button class="act surv" type="button" data-surveiller="${i}" title="Surveiller : le radar vérifiera à chaque run si l'attribution paraît">👁 Surveiller</button>`}<button class="act ecart" type="button" data-ecart="${i}" title="Pas pertinent : écarter et enregistrer la raison">✕ Pas pertinent</button></span></div></article>`;
}

// --- Rendu de la lentille Entreprises ---
let FICHES=[];
function ficheSignalRow(s){
  const attrib=s.src==='ATTRIB';
  const desc=attrib?`Titulaire d'un marché · ${esc(s.pays)}`:esc(s.titre||'Signal');
  const typ=attrib?'attribution':((s.grp&&s.grp!=='signal')?s.grp.replace(/_/g,' '):'signal');
  const sc=attrib?('score '+(s.final!=null?s.final.toFixed(1):'n.c.')):('signal '+s.final.toFixed(1));
  const date=s.date_det||s.mois_label||'—';
  const lien=s.lien?` <a class="fsl" href="${esc(s.lien)}" target="_blank" rel="noopener">${attrib?'avis':'article'} ↗</a>`:'';
  return `<div class="fsr"><span class="fsi">${desc}${lien}</span><span class="fsd">${esc(date)} · ${esc(typ)} ${esc(sc)}</span></div>`;
}
function ficheCard(f,i){
  const initiales=((f.nom||'?').split(/\s+/).slice(0,2).map(w=>w[0]||'').join('')||'?').toUpperCase();
  const prioLabel={contacter:'à contacter',surveiller:'à surveiller',ignorer:'faible',aucun:'sans signal'}[f.prio]||f.prio;
  const sansSignal=f.n===0;
  const secteur=f.secteurs[0]||'secteur n.c.';
  const meta=sansSignal
    ? [secteur,'aucun signal récent'].join(' · ')
    : [secteur,f.n+' signal'+(f.n>1?'s':''),(f.zones.join(', ')||'zone n.c.')].join(' · ');
  const sig=f.signaux.slice().sort((a,b)=>(b.date_det||'').localeCompare(a.date_det||'')).slice(0,4).map(ficheSignalRow).join('');
  const hasEnr=f.enr.nom||f.enr.email||f.enr.siren||f.enr.ca;
  const enr=hasEnr?`<div class="fenr">
     ${f.enr.nom?`<div class="er"><span class="ek">Dirigeant</span><span>${esc(f.enr.nom)}</span></div>`:''}
     ${f.enr.email?`<div class="er"><span class="ek">Email</span><span>${esc(f.enr.email)}</span></div>`:''}
     ${f.enr.siren?`<div class="er"><span class="ek">SIREN</span><span>${esc(f.enr.siren)}</span></div>`:''}
     ${f.enr.ca?`<div class="er"><span class="ek">CA</span><span>${esc(f.enr.ca)} €</span></div>`:''}
   </div>`:(sansSignal?'':`<div class="fmiss">⚠ Contact non enrichi (entreprise probablement hors France). À rechercher manuellement.</div>`);
  const mailBtn=f.repr?`<a class="act mail" href="${mailtoHref(f.repr)}">✉ Rédiger l'email</a>`:'';
  const voirBtn=f.n>0?`<button class="act" type="button" data-fiche-ent="${i}">Voir les ${f.n} signal${f.n>1?'s':''} ↗</button>`:'';
  return `<article class="fiche" data-fidx="${i}">
    <div class="fhead"><div class="fav">${esc(initiales)}</div>
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><span class="fnom">${esc(f.nom)}</span><span class="fprio ${f.prio}">${prioLabel}</span>${(f.srcs||[]).map(s=>`<span class="fsrc ${s}">${({watchlist:'Watchlist',dfi:'Financement DFI',signal:'Signal',titulaire:'Titulaire'})[s]||s}</span>`).join('')}</div>
        <div class="fmeta">${esc(meta)}</div>
      </div></div>
    ${sansSignal?'<div class="fnosig">Cible surveillée, aucun signal récent capté. Elle reste dans le radar dès qu\'une actualité tombe.</div>':`<div class="fsig">${sig}</div>`}
    ${enr}
    <div class="facts">${mailBtn}${voirBtn}</div>
  </article>`;
}
function renderFiches(fichesSource){
  const box=document.getElementById('leads');
  let fiches=(fichesSource||[]).filter(f=>ficheOK(f));
  if(state.tri==='date') fiches.sort((a,b)=>(b.dernier||'').localeCompare(a.dernier||''));
  FICHES=fiches;
  const moisLabel=state.mois?(META.mois.find(m=>m.cle===state.mois)||{}).label:null;
  document.getElementById('count').textContent=
    `${fiches.length} entreprise${fiches.length>1?'s':''}`+
    (moisLabel?`, ${moisLabel}`:'')+(state.zone?`, zone ${state.zone}`:'');
  if(!fiches.length){box.innerHTML='<div class="empty">Aucune entreprise ne correspond à ce filtre.</div>';updateMap([]);return;}
  box.innerHTML = state.group==='aucun'
    ? fiches.map((f,i)=>ficheCard(f,i)).join('')
    : rendreGroupes(grouperListe(fiches.map((f,i)=>({f,i})),grpKeyFiche),(o)=>ficheCard(o.f,o.i));
  const sigVis=[]; fiches.forEach(f=>f.signaux.forEach(s=>{ if(signalOK(s)) sigVis.push(s); }));
  updateMap(sigVis);
}
function openFicheEnt(i){
  const f=FICHES[i]; if(!f)return;
  const sigs=f.signaux.slice().sort((a,b)=>(b.date_det||'').localeCompare(a.date_det||''));
  const tl=sigs.map(s=>{
    const typ=s.src==='ATTRIB'?'attribution':'signal';
    const lien=s.lien?` <a class="fsl" href="${esc(s.lien)}" target="_blank" rel="noopener">${s.src==='ATTRIB'?'avis':'article'} ↗</a>`:'';
    // Etiquette d'echelle par ligne : un score 6 attribution-indicatif ne se
    // lit pas comme un 6 sûreté-analysée. Sans elle, la timeline melangeait
    // des chiffres non comparables.
    const ech=`<span class="tlech">${esc(echelleLabel(s))}</span>`;
    return `<div class="tlrow"><span class="tld">${esc(s.date_det||'—')}</span><span>${esc(s.pays)}${lien}</span><span>${esc(typ)}</span><span class="tls">${s.final.toFixed(1)}${ech}</span></div>`;
  }).join('');
  const prioLabel={contacter:'à contacter',surveiller:'à surveiller',ignorer:'faible',aucun:'sans signal'}[f.prio]||f.prio;
  const manque=!f.enr.nom&&!f.enr.email;
  document.getElementById('modalcard').innerHTML=`
   <div class="mhead"><button class="mclose" type="button" onclick="closeFiche()" aria-label="Fermer">×</button>
     <div class="msrc">Fiche entreprise · ${esc(prioLabel)}${(f.srcs||[]).map(s=>` · ${({watchlist:'Watchlist',dfi:'Financement DFI',signal:'Signal',titulaire:'Titulaire'})[s]||s}`).join('')}</div>
     <h2>${esc(f.nom)}</h2>
     <div class="mscore"><span class="big">${f.n}</span><span class="sub">signal(aux) · ${f.zones.length} zone(s)</span></div>
   </div>
   <div class="mbody">
     ${f.secteurs.length?`<div class="frow"><span class="fk">Secteur(s)</span><span class="fv">${f.secteurs.map(esc).join(', ')}</span></div>`:''}
     ${f.zones.length?`<div class="frow"><span class="fk">Zone(s)</span><span class="fv">${f.zones.map(esc).join(', ')}</span></div>`:''}
     ${f.enr.nom?`<div class="frow"><span class="fk">Dirigeant</span><span class="fv">${esc(f.enr.nom)}</span></div>`:''}
     ${f.enr.email?`<div class="frow"><span class="fk">Email</span><span class="fv"><a href="mailto:${esc(f.enr.email)}">${esc(f.enr.email)}</a></span></div>`:''}
     ${f.enr.siren?`<div class="frow"><span class="fk">SIREN</span><span class="fv">${esc(f.enr.siren)}</span></div>`:''}
     ${manque?`<div class="frow"><span class="fk">Contact</span><span class="fv" style="color:var(--watch)">non enrichi (hors France), à rechercher</span></div>`:''}
     <div class="tlhead">Signaux regroupés</div>
     <div class="timeline">${tl}</div>
   </div>
   <div class="mactions">
     ${f.repr?`<a class="mbtn primary" href="${mailtoHref(f.repr)}">✉ Rédiger l'email</a>`:''}
     <button class="mbtn ghost" type="button" onclick="closeFiche()">Fermer</button>
   </div>`;
  document.getElementById('modal').classList.add('open');
}
document.getElementById('foot').innerHTML=
  'Généré automatiquement après le run du radar.<br>Vues : Opportunités (avis) · Entreprises 360° (fiche unifiée par société : watchlist + signaux + titulaires) · Cibles privées · Titulaires. Le destinataire commercial réel est le titulaire qui déploie, pas l\'agence acheteuse.';

// --- Fiche lead detaillee (modale) ---
function ficheHtml(l){
  const row=(k,v)=>v?`<div class="frow"><span class="fk">${k}</span><span class="fv">${v}</span></div>`:'';
  const mail=(l.email&&l.email!=='n.c.')?`<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>`:'';
  const tel=(l.tel&&l.tel!=='n.c.')?esc(l.tel):'';
  const jr=joursRestants(l.deadline);
  const dl=(jr!==null)?(jr>=0?`${jr} j restants (${esc(l.deadline)})`:`clôturé (${esc(l.deadline)})`):'';
  return `
   <div class="mhead"><button class="mclose" type="button" onclick="closeFiche()" aria-label="Fermer">×</button>
     <div class="msrc">${SRC_LABEL[l.src]||l.src}</div>
     <h2>${esc(l.titre)}</h2>
     <div class="mscore"><span class="big">${l.final.toFixed(1)}</span><span class="sub">sûreté ${l.surete.toFixed(1)} · commercial ${l.comm.toFixed(1)}</span><span class="sub sub-echelle">${echelleLabel(l)}</span></div>
   </div>
   <div class="mbody">
     ${row('Pays',esc(l.pays))}
     ${row('Zone',esc(l.zone))}
     ${row('Action',esc(l.action))}
     ${row('Fenêtre',winLabel[l.win]||esc(l.win))}
     ${row('Échéance',dl)}
     ${row(l.src==='PRIVÉ'?'Activité':'Groupe',esc(l.grp))}
     ${row(l.src==='PRIVÉ'?'Entreprise':'Agence',esc(l.agence))}
     ${row('Contact',(l.nom&&l.nom!=='n.c.')?esc(l.nom):'')}
     ${row('Email',mail)}
     ${row('Tél',tel)}
     ${row('SIREN',esc(l.siren||''))}
     ${row("Chiffre d'affaires",l.ca?esc(l.ca)+' €':'')}
     ${row('Qui démarcher',esc(l.cible))}
     ${l.geo_boost?row('Contexte géo',`<span style="color:#e08e98">▲ Pays en aggravation récente (${esc(l.geo_date||'')}) — score rehaussé de ${l.final_base!=null?esc(l.final_base.toFixed(1)):''} à ${esc(l.final.toFixed(1))}.</span>${l.geo_motif?'<br>'+esc(l.geo_motif):''}`):''}
     ${row('Justification',esc((l.justif||'').replace('[DÉPLACEMENT CONCURRENT]','').trim()))}
   </div>
   <div class="mactions">
     <a class="mbtn primary" href="${mailtoHref(l)}">✉ Rédiger l'email</a>
     ${l.src==='PRIVÉ'?`<button class="mbtn ghost" type="button" data-compte-link="${esc(l.agence)}">Historique du compte ↗</button>`:''}
     ${l.lien?`<a class="mbtn ghost" href="${esc(l.lien)}" target="_blank" rel="noopener">Voir l'avis ↗</a>`:''}
     <button class="mbtn ghost" type="button" onclick="closeFiche()">Fermer</button>
   </div>`;
}
function openFiche(l){if(!l)return;document.getElementById('modalcard').innerHTML=ficheHtml(l);document.getElementById('modal').classList.add('open');}
function closeFiche(){document.getElementById('modal').classList.remove('open');}
document.getElementById('leads').addEventListener('click',e=>{
  const bc=e.target.closest('[data-contact]');
  if(bc){e.preventDefault();marquerContacte(+bc.getAttribute('data-contact'),bc);return;}
  const bec=e.target.closest('[data-ecart]');
  if(bec){e.preventDefault();
    const footacts=bec.closest('.footacts'); if(footacts){footacts.dataset.saved=footacts.innerHTML; footacts.innerHTML=menuRaisons(+bec.getAttribute('data-ecart'));}
    return;}
  const bm=e.target.closest('[data-ecartmotif]');
  if(bm){e.preventDefault();const [i,m]=bm.getAttribute('data-ecartmotif').split('|');marquerNonPertinent(+i,m);return;}
  const bsv=e.target.closest('[data-surveiller]');
  if(bsv){e.preventDefault();marquerSurveille(+bsv.getAttribute('data-surveiller'));return;}
  const ba=e.target.closest('[data-ecartannule]');
  if(ba){e.preventDefault();const fa=ba.closest('.footacts');if(fa&&fa.dataset.saved){fa.innerHTML=fa.dataset.saved;}return;}
  const br=e.target.closest('[data-restaurer]');
  if(br){e.preventDefault();restaurerEcarte(br.getAttribute('data-restaurer'));return;}
  const b=e.target.closest('[data-fiche]');
  if(b){e.preventDefault();openFiche(AFFICHES[+b.getAttribute('data-fiche')]);return;}
  const be=e.target.closest('[data-fiche-ent]');
  if(be){e.preventDefault();openFicheEnt(+be.getAttribute('data-fiche-ent'));}
});
document.getElementById('ecartes').addEventListener('click',e=>{
  const br=e.target.closest('[data-restaurer]');
  if(br){e.preventDefault();restaurerEcarte(br.getAttribute('data-restaurer'));}
});
document.getElementById('surveillance').addEventListener('click',e=>{
  const ba=e.target.closest('[data-arreter-surv]');
  if(ba){e.preventDefault();arreterSurveille(ba.getAttribute('data-arreter-surv'));}
});
document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal')closeFiche();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeFiche();});

// --- Historique par entreprise + score d'accélération (signaux BITD) ---
function joursDepuis(d){if(!d)return null;const t=Date.parse(d);if(isNaN(t))return null;return Math.floor((Date.now()-t)/86400000);}
function poidsRecence(j){if(j==null)return 0.3;if(j<=30)return 1;if(j<=90)return 0.6;if(j<=180)return 0.3;return 0.1;}
function agregerComptes(){
  const parNom={};
  LEADS.filter(l=>l.src==='PRIVÉ').forEach(l=>{
    const k=l.agence||'?';
    (parNom[k]=parNom[k]||{nom:k,signaux:[],pays:new Set(),act:new Set()});
    parNom[k].signaux.push(l);
    if(l.pays)parNom[k].pays.add(l.pays);
    if(l.grp&&l.grp!=='signal')parNom[k].act.add(l.grp.replace(/_/g,' '));
  });
  const comptes=Object.values(parNom).map(c=>{
    let accel=0,recents=0,dernier=null;
    c.signaux.forEach(s=>{
      const j=joursDepuis(s.date_det);
      accel+=(s.final/10)*poidsRecence(j);
      if(j!=null&&j<=90)recents++;
      if(s.date_det&&(!dernier||s.date_det>dernier))dernier=s.date_det;
    });
    if(recents>=3)accel*=1.2;                 // momentum : rafale de signaux
    accel=Math.round(accel*100)/100;
    const tier=accel>=1.5?'chaud':accel>=0.7?'tiede':'froid';
    return Object.assign(c,{n:c.signaux.length,recents,dernier,accel,tier,
      pays:[...c.pays],act:[...c.act]});
  });
  comptes.sort((a,b)=>b.accel-a.accel);
  return comptes;
}
function buildComptes(){
  const box=document.getElementById('comptes');
  const comptes=agregerComptes().filter(c=>c.accel>0);
  if(!comptes.length){box.style.display='none';return;}
  box.style.display='';
  const icon={chaud:'🔥',tiede:'🟠',froid:'·'};
  box.innerHTML=`<div class="chead"><b>Comptes chauds · BITD</b><span class="csub">accélération = signaux récents pondérés par le score. Cliquer pour l'historique.</span></div><div class="cgrid">`+
    comptes.slice(0,8).map(c=>`<button class="ccard t-${c.tier}" type="button" data-compte="${esc(c.nom)}">
      <div class="cn"><span>${icon[c.tier]}</span>${esc(c.nom)}</div>
      <div class="cmeta">${c.n} signal${c.n>1?'s':''}${c.recents?` · ${c.recents} récent${c.recents>1?'s':''}`:''} · ${c.pays.length} pays</div>
      <div class="cbar"><span style="width:${Math.min(100,Math.round(c.accel/2*100))}%"></span></div></button>`).join('')+`</div>`;
}
function openCompte(nom){
  const c=agregerComptes().find(x=>x.nom===nom);if(!c)return;
  const etiq={chaud:'🔥 Compte chaud',tiede:'🟠 Tiède',froid:'· Froid'};
  const sigs=c.signaux.slice().sort((a,b)=>(b.date_det||'').localeCompare(a.date_det||''));
  const tl=sigs.map(s=>`<div class="tlrow"><span class="tld">${esc(s.date_det||'—')}</span><span>${esc(s.pays)}</span><span>${esc((s.grp||'').replace(/_/g,' '))}</span><span class="tls">${s.final.toFixed(1)}</span></div>`).join('');
  document.getElementById('modalcard').innerHTML=`
   <div class="mhead"><button class="mclose" type="button" onclick="closeFiche()" aria-label="Fermer">×</button>
     <div class="msrc">${etiq[c.tier]} · accélération ${c.accel}</div>
     <h2>${esc(c.nom)}</h2>
     <div class="mscore"><span class="big">${c.n}</span><span class="sub">signaux · ${c.recents} récent(s) · ${c.pays.length} pays</span></div>
   </div>
   <div class="mbody">
     ${c.pays.length?`<div class="frow"><span class="fk">Pays</span><span class="fv">${c.pays.map(esc).join(', ')}</span></div>`:''}
     ${c.act.length?`<div class="frow"><span class="fk">Activités</span><span class="fv">${c.act.map(esc).join(', ')}</span></div>`:''}
     <div class="frow"><span class="fk">Dernier signal</span><span class="fv">${esc(c.dernier||'—')}</span></div>
     <div class="tlhead">Historique des signaux</div>
     <div class="timeline">${tl}</div>
   </div>
   <div class="mactions"><button class="mbtn ghost" type="button" onclick="closeFiche()">Fermer</button></div>`;
  document.getElementById('modal').classList.add('open');
}
document.getElementById('comptes').addEventListener('click',e=>{
  const b=e.target.closest('[data-compte]');
  if(b)openCompte(b.getAttribute('data-compte'));
});
document.getElementById('modalcard').addEventListener('click',e=>{
  const b=e.target.closest('[data-compte-link]');
  if(b){closeFiche();openCompte(b.getAttribute('data-compte-link'));}
});

buildExec();
renderConversion();
initMap();
buildSrcSeg();
buildSecteurSel();
buildStats();
buildPeriod();
buildComptes();
renderSante();
majControlesLentille();
render();

// Observabilite de run : etat du dernier run par source (volume + fraicheur).
// Rend visible une source qui s'est tue -- ce qui manquait quand le digest est
// tombe en silence. Derive cote Python (const SANTE), pas de calcul ici.
function renderSante(){
  const box=document.getElementById('santeRun');
  if(!box) return;
  if(!SANTE || !SANTE.sources || !SANTE.sources.length){ box.innerHTML=''; return; }
  const nomSrc=s=>SRC_NOMS_META[s]||s;
  const ageTxt=x=>{
    if(x.etat==='absent'||x.n===0) return '—';
    if(x.age===null||x.age===undefined) return 'n.c.';
    if(x.age===0) return "aujourd'hui";
    return 'il y a '+x.age+' j';
  };
  const chips=SANTE.sources.map(x=>`
    <span class="sante-chip ${esc(x.etat)}" title="${esc(nomSrc(x.src))} · ${x.n} lead(s) · plus récent : ${esc(ageTxt(x))}">
      <span class="dot"></span>
      <span class="src">${esc(x.src)}</span>
      <span class="n">${x.n}</span>
      <span class="ag">${esc(ageTxt(x))}</span>
    </span>`).join('');
  const av=SANTE.a_verifier>0
    ? `<span class="warn">${SANTE.a_verifier} à vérifier</span>`
    : 'toutes actives';
  box.innerHTML=`<div class="sante-tete">
      <span class="sante-titre">État du dernier run</span>
      <span class="sante-sub">${esc(SANTE.date||'')} · ${SANTE.actives} source(s) active(s) · ${av}</span>
    </div>
    <div class="sante-grid">${chips}</div>`;
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
