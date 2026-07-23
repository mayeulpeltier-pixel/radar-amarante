# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COLLECTEUR D'ATTRIBUTIONS BANQUE MONDIALE.
=============================================================

CE QU'IL FAIT
-------------
Recupere les avis d'ATTRIBUTION ("Contract Award") de la Banque Mondiale et en
extrait le NOM DU TITULAIRE, puis ecrit dans l'onglet `attributions_radar`,
CELUI-LA MEME que les attributions TED.

Consequence voulue : aucune modification du dashboard. Les lignes remontent
automatiquement dans la lentille "Titulaires - attributions" et dans la fiche
entreprise 360 (via `attribution_vers_lead`, qui lit `gagnant`).

POURQUOI CETTE SOURCE
---------------------
Un marche attribue il y a quelques semaines = une entreprise EN MOBILISATION.
C'est la fenetre ou le besoin de surete se decide, avant le deploiement. Le
titulaire est nomme, donc c'est un prospect direct, pas une intention de marche.

CHOIX TECHNIQUES (valides par sonde depuis GitHub Actions, juillet 2026)
------------------------------------------------------------------------
  - On reutilise l'API que le collecteur BM interroge DEJA (procnotices), en
    ajoutant simplement le type "Contract Award". Pas de nouvelle dependance,
    pas de nouveau domaine, fraicheur du jour meme (verifie : as_of du jour).
  - Le nom du gagnant n'est PAS un champ structure : il vit dans le HTML de
    `notice_text`, sous la section "Awarded Bidder(s):". D'ou le parseur
    tolerant ci-dessous, teste et faillible en douceur (une ligne sans gagnant
    identifiable est ignoree, jamais ecrite a moitie).
  - On ne garde que les groupes CS (conseil) et CW (travaux) : ce sont ceux ou
    du personnel se deploie. GO (fournitures) est ecarte, un marche de cables
    HDMI n'interesse pas Amarante.
  - On ne garde que les pays de l'univers de risque (MULTIPLICATEUR_ZONE).

MODE VERIFICATION (a utiliser au premier run)
---------------------------------------------
    RADAR_BM_ATTRIB_DEBUG=1  -> imprime ce qui a ete extrait, N'ECRIT RIEN.
C'est le moyen de confirmer que le parseur lit bien les vrais noms avant de
laisser le collecteur alimenter le Sheet.

Interrupteur : RADAR_BM_ATTRIB=0 desactive la collecte.
Fenetre       : RADAR_BM_ATTRIB_JOURS (defaut 120 jours).

LANCEMENT :  python bm_attributions.py
"""

import html as _html
import os
import re
from datetime import date, datetime, timedelta

import bitd_signaux as bitd
import ted_complet_v14 as ted
import ted_complet_bm as bm


# ===========================================================================
# CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_BM_ATTRIB", "1") != "0"
DEBUG = os.environ.get("RADAR_BM_ATTRIB_DEBUG", "0") == "1"

# Fenetre de mobilisation : au-dela, l'entreprise est deja installee et le
# besoin de surete a ete arbitre. 120 jours couvre large sans noyer le Sheet.
JOURS_FENETRE = int(os.environ.get("RADAR_BM_ATTRIB_JOURS", "120"))

# Par defaut on ECARTE les titulaires locaux (entreprise du pays du chantier) :
# ils n'expatrient personne et n'achetent pas de protection internationale.
# RADAR_BM_ATTRIB_LOCAUX=1 les reintegre si tu veux voir tout le marche.
GARDER_LOCAUX = os.environ.get("RADAR_BM_ATTRIB_LOCAUX", "0") == "1"

# Onglet PARTAGE avec les attributions TED (integration dashboard gratuite).
NOM_ONGLET = "attributions_radar"
COLONNES = [
    "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
    "acheteur", "titre", "cpv", "sous_traitance",
    "date_publication", "publication_number", "lien", "a_demarcher",
]
COL_STATUT = "statut_prospection"
COL_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COL_STATUT, COL_DETECTION]

TYPE_ATTRIBUTION = "Contract Award"
# Avec le tri par date decroissante, les premieres pages sont les plus
# fraiches et la collecte s'arrete d'elle-meme en sortant de la fenetre.
# On peut donc relever le plafond sans gaspiller : il ne sert plus que de
# garde-fou en cas de tri non honore par l'API.
PAGES_MAX = int(os.environ.get("RADAR_BM_ATTRIB_PAGES", "30"))

# Groupes d'achat ou du personnel se deploie reellement.
LIBELLE_GROUPE = {"CS": "Conseil / AT", "CW": "Travaux / BTP"}

# Un titulaire reel est TOUJOURS suivi de son identifiant fournisseur BM entre
# parentheses : "STECOL CORPORATION (333385)", "SNTM (1103160)". Le bruit du
# bloc (adresse "Jijiga", libelle "Country: Ethiopia", en-tete "Bid Price at
# Opening") n'en a JAMAIS. Ce motif, releve sur donnees reelles le 18/07/2026,
# remplace l'ancienne heuristique qui ramassait tout.
RE_TITULAIRE = re.compile(r"^(?P<nom>.{2,150}?)\s*\((?P<id>\d{4,})\)\s*$")

# Pays du titulaire, tel qu'ecrit dans le bloc : "Country: China".
RE_PAYS_TITULAIRE = re.compile(r"(?i)^country\s*:\s*(?P<pays>.+?)\s*$")

# Etiquettes de montant rencontrees dans le bloc titulaire.
LABELS_MONTANT = ("Contract Amount", "Bid Price at Opening", "Evaluated Bid Price",
                  "Contract Price", "Evaluated Cost", "Bid Price", "Amount")


def _lignes_bloc_titulaire(notice_text):
    """Lignes de la section 'Awarded Bidder(s)', ou [] si absente."""
    brut = str(notice_text or "")
    m = re.search(r"(?i)awarded\s+bidder", brut)
    if not m:
        return []
    lignes = texte_en_lignes(brut[m.start():])
    if lignes and re.match(r"(?i)^awarded\s+bidder", lignes[0]):
        lignes = lignes[1:]
    return lignes


def extraire_gagnants(notice_text, maxi=4):
    """Noms d'entreprises de la section "Awarded Bidder(s)".

    Strategie STRICTE : on ne retient que les lignes portant un identifiant
    fournisseur BM entre parentheses. C'est le seul marqueur fiable observe.
    Consequence assumee : un avis dont le titulaire n'a pas d'identifiant est
    IGNORE plutot qu'ecrit avec une adresse prise pour une raison sociale.
    Mieux vaut un lead en moins qu'un faux titulaire dans le CRM."""
    noms, vus = [], set()
    for ligne in _lignes_bloc_titulaire(notice_text):
        m = RE_TITULAIRE.match(ligne)
        if not m:
            continue
        nom = _norm(m.group("nom")).strip(" .,;:")
        if len(nom) < 3 or not re.search(r"[A-Za-zÀ-ÿ]{2}", nom):
            continue
        cle = nom.lower()
        if cle in vus:
            continue
        vus.add(cle)
        noms.append(nom)
        if len(noms) >= maxi:
            break
    return noms


def pays_titulaire(notice_text):
    """Pays d'origine du titulaire ("Country: China"). Sert a distinguer une
    entreprise qui SE DEPLACE d'un entrepreneur local."""
    for ligne in _lignes_bloc_titulaire(notice_text):
        m = RE_PAYS_TITULAIRE.match(ligne)
        if m:
            return _norm(m.group("pays"))
    return ""


# --- Resolution de pays tolerante (anglais / francais / accents perdus) -----
# Le notice_text est tantot anglais ("Congo, Democratic Republic of"), tantot
# francais, et les accents y sont parfois perdus ("Rpublique dmocratique du",
# "Bnin", "Chine"). Comparer les chaines brutes faisait passer des entreprises
# LOCALES pour des etrangeres (constate le 18/07/2026 sur la RDC, le Cameroun,
# le Benin). On resout donc les deux cotes en ISO3 avant de comparer.

# Noms dont l'accent n'est pas desaccentue mais SUPPRIME par la source
# ("Bénin" -> "Bnin", "Sénégal" -> "Sngal"). Desaccentuer ne suffit alors pas :
# la lettre a disparu. Table explicite, limitee aux pays a risque concernes.
ALIAS_TRONQUES = {
    "bnin": "BEN", "sngal": "SEN", "guine": "GIN", "guine bissau": "GNB",
    "guine quatoriale": "GNQ", "algrie": "DZA", "libria": "LBR",
    "nigria": "NGA", "hati": "HTI", "gypte": "EGY", "thiopie": "ETH",
    "rythre": "ERI", "mirats arabes unis": "ARE", "prou": "PER",
    "vnzula": "VEN", "ouzbkistan": "UZB", "npal": "NPL", "zimbabw": "ZWE",
    "camroun": "CMR", "trkiye": "TUR", "turkiye": "TUR",
}


def _cle_pays(nom):
    """Cle comparable : minuscules, sans accents ni ponctuation."""
    import unicodedata
    s = unicodedata.normalize("NFD", str(nom or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^A-Za-z ]", " ", s).lower()
    return re.sub(r"\s+", " ", s).strip()


def iso3_pays_libre(nom):
    """ISO3 depuis un nom de pays en anglais OU en francais. '' si inconnu.
    Un pays inconnu ici (Chine, Turquie hors perimetre, "World") est hors de
    l'univers de risque : le titulaire est donc bien etranger au chantier."""
    cle = _cle_pays(nom)
    if not cle:
        return ""
    # Cas ambigus ou reordonnes, traites par motif (les deux Congo surtout).
    if "congo" in cle:
        return "COD" if re.search(r"democr|dmocr|drc|rdc|kinshasa", cle) else "COG"
    if "ivoire" in cle or "ivory" in cle:
        return "CIV"
    if "central african" in cle or "centrafric" in cle:
        return "CAF"
    if cle in ALIAS_TRONQUES:
        return ALIAS_TRONQUES[cle]
    for essai in (nom, cle):
        code = bm.code_iso3_pays(essai)
        if code:
            return code
    return bitd.NOM_VERS_ISO3.get(cle, "") or ""


def titulaire_etranger(pays_titulaire_nom, pays_projet):
    """Vrai si le titulaire vient d'un autre pays que celui du chantier.

    C'EST LE FILTRE COMMERCIAL DECISIF. Un macon local qui construit dans sa
    propre ville n'achetera jamais de protection rapprochee internationale.
    Une entreprise etrangere qui arrive sur un chantier de 11 ans en zone a
    risque expatrie du personnel : c'est le prospect d'Amarante.
    Sans information exploitable, on repond True (on ne jette pas dans le doute)."""
    a, b = _norm(pays_titulaire_nom), _norm(pays_projet)
    if not a or not b:
        return True
    if _cle_pays(a) == _cle_pays(b):
        return False
    iso_a, iso_b = iso3_pays_libre(a), iso3_pays_libre(b)
    if iso_a and iso_b:
        return iso_a != iso_b
    return True


# --- Conversion des montants en USD ----------------------------------------
# Les avis expriment le montant en devise LOCALE. Sans conversion, le poids de
# valeur du mini-score est absurde : XAF 8 487 678 (~14 000 USD) etait lu comme
# "8,5 M" et scorait comme un marche moyen, tandis que XOF 4 806 034 530
# (~8 M USD) passait pour 4 806 M et saturait le score.
# Taux APPROXIMATIFS (1 USD = N unites), suffisants pour des paliers a 1/5/20 M.
# A reajuster si les paliers deviennent faux : c'est une table, pas un dogme.
TAUX_USD = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.78, "CHF": 0.88, "JPY": 155.0,
    "XOF": 600.0, "XAF": 600.0, "MAD": 10.0, "TND": 3.1, "DZD": 135.0,
    "EGP": 48.0, "NGN": 1500.0, "GHS": 15.0, "KES": 130.0, "TZS": 2600.0,
    "UGX": 3700.0, "RWF": 1300.0, "ETB": 130.0, "SDG": 600.0, "SOS": 570.0,
    "ZAR": 18.0, "ZMW": 26.0, "MWK": 1750.0, "MZN": 64.0, "AOA": 900.0,
    "MGA": 4500.0, "MUR": 46.0, "KZT": 500.0, "UZS": 12800.0, "KGS": 87.0,
    "TJS": 11.0, "AZN": 1.7, "GEL": 2.7, "UAH": 41.0, "TRY": 38.0,
    "PKR": 280.0, "BDT": 120.0, "INR": 84.0, "LKR": 300.0, "NPR": 135.0,
    "PHP": 57.0, "IDR": 16000.0, "VND": 25000.0, "KHR": 4100.0, "LAK": 21500.0,
    "MMK": 2100.0, "IQD": 1310.0, "JOD": 0.71, "LBP": 89000.0, "AFN": 70.0,
    "HTG": 132.0, "COP": 4100.0, "PEN": 3.7, "BOB": 6.9, "ARS": 1100.0,
    # Complement apres run reel du 18/07/2026 (BIF apparaissait non converti).
    "BIF": 2950.0, "CDF": 2800.0, "RWF_": 1300.0, "DJF": 178.0,
    "GNF": 8600.0, "LRD": 190.0, "SLE": 23.0, "SLL": 23000.0,
    "MRU": 40.0, "GMD": 70.0, "CVE": 100.0, "KMF": 450.0, "SCR": 14.0,
    "STN": 22.0, "BWP": 13.5, "NAD": 18.0, "SZL": 18.0, "LSL": 18.0,
    "LYD": 4.8, "YER": 250.0, "SYP": 13000.0, "SSP": 4500.0,
    "MVR": 15.4, "BTN": 84.0, "MNT": 3400.0, "PGK": 3.9, "FJD": 2.3,
    "SBD": 8.4, "VUV": 119.0, "WST": 2.7, "TOP": 2.4, "XPF": 110.0,
    "AMD": 390.0, "MDL": 17.5, "BAM": 1.8, "RSD": 108.0, "MKD": 57.0,
    "ALL": 90.0, "TMT": 3.5, "IRR": 42000.0, "TWD": 32.0,
}


def convertir_en_usd(devise, montant):
    """Montant converti en USD, ou None si la devise est inconnue."""
    taux = TAUX_USD.get(str(devise or "").upper())
    if not taux or montant is None:
        return None
    return montant / taux






# ===========================================================================
# PARSEUR (fonctions PURES : testables sans reseau)
# ===========================================================================

def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def texte_en_lignes(html_brut):
    """HTML de notice_text -> liste de lignes texte propres.

    Les separateurs de bloc (<br>, </div>, </p>, </td>) deviennent des sauts
    de ligne AVANT le retrait des balises, sinon tout le contenu se colle en
    une seule ligne illisible."""
    t = str(html_brut or "")
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?i)</\s*(div|p|tr|td|li|h\d)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", "", t)          # retire toutes les balises
    t = _html.unescape(t)
    lignes = [_norm(l) for l in t.split("\n")]
    return [l for l in lignes if l]


def valeur_label(lignes, label):
    """Valeur associee a une etiquette, dans les deux mises en forme vues :
      - meme ligne :  "Project:P180076-Lowlands..."
      - lignes suivantes : "Duration of Contract" / "" / "2 Week(s)"
    Les indications de format entre parentheses, comme "(YYYY/MM/DD)", sont
    sautees : ce sont des aides de lecture, pas des valeurs."""
    cible = _norm(label).lower().rstrip(":")
    for i, ligne in enumerate(lignes):
        bas = ligne.lower()
        if not bas.startswith(cible):
            continue
        reste = ligne[len(cible):].lstrip(" :")
        if reste and not reste.startswith("("):
            return _norm(reste)
        for suivante in lignes[i + 1:i + 4]:
            if suivante.startswith("(") and suivante.endswith(")"):
                continue               # "(YYYY/MM/DD)"
            if suivante.endswith(":") or RE_PAYS_TITULAIRE.match(suivante):
                break                  # on est tombe sur l'etiquette suivante
            return _norm(suivante)
    return ""


def date_attribution(notice_text, record):
    """Date de notification d'attribution (AAAA/MM/JJ dans le notice_text),
    avec repli sur la date de publication de l'avis."""
    lignes = texte_en_lignes(notice_text)
    brut = valeur_label(lignes, "Date Notification of Award Issued")
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", brut or "")
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    brut2 = _norm(record.get("noticedate"))
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(brut2, fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    return ""


def montant_attribue(notice_text):
    """Montant du contrat CONVERTI EN USD ("USD 8010057"), sinon chaine vide.

    Deux pieges corriges sur donnees reelles (18/07/2026) :
      - la devise apparaissait deux fois ("USD USD 7918777.87") car la ligne de
        valeur la reprend deja ;
      - le montant etait laisse en devise locale, ce qui faussait gravement le
        poids de valeur du mini-score (voir TAUX_USD).
    Format de sortie "USD X million" : c'est le SEUL que
    `_valeur_en_millions` du dashboard lise correctement sur toute la plage.
    Son heuristique ne divise par un million qu'au-dela de 100 000, si bien
    qu'un petit marche ("USD 14146") etait lu comme 14 146 MILLIONS. Le mot
    "million" leve l'ambiguite quel que soit l'ordre de grandeur."""
    lignes = texte_en_lignes(notice_text)
    for label in LABELS_MONTANT:
        cible = label.lower()
        for i, ligne in enumerate(lignes):
            if not ligne.lower().startswith(cible):
                continue
            candidats = []
            reste = ligne[len(cible):].lstrip(" :")
            if reste:
                candidats.append(reste)
            devise = ""
            for suivante in lignes[i + 1:i + 6]:
                s = _norm(suivante)
                if re.fullmatch(r"[A-Za-z]{3}", s):     # code devise isole
                    devise = s.upper()
                    continue
                if RE_TITULAIRE.match(s) or RE_PAYS_TITULAIRE.match(s):
                    break                               # bloc titulaire suivant
                if re.search(r"\d", s):
                    candidats.append(s)
                    break
            for cand in candidats:
                valeur = _lire_montant(cand, devise)
                if valeur:
                    return valeur
    return ""


def _lire_montant(texte, devise_contexte=""):
    """'USD 7918777.87' ou '4806034530' -> 'USD 7918777'. '' si illisible."""
    s = _norm(texte)
    if re.match(r"(?i)^\d{4}[/-]\d", s):                # une date, pas un montant
        return ""
    m_dev = re.search(r"(?i)\b([A-Z]{3})\b", s)
    # La devise de la ligne prime ; sinon celle vue juste au-dessus. On ne la
    # concatene JAMAIS deux fois (bug "USD USD ...").
    devise = (m_dev.group(1).upper() if m_dev else "") or devise_contexte
    nombre = re.search(r"(\d[\d\s.,]*\d|\d)", s)
    if not nombre:
        return ""
    brut = nombre.group(1).replace(" ", "")
    # Separateurs : la virgule groupe les milliers, le point est decimal.
    brut = brut.replace(",", "")
    try:
        montant = float(brut)
    except ValueError:
        return ""
    if montant <= 0:
        return ""
    usd = convertir_en_usd(devise, montant)
    if usd is None:
        return "{} {:.0f}".format(devise, montant).strip()   # devise inconnue
    return "USD {:.3f} million".format(usd / 1_000_000.0)


def duree_contrat(notice_text):
    """Duree du contrat ("60 Day(s)", "2 Week(s)"...). Signal de deploiement :
    un chantier long implique une presence residente, donc un besoin durable."""
    return _norm(valeur_label(texte_en_lignes(notice_text), "Duration of Contract"))[:40]


# ===========================================================================
# FILTRAGE ET NORMALISATION
# ===========================================================================

def dans_la_fenetre(iso_date, aujourdhui=None, jours=None):
    """Attribution assez recente pour que la mobilisation soit en cours."""
    if not iso_date:
        return False
    jours = JOURS_FENETRE if jours is None else jours
    aujourdhui = aujourdhui or date.today()
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return False
    return timedelta(0) <= (aujourdhui - d) <= timedelta(days=jours)


def record_retenu(record):
    """Filtre de pertinence : type attribution, groupe CS/CW, pays a risque."""
    if _norm(record.get("notice_type")) != TYPE_ATTRIBUTION:
        return False, "type"
    if _norm(record.get("procurement_group")).upper() not in bm.BM_GROUPES_RETENUS:
        return False, "groupe"
    iso3 = bm.code_iso3_pays(record.get("project_ctry_name") or "")
    if not iso3 or iso3 not in ted.MULTIPLICATEUR_ZONE:
        return False, "pays"
    return True, ""


def normaliser(record):
    """Enregistrement BM -> ligne de l'onglet `attributions_radar`.
    Renvoie None si l'avis est hors perimetre ou sans gagnant identifiable."""
    ok, _motif = record_retenu(record)
    if not ok:
        return None
    texte = record.get("notice_text") or ""
    gagnants = extraire_gagnants(texte)
    if not gagnants:
        return None                     # mieux vaut rien qu'un faux titulaire
    d_attrib = date_attribution(texte, record)
    if not dans_la_fenetre(d_attrib):
        return None

    groupe = _norm(record.get("procurement_group")).upper()
    pays_nom = _norm(record.get("project_ctry_name"))
    origine = pays_titulaire(texte)
    etranger = titulaire_etranger(origine, pays_nom)
    if not etranger and not GARDER_LOCAUX:
        return None                     # entrepreneur local : pas un prospect

    duree = duree_contrat(texte)
    titre = _norm(record.get("bid_description")) or _norm(record.get("project_name"))
    morceaux = [titre] if titre else []
    if origine and etranger:
        morceaux.append("titulaire {}".format(origine))
    if duree:
        morceaux.append("duree {}".format(duree))
    titre = " · ".join(morceaux)

    # BUG D'INTEGRATION CORRIGE : le dashboard resout le pays des attributions
    # en mode ISO3 (resoudre_pays(..., "TED")). Ecrire "Mali" donnait
    # "Non classe" ; il faut le CODE. Verifie le 18/07/2026.
    iso3 = bm.code_iso3_pays(pays_nom)

    return {
        "date_maj": date.today().isoformat(),
        "gagnant": " ; ".join(gagnants),
        "secteur": LIBELLE_GROUPE.get(groupe, groupe or "Attribution"),
        "pays_execution": iso3 or pays_nom,
        "valeur_attribuee": montant_attribue(texte),
        "acheteur": _norm(record.get("project_name")) or "Banque Mondiale",
        "titre": titre[:300],
        "cpv": _norm(record.get("procurement_method_code")),
        "sous_traitance": "",
        "date_publication": d_attrib,
        "publication_number": _norm(record.get("id")),
        "lien": bm.LIEN_BM.format(_norm(record.get("id"))),
        "a_demarcher": "oui",
        "_nb_gagnants": len(gagnants),
        "_duree": duree,
        "_origine": origine,
        "_etranger": etranger,
    }


# ===========================================================================
# COLLECTE
# ===========================================================================

def collecte(session=None):
    """Pagine les attributions BM, LES PLUS RECENTES D'ABORD.

    Le tri serveur (srt/ord, supporte par l'API de recherche BM) est essentiel :
    sans lui, la pagination ramenait des avis dans un ordre arbitraire et le
    plafond de pages etait atteint sur des attributions trop anciennes (577
    ecartees "hors fenetre" au run du 18/07/2026, pour 12 pages sur 12 = plafond
    sature). Trie, le meme budget de pages couvre les avis les PLUS FRAIS, et
    l'on peut s'arreter des qu'on sort de la fenetre de mobilisation.

    Best-effort : une page en echec interrompt la pagination sans faire echouer
    le run. Si le tri n'etait pas honore, le comportement reste correct (on
    lit simplement autant de pages qu'avant)."""
    session = session or ted.session_robuste()
    records, stats = [], {"pages": 0, "recus": 0, "arret": "plafond de pages"}
    # Marge : la date de NOTIFICATION d'attribution (lue dans notice_text) peut
    # preceder la date de publication de l'avis. On ne coupe donc pas au ras de
    # la fenetre, sous peine de perdre des attributions encore valides.
    marge = JOURS_FENETRE + 60
    for page in range(PAGES_MAX):
        params = {"format": "json", "rows": bm.ROWS_BM,
                  "os": page * bm.ROWS_BM,
                  "notice_type_exact": TYPE_ATTRIBUTION,
                  "srt": "noticedate", "ord": "desc"}
        try:
            rep = session.get(bm.BM_ENDPOINT, params=params, timeout=45)
            if rep.status_code >= 400:
                print("(bm-attrib) page {} : HTTP {}, arret de la pagination.".format(
                    page, rep.status_code))
                stats["arret"] = "erreur HTTP"
                break
            data = rep.json()
        except Exception as e:
            print("(bm-attrib) page {} illisible ({}), arret.".format(page, e))
            stats["arret"] = "reponse illisible"
            break
        lot = data.get("procnotices") or []
        if not lot:
            stats["arret"] = "fin des donnees"
            break
        records.extend(lot)
        stats["pages"] += 1
        stats["recus"] += len(lot)
        if len(lot) < bm.ROWS_BM:
            stats["arret"] = "fin des donnees"
            break
        if _page_trop_ancienne(lot, marge):
            stats["arret"] = "avis anterieurs a la fenetre (tri par date)"
            break
    return records, stats


def _page_trop_ancienne(lot, marge_jours, aujourdhui=None):
    """Vrai si TOUS les avis de la page precedent la fenetre utile. Sert a
    stopper la pagination une fois le tri par date epuise. Prudent : si aucune
    date n'est lisible, on ne coupe pas."""
    aujourdhui = aujourdhui or date.today()
    limite = aujourdhui - timedelta(days=marge_jours)
    vues = 0
    for rec in lot:
        d = _date_notice(rec)
        if not d:
            continue
        vues += 1
        if d >= limite:
            return False
    return vues > 0


def _date_notice(record):
    """Date de publication de l'avis ('17-Jul-2026'), ou None."""
    brut = _norm(record.get("noticedate"))
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(brut, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def construire(records):
    """Records bruts -> attributions normalisees, dedupliquees par identifiant."""
    sorties, vus = [], set()
    motifs = {"type": 0, "groupe": 0, "pays": 0, "sans_gagnant": 0,
              "hors_fenetre": 0, "local": 0, "republie": 0}
    for rec in records:
        ok, motif = record_retenu(rec)
        if not ok:
            motifs[motif] = motifs.get(motif, 0) + 1
            continue
        ligne = normaliser(rec)
        if ligne is None:
            texte = rec.get("notice_text") or ""
            if not extraire_gagnants(texte):
                motifs["sans_gagnant"] += 1
            elif not dans_la_fenetre(date_attribution(texte, rec)):
                motifs["hors_fenetre"] += 1
            else:
                motifs["local"] += 1
            continue
        pub = ligne["publication_number"]
        if pub and pub in vus:
            continue
        # Dedoublonnage SECONDAIRE : la BM republie le meme marche sous
        # plusieurs identifiants (constate : BETH BETSALEEL SARL trois fois le
        # meme jour pour le meme montant). Meme titulaire + meme date + meme
        # montant = meme attribution, on n'en garde qu'une.
        empreinte = (ligne["gagnant"].lower(), ligne["date_publication"],
                     ligne["valeur_attribuee"])
        if empreinte in vus:
            motifs["republie"] = motifs.get("republie", 0) + 1
            continue
        vus.add(pub)
        vus.add(empreinte)
        sorties.append(ligne)
    return sorties, motifs


# ===========================================================================
# ECRITURE (onglet partage avec les attributions TED)
# ===========================================================================

def ouvrir_feuille(sheet_id, fichier):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        f = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000,
                                   cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f
    if COL_DETECTION not in f.row_values(1):
        f.update(values=[TOUTES_COLONNES], range_name="A1")
    return f


def ligne_pour_sheet(a):
    return [str(a.get(c, "")) for c in COLONNES]


def ecrire(feuille, attributions):
    """N'ajoute que les nouvelles lignes. Ne REECRIT jamais une ligne existante :
    la colonne `statut_prospection` est une zone de saisie humaine."""
    # Index construit en LECTURE POSITIONNELLE depuis le SCHEMA (regle 4) :
    # la position de `publication_number` vient de COLONNES, jamais de
    # l'en-tete de la feuille. Immunise contre un en-tete desaligne, un en-tete
    # duplique et la numerisation des identifiants. Voir ted.index_publications.
    index = ted.charger_index_publication(feuille, COLONNES)
    nouvelles, ignorees = [], 0
    for a in attributions:
        pub = a.get("publication_number", "")
        if pub and pub in index:
            ignorees += 1
            continue
        nouvelles.append(ligne_pour_sheet(a) + ["", date.today().isoformat()])
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
    return len(nouvelles), ignorees


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    if not ACTIVER:
        print("(info) Collecteur attributions BM desactive (RADAR_BM_ATTRIB=0).")
        return

    print("Collecte des attributions Banque Mondiale "
          "(groupes {}, fenetre {} jours)...".format(
              "/".join(sorted(bm.BM_GROUPES_RETENUS)), JOURS_FENETRE))
    records, stats = collecte()
    print("  {} enregistrement(s) recu(s) sur {} page(s) (arret : {}).".format(
        stats["recus"], stats["pages"], stats.get("arret", "n.c.")))

    attributions, motifs = construire(records)
    print("  ecartes -> groupe hors CS/CW : {groupe} | pays hors perimetre : {pays} | "
          "sans gagnant lisible : {sans_gagnant} | hors fenetre : {hors_fenetre} | "
          "titulaire local : {local} | republie : {republie}".format(**motifs))
    print("  {} attribution(s) exploitable(s).".format(len(attributions)))

    if DEBUG:
        print("\n--- MODE VERIFICATION (RADAR_BM_ATTRIB_DEBUG=1) : AUCUNE ECRITURE ---")
        for a in attributions[:25]:
            print("  [{}] {:38} | {} <- {} | {} | {}".format(
                a["date_publication"], a["gagnant"][:38], a["pays_execution"],
                (a.get("_origine") or "origine n.c."), a["secteur"],
                (a["valeur_attribuee"] or "montant n.c.")))
        print("--- Verifie que les noms ci-dessus sont bien des entreprises. ---")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not (sheet_id and fichier):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    try:
        feuille = ouvrir_feuille(sheet_id, fichier)
        ajoutees, deja = ecrire(feuille, attributions)
        print("  {} nouvelle(s) ligne(s) ecrite(s) dans '{}' "
              "({} deja connue(s)).".format(ajoutees, NOM_ONGLET, deja))
    except Exception as e:
        print("(bm-attrib) ecriture impossible ({}). Le run continue.".format(e))


if __name__ == "__main__":
    main()
