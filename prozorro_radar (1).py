# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur Prozorro (Ukraine). Avis + Attributions.
=====================================================================

POURQUOI CETTE SOURCE
---------------------
Ukraine est une zone ROUGE du perimetre (reconstruction, deploiement d'equipes
etrangeres) et n'etait couverte par AUCUN collecteur : TED ne remonte pas les
marches NATIONAUX ukrainiens. Prozorro est le systeme d'achat public d'Ukraine.

CE QUE LA SONDE A ETABLI (sonde_prozorro.py, sortie reelle)
-----------------------------------------------------------
  - Feed public ouvert, SANS auth : GET /api/2.5/tenders (statut 200).
  - Le feed est un flux de REPLICATION trie par dateModified, ASCENDANT depuis
    2015. Pour avoir le recent, on lit en `descending=1`.
  - opt_fields renvoie status + procurementMethodType SANS fetch de fiche : on
    pre-filtre donc a bas cout (on jette le belowThreshold, le bruit local).
  - PAS de filtre CPV cote serveur : le CPV n'est QUE dans la fiche. On ne
    fetche donc la fiche que pour les survivants du pre-filtre + inconnus.
  - Le CPV est au format europeen : scheme="CPV", id="14410000-8". La doctrine
    CPV du coeur TED (avis_correspond) est donc REUTILISABLE, en retirant "-8".

DEUX PASSES (RADAR_PROZORRO_JEU) :
  - "avis"          : pipeline LLM complet, onglet dedie `prozorro_radar`.
  - "attributions"  : titulaires (awards[]), onglet PARTAGE `attributions_radar`,
                      sans LLM (socle deterministe, comme IDB/IsDB/BM/UNGM).
  - "tout"          : les deux passes.

POSTURE : NOUVELLE SOURCE => DEMARRE EN VERIFICATION (motif IsDB/IDB).
  RADAR_PROZORRO_DEBUG=1 => AUCUNE ECRITURE. Le run mesure l'entonnoir reel et,
  pour les attributions, DUMPE la structure brute de awards[] : c'est la sonde
  integree qui valide le parsing des titulaires AVANT toute ecriture. On ne code
  pas awards[] a l'aveugle (regle 4 : le bug des telephones sous
  publication_number ; lecon IsDB du filtre serveur ignore).

Isole : un echec ici n'affecte ni les autres collecteurs ni le dashboard.
Sortie en code 0 hors erreur de programmation.

LANCEMENT :
    RADAR_PROZORRO_DEBUG=1 RADAR_PROZORRO_JEU=tout python prozorro_radar.py
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import ted_complet_v14 as ted
import radar_resilience
import bm_attributions

# ===========================================================================
# CONFIGURATION
# ===========================================================================
BASE = os.environ.get("PROZORRO_BASE", "https://public-api.prozorro.gov.ua/api/2.5")
FEED = BASE + "/tenders"

NOM_ONGLET = "prozorro_radar"
NOM_ONGLET_ATTRIB = "attributions_radar"     # onglet PARTAGE, aucun cablage dashboard

DEBUG = os.environ.get("RADAR_PROZORRO_DEBUG", "") == "1"
ACTIF = os.environ.get("RADAR_PROZORRO", "1") != "0"
JEU = os.environ.get("RADAR_PROZORRO_JEU", "avis").strip().lower()   # avis | attributions | tout

BUDGET_LLM = int(os.environ.get("PROZORRO_BUDGET", "60"))            # plafond d'appels LLM (avis)
NB_JOURS = int(os.environ.get("PROZORRO_JOURS", str(ted.NB_JOURS_FENETRE)))
JOURS_ATTRIB = int(os.environ.get("PROZORRO_ATTRIB_JOURS", "365"))
PAGES_MAX = int(os.environ.get("PROZORRO_PAGES_MAX", "60"))          # garde-fou anti-firehose
MONTANT_MIN_UAH = float(os.environ.get("PROZORRO_MONTANT_MIN_UAH", "0"))  # 0 = pas de seuil

PAYS_EXEC = "UKR"                            # marche national ukrainien, par construction
TIMEOUT = 45
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ENTETES = {"User-Agent": UA, "Accept": "application/json, */*"}

# Pre-filtre AU NIVEAU DU FEED (opt_fields, sans fetch de fiche).
# belowThreshold = marches sous seuil : la masse du bruit local (le sel de Kyiv
# de la sonde). On les jette d'emblee. Pilotable si on veut les reintegrer.
METHODES_BRUIT = set(filter(None, (
    x.strip() for x in os.environ.get(
        "PROZORRO_METHODES_BRUIT", "belowThreshold").split(","))))

# Statuts pertinents cote AVIS : marche encore ouvert (une opportunite se
# travaille avant la cloture). Cote ATTRIBUTIONS : marche pourvu.
STATUTS_AVIS = {
    "active.enquiries", "active.tendering", "active.pre-qualification",
    "active.pre-qualification.stand-still", "active.auction",
    "active.qualification", "active.qualification.stand-still",
}
STATUTS_ATTRIB = {"active.awarded", "complete"}

OPT_FIELDS = "status,procurementMethodType,tenderID,dateModified"


def _plat(t, n=None):
    s = re.sub(r"\s+", " ", str(t if t is not None else "")).strip()
    return s[:n] if n else s


def _texte(fiche, cle):
    """Prefere l'ukrainien (title/description), repli sur *_en."""
    return _plat(fiche.get(cle) or fiche.get(cle + "_en"))


# ===========================================================================
# ACCES AU FEED ET AUX FICHES (fetch injectable pour les tests)
# ===========================================================================
def lire_feed(offset=None, fetch=None):
    """Une page du feed, en DESCENDANT (recent d'abord), enrichie par opt_fields.
    Renvoie (elements, offset_suivant). `fetch` injecte permet de tester sans
    reseau (meme motif que lire_datastore/collecter_flux ailleurs)."""
    params = {"limit": 100, "descending": 1, "opt_fields": OPT_FIELDS}
    if offset is not None:
        params["offset"] = offset
    if fetch is not None:
        charge = fetch(params)
    else:
        r = ted.session_robuste().get(FEED, params=params, headers=ENTETES, timeout=TIMEOUT)
        r.raise_for_status()
        charge = r.json()
    charge = charge or {}
    suivant = (charge.get("next_page") or {}).get("offset")
    return (charge.get("data") or []), suivant


def lire_fiche(tid, fetch=None):
    """Fiche complete d'un marche. `fetch` injecte pour les tests."""
    if fetch is not None:
        charge = fetch(tid)
    else:
        r = ted.session_robuste().get(FEED + "/" + tid, headers=ENTETES, timeout=TIMEOUT)
        r.raise_for_status()
        charge = r.json()
    return (charge or {}).get("data") or {}


def _date(iso):
    """ISO Prozorro -> datetime aware (ou None)."""
    if not iso:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", str(iso))
    if not m:
        return None
    try:
        d = datetime.fromisoformat(m.group(1))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def collecter_candidats(fetch_feed=None, connus=None, borne_jours=None, statuts=None):
    """Parcourt le feed DESCENDANT, pre-filtre (methode/statut), deduplique
    contre la memoire, s'arrete a la borne de fraicheur ou au plafond de pages.

    Ne fetche AUCUNE fiche ici : c'est l'etape a bas cout. Renvoie
    (candidats: [{id, tenderID, status, methode, dateModified}], stats)."""
    connus = connus or set()
    statuts = statuts or STATUTS_AVIS
    borne = None
    if borne_jours is not None:
        borne = datetime.now(timezone.utc) - timedelta(days=borne_jours)
    stats = {"pages": 0, "lus": 0, "bruit_methode": 0, "hors_statut": 0,
             "deja_connus": 0, "hors_fenetre": 0, "candidats": 0}
    candidats, offset, vus = [], None, set()
    for _ in range(PAGES_MAX):
        try:
            elements, offset = lire_feed(offset=offset, fetch=fetch_feed)
        except Exception as e:
            print("  (info) lecture feed interrompue : {}".format(_plat(e, 80)))
            break
        if not elements:
            break
        stats["pages"] += 1
        stats["lus"] += len(elements)
        page_hors_fenetre = 0
        for el in elements:
            dm = _date(el.get("dateModified"))
            if borne and dm and dm < borne:
                stats["hors_fenetre"] += 1
                page_hors_fenetre += 1
                continue
            methode = el.get("procurementMethodType") or ""
            if methode in METHODES_BRUIT:
                stats["bruit_methode"] += 1
                continue
            if statuts and el.get("status") not in statuts:
                stats["hors_statut"] += 1
                continue
            tid = el.get("tenderID") or ""
            ident = el.get("id") or ""
            if tid and tid in connus:
                stats["deja_connus"] += 1
                continue
            if ident in vus:
                continue
            vus.add(ident)
            candidats.append({"id": ident, "tenderID": tid,
                              "status": el.get("status"), "methode": methode,
                              "dateModified": el.get("dateModified")})
        # Feed descendant : une page entierement hors fenetre => les suivantes
        # aussi. On arrete de payer des pages inutiles.
        if borne and page_hors_fenetre >= len(elements) * 0.9:
            break
        if offset is None:
            break
    stats["candidats"] = len(candidats)
    return candidats, stats


# ===========================================================================
# CPV : reutilise la doctrine du coeur TED
# ===========================================================================
def _codes_cpv(fiche):
    """Codes CPV d'une fiche (items[].classification.id), suffixe '-8' retire.
    Prozorro : scheme='CPV', id='14410000-8' (format europeen confirme)."""
    codes = []
    for it in fiche.get("items") or []:
        cl = it.get("classification") or {}
        scheme = str(cl.get("scheme") or "").upper()
        code = str(cl.get("id") or "").strip()
        if not code:
            continue
        # On accepte CPV et sa localisation ukrainienne (memes codes numeriques).
        if scheme and scheme not in ("CPV", "ДК021", "DK021", "ДК021:2015"):
            continue
        codes.append(code.split("-")[0].strip())
    return [c for c in codes if c]


def cpv_pertinent(fiche):
    """Reutilise ted.avis_correspond avec un avis SYNTHETIQUE au format TED.
    pays_execution = UKR (dans CODES_PAYS_SUIVIS), donc seul le CPV decide."""
    codes = _codes_cpv(fiche)
    avis_ted = {
        "classification-cpv": codes,
        "place-of-performance": [PAYS_EXEC],
        "notice-title": _texte(fiche, "title"),
    }
    return ted.avis_correspond(avis_ted), codes


# ===========================================================================
# PASSE AVIS : mapping canonique + analyse LLM (coeur TED)
# ===========================================================================
def fiche_vers_avis(fiche, codes=None):
    """Fiche Prozorro -> dict d'avis au format attendu par ted.appeler_llm /
    calculer_scores. Le pays d'execution est UKR par construction."""
    codes = codes if codes is not None else _codes_cpv(fiche)
    tid = fiche.get("tenderID") or ""
    pe = fiche.get("procuringEntity") or {}
    val = fiche.get("value") or {}
    montant = val.get("amount")
    devise = val.get("currency") or ""
    valeur = "{} {}".format(_plat(montant), devise).strip() if montant else ""
    tp = fiche.get("tenderPeriod") or {}
    return {
        "publication_number": tid,
        "titre": _texte(fiche, "title")[:300],
        "acheteur": _plat(pe.get("name") or pe.get("name_en")),
        "pays_acheteur": PAYS_EXEC,
        "pays_execution": PAYS_EXEC,
        "acheteur_etranger": "non",                 # acheteur public ukrainien
        "cpv": ", ".join(codes),
        "description": _texte(fiche, "description")[:ted.MAX_CARACTERES_DESCRIPTION],
        "valeur_estimee": valeur,
        "deadline": tp.get("endDate") or "",
        "date_publication": (fiche.get("dateCreated") or "")[:10],
        "methode_passation": fiche.get("procurementMethodType") or "",
        "type_notice": fiche.get("status") or "",
        "lien_avis": "https://prozorro.gov.ua/tender/{}".format(tid) if tid else "",
    }


def merite_escalade(r):
    """Meme doctrine que TED/BM/IDB : relecture Sonnet sur les cas a enjeu."""
    e = r["extraction"]
    if e is None:
        return True
    try:
        confiance = float(e.get("confiance") or 0)
    except (TypeError, ValueError):
        confiance = 0.0
    return (r["score"] >= 5.0 or r["surete"] >= 6.0 or confiance < 0.55
            or ted.escalade_pour_securite(e))


def analyser(avis_list, budget=None):
    """Analyse LLM + scoring, dans la limite d'un budget d'appels. Disjoncteur
    LLM partage (coupe proprement si le solde Anthropic est epuise)."""
    budget = BUDGET_LLM if budget is None else budget
    resultats = []
    for avis in avis_list:
        arret = ted.sortie_selon_sante_llm("prozorro")
        if arret:
            print("  " + arret)
            break
        if budget <= 0:
            print("  (budget d'analyses epuise, arret propre)")
            break
        budget -= 1
        extraction = ted.appeler_llm(avis)
        surete, commercial, score = ted.calculer_scores(avis, extraction)
        r = {"avis": avis, "extraction": extraction, "score": score,
             "surete": surete, "commercial": commercial,
             "raffine": False, "divergence": ""}
        if merite_escalade(r) and extraction is not None:
            score_avant = score
            extraction2 = ted.appeler_llm(avis, modele=ted.MODELE_RAFFINEMENT)
            if extraction2 is not None:
                surete, commercial, score = ted.calculer_scores(avis, extraction2)
                r.update({"extraction": extraction2, "score": score,
                          "surete": surete, "commercial": commercial,
                          "raffine": True,
                          "divergence": "{}->{}".format(score_avant, score)
                          if score_avant != score else ""})
        resultats.append(r)
    resultats.sort(key=lambda x: -x["score"])
    return resultats


# ===========================================================================
# PASSE AVIS : ecriture (onglet dedie + miroir Postgres)
# ===========================================================================
COLONNES = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "pays_acheteur",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "justification", "confiance", "modele",
    "raffine", "divergence", "type_notice", "methode_passation",
    "valeur_estimee", "publication_number", "lien_avis",
    "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ligne_depuis_resultat(r):
    avis, e = r["avis"], (r["extraction"] or {})
    modele = ted.MODELE_RAFFINEMENT if r["raffine"] else ted.MODELE
    v = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"], "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.calculer_action_recommandee(
            r["score"], r["extraction"], surete=r["surete"]),
        "fenetre_action": ted.calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": e.get("niveau_opportunite_amarante", ""),
        "titre": avis.get("titre", ""), "acheteur": avis.get("acheteur", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "pays_acheteur": avis.get("pays_acheteur", ""),
        "type_client": e.get("type_client", ""),
        "type_mobilite": e.get("type_mobilite", ""),
        "profil_personnes_exposees": e.get("profil_personnes_exposees", ""),
        "duree_estimee": e.get("duree_estimee", ""),
        "accessibilite_commerciale": e.get("accessibilite_commerciale", ""),
        "securite_existante_detectee": e.get("securite_existante_detectee", ""),
        "profils_acteurs_probables": ", ".join(e.get("profils_acteurs_probables") or []),
        "justification": e.get("justification", ""),
        "confiance": e.get("confiance", ""),
        "modele": modele, "raffine": r["raffine"], "divergence": r["divergence"],
        "type_notice": avis.get("type_notice", ""),
        "methode_passation": avis.get("methode_passation", ""),
        "valeur_estimee": avis.get("valeur_estimee", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(v.get(c, "")) for c in COLONNES]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_cs)
    try:
        return classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000,
                                   cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f


def ecrire_resultats(feuille, resultats):
    """Insere les nouveaux avis, met a jour les scores des presents SANS toucher
    a statut_suivi / date_detection. Miroir Postgres best-effort."""
    index = ted.charger_index_publication(feuille, COLONNES)
    derniere = ted.lettre_colonne(len(COLONNES))
    maj, nouvelles, nb_maj, nb_new = [], [], 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne = ligne_depuis_resultat(r)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere),
                        "values": [ligne]})
            nb_maj += 1
        else:
            nouvelles.append(ligne + ["nouveau", date.today().isoformat()])
            nb_new += 1
    if maj:
        radar_resilience.avec_retry(lambda: feuille.batch_update(maj), "ecriture batch_update")
    if nouvelles:
        radar_resilience.avec_retry(
            lambda: feuille.append_rows(nouvelles, value_input_option="RAW"),
            "ecriture append_rows")
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, ligne_depuis_resultat(r))) for r in resultats]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_new, nb_maj


# ===========================================================================
# PASSE ATTRIBUTIONS : extraction des titulaires (awards[]), onglet partage
# ===========================================================================
# NB : la structure awards[] n'est PAS encore confirmee sur donnees reelles.
# En DEBUG, main() DUMPE le awards[] brut : c'est la sonde integree. Le parsing
# ci-dessous suit le schema documente OpenProcurement (awards[].suppliers[],
# identifier UA-EDR = societe ukrainienne = titulaire LOCAL).
INDISPONIBLES = {"", "n/a", "none", "-", "не визначено"}


def _titulaire_etranger(supplier):
    """UA-EDR (registre ukrainien) => LOCAL. Un autre scheme / pays => ETRANGER.
    Le titulaire etranger qui gagne un marche ukrainien est la cible Amarante."""
    ident = supplier.get("identifier") or {}
    scheme = str(ident.get("scheme") or "").upper()
    adr = supplier.get("address") or {}
    pays = str(adr.get("countryName") or adr.get("countryName_en") or "").strip().lower()
    if scheme in ("UA-EDR", "UA-IPN") or pays in ("ukraine", "україна", "украина"):
        return False, "UKR"
    if not scheme and not pays:
        return False, "UKR"          # defaut prudent : Prozorro est ukrainien
    return True, (adr.get("countryName") or scheme or "?")


def fiche_vers_attributions(fiche, aujourd_hui=None, codes=None):
    """Titulaires exploitables d'une fiche (awards[] actifs). Renvoie une liste
    de dicts au format bm_attributions.COLONNES (onglet partage)."""
    aujourd_hui = aujourd_hui or datetime.now(timezone.utc)
    codes = codes if codes is not None else _codes_cpv(fiche)
    tid = fiche.get("tenderID") or ""
    pe = fiche.get("procuringEntity") or {}
    acheteur = _plat(pe.get("name") or pe.get("name_en"))
    titre = _texte(fiche, "title")[:300]
    lien = "https://prozorro.gov.ua/tender/{}".format(tid) if tid else ""
    out = []
    for aw in fiche.get("awards") or []:
        if aw.get("status") not in ("active", "complete"):
            continue
        d = _date(aw.get("date"))
        if d and (aujourd_hui - d).days > JOURS_ATTRIB:
            continue
        val = aw.get("value") or {}
        montant = val.get("amount")
        devise = val.get("currency") or ""
        if montant and MONTANT_MIN_UAH and devise == "UAH" and float(montant) < MONTANT_MIN_UAH:
            continue
        for sup in aw.get("suppliers") or []:
            nom = _plat(sup.get("name") or sup.get("name_en"))
            if not nom or nom.lower() in INDISPONIBLES:
                continue
            etranger, pays_tit = _titulaire_etranger(sup)
            out.append({
                "date_maj": date.today().isoformat(),
                "gagnant": nom,
                "secteur": (codes[0][:2] if codes else ""),
                "pays_execution": PAYS_EXEC,
                "valeur_attribuee": "{} {}".format(_plat(montant), devise).strip() if montant else "",
                "acheteur": acheteur,
                "titre": titre,
                "cpv": ", ".join(codes),
                "sous_traitance": "",
                "date_publication": (aw.get("date") or "")[:10],
                "publication_number": "{}-{}".format(tid, (aw.get("id") or "")[:8]),
                "lien": lien,
                "a_demarcher": "oui" if etranger else "",
                "pays_titulaire": pays_tit,
                "titulaire_etranger": "oui" if etranger else "non",
            })
    return out


def ouvrir_feuille_attributions(sheet_id, fichier_cs):
    import gspread
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_cs)
    try:
        return classeur.worksheet(NOM_ONGLET_ATTRIB)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET_ATTRIB, rows=5000,
                                   cols=len(bm_attributions.TOUTES_COLONNES))
        f.append_row(bm_attributions.TOUTES_COLONNES)
        return f


def ecrire_attributions(feuille, attributions):
    """Ajoute dans l'onglet PARTAGE `attributions_radar` (aucun cablage
    dashboard). Dedup sur publication_number. Miroir Postgres best-effort."""
    cols = bm_attributions.COLONNES
    index = ted.charger_index_publication(feuille, cols)
    nouvelles, nb = [], 0
    for a in attributions:
        pub = a.get("publication_number", "")
        if pub and pub in index:
            continue
        nouvelles.append([str(a.get(c, "")) for c in cols]
                         + ["nouveau", date.today().isoformat()])
        nb += 1
    if nouvelles:
        radar_resilience.avec_retry(
            lambda: feuille.append_rows(nouvelles, value_input_option="RAW"),
            "ecriture append_rows")
    try:
        import radar_stockage
        plates = [dict(zip(cols, [str(a.get(c, "")) for c in cols]))
                  for a in attributions]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET_ATTRIB, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))
    return nb, len(attributions) - nb


# ===========================================================================
# COLLECTE (feed -> fiches -> avis / attributions)
# ===========================================================================
def collecter(jeu, connus=None, fetch_feed=None, fetch_fiche=None, aujourd_hui=None):
    """Retourne (avis_list, attributions_list, echantillon_fiches, stats).
    `echantillon_fiches` sert au dump DEBUG (structure awards[])."""
    veut_avis = jeu in ("avis", "tout")
    veut_attrib = jeu in ("attributions", "tout")
    statuts = (STATUTS_AVIS | STATUTS_ATTRIB) if jeu == "tout" else \
        (STATUTS_AVIS if veut_avis else STATUTS_ATTRIB)
    borne = max(NB_JOURS, JOURS_ATTRIB) if jeu == "tout" else \
        (NB_JOURS if veut_avis else JOURS_ATTRIB)

    candidats, stats = collecter_candidats(
        fetch_feed=fetch_feed, connus=connus, borne_jours=borne, statuts=statuts)
    stats.update({"fiches_lues": 0, "hors_cpv": 0,
                  "avis": 0, "fiches_avec_award": 0, "attributions": 0})

    avis_list, attributions, echantillon = [], [], []
    for c in candidats:
        try:
            fiche = lire_fiche(c["id"], fetch=fetch_fiche)
        except Exception as e:
            print("  (info) fiche {} illisible : {}".format(
                c.get("tenderID"), _plat(e, 60)))
            continue
        stats["fiches_lues"] += 1
        pertinent, codes = cpv_pertinent(fiche)
        if not pertinent:
            stats["hors_cpv"] += 1
            continue
        if len(echantillon) < 5:
            echantillon.append(fiche)
        statut = fiche.get("status")
        if veut_avis and statut in STATUTS_AVIS:
            avis_list.append(fiche_vers_avis(fiche, codes=codes))
            stats["avis"] += 1
        if veut_attrib:
            atts = fiche_vers_attributions(fiche, aujourd_hui=aujourd_hui, codes=codes)
            if fiche.get("awards"):
                stats["fiches_avec_award"] += 1
            attributions.extend(atts)
    stats["attributions"] = len(attributions)
    return avis_list, attributions, echantillon, stats


# ===========================================================================
# POINT D'ENTREE
# ===========================================================================
def _dump_debug(avis_list, attributions, echantillon, stats):
    print("\n--- MODE VERIFICATION (RADAR_PROZORRO_DEBUG=1) : AUCUNE ECRITURE ---")
    print("\n[ENTONNOIR]")
    for cle in ("pages", "lus", "bruit_methode", "hors_statut", "deja_connus",
                "hors_fenetre", "candidats", "fiches_lues", "hors_cpv",
                "avis", "fiches_avec_award", "attributions"):
        print("    {:16} = {}".format(cle, stats.get(cle, 0)))

    print("\n[AVIS] {} avis dans le perimetre CPV (12 plus riches) :".format(len(avis_list)))
    for a in avis_list[:12]:
        print("    {:8} | {:26} | {}".format(
            _plat(a.get("cpv"), 8), _plat(a.get("acheteur"), 26),
            _plat(a.get("titre"), 60)))

    # SONDE INTEGREE : structure brute de awards[]. C'est ce qui valide le
    # parsing des attributions AVANT toute ecriture (on ne code pas a l'aveugle).
    print("\n[ATTRIBUTIONS -- STRUCTURE BRUTE awards[] a valider]")
    montre = 0
    for fiche in echantillon:
        aws = fiche.get("awards") or []
        if not aws:
            continue
        montre += 1
        print("\n  tender {} | status={} | {} award(s) :".format(
            fiche.get("tenderID"), fiche.get("status"), len(aws)))
        print("  " + _plat(json.dumps(aws[0], ensure_ascii=False), 1000))
        if montre >= 3:
            break
    if montre == 0:
        print("    (aucune fiche de l'echantillon ne porte de awards[] :")
        print("     elargir la fenetre/les statuts, ou verifier /contracts)")

    print("\n[ATTRIBUTIONS -- ce que le parsing actuel extrait] {} titulaire(s) :".format(
        len(attributions)))
    for a in attributions[:12]:
        print("    {:32} <- {:10} | {} | {}".format(
            _plat(a.get("gagnant"), 32), _plat(a.get("pays_titulaire"), 10),
            _plat(a.get("valeur_attribuee"), 18), _plat(a.get("titre"), 30)))
    print("\n--- FIN VERIFICATION (aucune ecriture) ---")


def main():
    if not ACTIF:
        print("Prozorro desactive (RADAR_PROZORRO=0).")
        return
    if JEU not in ("avis", "attributions", "tout"):
        print("RADAR_PROZORRO_JEU invalide : {!r} (avis|attributions|tout).".format(JEU))
        return
    print("Collecteur Prozorro (Ukraine) -- jeu={} debug={}".format(JEU, DEBUG))

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    # Memoire inter-runs : ne pas refetcher/reanalyser les avis deja connus.
    connus = set()
    if not DEBUG and sheet_id and fichier and JEU in ("avis", "tout"):
        try:
            connus = ted.numeros_publication_existants(
                sheet_id, fichier, NOM_ONGLET, COLONNES)
        except Exception as e:
            print("  (info) memoire indisponible ({}). On analysera large.".format(_plat(e, 60)))

    try:
        avis_list, attributions, echantillon, stats = collecter(JEU, connus=connus)
    except Exception as e:
        print("ERREUR : collecte Prozorro impossible ({}).".format(_plat(e, 200)))
        print("(info) Les autres collecteurs et le dashboard ne sont pas affectes.")
        return

    print("{} page(s) | {} lus | candidats {} | fiches {} | hors CPV {}".format(
        stats["pages"], stats["lus"], stats["candidats"],
        stats["fiches_lues"], stats["hors_cpv"]))

    if DEBUG:
        # En DEBUG on n'appelle JAMAIS le LLM (cout). avis_list contient deja
        # les avis bruts (pre-analyse) ; on les dumpe tels quels, plus la
        # structure brute de awards[] qui valide le parsing des attributions.
        _dump_debug(avis_list, attributions, echantillon, stats)
        return

    # ---- MODE REEL ----
    if JEU in ("avis", "tout"):
        if avis_list:
            resultats = analyser(avis_list)
            if sheet_id and fichier:
                try:
                    feuille = ouvrir_feuille(sheet_id, fichier)
                    nb_new, nb_maj = ecrire_resultats(feuille, resultats)
                    print("-> AVIS : {} nouveau(x), {} mis a jour dans '{}'.".format(
                        nb_new, nb_maj, NOM_ONGLET))
                except Exception as e:
                    print("(prozorro) ecriture avis impossible ({}). Run continue.".format(_plat(e, 120)))
            else:
                print("(info) Pas de Sheet : {} avis analyses, affichage seul.".format(len(resultats)))
                for r in resultats[:15]:
                    print("  {:4} | {:26} | {}".format(
                        r["score"], _plat(r["avis"].get("acheteur"), 26),
                        _plat(r["avis"].get("titre"), 50)))
        else:
            print("Aucun avis Prozorro dans le perimetre ce run.")

    if JEU in ("attributions", "tout"):
        if attributions:
            if sheet_id and fichier:
                try:
                    feuille = ouvrir_feuille_attributions(sheet_id, fichier)
                    nb, deja = ecrire_attributions(feuille, attributions)
                    print("-> ATTRIB : {} nouvelle(s) dans '{}' ({} deja connue(s)).".format(
                        nb, NOM_ONGLET_ATTRIB, deja))
                except Exception as e:
                    print("(prozorro) ecriture attributions impossible ({}). Run continue.".format(_plat(e, 120)))
            else:
                print("(info) Pas de Sheet : {} titulaire(s), affichage seul.".format(len(attributions)))
        else:
            print("Aucune attribution Prozorro exploitable ce run.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Collecteur Prozorro interrompu : {}".format(e))
    sys.exit(0)
