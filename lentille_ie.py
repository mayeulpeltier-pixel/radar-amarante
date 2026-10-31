# -*- coding: utf-8 -*-
"""
Radar Amarante - LENTILLE IE (chantier IE-3, 30/10/2026).

Une lentille = la maniere dont UNE expertise d'Amarante lit un avis : son
lexique, son prompt, sa grille d'extraction et son bareme. La lentille IE
sert le pole ARISE (intelligence economique & conformite). Elle ne touche a
AUCUN module existant : les collecteurs IE l'importent, les collecteurs
Operations l'ignorent. Le bareme surete du coeur (ted_complet_v14) reste
strictement inchange.

Meme doctrine que le coeur : LE LLM FOURNIT LES FAITS, LE CODE APPLIQUE LA
DOCTRINE. Le modele classe et decrit ; il ne note jamais.

PERIMETRE (decision D1 du 30/10/2026)
-------------------------------------
  affiche : veille, cartographie d'acteurs, etudes strategiques, due diligence
            et evaluation des tiers, conformite anticorruption, formation IE ;
  ombre   : investigations, contrefacon et fraude, e-reputation, recouvrement
            d'actifs, Audit 360 -> classes et ecrits, jamais affiches, pour
            mesurer ce qu'un elargissement apporterait (revue a 8 semaines) ;
  hors    : etudes geopolitiques et observatoires (future lentille Analyste),
            sondages et statistiques, audit cyber, tout le reste.

CALIBRAGE
---------
Lexique : sondes IE-0 (BOAMP, 8 165 avis) et IE-1 (DECP, 3,36 M lignes),
30/10/2026. Seuils du bareme : HYPOTHESES de depart, a valider avec ARISE et
a recaler sur les issues gagne/perdu.

FLAG : RADAR_IE=1 active les collecteurs IE (defaut OFF). Ce module, lui,
est pur : l'importer n'a aucun effet de bord.
"""

import json
import os
import re
import time
import unicodedata

ACTIF = os.environ.get("RADAR_IE", "0") not in ("", "0")

# Modeles : alignes sur le coeur quand il est importable, sinon variables
# d'environnement. Import paresseux et resilient (regle du projet).
def _modeles():
    try:
        import ted_complet_v14 as ted
        return (getattr(ted, "MODELE", None), getattr(ted, "MODELE_RAFFINEMENT", None))
    except Exception:  # noqa: BLE001
        return (None, None)


_M, _MR = _modeles()
MODELE = os.environ.get("RADAR_IE_MODELE") or _M or "claude-haiku-4-5"
MODELE_RAFFINEMENT = (os.environ.get("RADAR_IE_MODELE_RAFFINEMENT") or _MR
                      or "claude-sonnet-4-6")
URL_API = "https://api.anthropic.com/v1/messages"

# ===========================================================================
# 1. LEXIQUE ET PRE-FILTRE (deterministe, avant tout appel LLM)
# ===========================================================================
TERMES = [
    # --- francais, termes forts ---
    ("intelligence economique", "fort"), ("intelligence strategique", "fort"),
    ("securite economique", "fort"), ("renseignement economique", "fort"),
    ("veille strategique", "fort"), ("veille concurrentielle", "fort"),
    ("veille economique", "fort"), ("veille sectorielle", "fort"),
    ("due diligence", "fort"), ("diligence raisonnable", "fort"),
    ("evaluation des tiers", "fort"), ("integrite des tiers", "fort"),
    ("cartographie des acteurs", "fort"), ("anticorruption", "fort"),
    ("anti-corruption", "fort"), ("probite", "fort"), ("sapin ii", "fort"),
    ("sapin 2", "fort"), ("osint", "fort"),
    # --- anglais (TED institutions UE, UNGM, bailleurs) ---
    ("integrity due diligence", "fort"), ("background check", "fort"),
    ("background screening", "fort"), ("vendor vetting", "fort"),
    ("know your customer", "fort"), ("kyc", "fort"),
    ("open-source intelligence", "fort"), ("open source intelligence", "fort"),
    ("stakeholder mapping", "fort"), ("political economy analysis", "fort"),
    ("anti-fraud", "fort"), ("asset tracing", "fort"),
    # --- ambigus : comptes a part, jamais retenus seuls ---
    ("veille technologique", "a_mesurer"), ("veille reglementaire", "a_mesurer"),
    ("etudes sectorielles", "a_mesurer"), ("intelligence territoriale", "a_mesurer"),
    ("revue de presse", "a_mesurer"), ("veille media", "a_mesurer"),
    ("business intelligence", "a_mesurer"), ("market intelligence", "a_mesurer"),
    ("third party monitoring", "a_mesurer"),
]
BRUIT = ("sondage", "satisfaction", "touris", "statistique", "barometre",
         "frequentation", "panel", "enquete telephonique", "recensement",
         "comptage", "evaluation de politique", "evaluation des politiques",
         "evaluation du programme", "evaluation d'impact", "hackathon",
         # IE-6 (30/10) : organisation d'evenements cyber ("CTF OSINT").
         "capture the flag", " ctf ", "rencontres professionnelles de la cyber")
BRUIT_COMBINE = (("due diligence", "immobili"),)
ANALYSTE = ("observatoire", "geopolitique", "geostrategique", "prospective")
NOYAU_IE = ("intelligence economique", "intelligence strategique",
            "securite economique", "due diligence", "anticorruption",
            "anti-corruption")
# Clauses contractuelles recurrentes (IE-0 : ~165 faux positifs Reunion,
# CCI Bordeaux, GHT) : ces termes ne comptent JAMAIS hors du titre.
TERMES_TITRE_SEULEMENT = ("anticorruption", "anti-corruption", "sapin ii",
                          "sapin 2", "probite")


def normaliser_texte(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("‘", "'").lower()
    return re.sub(r"\s+", " ", s).strip()


def _contient_mot(texte_n, motif):
    return re.search(r"(?<![a-z0-9])" + re.escape(motif) + r"(?![a-z0-9])",
                     texte_n) is not None


def termes_trouves(texte_n):
    return [(m, n) for m, n in TERMES if _contient_mot(texte_n, m)]


def _bruit_present(texte_n):
    """Vrai si un terme de BRUIT est present. Une entree ENCADREE d'espaces
    (ex. " ctf ") est traitee comme un MOT ENTIER, pour matcher aussi en fin
    de titre ("... osint ctf"), que la recherche par sous-chaine ratait
    (sonde IE-5, 31/10). Les autres entrees restent des sous-chaines
    volontaires ("touris" -> tourisme/touristique)."""
    for x in BRUIT:
        noyau = x.strip()
        if x != noyau:
            if _contient_mot(texte_n, noyau):
                return True
        elif x in texte_n:
            return True
    return False


def prefiltre(titre, corps=""):
    """Classe deterministe d'un avis, AVANT tout appel LLM.

    Renvoie (classe, termes). Classes, par priorite :
      hors_analyste > bruit > ie_titre > ie_corps > a_mesurer > sans_terme.
    Seules `ie_titre` et `ie_corps` partent au LLM."""
    t = normaliser_texte(titre)
    c = normaliser_texte(corps)
    t_titre = termes_trouves(t)
    t_corps = [(m, n) for m, n in termes_trouves(c)
               if m not in TERMES_TITRE_SEULEMENT]
    termes = sorted({m for m, _ in t_titre + t_corps})
    if any(x in t for x in ANALYSTE) and not any(_contient_mot(t, m) for m in NOYAU_IE):
        return "hors_analyste", termes
    if _bruit_present(t) or any(
            _contient_mot(t, a) and b in t for a, b in BRUIT_COMBINE):
        return "bruit", termes
    if any(n == "fort" for _, n in t_titre):
        return "ie_titre", termes
    if any(n == "fort" for _, n in t_corps):
        return "ie_corps", termes
    if t_titre or t_corps:
        return "a_mesurer", termes
    return "sans_terme", termes


def merite_llm(classe):
    return classe in ("ie_titre", "ie_corps")


# ===========================================================================
# 2. GRILLE D'EXTRACTION (faits demandes au modele)
# ===========================================================================
FAMILLES_AFFICHEES = ("veille_strategique", "cartographie_acteurs",
                      "etude_strategique", "due_diligence_tiers",
                      "conformite_anticorruption", "formation_ie")
FAMILLES_OMBRE = ("investigation_contentieux", "contrefacon_fraude",
                  "e_reputation", "recouvrement_actifs", "audit_360")
FAMILLES_HORS = ("etude_geopolitique", "sondage_statistique", "audit_cyber",
                 "hors_ie")
FAMILLES = FAMILLES_AFFICHEES + FAMILLES_OMBRE + FAMILLES_HORS

TYPES_ACHETEUR = ("region_agence_dev", "collectivite", "chambre_consulaire",
                  "etat_ministere", "defense_securite", "etablissement_public",
                  "agence_ue", "onu_bailleur", "entreprise", "autre")
FORMATS = ("accord_cadre_multi", "accord_cadre_mono", "marche_ponctuel", "inconnu")
BARRIERES = ("faible", "moyenne", "forte")

PROMPT_IE = """Tu es analyste commercial pour ARISE, le pole intelligence economique et conformite d'Amarante (societe francaise). ARISE vend des PRESTATIONS INTELLECTUELLES : veille strategique et concurrentielle, cartographie d'acteurs et de reseaux d'affaires, etudes strategiques et sectorielles, due diligence et evaluation des tiers (KYC, honorabilite), audit et mise en place de dispositifs anticorruption (Sapin II), formation et sensibilisation a l'intelligence economique. Atout distinctif : un reseau d'experts et de filiales a l'etranger.

ARISE ne vend PAS de logiciel, de base de donnees, d'abonnement presse ni d'outil de veille clef en main.

On te donne un avis de marche public. Decris-le factuellement. Tu ne notes pas l'opportunite : tu remplis la grille.

famille_offre (une seule valeur) :
- veille_strategique : veille strategique, concurrentielle, economique, sectorielle (prestation humaine)
- cartographie_acteurs : cartographie d'acteurs, de reseaux, de parties prenantes
- etude_strategique : etude strategique, sectorielle, de marche a visee decisionnelle, cellule ou dispositif d'intelligence economique
- due_diligence_tiers : due diligence, evaluation ou integrite des tiers, KYC, verification d'honorabilite
- conformite_anticorruption : cartographie des risques de corruption, dispositif Sapin II, audit anticorruption, appui a l'AFA
- formation_ie : formation ou sensibilisation a l'intelligence ou a la securite economique
- investigation_contentieux : enquete, investigation, appui a un contentieux ou un arbitrage
- contrefacon_fraude : lutte contre la contrefacon ou la fraude
- e_reputation : e-reputation, veille d'image en ligne
- recouvrement_actifs : recherche ou recouvrement d'actifs
- audit_360 : audit global de surete et de conformite d'une cible d'acquisition
- etude_geopolitique : observatoire, etude geopolitique ou prospective internationale
- sondage_statistique : sondage, enquete statistique, satisfaction, frequentation
- audit_cyber : audit ou prestation de cybersecurite
- hors_ie : tout le reste. DANS LE DOUTE, hors_ie.

type_acheteur : region_agence_dev | collectivite | chambre_consulaire | etat_ministere | defense_securite | etablissement_public | agence_ue | onu_bailleur | entreprise | autre
format : accord_cadre_multi (plusieurs titulaires) | accord_cadre_mono | marche_ponctuel | inconnu
barrieres_entree : faible | moyenne | forte (references ou chiffre d'affaires exiges eleves, habilitation, lots tres volumineux)
achat_outil : true si l'objet principal est un logiciel, une plateforme, une base de donnees ou un abonnement, false si c'est une prestation intellectuelle
dimension_internationale : true si la prestation porte sur des pays etrangers, l'export ou l'internationalisation
sensibilite : true si defense, securite economique, actifs ou technologies strategiques
confiance : de 0.0 a 1.0, ta certitude sur famille_offre

N'invente rien : si une information manque, choisis inconnu, false, ou la valeur la plus prudente.

AVIS
Source : {source}
Acheteur : {acheteur}
Titre : {titre}
Description : {description}
Montant ou duree connus : {montant}

Reponds UNIQUEMENT en JSON valide, sans texte autour :
{{"famille_offre": "...", "type_acheteur": "...", "format": "...", "barrieres_entree": "...", "achat_outil": false, "dimension_internationale": false, "sensibilite": false, "resume": "une phrase factuelle", "confiance": 0.0}}"""


def construire_prompt(avis):
    desc = str(avis.get("description") or "")[:2500]
    return PROMPT_IE.format(
        source=avis.get("source") or "n.c.", acheteur=avis.get("acheteur") or "n.c.",
        titre=str(avis.get("titre") or "")[:400], description=desc or "n.c.",
        montant=avis.get("montant") or "n.c.")


# ===========================================================================
# 3. APPEL LLM ET VALIDATION (ne leve jamais)
# ===========================================================================
def _appel_http(prompt, modele):
    import requests
    cle = os.environ.get("ANTHROPIC_API_KEY")
    if not cle:
        raise RuntimeError("ANTHROPIC_API_KEY absente")
    rep = requests.post(URL_API, timeout=60, headers={
        "x-api-key": cle, "anthropic-version": "2023-06-01",
        "content-type": "application/json"},
        json={"model": modele, "max_tokens": 400, "temperature": 0,
              "messages": [{"role": "user", "content": prompt}]})
    if rep.status_code >= 400:
        raise RuntimeError("HTTP {} : {}".format(rep.status_code, rep.text[:200]))
    blocs = rep.json().get("content") or []
    return "".join(b.get("text", "") for b in blocs if b.get("type") == "text")


def parser_json(texte):
    """Extrait le premier objet JSON d'une reponse (blocs ``` toleres)."""
    if not texte:
        return None
    t = re.sub(r"```(?:json)?", "", str(texte))
    debut, fin = t.find("{"), t.rfind("}")
    if debut < 0 or fin <= debut:
        return None
    try:
        v = json.loads(t[debut:fin + 1])
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _enum(v, valeurs, defaut):
    v = str(v or "").strip().lower()
    return v if v in valeurs else defaut


def _bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "oui", "yes")


def valider(brut):
    """Extraction brute -> extraction propre. Toute valeur hors grille est
    ramenee a la valeur prudente : une famille inconnue devient hors_ie
    (un faux negatif coute moins cher qu'un faux positif affiche)."""
    if not isinstance(brut, dict):
        return None
    try:
        conf = float(brut.get("confiance", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "famille_offre": _enum(brut.get("famille_offre"), FAMILLES, "hors_ie"),
        "type_acheteur": _enum(brut.get("type_acheteur"), TYPES_ACHETEUR, "autre"),
        "format": _enum(brut.get("format"), FORMATS, "inconnu"),
        "barrieres_entree": _enum(brut.get("barrieres_entree"), BARRIERES, "moyenne"),
        "achat_outil": _bool(brut.get("achat_outil")),
        "dimension_internationale": _bool(brut.get("dimension_internationale")),
        "sensibilite": _bool(brut.get("sensibilite")),
        "resume": str(brut.get("resume") or "")[:300],
        "confiance": max(0.0, min(conf, 1.0)),
    }


def extraire(avis, modele=None, appel=None, pause=0.0):
    """Avis -> extraction validee, ou None si l'appel ou le JSON echoue.
    Ne leve jamais : un avis rate repartira au run suivant."""
    appel = appel or _appel_http
    try:
        texte = appel(construire_prompt(avis), modele or MODELE)
    except Exception as e:  # noqa: BLE001
        print("  (info) extraction IE echouee ({}).".format(str(e)[:120]))
        return None
    finally:
        if pause:
            time.sleep(pause)
    return valider(parser_json(texte))


# ===========================================================================
# 4. BAREME (doctrine en code)
# ===========================================================================
SEUIL_CONTACTER = 6.0      # hypothese, a recaler sur les issues
SEUIL_SURVEILLER = 4.0
PLANCHER_ADEQUATION = 3.0  # coupe-circuit, miroir du plancher surete
CONFIANCE_ESCALADE = 0.7

BASE_FAMILLE = {
    "cartographie_acteurs": 9.0, "due_diligence_tiers": 9.0,
    "veille_strategique": 8.0, "conformite_anticorruption": 8.0,
    "etude_strategique": 7.0, "formation_ie": 7.0,
    "investigation_contentieux": 7.0, "contrefacon_fraude": 6.0,
    "e_reputation": 5.0, "recouvrement_actifs": 6.0, "audit_360": 7.0,
}
POIDS_ACHETEUR = {
    "region_agence_dev": 4.0, "etat_ministere": 3.5, "defense_securite": 3.5,
    "chambre_consulaire": 3.0, "collectivite": 3.0, "etablissement_public": 3.0,
    "entreprise": 3.0, "agence_ue": 2.5, "onu_bailleur": 2.5, "autre": 2.0,
}
POIDS_FORMAT = {"accord_cadre_multi": 3.0, "accord_cadre_mono": 2.5,
                "marche_ponctuel": 1.5, "inconnu": 1.5}
POIDS_BARRIERE = {"faible": 2.0, "moyenne": 1.0, "forte": 0.0}


def perimetre(famille):
    if famille in FAMILLES_AFFICHEES:
        return "affiche"
    if famille in FAMILLES_OMBRE:
        return "ombre"
    return "hors"


def noter(extraction, contexte=None):
    """Extraction (+ contexte connu du collecteur) -> note expliquee.

    `contexte` (optionnel) : {"amarante_sortant": bool, "titulaires_sortants":
    [...]} fourni par les attributions (IE-6). Le montant n'entre PAS dans le
    score (regle du projet : pas de potentiel en euros)."""
    ctx = contexte or {}
    if extraction is None:
        return {"adequation": 0.0, "commercial": 0.0, "score": 0.0,
                "perimetre": "hors", "action": "ignorer",
                "motifs": ["extraction echouee"]}
    fam = extraction["famille_offre"]
    per = perimetre(fam)
    motifs = []

    adequation = BASE_FAMILLE.get(fam, 0.0)
    motifs.append("famille {} ({:+.0f})".format(fam, adequation))
    if per != "hors":
        if extraction["dimension_internationale"]:
            adequation += 1.0
            motifs.append("dimension internationale (+1)")
        if extraction["sensibilite"]:
            adequation += 1.0
            motifs.append("sujet sensible (+1)")
        if extraction["achat_outil"]:
            adequation -= 4.0
            motifs.append("achat d'outil, pas une prestation (-4)")
    adequation = max(0.0, min(adequation, 10.0))

    commercial = POIDS_ACHETEUR[extraction["type_acheteur"]]
    commercial += POIDS_FORMAT[extraction["format"]]
    commercial += POIDS_BARRIERE[extraction["barrieres_entree"]]
    motifs.append("acheteur {}, {}, barrieres {}".format(
        extraction["type_acheteur"], extraction["format"],
        extraction["barrieres_entree"]))
    if ctx.get("amarante_sortant"):
        commercial += 1.5
        motifs.append("Amarante titulaire sortant (+1.5)")
    commercial = max(0.0, min(commercial, 10.0))

    score = round(0.5 * adequation + 0.5 * commercial, 1)

    if per == "hors":
        action = "ignorer"
        motifs.append("hors perimetre IE")
    elif per == "ombre":
        action = "ombre"
        motifs.append("perimetre ombre : journalise, non affiche (D1)")
    elif adequation < PLANCHER_ADEQUATION:
        action = "ignorer"
        motifs.append("adequation sous le plancher")
    elif score >= SEUIL_CONTACTER and not extraction["achat_outil"]:
        action = "contacter"
    elif score >= SEUIL_SURVEILLER:
        action = "surveiller"
    else:
        action = "ignorer"
    return {"adequation": round(adequation, 1), "commercial": round(commercial, 1),
            "score": score, "perimetre": per, "action": action, "motifs": motifs}


def merite_escalade(extraction, note):
    """Rejouer avec le modele le plus capable : leads prometteurs, doutes du
    modele, et perimetre ombre (c'est la que se joue la revue a 8 semaines)."""
    if extraction is None:
        return False
    return ((note["perimetre"] == "affiche" and note["score"] >= 5.0)
            or extraction["confiance"] < CONFIANCE_ESCALADE
            or note["perimetre"] == "ombre")


def analyser(avis, contexte=None, appel=None, pause=0.0):
    """Chaine complete pour UN avis deja pre-filtre : extraction, note,
    escalade eventuelle. Le resultat Sonnet ne remplace celui de Haiku que
    s'il est exploitable (lecon du coeur : ne pas enterrer un bon lead)."""
    ext = extraire(avis, appel=appel, pause=pause)
    note = noter(ext, contexte)
    escalade = False
    if merite_escalade(ext, note):
        ext2 = extraire(avis, modele=MODELE_RAFFINEMENT, appel=appel, pause=pause)
        if ext2 is not None:
            ext, note, escalade = ext2, noter(ext2, contexte), True
    return {"extraction": ext, "note": note, "escalade": escalade}
