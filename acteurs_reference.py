# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- BASE DE CONNAISSANCE DES ACTEURS (P6).
===============================================================================

CE QU'ELLE REMPLACE
-------------------
`projets.CONTRACTORS_INTERNATIONAUX` etait une LISTE FERMEE d'une trentaine de
noms. Consequence mesuree lors de l'audit : le projet Kamoa-Kakula identifiait
correctement Ivanhoe Mines et Zijin Mining, mais produisait ZERO prospect,
parce que ces deux societes n'etaient pas dans la liste. Un prospect reel etait
perdu par simple absence d'entree.

CE QU'ELLE APPORTE
------------------
Une base OUVERTE : elle sait repondre sur les acteurs connus, et RAISONNER sur
les inconnus. Pour toute entreprise citee dans un projet, elle produit :
    entite normalisee | secteur | role | pays d'origine | deploie ou non

LA QUESTION QUI COMPTE POUR AMARANTE
-------------------------------------
Ce n'est pas "cette entreprise est-elle celebre", c'est "cette entreprise
va-t-elle ENVOYER DES GENS SUR PLACE". Un operateur, un EPC, un foreur ou un
mineur deploient des expatries : ce sont des prospects. Un bailleur, un
assureur ou un ministere financent ou autorisent : ce sont des relais
d'influence, pas des clients de proximite. La base distingue les deux.

ROLES
-----
  operateur   : detient et exploite le projet (Shell, TotalEnergies, Ivanhoe)
  epc         : construit (Bechtel, Saipem, Webuild, Daewoo)
  consultant  : etudie, supervise (AECOM, Wood, Worley)
  minier      : exploite une mine (Rio Tinto, Barrick, Zijin)
  logisticien : transporte, manutentionne (Trafigura, Bollore)
  bailleur    : finance (Banque Mondiale, AFD, IFC) -> NE deploie PAS
  etat        : gouvernement, entreprise d'Etat locale -> NE deploie PAS
  inconnu     : non resolu, tranche par heuristique

EXTENSION : ajouter une entree dans ACTEURS. Le moteur n'a pas a changer.
"""

import re
import unicodedata


# ===========================================================================
# 1. ROLES ET COMPORTEMENT DE DEPLOIEMENT
# ===========================================================================
# deploie = envoie des personnels internationaux sur le terrain, donc genere
# un besoin de surete (transport securise, CPO, journey management...).
ROLES = {
    "operateur":   {"deploie": True,  "libelle": "Opérateur du projet"},
    "epc":         {"deploie": True,  "libelle": "Constructeur EPC"},
    "consultant":  {"deploie": True,  "libelle": "Ingénierie / conseil"},
    "minier":      {"deploie": True,  "libelle": "Exploitant minier"},
    "logisticien": {"deploie": True,  "libelle": "Logistique / négoce"},
    "bailleur":    {"deploie": False, "libelle": "Bailleur de fonds"},
    "etat":        {"deploie": False, "libelle": "État / entreprise publique"},
    "inconnu":     {"deploie": False, "libelle": "Rôle à qualifier"},
}


def _a(nom, role, secteur="", origine="", alias=()):
    return {"nom": nom, "role": role, "secteur": secteur, "origine": origine,
            "alias": list(alias)}


# ===========================================================================
# 2. BASE DES ACTEURS CONNUS
# ===========================================================================
ACTEURS = [
    # ------------------------------------------------- ENERGIE : operateurs
    _a("TotalEnergies", "operateur", "energie", "France", ["total", "total energies"]),
    _a("Shell", "operateur", "energie", "Royaume-Uni", ["royal dutch shell"]),
    _a("Equinor", "operateur", "energie", "Norvège", ["statoil"]),
    _a("ExxonMobil", "operateur", "energie", "États-Unis", ["exxon", "esso"]),
    _a("Eni", "operateur", "energie", "Italie"),
    _a("BP", "operateur", "energie", "Royaume-Uni", ["british petroleum"]),
    _a("Chevron", "operateur", "energie", "États-Unis"),
    _a("ConocoPhillips", "operateur", "energie", "États-Unis"),
    _a("QatarEnergy", "operateur", "energie", "Qatar", ["qatar petroleum"]),
    _a("CNOOC", "operateur", "energie", "Chine"),
    _a("CNPC", "operateur", "energie", "Chine", ["petrochina"]),
    _a("Kosmos Energy", "operateur", "energie", "États-Unis", ["kosmos"]),
    _a("Golar LNG", "operateur", "energie", "Norvège", ["golar"]),
    _a("Perenco", "operateur", "energie", "France"),
    _a("Vitol", "logisticien", "energie", "Suisse"),

    # ------------------------------------------------------ MINES (P6, test)
    _a("Ivanhoe Mines", "minier", "mines", "Canada", ["ivanhoe"]),
    _a("Zijin Mining", "minier", "mines", "Chine", ["zijin"]),
    _a("Barrick Gold", "minier", "mines", "Canada", ["barrick"]),
    _a("Rio Tinto", "minier", "mines", "Royaume-Uni"),
    _a("Glencore", "minier", "mines", "Suisse"),
    _a("Anglo American", "minier", "mines", "Royaume-Uni"),
    _a("BHP", "minier", "mines", "Australie"),
    _a("Vale", "minier", "mines", "Brésil"),
    _a("Newmont", "minier", "mines", "États-Unis"),
    _a("First Quantum", "minier", "mines", "Canada", ["first quantum minerals"]),
    _a("Endeavour Mining", "minier", "mines", "Royaume-Uni", ["endeavour"]),
    _a("Fortescue", "minier", "mines", "Australie"),
    _a("Chinalco", "minier", "mines", "Chine"),
    _a("Baowu", "minier", "mines", "Chine", ["china baowu"]),
    _a("Managem", "minier", "mines", "Maroc"),
    _a("Eramet", "minier", "mines", "France"),
    _a("Kinross", "minier", "mines", "Canada", ["kinross gold"]),
    _a("B2Gold", "minier", "mines", "Canada"),
    _a("Allied Gold", "minier", "mines", "Canada"),

    # ------------------------------------------------------------------ EPC
    _a("Bechtel", "epc", "", "États-Unis"),
    _a("Fluor", "epc", "", "États-Unis"),
    _a("Technip Energies", "epc", "energie", "France", ["technip"]),
    _a("Saipem", "epc", "energie", "Italie"),
    _a("McDermott", "epc", "energie", "États-Unis"),
    _a("Vinci", "epc", "", "France", ["vinci construction"]),
    _a("Bouygues", "epc", "", "France"),
    _a("Eiffage", "epc", "", "France"),
    _a("Webuild", "epc", "", "Italie", ["salini", "salini impregilo"]),
    _a("Mota-Engil", "epc", "", "Portugal"),
    _a("Daewoo E&C", "epc", "", "Corée du Sud", ["daewoo"]),
    _a("Hyundai E&C", "epc", "", "Corée du Sud", ["hyundai engineering"]),
    _a("Samsung C&T", "epc", "", "Corée du Sud"),
    _a("Sinohydro", "epc", "energie", "Chine"),
    _a("PowerChina", "epc", "energie", "Chine"),
    _a("China Harbour", "epc", "transport", "Chine", ["china harbour engineering", "chec"]),
    _a("CRCC", "epc", "transport", "Chine", ["china railway construction"]),
    _a("Yapi Merkezi", "epc", "transport", "Turquie"),
    _a("Onur Group", "epc", "", "Turquie", ["onur"]),
    _a("Limak", "epc", "", "Turquie"),
    _a("Orascom", "epc", "", "Égypte", ["orascom construction"]),
    _a("Larsen & Toubro", "epc", "", "Inde", ["l&t"]),
    _a("CWE", "epc", "energie", "Chine", ["china international water"]),

    # ---------------------------------------------------------- CONSULTANTS
    _a("AECOM", "consultant", "", "États-Unis"),
    _a("Wood", "consultant", "energie", "Royaume-Uni", ["wood group"]),
    _a("Worley", "consultant", "energie", "Australie"),
    _a("Jacobs", "consultant", "", "États-Unis"),
    _a("Arup", "consultant", "", "Royaume-Uni"),
    _a("Egis", "consultant", "", "France"),
    _a("Artelia", "consultant", "", "France"),
    _a("SNC-Lavalin", "consultant", "", "Canada", ["atkinsrealis"]),
    _a("Tractebel", "consultant", "energie", "Belgique"),
    _a("SRK Consulting", "consultant", "mines", "Royaume-Uni", ["srk"]),

    # -------------------------------------------------------- LOGISTICIENS
    _a("Trafigura", "logisticien", "", "Suisse"),
    _a("Bolloré Logistics", "logisticien", "transport", "France", ["bollore"]),
    _a("DP World", "logisticien", "transport", "Émirats arabes unis"),
    _a("AP Moller Maersk", "logisticien", "transport", "Danemark", ["maersk"]),
    _a("CMA CGM", "logisticien", "transport", "France"),
    _a("Necotrans", "logisticien", "transport", "France"),

    # ------------------------------------------------------------ BAILLEURS
    _a("Banque Mondiale", "bailleur", "", "International", ["world bank"]),
    _a("IFC", "bailleur", "", "International"),
    _a("MIGA", "bailleur", "", "International"),
    _a("Banque africaine de développement", "bailleur", "", "International",
       ["afdb", "bad", "african development bank"]),
    _a("BERD", "bailleur", "", "International", ["ebrd"]),
    _a("AFD", "bailleur", "", "France", ["agence française de développement"]),
    _a("Proparco", "bailleur", "", "France"),
    _a("DFC", "bailleur", "", "États-Unis", ["us dfc"]),
    _a("BEI", "bailleur", "", "International", ["eib"]),
    _a("Banque asiatique de développement", "bailleur", "", "International", ["adb"]),
    _a("BID", "bailleur", "", "International", ["isdb", "islamic development bank"]),
    _a("Exim Bank of China", "bailleur", "", "Chine", ["china exim"]),
    _a("Afreximbank", "bailleur", "", "International"),

    # ----------------------------------------------------- ETATS / PUBLICS
    _a("TPDC", "etat", "energie", "Tanzanie"),
    _a("ENH", "etat", "energie", "Mozambique"),
    _a("Sonangol", "etat", "energie", "Angola"),
    _a("NNPC", "etat", "energie", "Nigeria"),
    _a("Petrosen", "etat", "energie", "Sénégal"),
    _a("SNIM", "etat", "mines", "Mauritanie"),
    _a("KazMunayGas", "etat", "energie", "Kazakhstan"),
    _a("Aramco", "operateur", "energie", "Arabie saoudite", ["saudi aramco"]),
    _a("ADNOC", "operateur", "energie", "Émirats arabes unis"),
    _a("Eskom", "etat", "energie", "Afrique du Sud"),
    _a("SNEL", "etat", "energie", "RDC"),
    _a("ADPI-RDC", "etat", "", "RDC", ["adpi"]),
    _a("UNOC", "etat", "energie", "Ouganda"),
    _a("NOC", "etat", "energie", "Libye"),
]


# ===========================================================================
# 3. NORMALISATION ET RESOLUTION
# ===========================================================================
_SUFFIXES = (
    "sa", "sas", "sarl", "spa", "plc", "ltd", "limited", "llc", "inc",
    "corp", "corporation", "company", "co", "group", "groupe", "holding",
    "holdings", "international", "gmbh", "ag", "bv", "nv", "as", "asa",
    "pjsc", "jsc", "ojsc", "pte", "pty", "srl", "sl",
)


def _norm(nom):
    """Nom canonique : minuscules, sans accents, sans ponctuation ni forme
    juridique. Fonction PURE."""
    t = unicodedata.normalize("NFD", str(nom or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    mots = [m for m in t.split() if m and m not in _SUFFIXES]
    return " ".join(mots)


_INDEX = None


def _index():
    global _INDEX
    if _INDEX is None:
        _INDEX = {}
        for a in ACTEURS:
            for cle in [a["nom"]] + a["alias"]:
                n = _norm(cle)
                if n:
                    _INDEX.setdefault(n, a)
    return _INDEX


# Indices lexicaux pour RAISONNER sur un acteur inconnu (base ouverte). Un nom
# inconnu contenant "engineering" ou "construction" est presque surement un
# constructeur : on lui donne le role, avec la mention "infere".
INDICES_ROLE = [
    ("epc", ("engineering", "construction", "constructora", "contracting",
             "constructions", "batiment", "insaat", "epc")),
    ("consultant", ("consulting", "consultants", "conseil", "ingenierie",
                    "engineers", "advisory", "studies")),
    ("minier", ("mining", "mines", "minerals", "gold", "copper", "resources",
                "miniere")),
    ("operateur", ("petroleum", "petrol", "oil", "gas", "energy", "energie",
                   "power", "lng")),
    ("logisticien", ("logistics", "logistique", "shipping", "terminals",
                     "port", "transport", "freight")),
    ("bailleur", ("bank", "banque", "fund", "fonds", "development bank",
                  "financiere")),
    ("etat", ("ministry", "ministere", "government", "gouvernement",
              "authority", "autorite", "agence nationale", "national oil")),
]


def resoudre(nom, secteur_projet=""):
    """Resout une entreprise citee. TOUJOURS une reponse, meme pour un nom
    inconnu (base OUVERTE, pas liste fermee). Fonction PURE.

    Retour : {nom, cle, role, libelle_role, secteur, origine, deploie, connu,
              infere}"""
    cle = _norm(nom)
    if not cle:
        return None
    connu = _index().get(cle)
    if connu is None:
        # Rapprochement partiel : "ivanhoe mines drc" -> "ivanhoe mines".
        for ref_cle, ref in _index().items():
            if len(ref_cle) >= 5 and (ref_cle in cle or cle in ref_cle):
                connu = ref
                break
    if connu:
        role = connu["role"]
        return {"nom": connu["nom"], "cle": _norm(connu["nom"]), "role": role,
                "libelle_role": ROLES[role]["libelle"],
                "secteur": connu["secteur"] or secteur_projet,
                "origine": connu["origine"],
                "deploie": ROLES[role]["deploie"], "connu": True,
                "infere": False}
    role = _role_infere(cle)
    return {"nom": str(nom).strip(), "cle": cle, "role": role,
            "libelle_role": ROLES[role]["libelle"],
            "secteur": secteur_projet, "origine": "",
            "deploie": ROLES[role]["deploie"], "connu": False,
            "infere": role != "inconnu"}


def _role_infere(cle):
    """Role deduit du nom pour un acteur inconnu. Fonction PURE."""
    for role, indices in INDICES_ROLE:
        if any(i in cle for i in indices):
            return role
    return "inconnu"


def est_deployeur(nom, secteur_projet=""):
    """Cette entreprise va-t-elle envoyer des personnels sur place ?
    C'est le seul critere qui fait d'un acteur un PROSPECT. Fonction PURE."""
    a = resoudre(nom, secteur_projet)
    return bool(a and a["deploie"])


def acteurs_du_projet(noms, secteur_projet=""):
    """Liste d'acteurs resolus, dedupliquee par entite. Fonction PURE."""
    out, vus = [], set()
    for n in noms or []:
        a = resoudre(n, secteur_projet)
        if not a or a["cle"] in vus:
            continue
        vus.add(a["cle"])
        out.append(a)
    return out


def prospects_du_projet(noms, secteur_projet=""):
    """Acteurs qui deploient, donc prospects Amarante. Les deployeurs CONNUS
    d'abord (qualification plus sure), puis les inferes. Fonction PURE."""
    acteurs = [a for a in acteurs_du_projet(noms, secteur_projet) if a["deploie"]]
    acteurs.sort(key=lambda a: (not a["connu"], a["nom"].lower()))
    return acteurs


def variantes(nom):
    """Toutes les graphies sous lesquelles cet acteur peut apparaitre dans un
    texte : nom canonique + alias connus + le nom fourni. Fonction PURE.

    Sert au socle : un registre qui contient "ivanhoe mines" doit reconnaitre
    un titre qui ecrit seulement "Ivanhoe" (cas reel manque avant P6)."""
    out = {str(nom or "").strip().lower()}
    cle = _norm(nom)
    if cle:
        out.add(cle)
    connu = _index().get(cle)
    if connu is None:
        for ref_cle, ref in _index().items():
            if len(ref_cle) >= 5 and (ref_cle in cle or cle in ref_cle):
                connu = ref
                break
    if connu:
        out.add(connu["nom"].lower())
        out.add(_norm(connu["nom"]))
        for a in connu["alias"]:
            out.add(a.lower())
            out.add(_norm(a))
    return {v for v in out if len(v) >= 3}


def statistiques():
    import collections
    par_role = collections.Counter(a["role"] for a in ACTEURS)
    return {"total": len(ACTEURS), "par_role": dict(par_role),
            "deployeurs": sum(1 for a in ACTEURS if ROLES[a["role"]]["deploie"])}
