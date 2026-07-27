# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- ATTRIBUTIONS UNGM (marches ONU attribues).
=============================================================

CE QU'IL FAIT
-------------
Recupere les CONTRATS ATTRIBUES par les agences des Nations Unies (page
publique "Contract Awards" d'UNGM), en extrait le TITULAIRE, et ecrit dans
l'onglet `attributions_radar` -- CELUI DES ATTRIBUTIONS TED ET BANQUE MONDIALE.

Consequence voulue : AUCUN cablage dashboard. Les lignes remontent seules dans
la lentille "Titulaires - attributions" et dans la fiche entreprise 360.

POURQUOI CETTE SOURCE
---------------------
C'est la meme logique que les attributions BM, appliquee au systeme onusien :
une entreprise qui vient de remporter un marche PAM/HCR/UNOPS en Somalie ou au
Soudan du Sud va y deployer des equipes. Elle est nommee, datee, donc
directement demarchable, contrairement a un avis ou le futur titulaire est
inconnu.

ETAT DE LA SOURCE (sondes des 18 et 20/07/2026)
------------------------------------------------
  - GET  /Public/ContractAward         -> HTTP 200, tableau rendu en JavaScript
  - POST /Public/ContractAward/Search  -> HTTP 200 mais reponse de 101 octets
    (donc acceptee mais VIDE) avec la charge "awards1" ; HTTP 500 avec les
    autres formes essayees. La forme est donc presque bonne, il manque
    vraisemblablement un filtre obligatoire.
  - Depuis, on sait interroger UNGM PAYS PAR PAYS (identifiants extraits du
    formulaire). L'hypothese testee ici est que la recherche d'attributions
    exige justement un filtre de ce type.

D'ou le MODE DECOUVERTE ci-dessous, qui essaie plusieurs charges utiles et
imprime celle qui ramene des lignes, avec la structure brute.

MODE VERIFICATION / DECOUVERTE (a utiliser au premier run)
-----------------------------------------------------------
    RADAR_UNGM_ATTRIB_DEBUG=1  -> n'ecrit rien ; essaie les charges utiles,
    imprime celle qui fonctionne et la structure des lignes obtenues.

Interrupteur : RADAR_UNGM_ATTRIB=0 desactive la collecte.

LANCEMENT :  python ungm_attributions.py
"""

import json
import os
import re
from datetime import date, datetime, timedelta

import bm_attributions as bma      # resolveur de pays bilingue, deja teste
import ted_complet_v14 as ted
import radar_resilience
import ungm_radar as ungm          # parsing de lignes, identifiants pays


ACTIVER = os.environ.get("RADAR_UNGM_ATTRIB", "1") != "0"
DEBUG = os.environ.get("RADAR_UNGM_ATTRIB_DEBUG", "0") == "1"

PAGE_AWARDS = "https://www.ungm.org/Public/ContractAward"
# Endpoint REEL, releve dans le bundle ungmcommon (fonction
# UNGM.ContractAwardSearch.search) le 20/07/2026. Ce n'est PAS /Search :
# cette derniere existe mais renvoie un reliquat interne (nom de type .NET),
# ce qui a fait echouer quatorze tentatives avant qu'on lise le JS.
ENDPOINT_AWARDS = "https://www.ungm.org/Public/ContractAward/PublicSearch"
# Variante servie quand le champ fournisseur est absent de la page.
ENDPOINT_AWARDS_VENDOR = "https://www.ungm.org/Public/ContractAward/SearchByVendor"
# Fiche detaillee d'une attribution (vue par onGotAwardDetail).
LIEN_POPUP = "https://www.ungm.org/Public/ContractAward/Popup/{}"
LIEN_AWARD = "https://www.ungm.org/Public/ContractAward/{}"

JOURS_FENETRE = int(os.environ.get("RADAR_UNGM_ATTRIB_JOURS", "180"))
PAYS_MAX = int(os.environ.get("RADAR_UNGM_ATTRIB_PAYS_MAX", "45"))
PAGES_PAR_PAYS = int(os.environ.get("RADAR_UNGM_ATTRIB_PAGES", "3"))
# Le portail envoie PageSize=15 : on s'aligne pour ne pas sortir du cadre teste.
TAILLE_PAGE = int(os.environ.get("RADAR_UNGM_ATTRIB_TAILLE", "15"))
MINUTES_MAX = float(os.environ.get("RADAR_UNGM_ATTRIB_MINUTES", "10"))

# Onglet PARTAGE avec les attributions TED et BM : integration dashboard
# gratuite (lentille Titulaires + fiche entreprise 360).
NOM_ONGLET = "attributions_radar"
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
COL_STATUT = "statut_prospection"
COL_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COL_STATUT, COL_DETECTION]

ENTETES = dict(ungm.ENTETES, **{"Referer": PAGE_AWARDS})

# Identifiant de ligne : les attributions n'utilisent pas data-noticeid.
# L'identifiant alimente Public/ContractAward/Popup/{id} (vu dans le JS).
RE_ID_AWARD = re.compile(
    r'(?:data-(?:contractawardid|awardid|noticeid|id)|id)="(?:award_?)?(\d{3,})"', re.I)


# ===========================================================================
# CHARGES UTILES CANDIDATES
# ===========================================================================

def charge_officielle(page=0, taille=15, id_pays=None):
    """Charge utile EXACTE, copiee sur UNGM.ContractAwardSearch.buildOptions()
    du bundle ungmcommon (releve le 20/07/2026).

    Deux noms de champs avaient ete mal devines auparavant : c'est AwardFrom /
    AwardTo, et non AwardDateFrom / AwardDateTo. Le portail ignore
    silencieusement les champs inconnus, d'ou des reponses vides sans erreur."""
    return {
        "PageIndex": page,
        "PageSize": taille,
        "Title": "",
        "Description": "",
        "Reference": "",
        "Supplier": "",
        "UngmNumber": "",
        "AwardFrom": "",
        "AwardTo": "",
        "Countries": [id_pays] if id_pays else [],
        "SupplierCountries": [],
        "Agencies": [],
        "UNSPSCs": [],
        "SortField": "AwardDate",
        "SortAscending": False,
    }


def charges_candidates(page=0, taille=15, id_pays=None):
    """Formes a essayer. La premiere est la charge OFFICIELLE relevee dans le
    JavaScript du portail ; les suivantes ne sont que des filets de securite
    au cas ou UNGM ferait evoluer son appel.

    Renvoie [(nom, url, charge, encodage), ...]."""
    officielle = charge_officielle(page, taille, id_pays)
    formes = [
        ("officielle -> PublicSearch", ENDPOINT_AWARDS, officielle, "json"),
        ("officielle -> SearchByVendor", ENDPOINT_AWARDS_VENDOR, officielle, "json"),
    ]
    # Filet : meme charge avec une fenetre de dates explicite.
    avec_dates = dict(officielle)
    avec_dates["AwardFrom"] = (date.today() - timedelta(days=JOURS_FENETRE)
                               ).strftime("%d-%b-%Y")
    avec_dates["AwardTo"] = date.today().strftime("%d-%b-%Y")
    formes.append(("officielle + dates", ENDPOINT_AWARDS, avec_dates, "json"))
    return formes


def interroger(session, url, charge, encodage="json", timeout=45):
    """POST best-effort. Renvoie (status, texte). N'exception jamais."""
    try:
        if encodage == "json":
            corps = json.dumps(charge).encode("utf-8")
            ctype = "application/json"
        else:
            corps = charge
            ctype = "application/x-www-form-urlencoded"
        rep = session.post(url, data=corps,
                           headers=dict(ENTETES, **{"Content-Type": ctype}),
                           timeout=timeout)
        return rep.status_code, (rep.text or "")
    except Exception as e:
        return 0, "exception: {}".format(e)


def extraire_attributions(html_brut):
    """Lignes d'attribution -> [{'id':..., 'cellules':[...]}].

    Le JavaScript du portail traite la reponse par
    `$(data).toArray()` puis `.filter(".dataRow")` : les lignes se reconnaissent
    donc a leur CLASSE dataRow, pas seulement a role="row". On accepte les
    deux, car les avis utilisent role="row" et rien ne garantit que les
    attributions fassent pareil."""
    src = str(html_brut or "")
    bornes = sorted(set(
        [m.start() for m in ungm.RE_LIGNE.finditer(src)] +
        [m.start() for m in re.finditer(
            r'<div[^>]*\bclass="[^"]*\bdataRow\b[^"]*"[^>]*>', src, re.I)]))
    lignes = []
    for i, deb in enumerate(bornes):
        fin = bornes[i + 1] if i + 1 < len(bornes) else len(src)
        bloc = src[deb:fin]
        cellules = [ungm.nettoyer_cellule(ungm._texte(c))
                    for c in ungm.RE_CELLULE.findall(bloc)]
        cellules = [c for c in cellules if c]
        if not cellules:
            continue
        m = RE_ID_AWARD.search(bloc)
        lignes.append({"id": m.group(1) if m else "", "cellules": cellules})
    return lignes


# ===========================================================================
# INTERPRETATION
# ===========================================================================

def interpreter(cellules):
    """Cellules -> champs, par RECONNAISSANCE DE CONTENU (comme les avis).

    On ne fige aucun ordre de colonne : le portail peut le changer. L'agence
    ONU se reconnait a sa liste, les dates a leur motif, et le titulaire est
    la cellule textuelle restante la plus consequente."""
    dates, agence, reference, pays_cellule, masque, restes = [], "", "", "", False, []
    for c in cellules:
        d = ungm.lire_date(c)
        if d and ungm.RE_DATE.match(ungm.RE_RESIDU_DATE.sub("", c).strip()):
            dates.append(d)
            continue
        if not agence and ungm._est_agence(c):
            agence = c
            continue
        if not reference and ungm.RE_REFERENCE.match(c) and any(x.isdigit() for x in c):
            reference = c
            continue
        if not pays_cellule and _est_un_pays(c):
            pays_cellule = c              # colonne pays de la ligne
            continue
        if RE_TITULAIRE_MASQUE.search(c):
            masque = True                 # "Name withheld for security reasons"
            continue
        restes.append(c)
    dates = sorted(set(dates))
    return {"dates": dates, "agence": agence or "Nations Unies",
            "reference": reference, "restes": restes,
            "pays_cellule": pays_cellule, "titulaire_masque": masque,
            "date_attribution": dates[-1] if dates else ""}


# Mentions signalant un titulaire VOLONTAIREMENT masque. L'UNOPS notamment
# ecrit "Name withheld for security reasons" sur ses marches afghans : la
# ligne n'a alors aucune valeur commerciale et doit etre ecartee, pas contournee.
RE_TITULAIRE_MASQUE = re.compile(
    r"(?i)(withheld|not disclosed|undisclosed|confidential|redacted|"
    r"non (?:communique|divulgue)|masque|anonym)")


def _est_un_pays(txt):
    """Vrai si la cellule EST un nom de pays. La structure reelle expose une
    colonne pays en fin de ligne ; sans ce filtre, "Afghanistan" etait retenu
    comme titulaire (constate le 20/07/2026)."""
    t = re.sub(r"\s+", " ", str(txt or "")).strip()
    if not t or len(t) > 60:
        return False
    return bool(bma.iso3_pays_libre(t))


def _plausible_entreprise(txt):
    """Ecarte ce qui ne peut pas etre une raison sociale."""
    t = re.sub(r"\s+", " ", str(txt or "")).strip()
    if len(t) < 3 or len(t) > 160:
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]{3}", t):
        return False
    if re.match(r"^[\d\s.,/%-]+$", t):
        return False
    if re.match(r"(?i)^(n/?a|none|not (applicable|disclosed)|confidential)$", t):
        return False
    if RE_TITULAIRE_MASQUE.search(t):
        return False                      # titulaire masque par l'agence
    if _est_un_pays(t):
        return False                      # colonne pays, pas une societe
    return True


# --- Nature du marche : qui se DEPLACE, qui se contente d'expedier ? -------
# Constat du run reel : la liste melange des fournisseurs d'equipement (Kjaer
# & Kjaer, MANITOU, Guangxi Liugong : ils livrent des engins, ils n'envoient
# personne) et des entreprises de travaux (Harirod Construction, Rayan Saadat :
# elles mobilisent des equipes sur site). C'est la meme distinction que
# CS/CW contre GO a la Banque Mondiale.
#
# On CLASSE au lieu de filtrer : le libelle part dans la colonne `secteur`,
# donc il est visible et filtrable dans la lentille Titulaires, et le mot
# "travaux" declenche le bonus de secteur du mini-score du dashboard. C'est a
# l'analyste de trancher, pas au collecteur de decider a sa place.
#
# PIEGE CORRIGE (run du 20/07/2026) : chercher le mot "construction" faisait
# classer en TRAVAUX des vendeurs d'engins (Kjaer & Kjaer, MANITOU, Guangxi
# Liugong), car leurs marches portent sur du "construction equipment". Il faut
# distinguer l'ACTIVITE (construire, installer) de l'OBJET (des engins de
# construction). On teste donc des LOCUTIONS, pas des mots isoles.
MOTS_INTERVENTION = (
    "construction of", "construction works", "civil works", "civil engineering",
    "rehabilitation", "refurbishment", "renovation", "upgrading", "upgrade of",
    "installation", "erection", "drilling", "borehole", "forage", "paving",
    "excavation", "earthworks", "de-mining", "demining",
    "travaux", "rehabilitation de", "amenagement", "aménagement",
    "construction d", "realisation de", "réalisation de", "pose de",
)
# Un achat de biens : le titulaire expedie, il ne mobilise personne sur place.
MOTS_ACHAT = (
    "supply of", "supply and delivery", "supply, delivery", "procurement of",
    "purchase of", "delivery of", "provision of goods", "spare parts",
    "equipment", "machinery", "vehicles", "vehicle", "furniture", "hardware",
    "fourniture de", "acquisition de", "livraison de",
)
MOTS_SERVICES = (
    "services", "service", "transport", "logistics", "logistique", "freight",
    "security", "securite", "sécurité", "guard", "escort", "escorte",
    "maintenance", "consultancy", "cleaning", "catering", "management",
    "supervision", "training", "survey", "assessment", "monitoring",
)


def nature_marche(titre):
    """'travaux', 'services', 'fournitures' ou 'indetermine' selon l'OBJET.

    Ordre de decision, du plus discriminant au moins :
      1. une INTERVENTION sur site prime sur tout ("supply AND installation"
         reste des travaux : quelqu'un vient poser le materiel) ;
      2. sinon un ACHAT de biens ("supply of construction equipment" est une
         fourniture, malgre le mot construction) ;
      3. sinon une prestation de services."""
    t = " {} ".format(re.sub(r"\s+", " ", str(titre or "")).lower())
    if any(m in t for m in MOTS_INTERVENTION):
        return "travaux"
    if any(m in t for m in MOTS_ACHAT):
        return "fournitures"
    if any(m in t for m in MOTS_SERVICES):
        return "services"
    return "indetermine"
RE_FORME_JURIDIQUE = re.compile(
    r"(?i)\b(ltd|limited|llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|"
    r"sarl|s\.a\.r\.l|sas|s\.a|spa|s\.p\.a|gmbh|ag|bv|n\.v|nv|plc|pvt|pte|"
    r"sdn\s*bhd|oy|ab|a\.s|as|group|groupe|holding|holdings|enterprises?|"
    r"entreprises?|consortium|joint\s*venture|jv|partners|associates|"
    r"international|global|services\s+ltd|& sons|et fils)\b\.?$")
# Marqueurs d'OBJET de marche : ces tournures ouvrent un intitule, jamais une
# raison sociale.
RE_OBJET_MARCHE = re.compile(
    r"(?i)^(provision|supply|supplies|procurement|construction|delivery|"
    r"purchase|rental|lease|installation|maintenance|rehabilitation|"
    r"consultancy|consulting services|design|development of|works|"
    r"fourniture|travaux|acquisition|prestation|realisation|réalisation)\b")


def _score_entreprise(txt):
    """Vraisemblance qu'une cellule soit une RAISON SOCIALE plutot qu'un objet
    de marche. La longueur seule ne suffit pas : un intitule court comme
    "Convoy escort services" etait pris pour le titulaire."""
    t = re.sub(r"\s+", " ", str(txt or "")).strip()
    if not _plausible_entreprise(t):
        return -99.0
    score = 0.0
    if RE_FORME_JURIDIQUE.search(t):
        score += 3.0                      # "... Ltd", "... SARL" : decisif
    if RE_OBJET_MARCHE.match(t):
        score -= 3.0                      # "Provision of ..." : objet, pas societe
    if re.search(r"(?i)\b(of|pour|de la|des)\b", t):
        score -= 0.8                      # tournure descriptive
    mots = t.split()
    if len(mots) <= 6:
        score += 0.6                      # une raison sociale est concise
    if len(mots) > 10:
        score -= 1.2
    # Majuscules initiales sur la plupart des mots : signature d'un nom propre.
    capitalises = sum(1 for m in mots if m[:1].isupper())
    if mots and capitalises / len(mots) >= 0.6:
        score += 0.8
    if t.isupper() and len(mots) <= 8:
        score += 0.5                      # "STECOL CORPORATION"
    return score


def montant_et_titulaire(champs):
    """(titulaire, montant) depuis les cellules restantes.

    Le montant se reconnait a sa forte densite de chiffres ; le titulaire est
    la cellule qui RESSEMBLE LE PLUS a une raison sociale (voir
    _score_entreprise). Prudent : renvoie ('', ...) si rien de credible, pour
    ne jamais ecrire un objet de marche dans la colonne gagnant."""
    montant, candidats = "", []
    for c in champs.get("restes", []):
        chiffres = sum(x.isdigit() for x in c)
        if chiffres and chiffres >= max(3, len(c) * 0.4):
            if not montant:
                montant = c[:60]
            continue
        note = _score_entreprise(c)
        if note > -50:
            candidats.append((note, c))
    if not candidats:
        return "", montant
    candidats.sort(key=lambda x: (-x[0], len(x[1])))
    meilleur, texte = candidats[0]
    # Un score nul ou negatif signifie "ne ressemble pas a une societe" : on
    # refuse, meme s'il n'y a qu'un seul candidat. Mieux vaut aucune ligne
    # qu'un objet de marche inscrit dans la colonne gagnant du CRM.
    if meilleur <= 0:
        return "", montant
    return texte, montant


def normaliser(ligne, iso3):
    """Ligne d'attribution -> ligne de l'onglet `attributions_radar`.
    None si inexploitable. `iso3` vient de la requete : le pays est certain."""
    champs = interpreter(ligne.get("cellules") or [])
    if champs.get("titulaire_masque"):
        return None                       # l'agence a masque le nom : sans valeur
    titulaire, montant = montant_et_titulaire(champs)
    if not titulaire:
        return None
    d_attrib = champs["date_attribution"]
    if d_attrib and not bma.dans_la_fenetre(d_attrib, jours=JOURS_FENETRE):
        return None
    if not iso3 or iso3 not in ted.MULTIPLICATEUR_ZONE:
        return None

    restes = [r for r in champs["restes"] if r != titulaire]
    titre = max(restes, key=len) if restes else titulaire
    ident = ligne.get("id") or ""
    nature = nature_marche(titre)
    pub = "UNGMA-{}".format(ident) if ident else "UNGMA-{}-{}-{}".format(
        iso3, (d_attrib or "nd"), abs(hash(titulaire)) % 999983)
    return {
        "date_maj": date.today().isoformat(),
        "gagnant": titulaire,
        # La nature part dans `secteur` : visible et filtrable dans la lentille
        # Titulaires, et "travaux" declenche le bonus de secteur du mini-score.
        "secteur": "Marche ONU - {}".format(nature),
        # ISO3 obligatoire : le dashboard resout les attributions en mode ISO.
        "pays_execution": iso3,
        "valeur_attribuee": montant,
        "acheteur": champs["agence"],
        "titre": titre[:300],
        "cpv": champs["reference"][:40],
        "sous_traitance": "",
        "date_publication": d_attrib,
        # Nom du pays de livraison, conserve pour la comparaison avec le pays
        # d'origine du titulaire (filtre local/etranger a l'enrichissement).
        "_pays_nom": champs.get("pays_cellule") or "",
        "publication_number": pub,
        "lien": LIEN_AWARD.format(ident) if ident else PAGE_AWARDS,
        "a_demarcher": "oui",
    }


# ===========================================================================
# COLLECTE
# ===========================================================================

def lignes_depuis_reponse(texte):
    """Lignes exploitables d'une reponse, QUEL QUE SOIT SON FORMAT.

    Erreur corrigee le 20/07/2026 : on ne cherchait que des <div role="row">.
    Si l'endpoint repond en JSON (ce que 101 octets constants suggerent), on
    concluait a tort a l'echec sans jamais regarder le contenu."""
    t = (texte or "").strip()
    if not t:
        return []
    # Piste HTML (meme rendu que les avis).
    lignes = extraire_attributions(t)
    if lignes:
        return lignes
    # Piste JSON : enveloppe {..., "Rows"/"Data"/"Items": [...]} ou liste nue.
    if t[:1] in "[{":
        try:
            data = json.loads(t)
        except Exception:
            return []
        bloc = data if isinstance(data, list) else None
        if bloc is None and isinstance(data, dict):
            for cle in ("Rows", "rows", "Data", "data", "Items", "items",
                        "Results", "results", "ContractAwards"):
                if isinstance(data.get(cle), list):
                    bloc = data[cle]
                    break
            # L'enveloppe peut aussi contenir le HTML du tableau.
            if bloc is None:
                for cle in ("Html", "html", "Content", "content", "View"):
                    if isinstance(data.get(cle), str) and data[cle].strip():
                        return extraire_attributions(data[cle])
        if not bloc:
            return []
        sorties = []
        for element in bloc:
            if isinstance(element, dict):
                cellules = [str(v) for v in element.values()
                            if v not in (None, "", [], {})]
                sorties.append({"id": str(element.get("Id")
                                          or element.get("id") or ""),
                                "cellules": cellules, "_json": element})
        return sorties
    return []


def trouver_charge_qui_marche(session, id_pays=None):
    """Essaie les charges candidates et renvoie (nom, url, charge, encodage,
    lignes) pour la premiere qui ramene des lignes. Imprime SYSTEMATIQUEMENT
    le corps des reponses courtes : c'est l'information la plus utile, et son
    absence a fait perdre deux tours de diagnostic."""
    corps_vus = set()
    for nom, url, charge, encodage in charges_candidates(0, TAILLE_PAGE, id_pays):
        statut, texte = interroger(session, url, charge, encodage)
        lignes = lignes_depuis_reponse(texte) if statut and statut < 400 else []
        if DEBUG:
            print("    {:32} -> HTTP {} ({} octets) : {} ligne(s)".format(
                nom, statut, len(texte), len(lignes)))
            apercu = " ".join((texte or "").split())[:300]
            if apercu and apercu not in corps_vus:
                corps_vus.add(apercu)
                print("        corps : {}".format(apercu))
        if lignes:
            return nom, url, charge, encodage, lignes
    return None, None, None, None, []


def collecte(session=None):
    """Interroge les attributions pays par pays, comme les avis."""
    import time as _time
    session = session or ted.session_robuste()
    table = ungm.charger_pays_ungm(session)
    cibles = ungm.pays_a_interroger(table)[:PAYS_MAX] if table else []
    stats = {"pays": 0, "lignes": 0, "requetes": 0,
             "charge": "", "arret": "termine"}
    if not cibles:
        stats["arret"] = "aucun pays exploitable"
        return [], stats

    # On determine d'abord LA forme de requete acceptee, sur le premier pays.
    if DEBUG:
        print("\n[A] Recherche de la charge utile acceptee "
              "(pays test : {}) :".format(cibles[0][0]))
    nom_charge, url_ok, modele_charge, encodage, _ = trouver_charge_qui_marche(
        session, cibles[0][1])
    if not modele_charge:
        stats["arret"] = "aucune charge utile acceptee"
        return [], stats
    stats["charge"] = nom_charge

    lignes, vus = [], set()
    debut = _time.time()
    for iso, ident in cibles:
        if (_time.time() - debut) / 60.0 >= MINUTES_MAX:
            stats["arret"] = "garde-temps"
            break
        stats["pays"] += 1
        for page in range(PAGES_PAR_PAYS):
            charge = dict(modele_charge)
            charge["PageIndex"] = page
            # Le champ de pays differe selon la convention retenue : on
            # renseigne celui que la charge gagnante utilise reellement.
            for cle in ("Countries", "selContractAwardCountry", "FirstCountry"):
                if cle in charge:
                    charge[cle] = [ident] if cle == "Countries" else ident
            statut, texte = interroger(session, url_ok, charge, encodage)
            stats["requetes"] += 1
            if not statut or statut >= 400:
                break
            lot = lignes_depuis_reponse(texte)
            neuves = []
            for l in lot:
                cle = l["id"] or "|".join(l["cellules"])[:120]
                if cle in vus:
                    continue
                vus.add(cle)
                l["pays_iso3"] = iso
                neuves.append(l)
            lignes.extend(neuves)
            if not neuves or len(lot) < charge.get("PageSize", TAILLE_PAGE):
                break
    stats["lignes"] = len(lignes)
    return lignes, stats


def construire(lignes):
    sorties, motifs = [], {"sans_titulaire": 0, "hors_fenetre_ou_pays": 0,
                          "titulaire_masque": 0, "doublon": 0}
    vus = set()
    for ligne in lignes:
        a = normaliser(ligne, ligne.get("pays_iso3", ""))
        if a is None:
            champs = interpreter(ligne.get("cellules") or [])
            if champs.get("titulaire_masque"):
                motifs["titulaire_masque"] += 1
            elif not montant_et_titulaire(champs)[0]:
                motifs["sans_titulaire"] += 1
            else:
                motifs["hors_fenetre_ou_pays"] += 1
            continue
        empreinte = (a["gagnant"].lower(), a["pays_execution"], a["date_publication"])
        if empreinte in vus:
            motifs["doublon"] += 1
            continue
        vus.add(empreinte)
        sorties.append(a)
    return sorties, motifs


# ===========================================================================
# ENRICHISSEMENT PAR LA FICHE DETAILLEE
# ===========================================================================
# La liste UNGM ne donne ni le MONTANT ni le PAYS D'ORIGINE du titulaire. Or
# ces deux informations sont decisives :
#   - le montant alimente le poids de valeur du mini-score (sans lui, toutes
#     les attributions scorent au minimum) ;
#   - le pays d'origine permet le filtre local/etranger, celui-la meme qui a
#     ecarte 490 entrepreneurs locaux cote Banque Mondiale. Un macon de Kaboul
#     n'achete pas de protection internationale.
# La fiche detaillee est servie par GET Public/ContractAward/Popup/{id}
# (appel releve dans le bundle JS, fonction onGotAwardDetail).

# ETEINT PAR DEFAUT apres verification sur donnees reelles (21/07/2026) :
# la fiche detaillee ne contient QUE title, reference, award date et
# description. Ni pays du fournisseur, ni montant. 21 fiches lues ont
# donne 0 pays et 0 montant. On garde le code (regle ADB : on eteint, on
# ne supprime pas) au cas ou UNGM enrichirait ses fiches, mais l'activer
# aujourd'hui coute des requetes pour rien.
# CONSEQUENCE ASSUMEE : contrairement aux attributions BM, on ne peut ni
# filtrer les titulaires locaux, ni ponderer par le montant. Les noms
# restent exploitables, l'analyste juge a l'oeil.
ENRICHIR = os.environ.get("RADAR_UNGM_ATTRIB_ENRICHIR", "0") == "1"
# Budget de requetes : une par fiche. On se limite par defaut aux marches ou
# quelqu'un se deplace, ce qui divise le cout par trois ou quatre.
ENRICH_MAX = int(os.environ.get("RADAR_UNGM_ATTRIB_ENRICH_MAX", "40"))
ENRICH_NATURES = tuple(
    n.strip() for n in
    os.environ.get("RADAR_UNGM_ATTRIB_ENRICH_NATURES", "travaux,services").split(",")
    if n.strip())

RE_ETIQUETTE = re.compile(r"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’ /()-]{2,48})\s*:\s*(.*)$")
# Etiquettes candidates, dans l'ordre de preference.
LABELS_PAYS_FOURNISSEUR = (
    "supplier country", "vendor country", "country of supplier",
    "supplier's country", "supplier country/territory", "pays du fournisseur",
)
LABELS_MONTANT = (
    "contract value", "award value", "contract amount", "awarded amount",
    "total value", "value", "amount", "montant",
)


def url_popup(ident):
    return LIEN_POPUP.format(ident)


def _lignes_fiche(html_brut):
    """HTML de fiche -> lignes de texte propres."""
    t = str(html_brut or "")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?i)</\s*(div|p|tr|td|th|li|h\d|span|dt|dd)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    import html as _h
    t = _h.unescape(t)
    return [re.sub(r"\s+", " ", l).strip() for l in t.split("\n")
            if re.sub(r"\s+", " ", l).strip()]


def paires_fiche(html_brut):
    """{etiquette minuscule: valeur} depuis une fiche detaillee.

    Trois mises en forme possibles, toutes rencontrees sur ce type de portail
    et impossibles a departager sans avoir vu la vraie fiche :
      1. "Etiquette : valeur" sur une meme ligne ;
      2. "Etiquette :" puis la valeur a la ligne suivante ;
      3. listes de definition (<dt>Etiquette</dt><dd>valeur</dd>), qui ne
         produisent AUCUN deux-points apres retrait des balises."""
    lignes = _lignes_fiche(html_brut)
    paires = {}

    # Passes 1 et 2 : etiquettes suivies d'un deux-points.
    for i, ligne in enumerate(lignes):
        m = RE_ETIQUETTE.match(ligne)
        if not m:
            continue
        cle = m.group(1).strip().lower()
        val = m.group(2).strip()
        if not val and i + 1 < len(lignes):
            suivante = lignes[i + 1]
            if not RE_ETIQUETTE.match(suivante):
                val = suivante
        if val and cle not in paires:
            paires[cle] = val[:160]

    # Passe 3 : une ligne qui EST exactement une etiquette connue, la valeur
    # se trouvant a la ligne suivante.
    connues = set(LABELS_PAYS_FOURNISSEUR) | set(LABELS_MONTANT) | {
        "supplier", "vendor", "agency", "country", "award date", "reference",
        "description", "title", "fournisseur", "pays", "date"}
    for i, ligne in enumerate(lignes[:-1]):
        cle = ligne.strip().rstrip(":").lower()
        if cle in connues and cle not in paires:
            suivante = lignes[i + 1].strip()
            if suivante and suivante.rstrip(":").lower() not in connues:
                paires[cle] = suivante[:160]
    return paires


def _valeur_parmi(paires, labels):
    """Premiere valeur dont l'etiquette correspond, en tolerant les variantes."""
    for label in labels:
        if label in paires:
            return paires[label]
    for label in labels:
        for cle, val in paires.items():
            if label in cle:
                return val
    return ""


def extraire_fiche(html_brut):
    """(pays_fournisseur, montant_usd, paires) depuis une fiche detaillee."""
    paires = paires_fiche(html_brut)
    pays = _valeur_parmi(paires, LABELS_PAYS_FOURNISSEUR)
    montant_brut = _valeur_parmi(paires, LABELS_MONTANT)
    montant = ""
    if montant_brut:
        # On reutilise le lecteur de montant des attributions BM : il gere la
        # devise dupliquee et convertit en USD pour un scoring comparable.
        montant = bma._lire_montant(montant_brut)
    return pays.strip(), montant, paires


def enrichir(attributions, session=None, fetch=None):
    """Complete montant et pays d'origine du titulaire, dans la limite du
    budget. Best-effort : une fiche en echec laisse l'attribution telle quelle.

    Renvoie des statistiques pour le journal."""
    stats = {"tentees": 0, "pays": 0, "montants": 0, "echecs": 0, "fiches": []}
    if not ENRICHIR:
        return stats
    session = session or ted.session_robuste()
    for a in attributions:
        if stats["tentees"] >= ENRICH_MAX:
            break
        nature = a.get("secteur", "").replace("Marche ONU - ", "")
        if ENRICH_NATURES and nature not in ENRICH_NATURES:
            continue
        ident = (a.get("publication_number") or "").replace("UNGMA-", "")
        if not ident.isdigit():
            continue
        stats["tentees"] += 1
        try:
            if fetch is not None:
                html = fetch(ident)
            else:
                r = session.get(url_popup(ident), headers=ENTETES, timeout=30)
                if r.status_code >= 400:
                    stats["echecs"] += 1
                    continue
                html = r.text
        except Exception:
            stats["echecs"] += 1
            continue
        pays, montant, paires = extraire_fiche(html)
        if len(stats["fiches"]) < 2:
            stats["fiches"].append((ident, paires))
        if pays:
            a["_pays_titulaire"] = pays
            # Meme logique commerciale que les attributions BM : une entreprise
            # etrangere expatrie du personnel, un local non.
            a["_etranger"] = bma.titulaire_etranger(pays, a.get("_pays_nom", ""))
            # Persistes (sans prefixe _) : alimentent les colonnes du meme nom.
            a["pays_titulaire"] = pays
            a["titulaire_etranger"] = "oui" if a["_etranger"] else "non"
            stats["pays"] += 1
        if montant:
            a["valeur_attribuee"] = montant
            stats["montants"] += 1
    return stats




# ===========================================================================
# ECRITURE ET MAIN
# ===========================================================================

def ouvrir_feuille(sheet_id, fichier):
    import gspread
    from google.oauth2.service_account import Credentials
    # Ouverture protegee par retry (503/429).
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier)
    try:
        return classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000,
                                   cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f


def ecrire(feuille, attributions):
    # Index construit en LECTURE POSITIONNELLE depuis le SCHEMA (regle 4) :
    # la position de `publication_number` vient de COLONNES, jamais de
    # l'en-tete de la feuille. Immunise contre un en-tete desaligne, un en-tete
    # duplique et la numerisation des identifiants. Voir ted.index_publications.
    index = ted.charger_index_publication(feuille, COLONNES)
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
    # Double ecriture (etape 2 du cap produit, 21/07/2026) : miroir Postgres
    # best-effort. On passe TOUTES les attributions, pas seulement les
    # nouvelles : le miroir a sa propre memoire (ON CONFLICT DO NOTHING) et se
    # remplit ainsi retroactivement. Ne peut JAMAIS faire echouer le run.
    try:
        import radar_stockage
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, attributions))
    except Exception as e:                     # module absent : run intact
        print("  (pg) miroir indisponible ({})".format(e))
    return len(nouvelles), deja


def main():
    if not ACTIVER:
        print("(info) Attributions UNGM desactivees (RADAR_UNGM_ATTRIB=0).")
        return

    print("Collecte des attributions UNGM (fenetre {} jours)...".format(JOURS_FENETRE))
    lignes, stats = collecte()
    print("  {} ligne(s) | {} pays | {} requetes | charge : {} (arret : {}).".format(
        stats["lignes"], stats["pays"], stats["requetes"],
        stats["charge"] or "aucune", stats["arret"]))

    if not lignes:
        print("  Aucune ligne. L'endpoint des attributions n'a pas livre de "
              "resultat exploitable ; relancer avec RADAR_UNGM_ATTRIB_DEBUG=1 "
              "pour voir les charges essayees.")
        if DEBUG:
            _dump_page(ted.session_robuste())
        return

    attributions, motifs = construire(lignes)
    print("  ecartes -> titulaire masque par l'agence : {titulaire_masque} | "
          "sans titulaire lisible : {sans_titulaire} | hors fenetre ou pays : "
          "{hors_fenetre_ou_pays} | doublons : {doublon}".format(**motifs))
    print("  {} attribution(s) exploitable(s).".format(len(attributions)))

    stats_enr = enrichir(attributions)
    if stats_enr["tentees"]:
        print("  enrichissement : {} fiche(s) lue(s) -> {} pays d'origine, "
              "{} montant(s), {} echec(s).".format(
                  stats_enr["tentees"], stats_enr["pays"],
                  stats_enr["montants"], stats_enr["echecs"]))

    if DEBUG:
        print("\n--- MODE VERIFICATION : AUCUNE ECRITURE ---")
        if stats_enr.get("fiches"):
            print("\n[D] Etiquettes trouvees dans les fiches detaillees "
                  "(pour reperer les bons noms de champs) :")
            for ident, paires in stats_enr["fiches"]:
                print("  --- fiche {} : {} etiquette(s) ---".format(ident, len(paires)))
                for cle, val in list(paires.items())[:30]:
                    print("      {:34} = {}".format(cle[:34], str(val)[:70]))
        print("\n[B] Attributions interpretees :")
        for a in attributions[:20]:
            origine = a.get("_pays_titulaire") or ""
            marque = ""
            if origine:
                marque = " <- {}{}".format(
                    origine[:16], "" if a.get("_etranger") else " (LOCAL)")
            print("  [{}] {} | {:11} | {:30}{} | {} | {}".format(
                a["date_publication"] or "n.c.", a["pays_execution"],
                a["secteur"].replace("Marche ONU - ", "")[:11],
                a["gagnant"][:30], marque,
                a["valeur_attribuee"] or "montant n.c.", a["titre"][:48]))
        print("\n[C] Structure BRUTE des 3 premieres lignes :")
        for ligne in lignes[:3]:
            print("  --- id {!r} : {} cellule(s) ---".format(
                ligne.get("id"), len(ligne["cellules"])))
            for i, c in enumerate(ligne["cellules"]):
                print("      [{}] {}".format(i, c[:110]))
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not (sheet_id and fichier):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    try:
        feuille = ouvrir_feuille(sheet_id, fichier)
        ajoutees, deja = ecrire(feuille, attributions)
        print("  {} nouvelle(s) ligne(s) dans '{}' ({} deja connue(s)).".format(
            ajoutees, NOM_ONGLET, deja))
    except Exception as e:
        print("(ungm-attrib) ecriture impossible ({}). Le run continue.".format(e))


def _dump_page(session):
    """Dernier recours : au lieu de deviner la requete, on la CHERCHE dans le
    JavaScript de la page publique.

    Constat du 20/07/2026 : les 4 charges essayees renvoient toutes exactement
    101 octets, donc une enveloppe vide constante. Or la documentation UNGM
    indique que les attributions sont publiques et gratuites. La requete
    existe donc, mais sa forme nous echappe. Ce diagnostic extrait du code de
    la page les URL d'appel et les noms de champs reellement utilises, ce qui
    remplace une inspection manuelle par F12 > Reseau."""
    try:
        rep = session.get(PAGE_AWARDS, headers=ENTETES, timeout=45)
        html = rep.text or ""
        print("\n[D] GET {} -> HTTP {} ({} octets)".format(
            PAGE_AWARDS, rep.status_code, len(rep.content)))

        print("\n[E] URL d'appel citees dans le code de la page :")
        urls = set()
        for motif in (r'url\s*:\s*[\'"]([^\'"]{4,120})[\'"]',
                      r'[\'"](/Public/[A-Za-z]*(?:Award|Search)[A-Za-z/]*)[\'"]',
                      r'action\s*=\s*[\'"]([^\'"]{4,120})[\'"]',
                      r'data-url\s*=\s*[\'"]([^\'"]{4,120})[\'"]'):
            for u in re.findall(motif, html, re.I):
                if re.search(r"(?i)award|search", u):
                    urls.add(u.strip())
        for u in sorted(urls)[:20]:
            print("    {}".format(u))
        if not urls:
            print("    (aucune)")

        print("\n[F] Noms de champs du formulaire (candidats pour la charge utile) :")
        champs = set()
        for motif in (r'name\s*=\s*[\'"]([A-Za-z][A-Za-z0-9_.]{2,40})[\'"]',
                      r'id\s*=\s*[\'"]([A-Za-z][A-Za-z0-9_.]{2,40})[\'"]'):
            champs.update(re.findall(motif, html))
        interessants = sorted(c for c in champs if re.search(
            r"(?i)award|supplier|vendor|date|country|agenc|page|sort|search|"
            r"descr|refer|unspsc", c))
        for c in interessants[:35]:
            print("    {}".format(c))
        if not interessants:
            print("    (aucun)")

        print("\n[G] Fragments de code entourant 'ContractAward' :")
        vus = 0
        for m in re.finditer(r"(?i)contractaward", html):
            deb, fin = max(0, m.start() - 110), min(len(html), m.end() + 110)
            extrait = re.sub(r"\s+", " ", html[deb:fin]).strip()
            if "<" in extrait and "url" not in extrait.lower():
                continue                  # simple lien de navigation
            print("    ...{}...".format(extrait[:210]))
            vus += 1
            if vus >= 6:
                break
        if not vus:
            print("    (aucun fragment de code, la page ne contient que des liens)")
    except Exception as e:
        print("    echec du dump : {}".format(e))


if __name__ == "__main__":
    main()
