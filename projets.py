# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- MOTEUR PROJECT INTELLIGENCE (couches 3 et 4).
===============================================================================

CE QUE FAIT CE MODULE
---------------------
Transforme un flot de SIGNAUX epars (articles, avis, attributions) en PROJETS
suivis dans le temps :

    signaux  ->  rattachement (PROJECT_ID)  ->  cycle de vie  ->  timeline
                                             ->  score de maturite
                                             ->  score d'opportunite Amarante

TOUT EST PUR ET TESTABLE OFFLINE. Aucun reseau, aucun LLM, aucune ecriture ici :
la classification de phase par LLM (Haiku, en lots) est faite EN AMONT et
arrive dans le champ `phase` du signal. Ce module ne fait que du raisonnement.

LES DEUX SCORES NE DOIVENT JAMAIS ETRE CONFONDUS
-------------------------------------------------
  - MATURITE  : ou en est le PROJET (idee ... exploitation). Independant
    d'Amarante. Un projet peut etre tres mature et sans interet commercial.
  - OPPORTUNITE AMARANTE : ce que le projet peut rapporter a Amarante
    (taille, risque pays, expatries attendus, phase, contractors presents).
    Toujours EXPLICABLE : chaque point est justifie par un motif affichable.

REGLE D'HISTORIQUE : ON N'ECRASE JAMAIS
----------------------------------------
Chaque signal date portant une phase entre dans l'historique. La phase
COURANTE est celle du signal DATE LE PLUS RECENT, pas la plus avancee jamais
vue : un projet peut caler ou reculer (le corpus reel contient "Tanzania LNG
project talks drag on" et "Impairment at Tanzania LNG Project", qui sont des
reculs). On conserve aussi `phase_max_atteinte` pour garder la memoire du
point le plus avance.

FORMAT D'UN SIGNAL EN ENTREE
-----------------------------
    {"titre":..., "resume":..., "date": "YYYY-MM-DD", "lien":...,
     "phase": "FID" ou "", "acteurs": [...], "source": "news|BM|ATTRIB", ...}
Le rattachement se fait sur le texte ; `project_id` peut deja etre fourni
(ex. avis BM porteur d'un proj_id), auquel cas il est respecte tel quel.
"""

import collections
import datetime
import re
import unicodedata

import projets_reference as ref


# ===========================================================================
# 1. CYCLE DE VIE
# ===========================================================================
# Ordre = progression du projet. Le rang sert au score de maturite et a
# reperer les reculs. `mois_avant_besoin` = delai typique avant que le projet
# genere un besoin de surete concret (base de la fenetre d'opportunite).
PHASES = collections.OrderedDict([
    ("IDEA",                   {"rang": 1,  "maturite": 10,  "mois_avant_besoin": 60}),
    ("POLITICAL_ANNOUNCEMENT", {"rang": 2,  "maturite": 20,  "mois_avant_besoin": 48}),
    ("PRE_FEASIBILITY",        {"rang": 3,  "maturite": 28,  "mois_avant_besoin": 42}),
    ("FEASIBILITY",            {"rang": 4,  "maturite": 35,  "mois_avant_besoin": 36}),
    ("GOVERNMENT_AGREEMENT",   {"rang": 5,  "maturite": 45,  "mois_avant_besoin": 30}),
    ("MOU",                    {"rang": 6,  "maturite": 48,  "mois_avant_besoin": 30}),
    ("FUNDING_SEARCH",         {"rang": 7,  "maturite": 52,  "mois_avant_besoin": 28}),
    ("FUNDING_APPROVED",       {"rang": 8,  "maturite": 60,  "mois_avant_besoin": 24}),
    ("CONSULTANT_SELECTION",   {"rang": 9,  "maturite": 65,  "mois_avant_besoin": 18}),
    ("FEED",                   {"rang": 10, "maturite": 72,  "mois_avant_besoin": 15}),
    ("PRE_FID",                {"rang": 11, "maturite": 78,  "mois_avant_besoin": 12}),
    ("FID",                    {"rang": 12, "maturite": 85,  "mois_avant_besoin": 9}),
    ("EPC_PROCUREMENT",        {"rang": 13, "maturite": 88,  "mois_avant_besoin": 6}),
    ("EPC_AWARDED",            {"rang": 14, "maturite": 91,  "mois_avant_besoin": 3}),
    ("CONSTRUCTION",           {"rang": 15, "maturite": 95,  "mois_avant_besoin": 0}),
    ("COMMISSIONING",          {"rang": 16, "maturite": 98,  "mois_avant_besoin": 0}),
    ("OPERATIONS",             {"rang": 17, "maturite": 100, "mois_avant_besoin": 0}),
])

LIBELLE_PHASE = {
    "IDEA": "Idée", "POLITICAL_ANNOUNCEMENT": "Annonce politique",
    "PRE_FEASIBILITY": "Pré-faisabilité", "FEASIBILITY": "Faisabilité",
    "GOVERNMENT_AGREEMENT": "Accord gouvernemental", "MOU": "Protocole d'accord",
    "FUNDING_SEARCH": "Recherche de financement",
    "FUNDING_APPROVED": "Financement approuvé",
    "CONSULTANT_SELECTION": "Sélection de consultant", "FEED": "FEED",
    "PRE_FID": "Pré-FID", "FID": "Décision finale d'investissement",
    "EPC_PROCUREMENT": "Appel d'offres EPC", "EPC_AWARDED": "EPC attribué",
    "CONSTRUCTION": "Construction", "COMMISSIONING": "Mise en service",
    "OPERATIONS": "Exploitation",
}

# Phases a partir desquelles le besoin de surete devient concret (mobilisation
# d'equipes internationales sur site).
PHASES_CHAUDES = {"FID", "EPC_PROCUREMENT", "EPC_AWARDED", "CONSTRUCTION"}

# Niveaux d'alerte (early warning), section 13 du cahier des charges.
ALERTE_HAUTE = {"FID", "EPC_AWARDED", "CONSTRUCTION", "FUNDING_APPROVED"}
ALERTE_MOYENNE = {"FEASIBILITY", "CONSULTANT_SELECTION", "GOVERNMENT_AGREEMENT",
                  "EPC_PROCUREMENT", "FEED", "PRE_FID"}


def rang(phase):
    return PHASES.get(phase, {}).get("rang", 0)


# ===========================================================================
# 2. RATTACHEMENT (resolution d'entite PROJET)
# ===========================================================================
CONTEXTE_PROJET = [
    "project", "projet", "dam", "barrage", "hydro", "hydropower", "hydroelectric",
    "study", "studies", "etude", "power", "electricity", "electrification",
    "plant", "usine", "terminal", "pipeline", "oleoduc", "gazoduc", "construction",
    "epc", "investment", "financing", "financement", "consortium", "gas", "gaz",
    "mine", "miniere", "rail", "railway", "port", "corridor",
]


def _norm(txt):
    t = unicodedata.normalize("NFD", str(txt or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _texte_signal(signal):
    return _norm("{} {}".format(signal.get("titre", ""), signal.get("resume", "")))


def _mot_present(mot, texte):
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(_norm(mot)) + r"(?![a-z0-9])",
                          texte))


def rattacher(signal, registre=None):
    """PROJECT_ID du projet auquel ce signal se rattache, ou "".

    Ordre : (1) project_id deja porte par le signal (ex. proj_id BM) ;
    (2) alias FORT ; (3) alias FAIBLE + mot de contexte projet.
    Fonction PURE."""
    fourni = str(signal.get("project_id") or "").strip()
    if fourni:
        return fourni
    texte = _texte_signal(signal)
    if not texte.strip():
        return ""
    for p in ref.charger_registre(registre):
        if any(_norm(a) in texte for a in p["alias"]):
            return p["project_id"]
    a_contexte = any(c in texte for c in CONTEXTE_PROJET)
    if not a_contexte:
        return ""
    for p in ref.charger_registre(registre):
        if any(_mot_present(a, texte) for a in p["alias_faibles"]):
            return p["project_id"]
    return ""


def acteurs_du_signal(signal, projet):
    """Acteurs connus du projet cites dans le signal, plus ceux deja extraits
    en amont (champ `acteurs`). Fonction PURE.

    La reconnaissance passe par les VARIANTES de chaque acteur (P6) : un
    registre qui porte "ivanhoe mines" doit reconnaitre un titre qui n'ecrit
    que "Ivanhoe". Sans cela, des acteurs pourtant identifies a la decouverte
    disparaissaient du projet, et donc des prospects."""
    texte = _texte_signal(signal)
    try:
        import acteurs_reference as aref
        variantes = {a: aref.variantes(a) for a in projet.get("acteurs", [])}
    except Exception:
        variantes = {a: {str(a).lower()} for a in projet.get("acteurs", [])}
    trouves = set()
    for acteur, formes in variantes.items():
        if any(_norm(f) in texte for f in formes):
            trouves.add(acteur)
    for a in (signal.get("acteurs") or []):
        if str(a).strip():
            trouves.add(str(a).strip().lower())
    return sorted(trouves)


# ===========================================================================
# 3. DATES
# ===========================================================================
def _date(signal):
    """Date ISO du signal -> date, ou None. Tolere '2026-08-22T10:00:00Z'."""
    brut = str(signal.get("date") or "")[:10]
    try:
        return datetime.date.fromisoformat(brut)
    except ValueError:
        return None


def _mois_entre(d1, d2):
    return int(round((d2 - d1).days / 30.44))


# ===========================================================================
# 4. CONSTRUCTION DES PROJETS
# ===========================================================================
def construire_projets(signaux, registre=None, aujourd=None):
    """Signaux -> projets suivis, avec timeline, phase courante, historique,
    acteurs, scores. Fonction PURE. Trie par opportunite Amarante decroissante.

    Les signaux non rattaches sont simplement ignores (ils restent des leads
    classiques dans le reste du radar : ce module est ADDITIF)."""
    aujourd = aujourd or datetime.date.today()
    par_id = collections.OrderedDict()
    index = {p["project_id"]: p for p in ref.charger_registre(registre)}

    for s in signaux or []:
        pid = rattacher(s, registre)
        if not pid or pid not in index:
            continue
        projet = index[pid]
        seau = par_id.setdefault(pid, {
            "project_id": pid, "libelle": projet["libelle"],
            "pays": projet.get("pays", ""), "iso3": projet.get("iso3", ""),
            "secteur": projet.get("secteur", ""),
            "valeur_musd": projet.get("valeur_musd", 0),
            "signaux": [], "acteurs": collections.Counter(),
        })
        seau["signaux"].append(s)
        for a in acteurs_du_signal(s, projet):
            seau["acteurs"][a] += 1

    projets = [_finaliser(v, aujourd) for v in par_id.values()]
    projets.sort(key=lambda p: (-p["opportunite"]["score"], -p["maturite"]))
    return projets


def _finaliser(seau, aujourd):
    """Calcule timeline, phases, scores d'un projet a partir de ses signaux."""
    dates = [d for d in (_date(s) for s in seau["signaux"]) if d]
    seau["premiere_detection"] = min(dates).isoformat() if dates else ""
    seau["derniere_maj"] = max(dates).isoformat() if dates else ""
    seau["nb_signaux"] = len(seau["signaux"])

    # Historique : uniquement les signaux DATES et PORTEURS d'une phase.
    # Jamais ecrase, toujours chronologique.
    hist = []
    for s in seau["signaux"]:
        ph, d = s.get("phase") or "", _date(s)
        if ph in PHASES and d:
            hist.append({"date": d.isoformat(), "phase": ph,
                         "libelle_phase": LIBELLE_PHASE.get(ph, ph),
                         "titre": s.get("titre", ""), "lien": s.get("lien", "")})
    hist.sort(key=lambda h: h["date"])
    seau["historique"] = hist

    # Phase courante = la plus RECENTE (un projet peut caler ou reculer).
    # phase_max_atteinte = memoire du point le plus avance jamais observe.
    seau["phase_courante"] = hist[-1]["phase"] if hist else ""
    seau["libelle_phase"] = LIBELLE_PHASE.get(seau["phase_courante"], "Phase inconnue")
    seau["phase_max_atteinte"] = (max((h["phase"] for h in hist), key=rang)
                                  if hist else "")
    seau["recul"] = bool(hist and rang(seau["phase_courante"])
                         < rang(seau["phase_max_atteinte"]))

    seau["acteurs_top"] = [a for a, _ in seau["acteurs"].most_common(10)]
    seau["maturite"] = score_maturite(seau, aujourd)
    seau["opportunite"] = score_opportunite(seau, aujourd)
    seau["alerte"] = niveau_alerte(seau, aujourd)
    seau["prochaine_etape"] = prochaine_etape(seau)
    seau["fenetre"] = fenetre_opportunite(seau, aujourd)
    seau["services"] = services_probables(seau)
    seau["acteurs"] = dict(seau["acteurs"])       # serialisable
    return seau


# ===========================================================================
# 5. SCORE DE MATURITE (0-100) -- ou en est le PROJET
# ===========================================================================
def score_maturite(projet, aujourd=None):
    """0-100. Base = phase courante. Ajustements : densite de signaux (un
    projet tres commente avance vraiment) et obsolescence (un projet muet
    depuis des annees n'est plus "en cours"). Fonction PURE."""
    aujourd = aujourd or datetime.date.today()
    phase = projet.get("phase_courante") or ""
    base = PHASES.get(phase, {}).get("maturite", 5)
    if projet.get("nb_signaux", 0) >= 20:
        base += 4
    elif projet.get("nb_signaux", 0) >= 8:
        base += 2
    d = projet.get("derniere_maj")
    if d:
        try:
            mois = _mois_entre(datetime.date.fromisoformat(d), aujourd)
            if mois > 36:
                base -= 20                 # silence prolonge : projet dormant
            elif mois > 18:
                base -= 10
            elif mois > 9:
                base -= 4
        except ValueError:
            pass
    return max(0, min(int(round(base)), 100))


def palier_maturite(score):
    if score <= 20:
        return "idée"
    if score <= 40:
        return "projet émergent"
    if score <= 60:
        return "projet structuré"
    if score <= 75:
        return "préparation avancée"
    if score <= 90:
        return "pré-FID / FID"
    return "construction / exploitation"


# ===========================================================================
# 6. SCORE D'OPPORTUNITE AMARANTE (0-100) -- EXPLICABLE
# ===========================================================================
# Secteurs et intensite de deploiement expatrie (proxy du besoin de surete).
INTENSITE_SECTEUR = {
    "energie": 1.0,          # LNG, hydro : camps, expatries, sites isoles
    "mines": 0.95,
    "transport": 0.8,        # corridors, ports : lineaire, escortes
    "industrie": 0.7,
    "infrastructure": 0.7,
}

# Contractors internationaux dont la presence signale des equipes expatriees.
CONTRACTORS_INTERNATIONAUX = {
    "totalenergies", "total", "shell", "equinor", "exxonmobil", "exxon", "eni",
    "bp", "chevron", "bechtel", "fluor", "technip energies", "saipem", "vinci",
    "bouygues", "eiffage", "webuild", "salini", "aecom", "wood", "worley",
    "mota-engil", "daewoo", "sinohydro", "rio tinto", "kosmos", "golar",
    "cnooc", "cnpc", "baowu", "chinalco", "trafigura", "actis", "fortescue",
}


def _risque_pays(iso3):
    """Multiplicateur de risque du pays (0.3 a 1.0), depuis le referentiel du
    radar. Import tardif : garde ce module testable meme isole."""
    try:
        import ted_complet_v14 as ted
        return ted.MULTIPLICATEUR_ZONE.get((iso3 or "").upper(), 0.3)
    except Exception:
        return 0.3


def score_opportunite(projet, aujourd=None):
    """0-100 + motifs affichables. C'est la reponse a "ce projet vaut-il
    quelque chose POUR AMARANTE", distincte de la maturite du projet.
    Retour : {"score", "motifs": [...], "phrase"}. Fonction PURE."""
    aujourd = aujourd or datetime.date.today()
    pts, motifs = 0, []

    # a) Taille du projet : proxy du nombre d'expatries et de la duree.
    v = projet.get("valeur_musd") or 0
    if v >= 20000:
        pts += 30
        motifs.append("projet géant ({} Md$)".format(round(v / 1000)))
    elif v >= 5000:
        pts += 22
        motifs.append("très grand projet ({} Md$)".format(round(v / 1000)))
    elif v >= 1000:
        pts += 14
        motifs.append("grand projet ({:.1f} Md$)".format(v / 1000))
    elif v > 0:
        pts += 7
        motifs.append("projet de {} M$".format(int(v)))

    # b) Risque pays : coeur du metier Amarante.
    r = _risque_pays(projet.get("iso3"))
    if r >= 1.0:
        pts += 25
        motifs.append("pays à risque élevé")
    elif r >= 0.6:
        pts += 16
        motifs.append("pays à risque significatif")
    else:
        pts += 6
        motifs.append("pays à risque modéré")

    # c) Secteur : intensite de deploiement expatrie.
    inten = INTENSITE_SECTEUR.get(projet.get("secteur"), 0.7)
    pts += int(round(15 * inten))
    motifs.append("secteur {} (déploiement {})".format(
        projet.get("secteur", "n.c."),
        "lourd" if inten >= 0.9 else "modéré"))

    # d) Phase : proximite du besoin reel.
    phase = projet.get("phase_courante") or ""
    if phase in PHASES_CHAUDES:
        pts += 20
        motifs.append("phase {} : mobilisation imminente".format(
            LIBELLE_PHASE.get(phase, phase)))
    elif rang(phase) >= 8:
        pts += 12
        motifs.append("phase {} : besoin à moyen terme".format(
            LIBELLE_PHASE.get(phase, phase)))
    elif rang(phase) > 0:
        pts += 5
        motifs.append("phase amont ({})".format(LIBELLE_PHASE.get(phase, phase)))

    # e) Contractors internationaux presents = prospects directs.
    inter = [a for a in projet.get("acteurs_top", [])
             if a in CONTRACTORS_INTERNATIONAUX]
    if len(inter) >= 3:
        pts += 10
        motifs.append("{} contractors internationaux identifiés".format(len(inter)))
    elif inter:
        pts += 6
        motifs.append("contractors internationaux : {}".format(", ".join(inter[:3])))

    # f) Obsolescence : un projet muet depuis des annees ne se vend pas.
    d = projet.get("derniere_maj")
    if d:
        try:
            mois = _mois_entre(datetime.date.fromisoformat(d), aujourd)
            if mois > 36:
                pts -= 25
                motifs.append("aucun signal depuis {} mois (dormant)".format(mois))
            elif mois > 18:
                pts -= 12
                motifs.append("dernier signal il y a {} mois".format(mois))
        except ValueError:
            pass
    if projet.get("recul"):
        pts -= 6
        motifs.append("recul de phase constaté")

    score = max(0, min(int(round(pts)), 100))
    return {"score": score, "motifs": motifs,
            "phrase": _phrase_opportunite(projet, score, motifs)}


def _phrase_opportunite(projet, score, motifs):
    niveau = ("élevée" if score >= 70 else
              "moyenne" if score >= 45 else "faible")
    return "Opportunité Amarante {} ({}/100) : {}.".format(
        niveau, score, ", ".join(motifs[:5]) or "signaux insuffisants")


# ===========================================================================
# 7. ALERTE, PROCHAINE ETAPE, FENETRE, SERVICES
# ===========================================================================
def niveau_alerte(projet, aujourd=None):
    """haute | moyenne | signal_precoce | aucune. Fonction PURE."""
    aujourd = aujourd or datetime.date.today()
    hist = projet.get("historique") or []
    if not hist:
        return "aucune"
    try:
        mois = _mois_entre(datetime.date.fromisoformat(hist[-1]["date"]), aujourd)
    except ValueError:
        mois = 999
    if mois > 18:
        return "aucune"                  # l'evenement n'est plus actionnable
    ph = hist[-1]["phase"]
    if ph in ALERTE_HAUTE:
        return "haute"
    if ph in ALERTE_MOYENNE:
        return "moyenne"
    return "signal_precoce"


def prochaine_etape(projet):
    """Phase suivante attendue, en clair. Fonction PURE."""
    ph = projet.get("phase_courante") or ""
    noms = list(PHASES.keys())
    if ph not in PHASES:
        return "Phase à qualifier"
    i = noms.index(ph)
    if i + 1 >= len(noms):
        return "Exploitation (cycle complet)"
    return LIBELLE_PHASE.get(noms[i + 1], noms[i + 1])


def fenetre_opportunite(projet, aujourd=None):
    """Fenetre estimee du besoin Amarante : {"debut", "fin", "confiance"}.

    HONNETETE : pas de pourcentage invente. La confiance est qualitative et
    adossee a des faits comptables (nombre de signaux, fraicheur), pas a un
    modele probabiliste qu'on n'a pas. Fonction PURE."""
    aujourd = aujourd or datetime.date.today()
    ph = projet.get("phase_courante") or ""
    if ph not in PHASES:
        return {"debut": "", "fin": "", "confiance": "faible",
                "texte": "Phase inconnue : fenêtre non estimable"}
    mois = PHASES[ph]["mois_avant_besoin"]
    debut = aujourd.year + (aujourd.month - 1 + mois) // 12
    fin = debut + 2
    n = projet.get("nb_signaux", 0)
    try:
        recence = _mois_entre(
            datetime.date.fromisoformat(projet.get("derniere_maj") or ""), aujourd)
    except ValueError:
        recence = 999
    if n >= 10 and recence <= 6:
        conf = "élevée"
    elif n >= 4 and recence <= 18:
        conf = "moyenne"
    else:
        conf = "faible"
    return {"debut": debut, "fin": fin, "confiance": conf,
            "texte": "Besoin probable {}-{} (confiance {})".format(debut, fin, conf)}


SERVICES_PAR_SECTEUR = {
    "energie": ["transport sécurisé", "protection rapprochée (CPO)",
                "journey management", "sûreté de site", "travel security"],
    "mines": ["sûreté de site", "escorte de convois", "journey management",
              "protection rapprochée (CPO)"],
    "transport": ["escorte de convois", "journey management",
                  "sûreté de chantier linéaire", "travel security"],
    "industrie": ["sûreté de site", "travel security", "transport sécurisé"],
    "infrastructure": ["sûreté de chantier", "transport sécurisé",
                       "travel security", "risk management"],
}


def services_probables(projet):
    """Services Amarante plausibles pour ce projet. Fonction PURE."""
    base = list(SERVICES_PAR_SECTEUR.get(projet.get("secteur"),
                                         SERVICES_PAR_SECTEUR["infrastructure"]))
    if (projet.get("phase_courante") or "") in PHASES_CHAUDES:
        for s in ("support 24/7", "gestion de crise"):
            if s not in base:
                base.append(s)
    return base


# ===========================================================================
# 8. TIMELINE (affichage)
# ===========================================================================
def timeline(projet):
    """Historique regroupe par annee, pret a l'affichage. Fonction PURE.
    Retour : [{"annee": 2026, "evenements": [{date, phase, libelle, titre}]}]"""
    par_an = collections.OrderedDict()
    for h in projet.get("historique") or []:
        an = h["date"][:4]
        par_an.setdefault(an, []).append(h)
    return [{"annee": an, "evenements": ev} for an, ev in sorted(par_an.items())]


def prospects(projet):
    """PROJET -> ENTREPRISE -> besoin : les acteurs qui vont DEPLOYER des
    personnels deviennent des prospects qualifies. Fonction PURE.

    Depuis P6, la qualification passe par `acteurs_reference`, une base de
    connaissance OUVERTE : elle reconnait les acteurs connus, raisonne sur les
    inconnus, et distingue ceux qui envoient des gens sur place (operateur,
    EPC, minier, consultant, logisticien) de ceux qui financent ou autorisent
    (bailleur, Etat). L'ancienne liste fermee servait de filtre unique et
    perdait des prospects reels : Ivanhoe Mines et Zijin sur Kamoa-Kakula
    etaient identifies mais jamais proposes. Elle reste ici en REPLI, au cas
    ou le module de reference serait indisponible."""
    try:
        import acteurs_reference as aref
        qualifies = aref.prospects_du_projet(projet.get("acteurs_top", []),
                                             projet.get("secteur", ""))
    except Exception:
        qualifies = [{"nom": a, "role": "inconnu", "libelle_role": "",
                      "origine": "", "connu": True}
                     for a in projet.get("acteurs_top", [])
                     if a in CONTRACTORS_INTERNATIONAUX]
    out = []
    for a in qualifies:
        out.append({
            "entreprise": a["nom"],
            "role": a.get("libelle_role", ""),
            "origine": a.get("origine", ""),
            "qualification": "confirmé" if a.get("connu") else "à qualifier",
            "project_id": projet["project_id"],
            "pays": projet.get("pays", ""),
            "iso3": projet.get("iso3", ""),
            "besoin": ", ".join(projet.get("services", [])[:3]),
            "fenetre": (projet.get("fenetre") or {}).get("texte", ""),
        })
    return out
