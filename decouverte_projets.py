# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- PROJECT DISCOVERY (couche 2bis : decouverte de projets).
===============================================================================

LE MANQUE QUE CE MODULE COMBLE
------------------------------
`projets_reference.py` est une liste CUREE A LA MAIN : le radar ne sait suivre
que des projets qu'un humain a nommes. Un projet qui nait aujourd'hui au Tchad
ou en Guinee est invisible jusqu'a ce que quelqu'un pense a l'ajouter.
Ce module detecte AUTOMATIQUEMENT des projets ABSENTS du registre.

COMMENT IL S'INTEGRE SANS RIEN CASSER
--------------------------------------
Point d'accroche verifie dans le code : `projets_reference.charger_registre` et
`projets.rattacher` acceptent tous deux un `registre` INJECTABLE. La decouverte
n'a donc AUCUNE couche existante a modifier : elle produit des entrees AU MEME
FORMAT que le registre cure, et le socle les consomme telles quelles.

    requetes pays x cycle de vie
            v
    extraction LLM (nom, pays, secteur, phase, acteurs, montant)
            v
    regroupement en CANDIDATS (cle canonique de nom + pays)
            v
    dedup contre le registre existant  -> jamais de doublon
            v
    score de confiance
            v
    promotion -> entree de registre -> projets.construire_projets(registre=...)

Un candidat NON PROMU n'entre jamais dans le socle : pas de pollution du
lifecycle par des rumeurs. La promotion est un choix explicite et reversible.

DOCTRINE ANTI-FAUX-POSITIFS
---------------------------
Un projet invente coute plus cher qu'un projet manque : il pollue le registre,
fausse les scores et fait perdre du temps commercial. D'ou quatre filtres
cumulatifs, du moins cher au plus cher :
  1. deterministe : bruit evident, fraicheur, presence d'un mot de contexte ;
  2. LLM : extraction structuree, avec consigne explicite de renvoyer "rien"
     plutot que d'inventer, et une confiance par signal ;
  3. dedup : tout ce qui matche le registre existant est ecarte (c'est un
     signal d'un projet CONNU, il repart vers collecteur_projets) ;
  4. promotion : un candidat n'est promu qu'avec PLUSIEURS signaux, PLUSIEURS
     sources distinctes et une confiance moyenne suffisante.

FLAG : RADAR_DECOUVERTE_PROJETS=1 pour activer (defaut OFF).
"""

import collections
import json
import os
import re
import time
import unicodedata
from datetime import date

import bitd_signaux as bitd
import pays_projets_reference as pref
import projets as pj
import projets_reference as ref
import sources_reference as sref
import ted_complet_v14 as ted


ACTIVER = os.environ.get("RADAR_DECOUVERTE_PROJETS", "0") == "1"
NOM_ONGLET = "projets_candidats"

PAYS_PAR_RUN = int(os.environ.get("RADAR_DECOUVERTE_PROJ_PAYS", "6"))
TAILLE_LOT = int(os.environ.get("RADAR_DECOUVERTE_PROJ_LOT", "10"))
MAX_LOTS = int(os.environ.get("RADAR_DECOUVERTE_PROJ_MAX_LOTS", "30"))
MAX_ARTICLES = int(os.environ.get("RADAR_DECOUVERTE_PROJ_MAX_ART", "20"))
JOURS_FRAICHEUR = int(os.environ.get("RADAR_DECOUVERTE_PROJ_JOURS", "60"))
PAUSE = float(os.environ.get("RADAR_DECOUVERTE_PROJ_PAUSE", "1.0"))

# Seuils de PROMOTION (un candidat devient un projet suivi).
SEUIL_CONFIANCE = float(os.environ.get("RADAR_PROMO_CONFIANCE", "60"))
SEUIL_SIGNAUX = int(os.environ.get("RADAR_PROMO_SIGNAUX", "3"))
SEUIL_SOURCES = int(os.environ.get("RADAR_PROMO_SOURCES", "2"))

# Mode DISCOVERY_BACKFILL (P9) : exploration de l'archive au lieu de la seule
# fenetre courante. Desactive la fraicheur et decoupe la periode en tranches
# trimestrielles datees (voir fenetres_backfill).
BACKFILL = os.environ.get("DISCOVERY_BACKFILL", "0") == "1"
BACKFILL_MOIS = int(os.environ.get("DISCOVERY_BACKFILL_MOIS", "18"))


# ===========================================================================
# 1. REQUETES DE DECOUVERTE : pays x vocabulaire de NAISSANCE de projet
# ===========================================================================
# On ne cherche PAS "security" (trop tard) ni un nom de projet (inconnu par
# definition) : on cherche les tournures qui accompagnent la naissance d'un
# grand projet, croisees avec un pays.
DECLENCHEURS_NAISSANCE = (
    '("feasibility study" OR "memorandum of understanding" OR MoU OR '
    '"concession agreement" OR "investment agreement" OR "host government agreement" OR '
    '"financing agreement" OR "funding approved" OR "financial close" OR '
    '"cabinet approves" OR "government approves" OR "final investment decision" OR '
    '"EPC contract" OR "preferred bidder" OR "consultant selected" OR '
    '"development corridor" OR "master plan" OR "special economic zone" OR '
    '"industrial park" OR "power plant" OR "transmission line" OR pipeline OR '
    'LNG OR refinery OR hydropower OR "solar park" OR "wind farm" OR '
    '"mining project" OR "mine development" OR smelter OR '
    '"deepwater port" OR "port development" OR railway OR "rail corridor" OR '
    '"airport expansion" OR "data center" OR "fertilizer plant" OR "cement plant")')

# Traductions des declencheurs (P3). La presse locale d'un pays lusophone ou
# arabophone n'emploie PAS le vocabulaire anglais : sans ces grilles, on ne
# voit du pays que ce que sa presse anglophone veut bien relayer.
DECLENCHEURS_LANGUE = {
    "en": DECLENCHEURS_NAISSANCE,
    "fr": ('("etude de faisabilite" OR "protocole d\'accord" OR "accord de principe" OR '
           '"convention de concession" OR "accord d\'investissement" OR '
           '"accord de financement" OR "financement approuve" OR "bouclage financier" OR '
           '"conseil des ministres" OR "decision finale d\'investissement" OR '
           '"contrat EPC" OR "appel d\'offres" OR "consultant retenu" OR '
           '"corridor de developpement" OR "plan directeur" OR "zone economique speciale" OR '
           '"parc industriel" OR "centrale electrique" OR "ligne de transport" OR '
           'gazoduc OR oleoduc OR GNL OR raffinerie OR hydroelectrique OR '
           '"parc solaire" OR "ferme eolienne" OR "projet minier" OR '
           '"port en eau profonde" OR "chemin de fer" OR "extension aeroport" OR '
           '"usine d\'engrais" OR cimenterie)'),
    "pt": ('("estudo de viabilidade" OR "memorando de entendimento" OR '
           '"acordo de concessao" OR "acordo de investimento" OR '
           '"acordo de financiamento" OR "financiamento aprovado" OR '
           '"decisao final de investimento" OR "contrato EPC" OR '
           '"corredor de desenvolvimento" OR "plano diretor" OR '
           '"zona economica especial" OR "parque industrial" OR '
           '"central electrica" OR gasoduto OR oleoduto OR GNL OR refinaria OR '
           '"projeto mineiro" OR "porto de aguas profundas" OR ferrovia OR '
           '"parque solar" OR hidroeletrica)'),
    "es": ('("estudio de factibilidad" OR "memorando de entendimiento" OR '
           '"contrato de concesion" OR "acuerdo de inversion" OR '
           '"acuerdo de financiamiento" OR "cierre financiero" OR '
           '"decision final de inversion" OR "contrato EPC" OR '
           '"corredor de desarrollo" OR "plan maestro" OR "zona economica especial" OR '
           '"parque industrial" OR "central electrica" OR gasoducto OR oleoducto OR '
           'GNL OR refineria OR "proyecto minero" OR "puerto de aguas profundas" OR '
           'ferrocarril OR "parque solar" OR hidroelectrica)'),
    "ar": ('("دراسة الجدوى" OR "مذكرة تفاهم" OR "اتفاقية امتياز" OR '
           '"اتفاقية استثمار" OR "اتفاقية تمويل" OR "القرار النهائي للاستثمار" OR '
           '"عقد هندسة وتوريد وإنشاء" OR "محطة كهرباء" OR "خط أنابيب" OR '
           '"الغاز الطبيعي المسال" OR مصفاة OR "مشروع تعديني" OR "ميناء" OR '
           '"سكة حديد" OR "منطقة اقتصادية خاصة" OR "مجلس الوزراء")'),
    "ru": ('("технико-экономическое обоснование" OR "меморандум о взаимопонимании" OR '
           '"концессионное соглашение" OR "инвестиционное соглашение" OR '
           '"соглашение о финансировании" OR "окончательное инвестиционное решение" OR '
           '"EPC контракт" OR "электростанция" OR "трубопровод" OR СПГ OR '
           '"нефтеперерабатывающий завод" OR "горнодобывающий проект" OR '
           '"глубоководный порт" OR "железная дорога" OR "промышленный парк")'),
    "uk": ('("техніко-економічне обґрунтування" OR "меморандум про взаєморозуміння" OR '
           '"концесійна угода" OR "інвестиційна угода" OR "угода про фінансування" OR '
           '"остаточне інвестиційне рішення" OR "EPC контракт" OR "електростанція" OR '
           '"трубопровід" OR "нафтопереробний завод" OR "гірничодобувний проект" OR '
           '"глибоководний порт" OR "залізниця" OR "індустріальний парк")'),
    "sw": ('("upembuzi yakinifu" OR "makubaliano ya awali" OR "mkataba wa uwekezaji" OR '
           '"mradi wa nishati" OR "mradi wa madini" OR "bandari" OR "reli" OR '
           '"kiwanda" OR "mtambo wa umeme" OR "bomba la gesi")'),
}


def declencheurs(langue):
    """Grille de declencheurs dans la langue visee, repli anglais."""
    return DECLENCHEURS_LANGUE.get(langue, DECLENCHEURS_NAISSANCE)


# Pays balayes. Volontairement PLUS LARGE que PAYS_COUVERTS_AMARANTE : la
# decouverte doit voir naitre un projet meme dans un pays aujourd'hui hors
# perimetre de collecte (c'est exactement le cas Tanzanie / Tanzania LNG).
PAYS_DECOUVERTE = [
    ("Tanzania", "TZA", "en"), ("Democratic Republic of Congo", "COD", "en"),
    ("Mozambique", "MOZ", "en"), ("Guinea", "GIN", "en"),
    ("Nigeria", "NGA", "en"), ("Senegal", "SEN", "fr"),
    ("Mali", "MLI", "fr"), ("Niger", "NER", "fr"), ("Tchad", "TCD", "fr"),
    ("Burkina Faso", "BFA", "fr"), ("Mauritanie", "MRT", "fr"),
    ("Cote d'Ivoire", "CIV", "fr"), ("Angola", "AGO", "en"),
    ("Uganda", "UGA", "en"), ("Iraq", "IRQ", "en"), ("Ukraine", "UKR", "en"),
    ("Kazakhstan", "KAZ", "en"), ("Uzbekistan", "UZB", "en"),
    ("Libya", "LBY", "en"), ("Zambia", "ZMB", "en"),
]
_LOCALES = {"fr": ("fr", "FR", "FR:fr"), "en": ("en", "US", "US:en")}


def pays_du_run(curseur=0, par_run=None):
    """Fenetre de pays interroges ce run (rotation bornee). Fonction PURE."""
    par_run = PAYS_PAR_RUN if par_run is None else par_run
    n = len(PAYS_DECOUVERTE)
    if n == 0 or par_run <= 0:
        return []
    debut = curseur % n
    return (PAYS_DECOUVERTE[debut:] + PAYS_DECOUVERTE[:debut])[:par_run]


def url_pays(nom_pays, langue="en"):
    """Compatibilite : URL pour un nom de pays et une langue (ancien appel)."""
    hl, gl, ceid = _LOCALES.get(langue) or pref.PARAMS_LANGUE.get(
        langue, pref.PARAMS_LANGUE["en"])
    requete = '{} "{}"'.format(declencheurs(langue), nom_pays)
    return bitd.url_google_news("", requete_perso=requete, hl=hl, gl=gl, ceid=ceid)


def fenetres_backfill(mois=None, aujourd=None, pas_mois=3):
    """Fenetres datees pour explorer l'archive (P9). Fonction PURE.

    Google News limite le nombre de resultats par requete : demander "les 24
    derniers mois" en une fois ne rend que les articles recents. On decoupe
    donc en tranches trimestrielles, chacune interrogee separement, ce qui
    fait remonter les signaux anciens que la fenetre courante masque.

    Retour : [(debut_iso, fin_iso)], de la plus recente a la plus ancienne."""
    import datetime
    mois = BACKFILL_MOIS if mois is None else mois
    fin = aujourd or datetime.date.today()
    out = []
    restant = max(1, int(mois))
    while restant > 0:
        pas = min(pas_mois, restant)
        jours = int(round(pas * 30.44))
        debut = fin - datetime.timedelta(days=jours)
        out.append((debut.isoformat(), fin.isoformat()))
        fin = debut
        restant -= pas
    return out


def urls_du_pays(pays, fenetre=None):
    """URLs a interroger pour un pays du REFERENTIEL : une par langue
    pertinente, avec la grille de declencheurs traduite et l'edition Google
    News locale. Retour : [(langue, url)]. Fonction PURE.

    `fenetre` = (debut_iso, fin_iso) ajoute les operateurs after:/before: pour
    le mode backfill (P9)."""
    out = []
    borne = ""
    if fenetre:
        borne = " after:{} before:{}".format(fenetre[0], fenetre[1])
    for langue in (pays.get("langues") or ["en"]):
        hl, gl, ceid = pref.params_google_news(pays, langue)
        nom = pref.nom_pour_requete(pays, langue)
        requete = '{} "{}"{}'.format(declencheurs(langue), nom, borne)
        out.append((langue, bitd.url_google_news("", requete_perso=requete,
                                                 hl=hl, gl=gl, ceid=ceid)))
    return out


# ===========================================================================
# 2. CLE CANONIQUE DE NOM DE PROJET
# ===========================================================================
# Mots qui ne DISTINGUENT pas un projet : ils sont retires de la cle, sinon
# "Inga 3 project" et "projet Inga 3" seraient deux projets differents.
MOTS_GENERIQUES = {
    "project", "projet", "programme", "program", "scheme", "phase", "the",
    "of", "de", "du", "des", "la", "le", "les", "and", "et", "for", "new",
    "nouveau", "nouvelle", "development", "developpement", "plan", "initiative",
    "expansion", "extension", "construction", "site", "plant", "usine",
    "station", "centrale", "complex", "complexe", "terminal", "corridor",
    "hub", "park", "parc", "zone", "field", "champ", "block", "bloc",
}
# Mots trop communs pour servir de PREUVE de rapprochement entre candidats
# (voir fusionner). Un simple "solar" ou "tanzania" partage ne prouve rien.
MOTS_NON_DISTINCTIFS = MOTS_GENERIQUES | {
    "lng", "gas", "gaz", "oil", "petrole", "solar", "solaire", "wind", "eolien",
    "hydro", "hydropower", "hydroelectric", "power", "energie", "energy",
    "mine", "mining", "miniere", "port", "rail", "railway", "road", "route",
    "airport", "pipeline", "refinery", "raffinerie", "dam", "barrage", "steel",
    "cement", "fertilizer", "data", "center", "industrial", "industriel",
} | {_n.lower() for _n, _i, _l in PAYS_DECOUVERTE} | {
    "congo", "drc", "rdc", "tanzanian", "nigerian", "guinean", "ivorian",
}

_ROMAINS = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def _norm(txt):
    t = unicodedata.normalize("NFD", str(txt or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", " ", t)


def jetons_projet(nom):
    """Jetons distinctifs d'un nom de projet, chiffres romains normalises.
    "Inga III project" et "projet Inga 3" -> {"inga", "3"}. Fonction PURE."""
    out = []
    for mot in _norm(nom).split():
        mot = _ROMAINS.get(mot, mot)
        if mot and mot not in MOTS_GENERIQUES:
            out.append(mot)
    return set(out)


def cle_projet(nom, iso3=""):
    """Cle canonique d'un projet candidat : jetons distinctifs tries + pays.
    Fonction PURE."""
    jetons = sorted(jetons_projet(nom))
    if not jetons:
        return ""
    return "|".join(jetons) + "@" + str(iso3 or "").upper()


# ===========================================================================
# 3. DEDUP CONTRE LE REGISTRE EXISTANT
# ===========================================================================
def ordre_de_grandeur(montant_musd):
    """Tranche de taille d'un projet, pour rapprocher des signaux qui parlent
    du meme chantier sans le nommer. Fonction PURE."""
    try:
        m = float(montant_musd or 0)
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    if m < 100:
        return "<100M"
    if m < 1000:
        return "100M-1Md"
    if m < 10000:
        return "1-10Md"
    return ">10Md"


def empreinte_sans_nom(extraction):
    """Cle d'un projet NON NOMME (P4) : pays + secteur + le premier trait
    discriminant disponible (localisation, sinon acteur principal, sinon
    ordre de grandeur). Retourne "" si le faisceau est trop maigre pour
    identifier quoi que ce soit. Fonction PURE.

    Exigence : "meme pays, meme secteur, meme localisation, meme ordre de
    grandeur financier, memes acteurs". On n'exige pas les cinq (ce serait
    intenable sur des depeches courtes), mais pays + secteur + AU MOINS UN
    trait discriminant, sinon on regrouperait tout le gaz d'un pays."""
    iso3 = str(extraction.get("iso3") or "").strip().upper()
    secteur = str(extraction.get("secteur") or "").strip().lower()
    if not iso3 or not secteur:
        return ""
    loc = _norm(extraction.get("localisation", "")).strip()
    acteurs = sorted(_norm(a) for a in (extraction.get("acteurs") or []) if a)
    grandeur = ordre_de_grandeur(extraction.get("montant_musd"))
    if loc:
        trait = "loc:" + loc
    elif acteurs:
        trait = "act:" + acteurs[0]
    elif grandeur:
        trait = "tail:" + grandeur
    else:
        return ""
    return "SANSNOM|{}|{}|{}".format(iso3, secteur, trait)


def id_temporaire(empreinte):
    """Identifiant TEMPORAIRE et stable d'un candidat sans nom. Il permet au
    candidat de recevoir de nouveaux signaux run apres run, en attendant qu'un
    nom officiel apparaisse. Fonction PURE."""
    import hashlib
    parties = empreinte.split("|")
    iso3 = parties[1] if len(parties) > 1 else "XXX"
    secteur = (parties[2] if len(parties) > 2 else "na")[:6].upper()
    sceau = hashlib.sha1(empreinte.encode("utf-8")).hexdigest()[:6].upper()
    return "TMP-{}-{}-{}".format(iso3, secteur, sceau)
    """PROJECT_ID du projet EXISTANT que ce nom designe, ou "".

    On reutilise `projets.rattacher` (la resolution d'entite deja testee du
    socle) en lui presentant le nom comme un texte : un candidat qui matche un
    alias connu N'EST PAS une decouverte, c'est un signal d'un projet suivi.
    Fonction PURE."""
    texte = str(nom or "")
    if not texte.strip():
        return ""
    # On ajoute un mot de contexte : `rattacher` exige du contexte pour les
    # alias faibles, or un nom nu ("Inga 3") n'en contient pas forcement.
    return pj.rattacher({"titre": texte, "resume": "project"}, registre)


# ===========================================================================
# 4. EXTRACTION LLM (par lots)
# ===========================================================================
PROMPT_DECOUVERTE = """Tu analyses des actualités pour une société de sûreté qui veut repérer, LE PLUS TÔT POSSIBLE, les GRANDS PROJETS (énergie, mines, transport, industrie, infrastructure) qui vont mobiliser des équipes internationales dans des pays à risque.

Pour CHAQUE actualité numérotée, identifie s'il s'agit d'un GRAND PROJET IDENTIFIABLE, et extrais :
- "projet" : le NOM PROPRE du projet (ex. "Inga 3", "Tanzania LNG", "Simandou"). Si l'actualité ne nomme aucun projet précis, renvoie "".
- "localisation" : ville, région, site ou bassin mentionné (ex. "Lindi", "Kolwezi", "offshore bloc 4"), sinon "". Renseigne-la MÊME si le projet n'a pas de nom.
- "iso3" : code ISO3 du pays où le projet se réalise, sinon "".
- "secteur" : energie | mines | transport | industrie | infrastructure
- "phase" : une des phases suivantes, ou "" si aucune n'est démontrée :
{phases}
- "acteurs" : entreprises et institutions citées (noms propres).
- "montant_musd" : montant du projet en millions de dollars si mentionné, sinon 0.
- "confiance" : 0 à 100, ta certitude qu'il s'agit bien d'un grand projet réel et identifiable.

RÈGLES STRICTES :
- N'INVENTE JAMAIS un nom de projet. Si l'article parle d'un secteur sans nommer de projet, renvoie "projet": "".
- Une opinion, une analyse générale, un débat politique ou un article de bilan ne sont PAS un projet : "projet": "".
- Un projet purement financier (prêt, garantie) sans réalisation physique : "projet": "".
- Mieux vaut renvoyer "" que de produire un projet douteux.

Actualités :
{items}

Réponds UNIQUEMENT par un tableau JSON, un objet par actualité, dans le même ordre :
[{{"n": 1, "projet": "Inga 3", "iso3": "COD", "secteur": "energie", "phase": "FUNDING_APPROVED", "acteurs": ["World Bank"], "montant_musd": 250, "confiance": 85}}, ...]
Aucun texte avant ou après."""


def construire_prompt(signaux):
    """Prompt d'un lot de decouverte. Fonction PURE."""
    items = "\n".join(
        "{}. {} | {}".format(i + 1, s.get("titre", "")[:180],
                             (s.get("resume", "") or "")[:200])
        for i, s in enumerate(signaux))
    return PROMPT_DECOUVERTE.format(phases=", ".join(pj.PHASES.keys()), items=items)


def parser_reponse(texte, taille):
    """Reponse LLM -> liste d'extractions de longueur `taille`. Tolerante :
    JSON casse ou partiel -> entrees vides plutot que perte du lot.
    Fonction PURE."""
    vide = [{"projet": "", "iso3": "", "secteur": "", "phase": "",
             "localisation": "", "acteurs": [], "montant_musd": 0,
             "confiance": 0}
            for _ in range(taille)]
    if not texte:
        return vide
    brut = str(texte).strip()
    d, f = brut.find("["), brut.rfind("]")
    if d < 0 or f <= d:
        return vide
    try:
        data = json.loads(brut[d:f + 1])
    except ValueError:
        return vide
    if not isinstance(data, list):
        return vide
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n", i + 1)) - 1
        except (TypeError, ValueError):
            n = i
        if not (0 <= n < taille):
            continue
        e = vide[n]
        e["projet"] = str(item.get("projet") or "").strip()
        e["localisation"] = str(item.get("localisation") or "").strip()[:60]
        e["iso3"] = str(item.get("iso3") or "").strip().upper()[:3]
        sect = str(item.get("secteur") or "").strip().lower()
        e["secteur"] = sect if sect in pj.INTENSITE_SECTEUR else "infrastructure"
        ph = str(item.get("phase") or "").strip().upper()
        e["phase"] = ph if ph in pj.PHASES else ""
        acteurs = item.get("acteurs") or []
        if isinstance(acteurs, list):
            e["acteurs"] = [str(a).strip().lower() for a in acteurs
                            if str(a).strip()][:10]
        try:
            e["montant_musd"] = max(0.0, float(item.get("montant_musd") or 0))
        except (TypeError, ValueError):
            e["montant_musd"] = 0
        try:
            e["confiance"] = max(0.0, min(float(item.get("confiance") or 0), 100.0))
        except (TypeError, ValueError):
            e["confiance"] = 0
    return vide


def extraire_par_lots(signaux, appel=None, max_lots=None):
    """Applique l'extraction LLM par lots. Retour : (signaux enrichis, nb lots).
    `appel(prompt) -> texte` injectable. S'arrete si le disjoncteur s'ouvre."""
    max_lots = MAX_LOTS if max_lots is None else max_lots
    appel = appel or (lambda p: bitd._appel_llm(p, modele=ted.MODELE))
    out, lots = [dict(s) for s in signaux], 0
    for debut in range(0, len(out), TAILLE_LOT):
        if lots >= max_lots:
            print("  (budget) plafond de {} lots atteint.".format(max_lots))
            break
        if ted.STATS_LLM.get("arret"):
            print("  (disjoncteur) arret LLM : extraction interrompue.")
            break
        lot = out[debut:debut + TAILLE_LOT]
        lots += 1
        try:
            reponse = appel(construire_prompt(lot))
        except Exception as e:
            print("  (info) lot {} non extrait ({}).".format(lots, str(e)[:70]))
            continue
        for s, e in zip(lot, parser_reponse(reponse, len(lot))):
            s["extraction"] = e
        time.sleep(PAUSE)
    return out, lots


# ===========================================================================
# 5. REGROUPEMENT EN CANDIDATS
# ===========================================================================
def deja_connu(nom, registre=None):
    """PROJECT_ID du projet EXISTANT que ce nom designe, ou "".

    On reutilise `projets.rattacher` (la resolution d'entite deja testee du
    socle) en lui presentant le nom comme un texte : un candidat qui matche un
    alias connu N'EST PAS une decouverte, c'est un signal d'un projet suivi.
    Fonction PURE."""
    texte = str(nom or "")
    if not texte.strip():
        return ""
    # On ajoute un mot de contexte : `rattacher` exige du contexte pour les
    # alias faibles, or un nom nu ("Inga 3") n'en contient pas forcement.
    return pj.rattacher({"titre": texte, "resume": "project"}, registre)


def regrouper(signaux, registre=None, aujourd=None):
    """Signaux extraits -> candidats de NOUVEAUX projets. Fonction PURE.

    Ecarte : les extractions vides, celles sans nom de projet, et celles qui
    designent un projet DEJA au registre (ce n'est pas une decouverte)."""
    aujourd = aujourd or date.today()
    par_cle = collections.OrderedDict()
    for s in signaux or []:
        e = s.get("extraction") or {}
        nom = str(e.get("projet") or "").strip()
        sans_nom = False
        if not nom:
            # (P4) Pas de nom officiel : on tente une EMPREINTE. Si le faisceau
            # est trop maigre pour identifier un chantier, on abandonne le
            # signal plutot que de fabriquer un projet fantome.
            cle = empreinte_sans_nom(e)
            if not cle:
                continue
            sans_nom = True
        else:
            if deja_connu(nom, registre):
                continue
            cle = cle_projet(nom, e.get("iso3"))
            if not cle:
                continue
        c = par_cle.setdefault(cle, {
            "cle": cle, "noms": collections.Counter(), "iso3": e.get("iso3", ""),
            "secteurs": collections.Counter(), "phases": [],
            "acteurs": collections.Counter(), "montants": [], "confiances": [],
            "sources": set(), "signaux": [], "dates": [],
            "sans_nom": sans_nom, "localisations": collections.Counter(),
        })
        if nom:
            c["noms"][nom] += 1
        if e.get("localisation"):
            c["localisations"][e["localisation"]] += 1
        if e.get("secteur"):
            c["secteurs"][e["secteur"]] += 1
        if e.get("phase"):
            c["phases"].append((str(s.get("date") or ""), e["phase"]))
        for a in e.get("acteurs") or []:
            c["acteurs"][a] += 1
        if e.get("montant_musd"):
            c["montants"].append(float(e["montant_musd"]))
        c["confiances"].append(float(e.get("confiance") or 0))
        c["sources"].add(_domaine(s.get("lien", "")))
        c["signaux"].append({"titre": s.get("titre", ""), "date": s.get("date", ""),
                             "lien": s.get("lien", ""), "phase": e.get("phase", "")})
        if s.get("date"):
            c["dates"].append(str(s["date"]))
    candidats = list(par_cle.values())
    candidats = fusionner(candidats)
    candidats = absorber_sans_nom(candidats)
    candidats = [_finaliser_candidat(c) for c in candidats]
    for c in candidats:
        c["confiance"] = score_confiance(c)
    candidats.sort(key=lambda c: -c["confiance"])
    return candidats


def absorber_sans_nom(candidats):
    """(P4) Quand un NOM OFFICIEL apparait, le candidat temporaire qui suivait
    le meme chantier doit disparaitre dans le projet nomme, en lui apportant
    tout son historique. Fonction PURE.

    Rapprochement : meme pays, meme secteur, et un trait commun (localisation
    partagee ou acteur partage). Sans trait commun, le candidat temporaire
    reste autonome : mieux vaut deux fiches qu'une fusion abusive."""
    nommes = [c for c in candidats if not c.get("sans_nom")]
    anonymes = [c for c in candidats if c.get("sans_nom")]
    restants = []
    for anon in anonymes:
        cible = None
        for nomme in nommes:
            if anon.get("iso3") != nomme.get("iso3"):
                continue
            if _secteur_dominant(anon) != _secteur_dominant(nomme):
                continue
            locs_a = {_norm(x) for x in (anon.get("localisations") or {})}
            locs_n = {_norm(x) for x in (nomme.get("localisations") or {})}
            acts_a = {_norm(x) for x in (anon.get("acteurs") or {})}
            acts_n = {_norm(x) for x in (nomme.get("acteurs") or {})}
            if (locs_a & locs_n) or (acts_a & acts_n):
                cible = nomme
                break
        if cible is not None:
            _absorber(cible, anon)
            cible["absorbe_temporaires"] = (
                cible.get("absorbe_temporaires", 0) + 1)
        else:
            restants.append(anon)
    return nommes + restants


def _domaine(lien):
    """Domaine d'une URL (proxy de la SOURCE). Fonction PURE."""
    m = re.search(r"https?://([^/]+)", str(lien or ""))
    return (m.group(1).lower().replace("www.", "") if m else "")


def _finaliser_candidat(c):
    c["nom"] = c["noms"].most_common(1)[0][0] if c["noms"] else ""
    c["localisation"] = (c["localisations"].most_common(1)[0][0]
                         if c.get("localisations") else "")
    if not c["nom"]:
        # (P4) PROJECT_CANDIDATE sans nom officiel : identifiant TEMPORAIRE et
        # libelle descriptif, pour qu'il soit lisible et continue de recevoir
        # des signaux en attendant d'etre nomme.
        c["sans_nom"] = True
        c["id_temporaire"] = id_temporaire(c["cle"])
        c["nom"] = "[sans nom] {} {}{}".format(
            c.get("iso3", "?"), _secteur_dominant(c),
            " · " + c["localisation"] if c["localisation"] else "")
    # Toutes les autres graphies rencontrees deviennent des alias du projet.
    c["alias_fusionnes"] = sorted({n for n in c["noms"] if n != c["nom"]})
    c["secteur"] = (c["secteurs"].most_common(1)[0][0]
                    if c["secteurs"] else "infrastructure")
    c["nb_signaux"] = len(c["signaux"])
    c["nb_sources"] = len({s for s in c["sources"] if s})
    # Poids de preuve (P5) : somme des fiabilites des sources DISTINCTES. Une
    # source ne compte qu'une fois, quel que soit le nombre d'articles publies
    # (sinon une seule redaction prolixe simulerait une convergence).
    poids, officielles = 0.0, []
    meilleure = 0.0
    vus_sources = set()
    for s in c["signaux"]:
        cle = sref.domaine_du_lien(s.get("lien", "")) or str(s.get("source") or "")
        if not cle or cle in vus_sources:
            continue
        vus_sources.add(cle)
        f = sref.fiabilite_du_signal(s)
        poids += f
        meilleure = max(meilleure, f)
        if sref.est_officielle(s):
            officielles.append(sref.decrire_source(s))
    c["poids_sources"] = round(poids, 3)
    # La SOMME ne suffit pas : trois blogs inconnus (0.40 x 3 = 1.20) pesaient
    # plus que Bloomberg + un quotidien national (0.65 + 0.50 = 1.15). On
    # retient donc aussi la QUALITE de la meilleure source du faisceau.
    c["meilleure_fiabilite"] = round(meilleure, 3)
    c["sources_officielles"] = officielles
    c["acteurs_top"] = [a for a, _ in c["acteurs"].most_common(10)]
    c["montant_musd"] = max(c["montants"]) if c["montants"] else 0
    c["confiance_llm"] = (sum(c["confiances"]) / len(c["confiances"])
                          if c["confiances"] else 0)
    dates = sorted(d for d in c["dates"] if d)
    c["premiere_detection"] = dates[0] if dates else ""
    c["derniere_maj"] = dates[-1] if dates else ""
    # Phase la plus recente observee (meme regle que le socle : chronologie).
    phases = sorted((d, p) for d, p in c["phases"] if d and p)
    c["phase"] = phases[-1][1] if phases else ""
    for champ in ("noms", "secteurs", "acteurs", "confiances", "montants",
                  "dates", "phases"):
        c.pop(champ, None)
    c["sources"] = sorted(s for s in c["sources"] if s)
    return c


def fusionner(candidats):
    """Fusionne deux candidats qui designent visiblement le MEME projet :
    meme pays, meme secteur, et au moins un jeton DISTINCTIF partage.
    ("Grand Inga" et "Inga 3" partagent "inga".) Fonction PURE.

    Opere sur les compteurs BRUTS, avant que nom et phase ne soient figes :
    sinon la fusion conserverait le nom et la phase du PREMIER candidat, et un
    projet fusionne afficherait une phase perimee (bug trouve par la
    simulation historique : Inga restait en POLITICAL_ANNOUNCEMENT).

    Le garde-fou est le caractere DISTINCTIF du jeton : "solar", "port" ou
    "tanzania" ne prouvent rien et sont exclus (MOTS_NON_DISTINCTIFS). Deux
    noms sans jeton distinctif commun ("Tanzania LNG" et "Lindi LNG") restent
    donc SEPARES : c'est une limite assumee, la curation humaine tranche."""
    def _distinctifs(c):
        source = c["noms"] if isinstance(c.get("noms"), collections.Counter) else {}
        jetons = set()
        for nom in (source or {c.get("nom", ""): 1}):
            jetons |= jetons_projet(nom)
        return {j for j in jetons if j not in MOTS_NON_DISTINCTIFS and len(j) >= 4}

    restants, fusionnes = list(candidats), []
    while restants:
        base = restants.pop(0)
        garde = []
        for autre in restants:
            meme_pays = base.get("iso3") and base["iso3"] == autre.get("iso3")
            meme_sect = base.get("secteur") == autre.get("secteur")
            if not (meme_pays and meme_sect):
                # Secteur pas encore fige : comparer les compteurs dominants.
                meme_sect = _secteur_dominant(base) == _secteur_dominant(autre)
            if meme_pays and meme_sect and (_distinctifs(base) & _distinctifs(autre)):
                base = _absorber(base, autre)
            else:
                garde.append(autre)
        restants = garde
        fusionnes.append(base)
    return fusionnes


def _secteur_dominant(c):
    s = c.get("secteurs")
    if isinstance(s, collections.Counter) and s:
        return s.most_common(1)[0][0]
    return c.get("secteur", "infrastructure")


def _absorber(base, autre):
    """Fusionne `autre` dans `base`, en additionnant les COMPTEURS bruts pour
    que nom, secteur et phase soient recalcules ensuite sur l'ensemble."""
    for champ in ("noms", "secteurs", "acteurs"):
        if isinstance(base.get(champ), collections.Counter):
            base[champ] = base[champ] + autre.get(champ, collections.Counter())
    for champ in ("phases", "signaux", "confiances", "montants", "dates"):
        base[champ] = list(base.get(champ) or []) + list(autre.get(champ) or [])
    base["sources"] = set(base.get("sources") or ()) | set(autre.get("sources") or ())
    return base


# ===========================================================================
# 6. SCORE DE CONFIANCE (0-100) -- EXPLICABLE
# ===========================================================================
def score_confiance(candidat):
    """Certitude que ce candidat est un VRAI projet, distinct et suivable.

    PONDERE PAR LA FIABILITE DES SOURCES (P5). L'ancienne regle comptait les
    sources comme interchangeables, ce qui produisait deux absurdites :
    l'annonce d'un pret par la Banque Mondiale (1 source) ne suffisait pas,
    alors que deux reprises d'une meme depeche par deux blogs suffisaient.

    Desormais c'est le POIDS DE PREUVE accumule qui compte : la somme des
    fiabilites des sources DISTINCTES (une source ne compte qu'une fois, quel
    que soit le nombre d'articles qu'elle publie). Une source officielle
    (DFI/gouvernement/agence) pese 0.85 a 0.95, un agregateur 0.40.
    Fonction PURE."""
    pts = 0
    poids = float(candidat.get("poids_sources", 0) or 0)
    # Poids de preuve : 1.0 (une source officielle seule) suffit deja a
    # atteindre l'essentiel du bareme ; 2.0 le sature.
    pts += min(45.0, 45.0 * poids / 2.0)
    n = candidat.get("nb_signaux", 0)
    pts += 15 if n >= 5 else 10 if n >= 3 else 5 if n >= 2 else 0
    pts += int(round(0.25 * float(candidat.get("confiance_llm", 0))))   # max 25
    if candidat.get("sources_officielles"):
        # Le caractere officiel est un FAIT qualitatif, pas seulement un poids.
        # Sans ce bonus, une annonce isolee de la Banque Mondiale restait sous
        # le seuil de confiance et ne pouvait donc jamais etre promue.
        pts += 12
    if candidat.get("phase"):
        pts += 10
    if candidat.get("acteurs_top"):
        pts += 5
    if candidat.get("montant_musd"):
        pts += 5
    if not candidat.get("iso3"):
        pts -= 15          # un projet sans pays n'est pas exploitable
    return max(0, min(int(round(pts)), 100))


def motifs_confiance(candidat):
    """Justification lisible du score. Fonction PURE."""
    m = ["{} signal(aux)".format(candidat.get("nb_signaux", 0)),
         "{} source(s) distincte(s)".format(candidat.get("nb_sources", 0)),
         "poids de preuve {:.2f}".format(candidat.get("poids_sources", 0) or 0),
         "confiance LLM {}%".format(int(candidat.get("confiance_llm", 0)))]
    if candidat.get("sources_officielles"):
        m.insert(0, "source officielle : "
                 + ", ".join(candidat["sources_officielles"][:2]))
    if candidat.get("phase"):
        m.append("phase identifiee ({})".format(candidat["phase"]))
    if candidat.get("acteurs_top"):
        m.append("{} acteur(s)".format(len(candidat["acteurs_top"])))
    if not candidat.get("iso3"):
        m.append("PAYS INCONNU (bloquant)")
    return m


# ===========================================================================
# 7. PROMOTION : candidat -> entree de registre
# ===========================================================================
# Seuil de POIDS DE PREUVE pour la voie convergence. Calibre sur des cas
# reels : deux agregateurs (0.40+0.40=0.80) echouent, deux quotidiens
# nationaux (0.50+0.50=1.00) passent tout juste, un quotidien plus une presse
# economique (0.50+0.65=1.15) passent nettement. Une source officielle seule
# passe par l'autre voie (>= 0.85).
SEUIL_POIDS = float(os.environ.get("RADAR_PROMO_POIDS", "1.00"))


def promouvable(candidat, seuil=None, min_signaux=None, min_sources=None,
                seuil_poids=None):
    """Le candidat est-il assez confirme pour devenir un projet suivi ?

    REGLE PONDEREE (P5), qui remplace le rigide "3 signaux ET 2 sources".
    Un pays connu et une confiance suffisante restent OBLIGATOIRES. Ensuite,
    DEUX chemins alternatifs, parce qu'une preuve peut venir de la qualite
    autant que du nombre :

      A. VOIE OFFICIELLE : au moins une source faisant autorite (DFI,
         gouvernement, agence publique) et un poids de preuve suffisant.
         Une annonce de la Banque Mondiale n'a pas besoin d'etre reprise par
         deux blogs pour etre vraie.
      B. VOIE CONVERGENCE : plusieurs signaux et plusieurs sources distinctes,
         comme avant. C'est la voie de la presse, ou aucune source ne fait
         foi a elle seule.

    Fonction PURE."""
    seuil = SEUIL_CONFIANCE if seuil is None else seuil
    min_signaux = SEUIL_SIGNAUX if min_signaux is None else min_signaux
    min_sources = SEUIL_SOURCES if min_sources is None else min_sources
    seuil_poids = SEUIL_POIDS if seuil_poids is None else seuil_poids
    if not candidat.get("iso3"):
        return False
    if candidat.get("sans_nom"):
        # (P4) Un PROJECT_CANDIDATE sans nom officiel n'accede JAMAIS au statut
        # de projet suivi : un PROJECT_ID stable suppose un nom stable. Il reste
        # vivant, recoit des signaux, et sera absorbe des qu'un nom apparait.
        return False
    if candidat.get("confiance", 0) < seuil:
        return False
    poids = float(candidat.get("poids_sources", 0) or 0)
    meilleure = float(candidat.get("meilleure_fiabilite", 0) or 0)
    voie_officielle = bool(candidat.get("sources_officielles")) and poids >= 0.85
    # La convergence exige AUSSI qu'au moins une source soit identifiee et
    # credible (>= presse generaliste). Sans ce garde-fou, trois blogs inconnus
    # (0.40 x 3 = 1.20) l'emporteraient sur Bloomberg + un quotidien (1.15).
    voie_convergence = (candidat.get("nb_signaux", 0) >= min_signaux
                        and candidat.get("nb_sources", 0) >= min_sources
                        and poids >= seuil_poids
                        and meilleure >= 0.50)
    return voie_officielle or voie_convergence


def generer_project_id(nom, iso3):
    """PROJECT_ID stable et lisible : jetons distinctifs + ISO3.
    "Inga 3" et "Inga III" donnent le MEME id ("INGA3_COD") : sans cette
    normalisation, deux graphies du meme projet creeraient deux projets.
    Fonction PURE."""
    jetons = [_ROMAINS.get(j, j) for j in _norm(nom).split()
              if j not in MOTS_GENERIQUES]
    slug = "".join(jetons)[:24].upper() or "PROJET"
    return "{}_{}".format(slug, str(iso3 or "XXX").upper())


def alias_faible_auto(nom):
    """Alias FAIBLE derive du nom, ou [] si aucun jeton n'est assez distinctif.

    Justification : sans lui, "AECOM selected for Inga studies" ne se rattache
    pas au projet promu "Inga 3" (bug trouve par la simulation historique).
    Le risque d'homonymie est contenu par DEUX garde-fous cumulatifs :
      - le jeton doit etre DISTINCTIF (>= 4 caracteres, absent de
        MOTS_NON_DISTINCTIFS) : "inga" passe, "lng"/"tanzania"/"port" non ;
      - `projets.rattacher` n'accepte un alias faible QUE si le texte contient
        aussi un mot de contexte projet (regle deja testee du socle).
    Un seul alias faible au maximum, pour rester conservateur. Fonction PURE."""
    jetons = [_ROMAINS.get(j, j) for j in _norm(nom).split()]
    distinctifs = [j for j in jetons
                   if len(j) >= 4 and j not in MOTS_NON_DISTINCTIFS]
    # Un nom deja reduit a un seul jeton EST son propre alias fort : inutile.
    if len(jetons) <= 1 or not distinctifs:
        return []
    return [distinctifs[0]]


def entree_registre(candidat):
    """Candidat promu -> entree AU FORMAT `projets_reference`. Fonction PURE.

    C'est le point de jonction avec le socle : la valeur de retour est
    directement consommable par `projets.construire_projets(registre=...)`."""
    nom = candidat["nom"]
    alias = {nom.lower()}
    for a in candidat.get("alias_fusionnes", []):
        alias.add(a.lower())
    return {
        "project_id": generer_project_id(nom, candidat.get("iso3")),
        "libelle": nom,
        "pays": candidat.get("pays", "") or candidat.get("iso3", ""),
        "iso3": candidat.get("iso3", ""),
        "secteur": candidat.get("secteur", "infrastructure"),
        "valeur_musd": candidat.get("montant_musd", 0),
        "alias": sorted(alias),
        "alias_faibles": alias_faible_auto(nom),
        "acteurs": candidat.get("acteurs_top", []),
        "origine": "decouverte",
        "confiance": candidat.get("confiance", 0),
        "premiere_detection": candidat.get("premiere_detection", ""),
    }


def promouvoir(candidats, registre=None, seuil=None):
    """Candidats -> (entrees promues, candidats restes en attente).
    Ecarte au passage tout candidat devenu redondant avec le registre.
    Fonction PURE."""
    promus, attente = [], []
    connus = ref.charger_registre(registre)
    for c in candidats:
        if not promouvable(c, seuil=seuil) or deja_connu(c["nom"], registre):
            attente.append(c)
            continue
        e = entree_registre(c)
        if any(p["project_id"] == e["project_id"] for p in connus + promus):
            attente.append(c)
            continue
        promus.append(e)
    return promus, attente


def registre_enrichi(promus, registre=None):
    """Registre cure + projets decouverts : ce que l'on passe au socle.
    Fonction PURE."""
    return ref.charger_registre(registre) + [dict(p) for p in promus]


# ===========================================================================
# 8. COLLECTE (I/O tolerant, injectable)
# ===========================================================================
def collecter_referentiel(pays_liste, fetch=None, session=None, fenetres=None):
    """Collecte multilingue pilotee par le REFERENTIEL pays (P3, P7, P8).
    Une requete par langue pertinente x par fenetre temporelle (P9), avec
    declencheurs traduits et edition Google News locale. I/O tolerant."""
    if fetch is None:
        sess = session or ted.session_robuste()

        def fetch(url):
            rep = sess.get(url, timeout=30)
            rep.raise_for_status()
            return rep.text

    fenetres = fenetres or [None]
    articles = []
    for pays in pays_liste:
        for fenetre in fenetres:
            for langue, url in urls_du_pays(pays, fenetre=fenetre):
                try:
                    lot = bitd.parser_rss(fetch(url))[:MAX_ARTICLES]
                except Exception as e:
                    print("  (info) {} [{}] echec ({}).".format(
                        pays["iso3"], langue, str(e)[:50]))
                    lot = []
                for a in lot:
                    a["iso3_requete"] = pays["iso3"]
                    a["langue_requete"] = langue
                articles.extend(lot)
                time.sleep(PAUSE)
    return articles


def collecter(pays, fetch=None, session=None):
    """Articles bruts pour une liste de pays. I/O tolerant."""
    if fetch is None:
        sess = session or ted.session_robuste()

        def fetch(url):
            rep = sess.get(url, timeout=30)
            rep.raise_for_status()
            return rep.text

    articles = []
    for nom_pays, iso3, langue in pays:
        try:
            lot = bitd.parser_rss(fetch(url_pays(nom_pays, langue)))[:MAX_ARTICLES]
        except Exception as e:
            print("  (info) {} : requete echouee ({}).".format(nom_pays, str(e)[:60]))
            lot = []
        for a in lot:
            a["iso3_requete"] = iso3
        articles.extend(lot)
        time.sleep(PAUSE)
    return articles


def preparer(articles, vus=None, aujourd=None, backfill=None):
    """Articles -> signaux dedupliques et pre-filtres (avant tout appel LLM).
    En BACKFILL la fraicheur est ignoree : on reconstruit justement le passe.
    Fonction PURE."""
    backfill = BACKFILL if backfill is None else backfill
    vus, locaux, out = set(vus or ()), set(), []
    for a in articles or []:
        lien = a.get("lien", "")
        if not lien:
            continue
        ident = bitd.id_article(lien)
        if ident in vus or ident in locaux:
            continue
        if bitd.bruit_evident(a):
            continue
        if not backfill and not bitd.article_frais(a, aujourd=aujourd,
                                                   jours=JOURS_FRAICHEUR):
            continue
        locaux.add(ident)
        out.append({"id": ident, "titre": a.get("titre", ""),
                    "resume": a.get("resume", ""),
                    "date": _date_iso(a.get("date", "")),
                    "lien": lien, "iso3_requete": a.get("iso3_requete", "")})
    return out


def _date_iso(brut):
    import email.utils
    if not brut:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(brut)
        return dt.date().isoformat() if dt else ""
    except Exception:
        return ""


# ===========================================================================
# 9. SORTIE
# ===========================================================================
COLONNES = [
    "date_maj", "statut", "nom", "project_id_propose", "iso3", "secteur",
    "phase", "confiance", "motifs", "nb_signaux", "nb_sources",
    "montant_musd", "acteurs", "premiere_detection", "derniere_maj",
    "sources", "signaux_json",
]


def ligne_candidat(c, promu=False):
    """Candidat -> ligne du Sheet. Fonction PURE."""
    v = {
        "date_maj": date.today().isoformat(),
        "statut": "promu" if promu else "candidat",
        "nom": c.get("nom", ""),
        "project_id_propose": generer_project_id(c.get("nom", ""), c.get("iso3")),
        "iso3": c.get("iso3", ""), "secteur": c.get("secteur", ""),
        "phase": c.get("phase", ""), "confiance": c.get("confiance", 0),
        "motifs": " · ".join(motifs_confiance(c)),
        "nb_signaux": c.get("nb_signaux", 0), "nb_sources": c.get("nb_sources", 0),
        "montant_musd": c.get("montant_musd", 0),
        "acteurs": ", ".join(c.get("acteurs_top", [])),
        "premiere_detection": c.get("premiere_detection", ""),
        "derniere_maj": c.get("derniere_maj", ""),
        "sources": ", ".join(c.get("sources", [])),
        "signaux_json": json.dumps(c.get("signaux", [])[:20], ensure_ascii=False),
    }
    return [str(v.get(col, "")) for col in COLONNES]


def ecrire(candidats, promus_noms=(), sheet_id=None, fichier=None):
    """Ecrit l'onglet des candidats (etat courant : remplacement complet)."""
    lignes = [ligne_candidat(c, c.get("nom") in set(promus_noms))
              for c in candidats]
    if sheet_id and fichier:
        try:
            import radar_resilience
            import signaux_prives as sp
            classeur = sp._ouvrir_classeur(sheet_id, fichier)
            try:
                feuille = classeur.worksheet(NOM_ONGLET)
            except Exception:
                feuille = classeur.add_worksheet(title=NOM_ONGLET, rows=500,
                                                 cols=len(COLONNES))
            radar_resilience.avec_retry(lambda: feuille.clear(), "candidats clear")
            radar_resilience.avec_retry(
                lambda: feuille.update(values=[COLONNES] + lignes,
                                       range_name="A1"), "candidats update")
            print("  ecrit : {} candidat(s) dans '{}'.".format(len(lignes), NOM_ONGLET))
        except Exception as e:
            print("  (info) ecriture Sheet impossible ({}).".format(str(e)[:80]))
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, l)) for l in lignes]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(str(e)[:60]))
    return len(lignes)


# ===========================================================================
# 10. ORCHESTRATION
# ===========================================================================
def main():
    if not ACTIVER:
        print("(info) Project Discovery desactive (RADAR_DECOUVERTE_PROJETS != 1).")
        return
    import radar_etat

    print("=== PROJECT DISCOVERY -- decouverte de projets inconnus ===")
    stats = pref.statistiques()
    print("  referentiel : {} pays ({}), langues {}".format(
        stats["total"], stats["par_niveau"], sorted(stats["par_langue"])))
    curseur, vus = radar_etat.charger()
    curseur, vus = (curseur or 0), list(vus or [])
    # Cadence (P8) : faute d'un journal des passages par pays, on derive un
    # dernier passage approximatif du curseur de rotation. Un pays "suivi" est
    # donc du a chaque run, un "global_watch" une fois par cycle.
    plafond = PAYS_PAR_RUN
    fenetre = pref.selection_du_run({}, plafond=plafond) if curseur == 0 else None
    if fenetre is None:
        tous = pref.charger_pays()
        debut = curseur % len(tous)
        prioritaires = [p for p in tous if p["niveau"] == "suivi"]
        reste = (tous[debut:] + tous[:debut])
        vus_iso = {p["iso3"] for p in prioritaires}
        fenetre = (prioritaires
                   + [p for p in reste if p["iso3"] not in vus_iso])[:plafond]
    print("  {} pays interroges : {}".format(
        len(fenetre), ", ".join("{}[{}]".format(p["iso3"], p["niveau"][:4])
                                for p in fenetre)))

    fenetres = None
    if BACKFILL:
        fenetres = fenetres_backfill()
        print("  MODE BACKFILL : {} mois explores en {} fenetre(s) datee(s).".format(
            BACKFILL_MOIS, len(fenetres)))
    articles = collecter_referentiel(fenetre, fenetres=fenetres)
    signaux = preparer(articles, vus=vus)
    print("  presse : {} article(s), {} nouveau(x).".format(len(articles), len(signaux)))

    # (P1) Les collecteurs DFI deja en place alimentent LE MEME pipeline :
    # leurs avis sont convertis en signaux et concatenes ici, avant l'etape
    # LLM. Aucun pipeline parallele, aucune recollecte.
    try:
        import adaptateurs_dfi as adfi
        import radar_dashboard as dash
        leads, _ = dash.charger_leads(os.environ.get("TED_SHEET_ID"),
                                      os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"))
        signaux_dfi = adfi.signaux_depuis_leads(leads, vus=vus)
        rep = adfi.repartition(signaux_dfi)
        print("  DFI    : {} signal(aux) {} (poids cumule {}).".format(
            rep["total"], rep["par_source"], rep["poids_cumule"]))
        signaux = signaux + signaux_dfi
    except Exception as e:
        print("  (info) signaux DFI indisponibles ({}) : presse seule.".format(
            str(e)[:70]))

    extraits, lots = extraire_par_lots(signaux)
    print("  {} lot(s) LLM.".format(lots))

    candidats = regrouper(extraits)
    promus, attente = promouvoir(candidats)
    print("  {} candidat(s) : {} promu(s), {} en attente.".format(
        len(candidats), len(promus), len(attente)))
    for e in promus:
        print("    PROMU  [{}] {} ({}, {}) confiance {}".format(
            e["project_id"], e["libelle"], e["iso3"], e["secteur"], e["confiance"]))
    for c in attente[:8]:
        print("    attente  {:<34} confiance {:>3} · {}".format(
            (c.get("nom") or "?")[:34], c.get("confiance", 0),
            " · ".join(motifs_confiance(c)[:3])))

    ecrire(candidats, [e["libelle"] for e in promus],
           os.environ.get("TED_SHEET_ID"),
           os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"))
    radar_etat.sauver(curseur + len(fenetre), vus, [s["id"] for s in signaux])
    if promus:
        print("\n  Les projets promus sont a AJOUTER a projets_reference.py "
              "(revue humaine) : le registre cure reste la source de verite.")
    ted.sortie_selon_sante_llm("decouverte_projets")


if __name__ == "__main__":
    main()
