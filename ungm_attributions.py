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
import ungm_radar as ungm          # parsing de lignes, identifiants pays


ACTIVER = os.environ.get("RADAR_UNGM_ATTRIB", "1") != "0"
DEBUG = os.environ.get("RADAR_UNGM_ATTRIB_DEBUG", "0") == "1"

PAGE_AWARDS = "https://www.ungm.org/Public/ContractAward"
ENDPOINT_AWARDS = "https://www.ungm.org/Public/ContractAward/Search"
LIEN_AWARD = "https://www.ungm.org/Public/ContractAward/{}"

JOURS_FENETRE = int(os.environ.get("RADAR_UNGM_ATTRIB_JOURS", "180"))
PAYS_MAX = int(os.environ.get("RADAR_UNGM_ATTRIB_PAYS_MAX", "45"))
PAGES_PAR_PAYS = int(os.environ.get("RADAR_UNGM_ATTRIB_PAGES", "2"))
MINUTES_MAX = float(os.environ.get("RADAR_UNGM_ATTRIB_MINUTES", "10"))

# Onglet PARTAGE avec les attributions TED et BM : integration dashboard
# gratuite (lentille Titulaires + fiche entreprise 360).
NOM_ONGLET = "attributions_radar"
COLONNES = [
    "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
    "acheteur", "titre", "cpv", "sous_traitance",
    "date_publication", "publication_number", "lien", "a_demarcher",
]
COL_STATUT = "statut_prospection"
COL_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COL_STATUT, COL_DETECTION]

ENTETES = dict(ungm.ENTETES, **{"Referer": PAGE_AWARDS})

# Identifiant de ligne : les attributions n'utilisent pas data-noticeid.
RE_ID_AWARD = re.compile(
    r'data-(?:contractawardid|awardid|noticeid|id)="(\d+)"', re.I)


# ===========================================================================
# CHARGES UTILES CANDIDATES
# ===========================================================================

def charges_candidates(page=0, taille=25, id_pays=None):
    """Formes de requete a essayer, de la plus probable a la moins probable.

    La sonde a montre que la forme "awards1" est ACCEPTEE (HTTP 200) mais
    renvoie une reponse vide : il manque vraisemblablement un filtre. On
    decline donc la meme forme avec un filtre pays, puis avec une fenetre de
    dates, avant de retomber sur les variantes nues."""
    base = {
        "PageIndex": page, "PageSize": taille,
        "Description": "", "Supplier": "", "Reference": "",
        "AwardDateFrom": "", "AwardDateTo": "",
        "Agencies": [], "Countries": [], "UNSPSCs": [],
        "SortField": "AwardDate", "SortAscending": False,
    }
    formes = []
    if id_pays:
        avec_pays = dict(base); avec_pays["Countries"] = [id_pays]
        formes.append(("awards+pays", avec_pays))
    debut = (date.today() - timedelta(days=JOURS_FENETRE)).strftime("%d-%b-%Y")
    fin = date.today().strftime("%d-%b-%Y")
    avec_dates = dict(base, AwardDateFrom=debut, AwardDateTo=fin)
    if id_pays:
        avec_dates = dict(avec_dates); avec_dates["Countries"] = [id_pays]
    formes.append(("awards+dates", avec_dates))
    formes.append(("awards nu", base))
    # Variante 1-based : certains portails ASP.NET comptent les pages a partir
    # de 1, ce qui renvoie un resultat vide en page 0.
    une_base = dict(base); une_base["PageIndex"] = page + 1
    if id_pays:
        une_base["Countries"] = [id_pays]
    formes.append(("awards page 1-based", une_base))
    return formes


def interroger(session, charge, timeout=45):
    """POST best-effort. Renvoie (status, texte). N'exception jamais."""
    try:
        rep = session.post(
            ENDPOINT_AWARDS,
            data=json.dumps(charge).encode("utf-8"),
            headers=dict(ENTETES, **{"Content-Type": "application/json"}),
            timeout=timeout)
        return rep.status_code, (rep.text or "")
    except Exception as e:
        return 0, "exception: {}".format(e)


def extraire_attributions(html_brut):
    """Lignes d'attribution -> [{'id':..., 'cellules':[...]}].

    Meme rendu que les avis (<div role="row">), mais l'identifiant porte un
    autre nom. On accepte donc plusieurs attributs, et a defaut une ligne sans
    identifiant reste exploitable (on fabriquera une cle depuis son contenu)."""
    src = str(html_brut or "")
    bornes = [m.start() for m in ungm.RE_LIGNE.finditer(src)]
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
    dates, agence, reference, restes = [], "", "", []
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
        restes.append(c)
    dates = sorted(set(dates))
    return {"dates": dates, "agence": agence or "Nations Unies",
            "reference": reference, "restes": restes,
            "date_attribution": dates[-1] if dates else ""}


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
    return True


# Marqueurs de raison sociale : formes juridiques et suffixes d'entreprise.
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
    pub = "UNGMA-{}".format(ident) if ident else "UNGMA-{}-{}-{}".format(
        iso3, (d_attrib or "nd"), abs(hash(titulaire)) % 999983)
    return {
        "date_maj": date.today().isoformat(),
        "gagnant": titulaire,
        "secteur": "Marche ONU",
        # ISO3 obligatoire : le dashboard resout les attributions en mode ISO.
        "pays_execution": iso3,
        "valeur_attribuee": montant,
        "acheteur": champs["agence"],
        "titre": titre[:300],
        "cpv": champs["reference"][:40],
        "sous_traitance": "",
        "date_publication": d_attrib,
        "publication_number": pub,
        "lien": LIEN_AWARD.format(ident) if ident else PAGE_AWARDS,
        "a_demarcher": "oui",
    }


# ===========================================================================
# COLLECTE
# ===========================================================================

def trouver_charge_qui_marche(session, id_pays=None):
    """Essaie les charges candidates et renvoie (nom, charge, lignes) pour la
    premiere qui ramene des lignes. (None, None, []) si aucune."""
    for nom, charge in charges_candidates(0, 25, id_pays):
        statut, texte = interroger(session, charge)
        lignes = extraire_attributions(texte) if statut and statut < 400 else []
        if DEBUG:
            print("    {:22} -> HTTP {} ({} octets) : {} ligne(s)".format(
                nom, statut, len(texte), len(lignes)))
        if lignes:
            return nom, charge, lignes
    return None, None, []


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
    nom_charge, modele_charge, _ = trouver_charge_qui_marche(session, cibles[0][1])
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
            charge["PageIndex"] = page + (1 if "1-based" in (nom_charge or "") else 0)
            charge["Countries"] = [ident]
            statut, texte = interroger(session, charge)
            stats["requetes"] += 1
            if not statut or statut >= 400:
                break
            lot = extraire_attributions(texte)
            neuves = []
            for l in lot:
                cle = l["id"] or "|".join(l["cellules"])[:120]
                if cle in vus:
                    continue
                vus.add(cle)
                l["pays_iso3"] = iso
                neuves.append(l)
            lignes.extend(neuves)
            if not neuves or len(lot) < charge.get("PageSize", 25):
                break
    stats["lignes"] = len(lignes)
    return lignes, stats


def construire(lignes):
    sorties, motifs = [], {"sans_titulaire": 0, "hors_fenetre_ou_pays": 0}
    vus = set()
    for ligne in lignes:
        a = normaliser(ligne, ligne.get("pays_iso3", ""))
        if a is None:
            champs = interpreter(ligne.get("cellules") or [])
            if not montant_et_titulaire(champs)[0]:
                motifs["sans_titulaire"] += 1
            else:
                motifs["hors_fenetre_ou_pays"] += 1
            continue
        empreinte = (a["gagnant"].lower(), a["pays_execution"], a["date_publication"])
        if empreinte in vus:
            continue
        vus.add(empreinte)
        sorties.append(a)
    return sorties, motifs


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
    print("  ecartes -> sans titulaire lisible : {sans_titulaire} | "
          "hors fenetre ou pays : {hors_fenetre_ou_pays}".format(**motifs))
    print("  {} attribution(s) exploitable(s).".format(len(attributions)))

    if DEBUG:
        print("\n--- MODE VERIFICATION : AUCUNE ECRITURE ---")
        print("\n[B] Attributions interpretees :")
        for a in attributions[:20]:
            print("  [{}] {} | {} | {} | {}".format(
                a["date_publication"] or "n.c.", a["gagnant"][:34],
                a["pays_execution"], a["acheteur"][:18],
                a["valeur_attribuee"] or "montant n.c."))
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
    """Dernier recours en mode verification : montrer ce que rend la page
    publique des attributions, pour arbitrer si la source est exploitable."""
    try:
        rep = session.get(PAGE_AWARDS, headers=ENTETES, timeout=45)
        html = rep.text or ""
        print("\n[D] GET {} -> HTTP {} ({} octets)".format(
            PAGE_AWARDS, rep.status_code, len(rep.content)))
        print("    marqueurs de ligne : {}".format(
            html.count('role="row"') + html.count("dataRow")))
        for attr in ("contractawardid", "awardid", "noticeid"):
            print("    attribut {!r} present : {}".format(
                attr, attr in html.lower()))
    except Exception as e:
        print("    echec du dump : {}".format(e))


if __name__ == "__main__":
    main()
