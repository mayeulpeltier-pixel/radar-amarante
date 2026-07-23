# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Collecteur AfDB (Banque Africaine de Developpement).
=====================================================================

POURQUOI CETTE SOURCE
---------------------
AfDB finance des projets partout en Afrique (Sahel, Corne, RDC...), le coeur
des zones Amarante. Elle publie ses avis de passation TRES EN AMONT du cycle :
  - GPN / AGPM : avis general de passation (debut du projet, le plus amont).
  - EOI / AMI  : manifestation d'interet (recrutement de consultants/experts
                 = deploiement de personnel a venir).
  - SPN / AOI  : appel d'offres (stade tender, comme TED/BM).
C'est l'avance de phase que TED et la Banque Mondiale ne donnent pas seuls.

ACCES (verifie, juillet 2026) : flux RSS officiel "Projects Procurement" :
  https://www.afdb.org/en/projects-and-operations/procurement.xml
Pas d'API JSON. RSS 2.0 standard : chaque <item> porte titre, lien, date et
une description. Les titres sont normalises "TYPE - Pays - Description projet"
(ex: "GPN - Rwanda - Muvumba Multipurpose Water..."), ce qui permet d'extraire
le type de notice et le pays SANS lire la page (V1, leger). L'enrichissement
page-par-page (valeur, contacts) est possible en V2.

REUTILISATION : ce collecteur REUTILISE le coeur ted_complet_v14 (session
resiliente, appel LLM, scoring deterministe, garde-fous, escalade Sonnet,
nettoyage HTML) SANS le modifier ni le dupliquer, exactement comme le
collecteur Banque Mondiale. Seuls la collecte RSS et le schema Sheet sont
propres a AfDB. Ecrit dans l'onglet SEPARE "afdb_radar", lu par le dashboard
comme une source d'avis (meme echelle de score que TED/BM).

Interrupteur : RADAR_AFDB=0 desactive le collecteur.
Mode reglage : AFDB_DRY_RUN=1 montre l'entonnoir SANS aucun appel LLM paye.

Compromis assumes (V1) :
  - pays_execution stocke en CODE ISO3 (comme TED), pour un scoring direct et
    un affichage dashboard par code. Le LLM recoit le code ISO3, qu'il sait
    resoudre en nom de pays.
  - pas de CPV (nomenclature europeenne absente ici) : le bonus infrastructure
    du scoring ne s'active pas, le LLM juge sur titre + description.
  - deadline non fournie par le RSS : la fenetre d'action reste "indetermine"
    tant qu'on n'enrichit pas la page (V2).
"""

import email.utils
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

try:
    import ted_complet_v14 as ted
except ModuleNotFoundError:
    raise SystemExit(
        "ERREUR : ted_complet_v14.py doit etre dans le MEME dossier que ce "
        "collecteur (il en reutilise la session, le LLM, le scoring, le Sheet)."
    )


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_AFDB", "1") != "0"
DRY_RUN = os.environ.get("AFDB_DRY_RUN", "0") == "1"

FLUX_AFDB = os.environ.get(
    "AFDB_FLUX", "https://www.afdb.org/en/projects-and-operations/procurement.xml")
NOM_ONGLET = "afdb_radar"

NB_JOURS_FENETRE = int(os.environ.get("AFDB_JOURS", "30"))   # notices publiees dans les N derniers jours
MAX_AVIS_LLM = int(os.environ.get("AFDB_BUDGET", "60"))       # plafond d'appels LLM par run

# Types de notice reconnus depuis le prefixe du titre. "phase" documente
# l'avance : 'amont' (avant l'appel d'offres) vs 'tender' (appel d'offres).
# On GARDE tout ce qui est une opportunite ; on EXCLUT les attributions
# (elles relevent du collecteur d'attributions, pas de la detection amont).
TYPES_NOTICE = {
    "GPN": ("Avis general de passation", "amont"),
    "AGPM": ("Avis general de passation", "amont"),
    "EOI": ("Manifestation d'interet", "amont"),
    "AMI": ("Manifestation d'interet", "amont"),
    "PPM": ("Plan de passation", "amont"),
    "SPN": ("Avis specifique de passation", "tender"),
    "AOI": ("Appel d'offres international", "tender"),
    "AON": ("Appel d'offres national", "tender"),
    "IFB": ("Invitation to bid", "tender"),
    "AAO": ("Appel d'offres", "tender"),
}
# Marqueurs d'ATTRIBUTION (a exclure de ce collecteur).
MARQUEURS_ATTRIBUTION = ("award", "attribution", "attribue")


def _norm(s):
    """minuscule + sans accents + espaces normalises (pour matcher EN/FR)."""
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# Nom de pays (EN + FR, sans accents) -> ISO3. Base : le dict AFRIQUE du coeur
# (noms FR). On complete avec les noms anglais du flux AfDB. On ne garde que
# des pays africains, tous presents dans ted.CODES_PAYS_SUIVIS.
def _construire_mapping_pays():
    m = {}
    for nom_fr, iso3 in getattr(ted, "AFRIQUE", {}).items():
        m[_norm(nom_fr)] = iso3
    noms_en = {
        "algeria": "DZA", "angola": "AGO", "benin": "BEN", "botswana": "BWA",
        "burkina faso": "BFA", "burundi": "BDI", "cabo verde": "CPV",
        "cape verde": "CPV", "cameroon": "CMR", "central african republic": "CAF",
        "chad": "TCD", "comoros": "COM", "congo": "COG",
        "democratic republic of congo": "COD", "dr congo": "COD", "drc": "COD",
        "cote d'ivoire": "CIV", "cote divoire": "CIV", "ivory coast": "CIV",
        "djibouti": "DJI", "egypt": "EGY", "equatorial guinea": "GNQ",
        "eritrea": "ERI", "eswatini": "SWZ", "swaziland": "SWZ",
        "ethiopia": "ETH", "gabon": "GAB", "gambia": "GMB", "ghana": "GHA",
        "guinea": "GIN", "guinea-bissau": "GNB", "guinea bissau": "GNB",
        "kenya": "KEN", "lesotho": "LSO", "liberia": "LBR", "libya": "LBY",
        "madagascar": "MDG", "malawi": "MWI", "mali": "MLI", "mauritania": "MRT",
        "mauritius": "MUS", "morocco": "MAR", "mozambique": "MOZ",
        "namibia": "NAM", "niger": "NER", "nigeria": "NGA", "rwanda": "RWA",
        "sao tome and principe": "STP", "sao tome & principe": "STP",
        "senegal": "SEN", "seychelles": "SYC", "sierra leone": "SLE",
        "somalia": "SOM", "south africa": "ZAF", "south sudan": "SSD",
        "sudan": "SDN", "tanzania": "TZA", "togo": "TGO", "tunisia": "TUN",
        "uganda": "UGA", "zambia": "ZMB", "zimbabwe": "ZWE",
    }
    for nom, iso3 in noms_en.items():
        m.setdefault(_norm(nom), iso3)
    # On borne aux pays reellement suivis par le radar.
    suivis = set(getattr(ted, "CODES_PAYS_SUIVIS", []))
    return {k: v for k, v in m.items() if v in suivis}


NOM_VERS_ISO3 = _construire_mapping_pays()
# Noms tries du plus long au plus court, pour le repli "scan du titre entier"
# (matcher "guinea-bissau" avant "guinea", "south sudan" avant "sudan").
_NOMS_TRIES = sorted(NOM_VERS_ISO3, key=len, reverse=True)


# ===========================================================================
# PARTIE 2 -- COLLECTE RSS
# ===========================================================================

def collecter_flux(fetch=None, session=None):
    """Renvoie le texte XML brut du flux. `fetch` injectable pour tests :
    callable() -> str. En production, GET resiliente via la session du coeur."""
    if fetch is not None:
        return fetch()
    session = session or ted.session_robuste()
    rep = session.get(FLUX_AFDB, timeout=30, headers={
        "User-Agent": "radar-amarante/1.0 (veille commerciale)"})
    rep.raise_for_status()
    return rep.text


def _localname(tag):
    """'{ns}title' -> 'title' (robuste a un eventuel namespace)."""
    return tag.rsplit("}", 1)[-1]


def parser_items(xml_texte):
    """Parse un flux RSS 2.0 -> liste de dicts {titre, lien, date, description}.
    Robuste : tolere un namespace par defaut, ignore un item sans titre."""
    items = []
    try:
        root = ET.fromstring(xml_texte)
    except ET.ParseError as e:
        print("  (attention) flux AfDB illisible (XML invalide : {}).".format(e))
        return items
    # Cherche tous les <item> quel que soit le namespace / la profondeur.
    noeuds = [n for n in root.iter() if _localname(n.tag) == "item"]
    for n in noeuds:
        champ = {"titre": "", "lien": "", "date": "", "description": ""}
        for enfant in list(n):
            nom = _localname(enfant.tag)
            txt = (enfant.text or "").strip()
            if nom == "title":
                champ["titre"] = txt
            elif nom == "link":
                champ["lien"] = txt or (enfant.attrib.get("href", "").strip())
            elif nom in ("pubDate", "date"):
                champ["date"] = champ["date"] or txt
            elif nom == "description":
                champ["description"] = txt
            elif nom == "guid" and not champ["lien"]:
                champ["lien"] = txt
        if champ["titre"]:
            items.append(champ)
    return items


# ===========================================================================
# PARTIE 3 -- NORMALISATION (titre -> type de notice + pays ISO3)
# ===========================================================================

def parser_titre(titre):
    """'GPN - Rwanda - Projet X' -> ('GPN', 'Rwanda', 'Projet X').
    Le separateur est ' - ' (espace-tiret-espace), qui ne coupe PAS
    'Guinea-Bissau' ni 'Cote d'Ivoire'."""
    parts = [p.strip() for p in re.split(r"\s+-\s+", titre) if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], " - ".join(parts[2:])
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return "", "", titre


def type_notice(prefixe):
    """Prefixe du titre -> (libelle, phase, est_attribution)."""
    p = _norm(prefixe)
    if any(m in p for m in MARQUEURS_ATTRIBUTION):
        return ("Attribution", "attribution", True)
    cle = prefixe.strip().upper()
    if cle in TYPES_NOTICE:
        lib, phase = TYPES_NOTICE[cle]
        return (lib, phase, False)
    return ("Autre notice", "tender", False)


def resoudre_iso3(pays_brut, titre_complet):
    """pays_brut = 2e segment du titre. On le mappe ; si 'Multinational' ou
    inconnu, on scanne le titre entier pour un pays a risque connu (frontieres
    de mot, du nom le plus long au plus court pour eviter niger c nigeria)."""
    cible = _norm(pays_brut)
    if cible in NOM_VERS_ISO3:
        return NOM_VERS_ISO3[cible]
    hay = " " + _norm(titre_complet) + " "
    for nom in _NOMS_TRIES:
        if re.search(r"(?<![a-z])" + re.escape(nom) + r"(?![a-z])", hay):
            return NOM_VERS_ISO3[nom]
    return ""


def normaliser(item):
    """Item RSS -> avis dict COMPATIBLE avec ted.appeler_llm / ted.calculer_scores.
    Renvoie None si hors perimetre (attribution, pays non suivi)."""
    prefixe, pays_brut, reste = parser_titre(item["titre"])
    libelle, phase, est_attrib = type_notice(prefixe)
    if est_attrib:
        return None                      # les attributions ne sont pas ce collecteur
    iso3 = resoudre_iso3(pays_brut, item["titre"])
    if not iso3:
        return None                      # pas de pays a risque identifiable
    description = ted._nettoyer_html(item.get("description", ""))[:ted.MAX_CARACTERES_DESCRIPTION]
    return {
        # Cles lues par ted.appeler_llm (prompt) ET ted.calculer_scores (score).
        "acheteur": "African Development Bank",
        "pays_acheteur": "",                        # bailleur multilateral
        "pays_execution": iso3,                     # ISO3 -> scoring direct + dashboard
        "titre": item["titre"][:300],
        "cpv": "",                                  # pas de CPV a l'AfDB
        "description": description or reste,
        # Metadonnees propres AfDB.
        "type_notice": libelle,
        "phase": phase,
        "lien_avis": item.get("lien", ""),
        "publication_number": item.get("lien", ""),  # identifiant memoire inter-runs
        "deadline": "",                              # non fourni par le RSS (V2)
        "date_publication": item.get("date", ""),
        "pays_execution_incertitude": False,
    }


def _date_dans_fenetre(date_rfc822, seuil):
    """True si la date RFC822 du RSS est >= seuil (datetime aware). Tolerant :
    une date illisible est CONSERVEE (on prefere analyser en trop qu'en moins)."""
    if not date_rfc822:
        return True
    try:
        d = email.utils.parsedate_to_datetime(date_rfc822)
    except (TypeError, ValueError):
        return True
    if d is None:
        return True
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d >= seuil


# ===========================================================================
# PARTIE 4 -- CIBLE COMMERCIALE
# ===========================================================================

def cible_commerciale(avis, extraction):
    """Qui demarcher. Sur un projet AfDB, le bailleur (la Banque) ne deploie
    pas : c'est le bureau/consortium titulaire ou l'unite d'execution du projet
    qui met des equipes sur le terrain. C'est la cible, pas la Banque."""
    phase = avis.get("phase")
    if phase == "amont":
        return ("Projet AfDB en phase amont : identifier tot le bureau d'etudes / "
                "consortium qui repondra et deploiera des experts en zone a risque.")
    return ("Titulaire (bureau d'etudes ou entreprise) qui executera le marche "
            "AfDB sur le terrain, pas la Banque. Viser sa direction operations / surete.")


# ===========================================================================
# PARTIE 5 -- SORTIE GOOGLE SHEET
# ===========================================================================

COLONNES_AFDB = [
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
TOUTES_COLONNES_AFDB = COLONNES_AFDB + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


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
            title=NOM_ONGLET, rows=2000, cols=len(TOUTES_COLONNES_AFDB))
        feuille.append_row(TOUTES_COLONNES_AFDB)
        return feuille
    entetes = feuille.row_values(1)
    if COLONNE_DATE_DETECTION not in entetes:
        feuille.update(values=[TOUTES_COLONNES_AFDB], range_name="A1")
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
    return [str(v.get(c, "")) for c in COLONNES_AFDB]


def ecrire_resultats(feuille, resultats):
    """Insere les nouveaux avis, met a jour les scores des avis deja presents
    SANS toucher a statut_suivi ni date_detection (zone preservee). Ecriture
    groupee (2 appels reseau max), meme logique que le coeur TED."""
    # Index construit en LECTURE POSITIONNELLE depuis le SCHEMA (regle 4).
    # `get_all_records()` numerisait les identifiants ("12345678" -> entier,
    # donc plus aucune correspondance avec les chaines, donc re-ajout
    # silencieux a chaque run) et levait `GSpreadException` sur un en-tete
    # duplique, en fin de run, apres avoir paye les appels au modele.
    index = ted.charger_index_publication(feuille, COLONNES_AFDB)
    derniere = ted.lettre_colonne(len(COLONNES_AFDB))
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
    # best-effort, sous FORME PLATE (colonnes du Sheet) : la forme canonique
    # que lit le dashboard. On passe TOUT (le miroir a sa propre memoire,
    # ON CONFLICT DO NOTHING : remplissage retroactif inclus). Ne peut JAMAIS
    # faire echouer le run. NB : en phase de double ecriture, le Sheet reste
    # la reference ; les mises a jour de scores ne touchent que le Sheet.
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES_AFDB, ligne_depuis_resultat(r))) for r in resultats]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:                     # module absent : run intact
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_new, nb_maj


# ===========================================================================
# PARTIE 6 -- POINT D'ENTREE
# ===========================================================================

def merite_escalade(r):
    """Meme doctrine que TED/BM : on relit avec Sonnet les cas a fort enjeu."""
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


def collecter_et_normaliser(fetch=None, session=None):
    """Etapes deterministes (testables sans LLM) : flux -> items -> avis
    normalises, filtres pays + type + fenetre de fraicheur + dedup par lien."""
    xml_texte = collecter_flux(fetch=fetch, session=session)
    items = parser_items(xml_texte)
    seuil = datetime.now(timezone.utc) - timedelta(days=NB_JOURS_FENETRE)
    avis, vus = [], set()
    hors_perimetre = 0
    for it in items:
        if not _date_dans_fenetre(it.get("date"), seuil):
            continue
        a = normaliser(it)
        if a is None:
            hors_perimetre += 1
            continue
        cle = a["publication_number"] or a["titre"]
        if cle in vus:
            continue
        vus.add(cle)
        avis.append(a)
    return avis, {"items": len(items), "hors_perimetre": hors_perimetre, "retenus": len(avis)}


def main():
    if not ACTIVER:
        print("(info) Collecteur AfDB desactive (RADAR_AFDB=0).")
        return
    print("=" * 60)
    print("COLLECTEUR AfDB (Banque Africaine de Developpement) - Radar Amarante")
    print("=" * 60)

    try:
        avis, stats = collecter_et_normaliser()
    except Exception as e:
        print("ERREUR : collecte du flux AfDB impossible ({}).".format(e))
        raise
    print("Flux : {items} item(s) | hors perimetre : {hors_perimetre} | "
          "retenus (pays a risque, non attribution, frais) : {retenus}".format(**stats))
    if not avis:
        print("Aucun avis AfDB a analyser ce run.")
        return

    if DRY_RUN:
        print("(AFDB_DRY_RUN=1 : entonnoir seulement, aucun appel LLM)")
        for a in avis[:30]:
            print("  [{:5}] {:4} {}".format(a["phase"][:5], a["pays_execution"], a["titre"][:80]))
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente (analyse LLM impossible).")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    # Memoire inter-runs : on ne reanalyse pas un avis deja traite (economie de
    # tokens). Reutilise le helper du coeur TED (lecture positionnelle robuste).
    deja_vus = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET, COLONNES_AFDB)
    a_traiter = [a for a in avis if a["publication_number"] not in deja_vus]
    print("Memoire : {} deja vu(s) ignore(s), {} nouveau(x) a analyser.".format(
        len(avis) - len(a_traiter), len(a_traiter)))
    a_traiter = a_traiter[:MAX_AVIS_LLM]
    if not a_traiter:
        print("Rien de nouveau. Onglet et dashboard restent a jour.")
        return

    print("\nAnalyse LLM ({} avis, modele {})...\n".format(len(a_traiter), ted.MODELE))
    resultats = []
    for i, avis_un in enumerate(a_traiter, start=1):
        print("[{}/{}] {}...".format(i, len(a_traiter), avis_un["titre"][:60]))
        extraction = ted.appeler_llm(avis_un)
        surete, commercial, final = ted.calculer_scores(avis_un, extraction)
        resultats.append({
            "avis": avis_un, "extraction": extraction,
            "surete": surete, "commercial": commercial, "score": final,
            "final_haiku": final, "raffine": False, "divergence": False,
        })
        time.sleep(0.4)

    # Escalade Sonnet sur les cas a fort enjeu (meme doctrine que TED/BM).
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
        print("\n(dry-run Sheet : TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents)")


if __name__ == "__main__":
    main()
