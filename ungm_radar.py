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

# Types d'avis courants sur UNGM : ce sont des LIBELLES DE PROCEDURE, jamais
# le nom de l'agence emettrice. Sans cette liste, "Request for Proposal" etait
# pris pour l'acheteur (constate au premier essai du parseur).
RE_TYPE_AVIS = re.compile(
    r"(?i)^(request for (proposal|quotation|information|expression)"
    r"|invitation to bid|invitation for bids?|expression of interest"
    r"|call for (proposals?|expressions?)|pre[- ]?qualification"
    r"|notice of|general procurement notice|advance procurement notice"
    r"|tender|rfp|rfq|rfi|itb|eoi)\b")

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
        cellules = [_texte(c) for c in RE_CELLULE.findall(bloc)]
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
        if d and len(c) <= 40:            # une cellule courte contenant une date
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
    iso3 = champs["pays"]
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


def construire(lignes):
    """Lignes brutes -> avis normalises, avec le detail des rejets."""
    avis, motifs = [], {"sans_pays": 0, "sans_titre": 0, "hors_fenetre": 0}
    for ligne in lignes:
        a = normaliser(ligne)
        if a is not None:
            avis.append(a)
            continue
        champs = interpreter_ligne(ligne.get("cellules") or [])
        if not champs["pays"] or champs["pays"] not in ted.MULTIPLICATEUR_ZONE:
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

    print("Collecte UNGM (fenetre {} jours, {} pages max)...".format(
        JOURS_FENETRE, PAGES_MAX))
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
    print("  {} avis exploitable(s).".format(len(avis)))

    if DEBUG:
        print("\n--- MODE VERIFICATION (RADAR_UNGM_DEBUG=1) : AUCUNE ECRITURE ---")
        print("\n[1] Avis interpretes :")
        for a in avis[:20]:
            print("  {} | {} | {} | pub {} | deadline {}".format(
                a["pays_execution"], a["acheteur"][:28], a["titre"][:60],
                a["date_publication"] or "n.c.", a["deadline"] or "n.c."))
        print("\n[2] Structure BRUTE des 3 premieres lignes (pour corriger le")
        print("    parseur si l'interpretation ci-dessus est fausse) :")
        for ligne in lignes[:3]:
            print("  --- avis {} : {} cellule(s) ---".format(
                ligne["id"], len(ligne["cellules"])))
            for i, c in enumerate(ligne["cellules"]):
                print("      [{}] {}".format(i, c[:110]))
        print("\n--- Verifie que pays, titre et dates sont au bon endroit. ---")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not (sheet_id and fichier_cs):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    resultats = analyser(avis)
    print("  {} avis analyse(s) par le modele.".format(len(resultats)))
    try:
        feuille = ouvrir_feuille(sheet_id, fichier_cs)
        ajoutes, deja = ecrire(feuille, resultats)
        print("  {} nouvelle(s) ligne(s) dans '{}' ({} deja connue(s)).".format(
            ajoutes, NOM_ONGLET, deja))
    except Exception as e:
        print("(ungm) ecriture impossible ({}). Le run continue.".format(e))


if __name__ == "__main__":
    main()
