# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur EBRD (Banque Europeenne pour la Reconstruction).
=============================================================================

POURQUOI CETTE SOURCE (la plus riche des trois)
-----------------------------------------------
EBRD finance des projets en Ukraine (zone phare Amarante, volume massif),
Caucase (Georgie, Armenie, Azerbaidjan), Asie centrale (Kazakhstan, Kirghizistan,
Tadjikistan, Turkmenistan, Ouzbekistan), Balkans et MENA. Surtout, ses avis
exposent :
  - le PAYS en clair,
  - le CLIENT = l'acheteur reel (ex "Ukrnafta PJSC", "State Water Resources
    Agency") = LA cible commerciale directe, qu'aucune autre source ne donne,
  - beaucoup de GENERAL PROCUREMENT NOTICES (GPN) = le stade le plus AMONT.

ACCES (verifie, juillet 2026)
-----------------------------
Le portail ECEPP (plateforme Delta eSourcing) publie les avis sur une page de
recherche SERVIE COMPLETE cote serveur, accessible sans compte :
  https://ecepp.ebrd.com/delta/noticeSearchResults.html
Chaque avis porte un bloc structure :
  [Projet, ID projet, PAYS, ..., CLIENT, Secteur, Type de notice]
Ancres FIABLES : champs[0]=projet, [1]=ID, [2]=pays, [-1]=type de notice,
[-2]=secteur, [-3]=client (best-effort, peut etre tronque si le nom du client
contient des virgules). L'acces machine est CONFIRME (au contraire d'ADB).

REUTILISATION : comme AfDB/ADB, reutilise le coeur ted_complet_v14 (session,
LLM, scoring, escalade, memoire, Sheet) sans le modifier. Onglet "ebrd_radar",
meme echelle de score que TED/BM.

Interrupteur : RADAR_EBRD=0 desactive.
Reglage : EBRD_DRY_RUN=1 montre l'entonnoir SANS appel LLM (et liste les pays
hors zone rencontres).
"""

import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

try:
    import ted_complet_v14 as ted
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce collecteur.")


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_EBRD", "1") != "0"
DRY_RUN = os.environ.get("EBRD_DRY_RUN", "0") == "1"

EBRD_URL = os.environ.get("EBRD_URL", "https://ecepp.ebrd.com/delta/noticeSearchResults.html")
BASE_NOTICE = "https://ecepp.ebrd.com/delta/viewNotice.html?displayNoticeId="
NOM_ONGLET = "ebrd_radar"

NB_JOURS_FENETRE = int(os.environ.get("EBRD_JOURS", "30"))
MAX_AVIS_LLM = int(os.environ.get("EBRD_BUDGET", "80"))


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# --- Pays d'operation EBRD (nom en clair) -> ISO3 ----------------------------
NOM_VERS_ISO3 = {
    # -- zones Amarante (suivies) --
    "ukraine": "UKR", "georgia": "GEO", "armenia": "ARM", "azerbaijan": "AZE",
    "kazakhstan": "KAZ", "kyrgyz republic": "KGZ", "kyrgyzstan": "KGZ",
    "tajikistan": "TJK", "turkmenistan": "TKM", "uzbekistan": "UZB",
    "serbia": "SRB", "kosovo": "XKX", "bosnia and herzegovina": "BIH",
    "montenegro": "MNE", "albania": "ALB", "north macedonia": "MKD",
    "moldova": "MDA", "turkey": "TUR", "turkiye": "TUR", "egypt": "EGY",
    "morocco": "MAR", "tunisia": "TUN", "jordan": "JOR", "lebanon": "LBN",
    "nigeria": "NGA", "west bank and gaza": "PSE",
    # -- non suivis, mappes pour etre reconnus puis filtres --
    "mongolia": "MNG", "bulgaria": "BGR", "croatia": "HRV", "romania": "ROU",
    "lithuania": "LTU", "greece": "GRC", "estonia": "EST", "latvia": "LVA",
    "poland": "POL", "hungary": "HUN", "slovak republic": "SVK",
    "slovenia": "SVN", "cyprus": "CYP",
}

_SUIVIS = set(getattr(ted, "CODES_PAYS_SUIVIS", []))

# Types de notice (derniere position du bloc). "amont" = avant l'appel d'offres.
TYPES_NOTICE = {
    "general procurement notice": ("Avis general de passation", "amont"),
    "invitation for prequalification": ("Prequalification", "amont"),
    "shortlist notice": ("Liste restreinte", "tender"),
    "invitation for tenders": ("Appel d'offres", "tender"),
    "ebrd contract notice addendum": ("Addendum d'avis", "tender"),
}
MARQUEURS_ATTRIBUTION = ("contract award", "award notice")

# Secteurs EBRD (liste fermee) : sert a valider champs[-2].
SECTEURS = {
    "municipal and environmental infrastructure", "energy emea", "energy eurasia",
    "power and energy", "transport", "natural resources", "nuclear safety",
    "infra europe", "infra eurasia", "infra emea", "infra tmea", "agribusiness",
    "information & communication technologies", "energy efficiency and climate change",
    "manufacturing and services", "financial institutions", "property and tourism",
}


# ===========================================================================
# PARTIE 2 -- COLLECTE
# ===========================================================================

def collecter_html(fetch=None, session=None):
    """HTML de la page de recherche ECEPP. `fetch` injectable pour tests."""
    if fetch is not None:
        try:
            return fetch(EBRD_URL)
        except TypeError:
            return fetch()
    session = session or ted.session_robuste()
    entetes = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
    }
    rep = session.get(EBRD_URL, timeout=45, headers=entetes)
    rep.raise_for_status()
    return rep.text


# ===========================================================================
# PARTIE 3 -- PARSING
# ===========================================================================

_RE_BLOC = re.compile(r"\[([^\[\]]+)\]")
_RE_BLOC_SPLIT = re.compile(r"\[[^\[\]]+\]")
_RE_NOTICE_ID = re.compile(r"displayNoticeId=(\d+)")
_RE_DATE = re.compile(r"(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}\s*UK Time")
_RE_ETAT = re.compile(r"\b(Open|Closed|Information Only)\b")


def _iso_depuis_jjmmaaaa(txt):
    try:
        return datetime.strptime(txt, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def type_et_phase(dernier_champ):
    t = _norm(dernier_champ)
    if any(m in t for m in MARQUEURS_ATTRIBUTION):
        return ("Attribution", "attribution", True)
    for cle, (lib, phase) in TYPES_NOTICE.items():
        if cle in t:
            return (lib, phase, False)
    return ("Avis", "tender", False)


def parser_notices(html):
    """HTML -> liste de notices structurees. On aligne chaque bloc [...] avec
    le segment de texte lisible qui le precede (dates, etat) et, si possible,
    l'identifiant de notice (href du HTML brut)."""
    ids = _RE_NOTICE_ID.findall(html)
    blocs = _RE_BLOC.findall(html)
    segments = _RE_BLOC_SPLIT.split(html)   # sans groupe capturant : segments[i] precede blocs[i]
    aligne_ids = (len(ids) == len(blocs))

    notices = []
    for i, brut in enumerate(blocs):
        champs = [c.strip() for c in brut.split(",")]
        if len(champs) < 4:
            continue
        projet, id_projet, pays = champs[0], champs[1], champs[2]
        secteur = champs[-2]
        # Validation souple : le pays doit ressembler a un pays, le type au bout.
        if not pays:
            continue
        libelle, phase, est_attrib = type_et_phase(champs[-1])
        # Client = champs[-3], best-effort (peut etre tronque si virgules).
        client = champs[-3] if len(champs) >= 3 else ""
        # Segment lisible precedent : dates + etat.
        seg = segments[i] if i < len(segments) else ""
        dates = _RE_DATE.findall(seg)
        date_pub = _iso_depuis_jjmmaaaa(dates[0]) if dates else None
        date_cloture = _iso_depuis_jjmmaaaa(dates[1]) if len(dates) > 1 else None
        etat_m = _RE_ETAT.search(seg)
        notices.append({
            "projet": projet, "id_projet": id_projet, "pays_clair": pays,
            "client": client, "secteur": secteur,
            "type_notice": libelle, "phase": phase, "est_attribution": est_attrib,
            "notice_id": ids[i] if aligne_ids else "",
            "date_publication": date_pub, "date_cloture": date_cloture,
            "etat": etat_m.group(1) if etat_m else "",
        })
    return notices


def resoudre_iso3(pays_clair):
    cle = _norm(pays_clair)
    iso = NOM_VERS_ISO3.get(cle)
    if iso and iso in _SUIVIS:
        return iso, ""
    if iso:
        return "", pays_clair          # pays EBRD reconnu mais hors zone suivie
    return "", ""


def cible_commerciale(client, phase):
    client = (client or "").strip()
    if client:
        base = "Client maitre d'ouvrage : {}. ".format(client)
    else:
        base = "Client maitre d'ouvrage du projet EBRD. "
    if phase == "amont":
        return base + ("Projet en phase amont (GPN/prequalif) : se positionner tot "
                       "aupres du client et du futur titulaire deploye en zone a risque.")
    return base + ("Viser le client (direction projet / surete) et le bureau titulaire "
                   "qui deploiera des equipes sur le terrain.")


def normaliser(notice):
    """Notice -> avis dict compatible coeur TED, ou (None, hors_zone)."""
    if notice["est_attribution"]:
        return None, ""                # les attributions ne sont pas ce collecteur
    iso3, hors_zone = resoudre_iso3(notice["pays_clair"])
    if not iso3:
        return None, hors_zone
    lien = (BASE_NOTICE + notice["notice_id"]) if notice["notice_id"] else EBRD_URL
    pub = notice["notice_id"] or "{}-{}-{}".format(
        notice["id_projet"], _norm(notice["type_notice"])[:12],
        notice["date_publication"].isoformat() if notice["date_publication"] else "na")
    description = "Secteur : {}. Type : {}.".format(notice["secteur"], notice["type_notice"])
    avis = {
        "acheteur": notice["client"] or "EBRD client",
        "pays_acheteur": "",
        "pays_execution": iso3,
        "titre": notice["projet"][:300],
        "cpv": "",
        "description": description,
        "type_notice": notice["type_notice"],
        "phase": notice["phase"],
        "secteur": notice["secteur"],
        "client": notice["client"],
        "lien_avis": lien,
        "publication_number": pub,
        "deadline": notice["date_cloture"].isoformat() if notice["date_cloture"] else "",
        "date_publication": notice["date_publication"].isoformat() if notice["date_publication"] else "",
    }
    return avis, ""


def collecter_et_normaliser(fetch=None, session=None):
    html = collecter_html(fetch=fetch, session=session)
    notices = parser_notices(html)
    seuil = date.today() - timedelta(days=NB_JOURS_FENETRE)
    avis, vus, hors_zone = [], set(), {}
    hors_perimetre = 0
    for n in notices:
        if n["date_publication"] and n["date_publication"] < seuil:
            continue
        a, hz = normaliser(n)
        if a is None:
            hors_perimetre += 1
            if hz:
                hors_zone[hz] = hors_zone.get(hz, 0) + 1
            continue
        if a["publication_number"] in vus:
            continue
        vus.add(a["publication_number"])
        avis.append(a)
    return avis, {
        "notices": len(notices), "hors_perimetre": hors_perimetre,
        "retenus": len(avis), "pays_hors_zone": hors_zone,
        "page_vide": len(notices) == 0,
    }


# ===========================================================================
# PARTIE 4 -- SORTIE GOOGLE SHEET
# ===========================================================================

COLONNES_EBRD = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "pays_acheteur",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance", "modele", "raffine", "divergence",
    "type_notice", "phase", "secteur", "client", "publication_number",
    "lien_avis", "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_EBRD = COLONNES_EBRD + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        feuille = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET, rows=2000, cols=len(TOUTES_COLONNES_EBRD))
        feuille.append_row(TOUTES_COLONNES_EBRD)
        return feuille
    if COLONNE_DATE_DETECTION not in feuille.row_values(1):
        feuille.update(values=[TOUTES_COLONNES_EBRD], range_name="A1")
    return feuille


def ligne_depuis_resultat(r):
    avis, extraction = r["avis"], r["extraction"]
    modele = ted.MODELE_RAFFINEMENT if r["raffine"] else ted.MODELE
    v = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"], "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.calculer_action_recommandee(r["score"], extraction, surete=r["surete"]),
        "fenetre_action": ted.calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": (extraction or {}).get("niveau_opportunite_amarante", ""),
        "titre": avis.get("titre", ""), "acheteur": avis.get("acheteur", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "pays_acheteur": avis.get("pays_acheteur", ""),
        "type_client": (extraction or {}).get("type_client", ""),
        "type_mobilite": (extraction or {}).get("type_mobilite", ""),
        "profil_personnes_exposees": (extraction or {}).get("profil_personnes_exposees", ""),
        "duree_estimee": (extraction or {}).get("duree_estimee", ""),
        "accessibilite_commerciale": (extraction or {}).get("accessibilite_commerciale", ""),
        "securite_existante_detectee": (extraction or {}).get("securite_existante_detectee", ""),
        "profils_acteurs_probables": ", ".join((extraction or {}).get("profils_acteurs_probables") or []),
        "cible_commerciale_reelle": cible_commerciale(avis.get("client"), avis.get("phase")),
        "justification": (extraction or {}).get("justification", ""),
        "confiance": (extraction or {}).get("confiance", ""),
        "modele": modele, "raffine": r["raffine"], "divergence": r["divergence"],
        "type_notice": avis.get("type_notice", ""), "phase": avis.get("phase", ""),
        "secteur": avis.get("secteur", ""), "client": avis.get("client", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(v.get(c, "")) for c in COLONNES_EBRD]


def ecrire_resultats(feuille, resultats):
    valeurs = feuille.get_all_records()
    index = {}
    for num, ligne in enumerate(valeurs, start=2):
        pub = ligne.get("publication_number", "")
        if pub:
            index[pub] = num
    derniere = ted.lettre_colonne(len(COLONNES_EBRD))
    maj, nouvelles, nb_maj, nb_new = [], [], 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne = ligne_depuis_resultat(r)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere), "values": [ligne]})
            nb_maj += 1
        else:
            nouvelles.append(ligne + ["nouveau", date.today().isoformat()])
            nb_new += 1
    if maj:
        feuille.batch_update(maj)
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    # Double ecriture (etape 2 du cap produit, 21/07/2026) : miroir Postgres
    # best-effort. On passe TOUS les resultats (le miroir a sa propre memoire,
    # ON CONFLICT DO NOTHING : remplissage retroactif inclus). Ne peut JAMAIS
    # faire echouer le run. NB : en phase de double ecriture, le Sheet reste
    # la reference ; les mises a jour de scores ne touchent que le Sheet.
    try:
        import radar_stockage
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, resultats))
    except Exception as e:                     # module absent : run intact
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_new, nb_maj


# ===========================================================================
# PARTIE 5 -- POINT D'ENTREE
# ===========================================================================

def merite_escalade(r):
    e = r["extraction"]
    if e is None:
        return False
    if r["final_haiku"] >= 5:
        return True
    if e.get("confiance", 1.0) < 0.7:
        return True
    if e.get("securite_existante_detectee"):
        return True
    return False


def main():
    if not ACTIVER:
        print("(info) Collecteur EBRD desactive (RADAR_EBRD=0).")
        return
    print("=" * 60)
    print("COLLECTEUR EBRD (Banque Europeenne Reconstruction) - Radar Amarante")
    print("=" * 60)

    try:
        avis, stats = collecter_et_normaliser()
    except Exception as e:
        print("ERREUR : collecte EBRD impossible ({}).".format(e))
        raise

    if stats["page_vide"]:
        print("GARDE-FOU : la page ECEPP est revenue SANS notice exploitable.")
        print("  -> Verifie l'URL EBRD_URL ou signale-le : le format a peut-etre change.")
        return

    print("Page : {notices} notice(s) | hors perimetre : {hors_perimetre} | retenus : {retenus}".format(**stats))
    if stats["pays_hors_zone"]:
        top = sorted(stats["pays_hors_zone"].items(), key=lambda x: -x[1])[:8]
        print("  (info) pays hors zone suivie : " +
              ", ".join("{}x{}".format(n, p) for p, n in top))

    if DRY_RUN:
        print("(EBRD_DRY_RUN=1 : entonnoir seulement, aucun appel LLM)")
        for a in avis[:40]:
            print("  [{:5}] {:4} {:28} | {}".format(
                a["phase"][:5], a["pays_execution"], (a["client"] or "")[:28], a["titre"][:50]))
        return

    if not avis:
        print("Aucun avis EBRD en zone suivie ce run.")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES_EBRD)
    a_traiter = [a for a in avis if a["publication_number"] not in deja_vus][:MAX_AVIS_LLM]
    print("Memoire : {} deja vu(s), {} a analyser.".format(len(avis) - len(a_traiter), len(a_traiter)))
    if not a_traiter:
        print("Rien de nouveau.")
        return

    print("\nAnalyse LLM ({} avis, modele {})...\n".format(len(a_traiter), ted.MODELE))
    resultats = []
    for i, avis_un in enumerate(a_traiter, start=1):
        print("[{}/{}] {} {}...".format(i, len(a_traiter), avis_un["pays_execution"], avis_un["titre"][:50]))
        extraction = ted.appeler_llm(avis_un)
        surete, commercial, final = ted.calculer_scores(avis_un, extraction)
        resultats.append({
            "avis": avis_un, "extraction": extraction, "surete": surete,
            "commercial": commercial, "score": final, "final_haiku": final,
            "raffine": False, "divergence": False,
        })
        time.sleep(0.4)

    a_escalader = [r for r in resultats if merite_escalade(r)]
    if a_escalader:
        print("\n{} avis escalade(s) vers {}...\n".format(len(a_escalader), ted.MODELE_RAFFINEMENT))
        for r in a_escalader:
            ex = ted.appeler_llm(r["avis"], modele=ted.MODELE_RAFFINEMENT)
            if ex is not None:
                s, c, f = ted.calculer_scores(r["avis"], ex)
                r["extraction"], r["surete"], r["commercial"], r["score"] = ex, s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0
            time.sleep(0.4)

    resultats.sort(key=lambda r: r["score"], reverse=True)
    if sheet_id and fichier:
        try:
            feuille = ouvrir_feuille(sheet_id, fichier)
            nb_new, nb_maj = ecrire_resultats(feuille, resultats)
            print("\n-> {} nouvel(s) avis, {} mis a jour dans '{}'.".format(nb_new, nb_maj, NOM_ONGLET))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("\n(dry-run Sheet : identifiants Google absents)")


if __name__ == "__main__":
    main()
