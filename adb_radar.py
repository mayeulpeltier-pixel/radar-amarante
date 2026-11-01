# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur ADB (Asian Development Bank).
=========================================================

POURQUOI CETTE SOURCE
---------------------
ADB finance des projets dans toute l'Asie-Pacifique, dont des zones Amarante :
Afghanistan, Pakistan, Bangladesh, Sri Lanka, Myanmar, plus le Caucase
(Armenie, Azerbaidjan, Georgie) et l'Asie centrale (Kazakhstan, Kirghizistan,
Tadjikistan, Turkmenistan, Ouzbekistan).

ACCES (bascule API JSON, 01/11/2026)
------------------------------------
La page https://www.adb.org/projects/tenders est rendue en JavaScript (migration
SearchStax) : le scraping HTML revenait VIDE, d'ou la desactivation (RADAR_ADB=0).
Une sonde a trouve l'API JSON que la page appelle elle-meme -- un endpoint Solr
SearchStax, champs STRUCTURES, aucun navigateur requis :

    GET searchcloud-2-ap-southeast-1.searchstax.com/29847/tenders-11959/emselect
    En-tete : Authorization: Token <jeton de LECTURE expose dans le JS public>
    q=*:*  fq=tm_X3b_en_status:Active  sort=ds_date_posted desc  wt=json

Doctrine : on prefere l'API au rendu JS (leçon ADB). Rendement mesure a la sonde :
435 avis actifs, dont 123 en zone Amarante (Bangladesh, Pakistan, Ouzbekistan,
Tadjikistan, Nepal, Kirghizstan, Georgie...). Le filtre `status:Active` remplace
l'ancienne fenetre de fraicheur : il ne garde que les marches OUVERTS, ce qui est
plus juste qu'une fenetre sur la date de publication. La memoire (dedup par
publication_number) + le plafond LLM gerent le reste (regle 9).

REUTILISATION : ce collecteur REUTILISE le coeur ted_complet_v14 (session, LLM,
scoring, escalade, memoire, Sheet) sans le modifier. Onglet SEPARE "adb_radar",
meme echelle de score que TED/BM. Double ecriture Sheet + miroir Postgres (source
de verite depuis le 22/07), best-effort (le miroir ne fait jamais echouer un run).

Interrupteur : RADAR_ADB=0 desactive. Reactivation = retirer la variable.
Reglages : ADB_DRY_RUN=1 montre l'entonnoir SANS appel LLM ; ADB_API_URL /
ADB_API_TOKEN surchargent l'endpoint et le jeton si ADB les fait tourner.
"""

import os
import re
import time
import unicodedata
from datetime import date, datetime

try:
    import ted_complet_v14 as ted
    import radar_resilience
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce collecteur.")


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_ADB", "1") != "0"
DRY_RUN = os.environ.get("ADB_DRY_RUN", "0") == "1"

# --- API JSON SearchStax (Solr) d'ADB, decouverte par sonde le 01/11/2026 ------
# Endpoint public que la page tenders appelle elle-meme. Le jeton est un jeton de
# LECTURE SEULE expose en clair dans le JavaScript public de adb.org (il autorise
# uniquement la recherche publique des tenders) : ce n'est pas un secret au sens
# GitHub, d'ou sa presence comme defaut. Surchargeables par variable si ADB les
# fait tourner.
ADB_API_URL = os.environ.get(
    "ADB_API_URL",
    "https://searchcloud-2-ap-southeast-1.searchstax.com/29847/tenders-11959/emselect")
ADB_API_TOKEN = os.environ.get(
    "ADB_API_TOKEN", "2a076eb3a48fd68fc78506c1a16a5d5000da76e4")
ADB_ROWS = int(os.environ.get("ADB_ROWS", "500"))    # couvre tous les avis actifs
BASE_ADB = "https://www.adb.org"                     # prefixe des liens relatifs
NOM_ONGLET = "adb_radar"

MAX_AVIS_LLM = int(os.environ.get("ADB_BUDGET", "60"))


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# --- Country/Economy (nom en clair) -> ISO3 ----------------------------------
# On mappe les pays suivis ET quelques non-suivis courants (pour les reconnaitre
# puis les filtrer proprement). "Regional" et "Multinational" = pas de pays.
NOM_VERS_ISO3 = {
    # -- zones Amarante (suivies) --
    "afghanistan": "AFG", "pakistan": "PAK", "bangladesh": "BGD",
    "sri lanka": "LKA", "nepal": "NPL", "myanmar": "MMR", "burma": "MMR",
    "cambodia": "KHM", "lao pdr": "LAO", "lao people's democratic republic": "LAO",
    "laos": "LAO", "indonesia": "IDN", "philippines": "PHL",
    "papua new guinea": "PNG", "kazakhstan": "KAZ", "kyrgyz republic": "KGZ",
    "kyrgyzstan": "KGZ", "tajikistan": "TJK", "turkmenistan": "TKM",
    "uzbekistan": "UZB", "georgia": "GEO", "armenia": "ARM", "azerbaijan": "AZE",
    # -- non suivis, mappes pour etre reconnus puis filtres --
    "viet nam": "VNM", "vietnam": "VNM", "india": "IND", "mongolia": "MNG",
    "china": "CHN", "people's republic of china": "CHN", "thailand": "THA",
    "maldives": "MDV", "bhutan": "BTN", "fiji": "FJI", "samoa": "WSM",
    "tonga": "TON", "vanuatu": "VUT", "solomon islands": "SLB",
    "timor-leste": "TLS", "malaysia": "MYS",
}
# Valeurs "region" a ignorer (pas de pays precis exploitable).
REGIONAUX = {"regional", "multinational", "asia and the pacific", "asia pacific", ""}

_SUIVIS = set(getattr(ted, "CODES_PAYS_SUIVIS", []))


# ===========================================================================
# PARTIE 2 -- COLLECTE (API JSON SearchStax)
# ===========================================================================

def interroger_api(session=None, rows=ADB_ROWS):
    """Appelle l'API Solr SearchStax d'ADB et renvoie la liste des documents
    bruts. Tenders ACTIFS seulement (marches ouverts), tri date de publication
    decroissante. `fetch_json` dans collecter_et_normaliser permet de tester
    sans reseau."""
    session = session or ted.session_robuste()
    params = {"q": "*:*", "fq": "tm_X3b_en_status:Active",
              "sort": "ds_date_posted desc", "rows": rows, "wt": "json"}
    rep = session.get(ADB_API_URL, params=params, timeout=30, headers={
        "Authorization": "Token " + ADB_API_TOKEN, "Accept": "application/json"})
    rep.raise_for_status()
    return (rep.json().get("response") or {}).get("docs") or []


def _premier(v):
    """Champ Solr multivalue (liste) -> premiere valeur en chaine."""
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def notice_depuis_doc(doc):
    """Document Solr ADB -> notice brute (meme forme que l'ancien parseur, pour
    que `normaliser` reste inchange). Les liens relatifs sont rendus absolus."""
    lien = _premier(doc.get("ss_file_url")) or _premier(doc.get("ss_url"))
    if lien.startswith("/"):
        lien = BASE_ADB + lien
    return {
        "pays_clair": _premier(doc.get("tm_X3b_en_country")),
        # Reference stable pour la memoire : n° d'agrement, sinon n° de projet,
        # sinon l'id Solr (toujours present et stable).
        "reference": (_premier(doc.get("tm_X3b_en_approval_number"))
                      or _premier(doc.get("tm_X3b_en_project_number"))
                      or _premier(doc.get("id"))),
        "lien": lien,
        "titre": _premier(doc.get("tm_X3b_en_title")),
        "type_notice_txt": _premier(doc.get("tm_X3b_en_type")),
        "secteur": _premier(doc.get("tm_X3b_en_sector")),
        "deadline": _premier(doc.get("ds_date_closing")),
        "posting_date": _premier(doc.get("ds_date_posted")),
    }


def resoudre_iso3(pays_clair, titre=""):
    """Country/Economy en clair -> ISO3 (source primaire). Repli : scan du titre.
    Renvoie (iso3, nom_hors_zone)."""
    cle = _norm(pays_clair)
    if cle in REGIONAUX:
        return "", ""
    iso = NOM_VERS_ISO3.get(cle)
    if iso and iso in _SUIVIS:
        return iso, ""
    if iso:
        return "", pays_clair            # pays reconnu mais hors zone suivie
    # Repli : un nom de pays suivi apparait-il dans le titre ?
    hay = " " + _norm(titre) + " "
    for nom in sorted((n for n, v in NOM_VERS_ISO3.items() if v in _SUIVIS), key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(nom) + r"(?![a-z])", hay):
            return NOM_VERS_ISO3[nom], ""
    return "", ""


def type_notice(texte):
    t = _norm(texte)
    if "advance" in t or "general procurement" in t:
        return ("Avis anticipe", "amont")
    if "prequalification" in t or "expression of interest" in t or "eoi" in t:
        return ("Prequalification / EOI", "amont")
    if "consult" in t:
        return ("Services de conseil", "tender")
    if "invitation for bid" in t or "ifb" in t or "bid" in t:
        return ("Appel d'offres", "tender")
    if "works" in t:
        return ("Travaux", "tender")
    if "goods" in t:
        return ("Fournitures", "tender")
    return ("Avis de marche", "tender")


def _date_iso(txt):
    if not txt:
        return None
    # L'API renvoie de l'ISO ("2026-11-19T12:00:00Z"). On garde le repli
    # jour-mois-annee par prudence (robustesse a un changement de format).
    try:
        return date.fromisoformat(str(txt)[:10])
    except ValueError:
        pass
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(str(txt).strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def normaliser(notice):
    """Notice brute -> avis dict compatible coeur TED, ou (None, hors_zone)."""
    iso3, hors_zone = resoudre_iso3(notice["pays_clair"], notice["titre"])
    if not iso3:
        return None, hors_zone
    libelle, phase = type_notice(notice["type_notice_txt"] + " " + notice["titre"])
    d = _date_iso(notice["deadline"])
    avis = {
        "acheteur": "Asian Development Bank",
        "pays_acheteur": "",
        "pays_execution": iso3,
        "titre": notice["titre"][:300],
        "cpv": "",
        "description": "",
        "type_notice": libelle,
        "phase": phase,
        "lien_avis": notice["lien"],
        "publication_number": notice["reference"] or notice["lien"] or notice["titre"],
        "deadline": d.isoformat() if d else "",
        "date_publication": (_date_iso(notice["posting_date"]).isoformat()
                             if _date_iso(notice["posting_date"]) else ""),
    }
    return avis, ""


def collecter_et_normaliser(fetch_json=None, session=None, rows=ADB_ROWS):
    """API -> notices -> avis normalises (filtre pays zone + dedup). `fetch_json`
    injectable pour tests : callable() -> liste de documents Solr.

    Pas de fenetre sur la date de publication : le filtre serveur
    `status:Active` ne garde deja que les marches OUVERTS (plus juste qu'une
    fenetre), et la memoire (dedup par publication_number) + le plafond LLM
    gerent la file (regle 9 : memoire avant plafond).

    stats['pays_hors_zone'] = canari de derive du mapping ;
    stats['api_vide'] = aucun document (endpoint/jeton a verifier)."""
    docs = fetch_json() if fetch_json is not None else interroger_api(session=session, rows=rows)
    avis, vus, hors_zone = [], set(), {}
    hors_perimetre = 0
    for doc in docs:
        a, hz = normaliser(notice_depuis_doc(doc))
        if a is None:
            hors_perimetre += 1
            if hz:
                hors_zone[hz] = hors_zone.get(hz, 0) + 1
            continue
        cle = a["publication_number"]
        if cle in vus:
            continue
        vus.add(cle)
        avis.append(a)
    return avis, {
        "notices": len(docs), "hors_perimetre": hors_perimetre,
        "retenus": len(avis), "pays_hors_zone": hors_zone,
        "api_vide": len(docs) == 0,
    }


# ===========================================================================
# PARTIE 4 -- CIBLE COMMERCIALE
# ===========================================================================

def cible_commerciale(avis, extraction):
    if avis.get("phase") == "amont":
        return ("Projet ADB en phase amont : reperer tot le bureau / consortium "
                "qui repondra et deploiera des experts en zone a risque (Asie centrale, "
                "Caucase, Pakistan...).")
    return ("Titulaire (bureau d'etudes ou entreprise) qui executera le marche ADB "
            "sur le terrain, pas la Banque. Viser sa direction operations / surete.")


# ===========================================================================
# PARTIE 5 -- SORTIE GOOGLE SHEET
# ===========================================================================

COLONNES_ADB = [
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
TOUTES_COLONNES_ADB = COLONNES_ADB + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    # Ouverture protegee par retry (503/429).
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_cs)
    try:
        feuille = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET, rows=2000, cols=len(TOUTES_COLONNES_ADB))
        feuille.append_row(TOUTES_COLONNES_ADB)
        return feuille
    if COLONNE_DATE_DETECTION not in feuille.row_values(1):
        feuille.update(values=[TOUTES_COLONNES_ADB], range_name="A1")
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
        "cible_commerciale_reelle": cible_commerciale(avis, extraction),
        "justification": (extraction or {}).get("justification", ""),
        "confiance": (extraction or {}).get("confiance", ""),
        "modele": modele, "raffine": r["raffine"], "divergence": r["divergence"],
        "type_notice": avis.get("type_notice", ""), "phase": avis.get("phase", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(v.get(c, "")) for c in COLONNES_ADB]


def ecrire_resultats(feuille, resultats):
    # Index construit en LECTURE POSITIONNELLE depuis le SCHEMA (regle 4).
    # `get_all_records()` numerisait les identifiants ("12345678" -> entier,
    # donc plus aucune correspondance avec les chaines, donc re-ajout
    # silencieux a chaque run) et levait `GSpreadException` sur un en-tete
    # duplique, en fin de run, apres avoir paye les appels au modele.
    index = ted.charger_index_publication(feuille, COLONNES_ADB)
    derniere = ted.lettre_colonne(len(COLONNES_ADB))
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
        radar_resilience.avec_retry(lambda: feuille.batch_update(maj), "ecriture batch_update")
    if nouvelles:
        radar_resilience.avec_retry(lambda: feuille.append_rows(nouvelles, value_input_option="RAW"), "ecriture append_rows")
    return nb_new, nb_maj


def ecrire_miroir(resultats):
    """Double ecriture dans le miroir Postgres (source de verite depuis le
    22/07). Upsert des `donnees` (regle 5), aligne POSITIONNELLEMENT sur
    COLONNES_ADB (regle 4). Best-effort absolu : n'interrompt JAMAIS un run
    (regle 11). Point d'entree isole pour etre testable."""
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES_ADB, ligne_depuis_resultat(r))) for r in resultats]
        return radar_stockage.ecrire_miroir(NOM_ONGLET, plates)
    except Exception as e:  # noqa: BLE001
        return "miroir indisponible ({})".format(e)


# ===========================================================================
# PARTIE 6 -- POINT D'ENTREE
# ===========================================================================

def merite_escalade(r):
    e = r["extraction"]
    if e is None:
        return False
    if r["final_haiku"] >= 5:
        return True
    if e.get("confiance", 1.0) < 0.7:
        return True
    # Securite deja en place : critere PARTAGE (ted.escalade_pour_securite).
    # Couvre `interne_client` (on va ecarter : une erreur coute un marche)
    # ET `prestataire_tiers` (on va pousser en conquete : jugement le plus
    # fin du pipeline). La lecture directe de `securite_existante_detectee`
    # avait cesse de couvrir le second depuis le passage a l'enum.
    if ted.escalade_pour_securite(e):
        return True
    return False


def main():
    if not ACTIVER:
        print("(info) Collecteur ADB desactive (RADAR_ADB=0).")
        return
    print("=" * 60)
    print("COLLECTEUR ADB (Asian Development Bank) - Radar Amarante")
    print("=" * 60)

    try:
        avis, stats = collecter_et_normaliser()
    except Exception as e:
        print("ERREUR : collecte ADB impossible ({}).".format(e))
        raise

    if stats["api_vide"]:
        print("GARDE-FOU : l'API ADB n'a renvoye AUCUN document.")
        print("  -> Endpoint ou jeton a verifier (ADB_API_URL / ADB_API_TOKEN),")
        print("     ou le jeton de lecture public d'adb.org a change.")
        return

    print("API : {notices} doc(s) actif(s) | hors perimetre : {hors_perimetre} | retenus : {retenus}".format(**stats))
    if stats["pays_hors_zone"]:
        top = sorted(stats["pays_hors_zone"].items(), key=lambda x: -x[1])[:8]
        print("  (info) pays hors zone suivie : " +
              ", ".join("{}x{}".format(n, p) for p, n in top))

    if DRY_RUN:
        print("(ADB_DRY_RUN=1 : entonnoir seulement, aucun appel LLM)")
        for a in avis[:30]:
            print("  [{:5}] {:4} {}".format(a["phase"][:5], a["pays_execution"], a["titre"][:80]))
        return

    if not avis:
        print("Aucun avis ADB en zone suivie ce run.")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES_ADB)
    a_traiter = [a for a in avis if a["publication_number"] not in deja_vus][:MAX_AVIS_LLM]
    print("Memoire : {} deja vu(s), {} a analyser.".format(len(avis) - len(a_traiter), len(a_traiter)))
    if not a_traiter:
        print("Rien de nouveau.")
        return

    print("\nAnalyse LLM ({} avis, modele {})...\n".format(len(a_traiter), ted.MODELE))
    resultats = []
    for i, avis_un in enumerate(a_traiter, start=1):
        print("[{}/{}] {}...".format(i, len(a_traiter), avis_un["titre"][:60]))
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
    # Miroir Postgres (source de verite) : independant du Sheet, best-effort.
    print("  (pg) " + ecrire_miroir(resultats))


if __name__ == "__main__":
    main()
