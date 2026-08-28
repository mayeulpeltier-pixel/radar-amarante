# -*- coding: utf-8 -*-
"""Le COMPTE (P2.5) — la lecture commerciale d'une entreprise.

CE QUI MANQUAIT
---------------
La fiche entreprise repond deja a « qui est-ce », « ou travaille-t-elle »,
« qu'a-t-elle gagne » : identite, presence par theatre, historique unifie des
marches et des signaux. C'est du bon renseignement.

Il lui manquait les deux questions que se pose reellement un commercial :

    QUI est-ce que j'appelle ?
    POURQUOI maintenant, et qu'est-ce que je lui dis ?

L'enrichissement existant est FIRMOGRAPHIQUE (identite legale, dirigeant,
e-mail generique). Ce module le complete par une lecture COMMERCIALE.

CE QU'IL NE FAIT PAS
--------------------
Il ne nomme AUCUNE personne. Nommer un « Directeur sûreté Afrique » sans
source serait inventer un individu, et rien dans les donnees collectees ne
permet de le connaitre. Il propose des FONCTIONS a viser, ce qui est une
recommandation de methode, pas un renseignement fabrique. La distinction est
signalee a l'ecran.

L'angle d'approche, lui, n'est assemble QUE de faits deja detectes : le pays,
le projet, la degradation du contexte, l'echeance. Aucune phrase n'y est
generee a partir de rien.
"""

# ---------------------------------------------------------------------------
# 1. FONCTIONS A CIBLER
# ---------------------------------------------------------------------------
# Ces listes sont une DOCTRINE COMMERCIALE, pas une donnee collectee. Elles
# disent qui, dans une organisation de ce type, decide ou prescrit un contrat
# de surete. Elles sont donc discutables et modifiables sans toucher au
# moteur : c'est voulu.

FONCTIONS_PAR_NATURE = {
    "expatrie_significatif": [
        ("Directeur sûreté / Security Manager",
         "décideur direct quand des expatriés sont déployés"),
        ("Directeur des opérations pays",
         "porte le budget et subit le risque au quotidien"),
        ("Directeur de projet / Project Director",
         "arbitre la mobilisation et son calendrier"),
    ],
    "mixte": [
        ("Responsable HSE / QHSE",
         "la sûreté est souvent rattachée à la santé-sécurité sur ces profils"),
        ("Directeur des opérations pays",
         "encadrement international à protéger"),
        ("Responsable achats / procurement",
         "point d'entrée quand la sûreté n'est pas encore structurée"),
    ],
    "local_uniquement": [
        ("Responsable achats / procurement",
         "besoin probablement traité comme un achat de gardiennage"),
        ("Responsable HSE / QHSE",
         "seul relais sûreté quand il n'y a pas d'expatriés"),
    ],
}
# Repli quand la nature du deploiement n'a pas ete analysee.
FONCTIONS_DEFAUT = [
    ("Responsable HSE / QHSE", "relais sûreté le plus fréquent"),
    ("Directeur des opérations pays", "porte le risque opérationnel"),
    ("Responsable achats / procurement", "point d'entrée procédural"),
]
# Ajouts par secteur, en TETE de liste quand ils s'appliquent.
FONCTIONS_PAR_SECTEUR = {
    "Extractif / Mines": [
        ("Directeur sûreté site / Mine Security Manager",
         "les sites miniers isolés ont presque toujours une fonction dédiée")],
    "Énergie": [
        ("Directeur sûreté des installations",
         "sites critiques : la fonction existe et décide")],
    "Défense": [
        ("Directeur des opérations internationales",
         "interlocuteur habituel sur les marchés de défense")],
}


def fonctions_cibles(secteur="", nature="", limite=3):
    """[(fonction, pourquoi)] a viser. Fonction PURE.

    Ce sont des FONCTIONS, jamais des personnes : rien dans les donnees
    collectees ne permet de nommer quelqu'un, et le faire serait inventer."""
    out = list(FONCTIONS_PAR_SECTEUR.get(secteur, []))
    out += FONCTIONS_PAR_NATURE.get(nature, FONCTIONS_DEFAUT)
    vues, uniques = set(), []
    for f, pourquoi in out:
        if f not in vues:
            vues.add(f)
            uniques.append((f, pourquoi))
    return uniques[:limite]


# ---------------------------------------------------------------------------
# 2. ANGLE D'APPROCHE
# ---------------------------------------------------------------------------
def angle_approche(compte):
    """Phrases d'accroche, assemblees UNIQUEMENT de faits detectes.

    Renvoie [(fait, source)] : chaque element est verifiable dans les donnees.
    Une liste vide est un resultat honnete -- elle signifie qu'on n'a rien de
    concret a dire a cette entreprise, ce qui est en soi une information."""
    faits = []
    zones = compte.get("zones") or []
    pays = compte.get("pays") or []
    if pays:
        faits.append(("présence détectée en {}".format(", ".join(pays[:3])),
                      "marchés et signaux collectés"))
    elif zones:
        faits.append(("présence détectée sur {}".format(", ".join(zones[:3])),
                      "marchés et signaux collectés"))
    if compte.get("projets"):
        faits.append(("engagée sur {} projet(s) suivi(s)".format(
            len(compte["projets"])), "rattachement par identifiant de projet"))
    if compte.get("pays_aggraves"):
        faits.append(("contexte dégradé récemment en {}".format(
            ", ".join(sorted(compte["pays_aggraves"])[:2])),
            "alertes voyageurs FCDO"))
    if compte.get("renouvellements"):
        faits.append(("{} contrat(s) arrivant à échéance".format(
            compte["renouvellements"]), "suivi des attributions"))
    if compte.get("n_marches", 0) >= 2:
        faits.append(("titulaire récurrent : {} marchés en zone à risque".format(
            compte["n_marches"]), "historique des attributions"))
    if compte.get("deploiement"):
        faits.append(("signal de déploiement humain détecté",
                      "offres d'emploi et presse"))
    return faits


# ---------------------------------------------------------------------------
# 3. PRIORITE DU COMPTE
# ---------------------------------------------------------------------------
def priorite_compte(compte):
    """(note 0-100, motifs). Fonction PURE.

    Ne mesure PAS la taille de l'entreprise -- une multinationale sans
    deploiement en zone a risque n'interesse pas Amarante -- mais son
    EXPOSITION : ou elle est, ce qu'elle y fait, et si le contexte bouge."""
    n, motifs = 20, []
    marches = int(compte.get("n_marches", 0))
    if marches >= 3:
        n += 25
        motifs.append("titulaire récurrent ({} marchés)".format(marches))
    elif marches >= 1:
        n += 15
        motifs.append("{} marché(s) remporté(s)".format(marches))
    zones = len(compte.get("zones") or [])
    if zones >= 2:
        n += 15
        motifs.append("présente sur {} théâtres".format(zones))
    elif zones == 1:
        n += 8
        motifs.append("présente sur un théâtre")
    if compte.get("etranger"):
        n += 15
        motifs.append("titulaire étranger : expatriés à protéger")
    if compte.get("deploiement"):
        n += 15
        motifs.append("déploiement humain détecté")
    if compte.get("pays_aggraves"):
        n += 15
        motifs.append("contexte sécuritaire en dégradation")
    if compte.get("renouvellements"):
        n += 10
        motifs.append("échéance de contrat à travailler en amont")
    if not motifs:
        motifs.append("aucun signal d'exposition : compte en veille")
    return (min(100, n), motifs)


# ---------------------------------------------------------------------------
# 4. CONSTRUCTION
# ---------------------------------------------------------------------------
def cle_compte(nom, ent_cle=""):
    """Cle de regroupement d'un compte. Fonction PURE.

    MIROIR EXACT de `cleEnt` cote cockpit :
        (entcle||"").trim() || String(nom||"").trim().toLowerCase()

    Ce repli sur le nom en minuscules N'EST PAS une seconde resolution
    d'entite : c'est la meme regle, recopiee parce que les deux surfaces
    doivent grouper a l'identique. Sans lui, un lead sans `ent_cle` cree une
    fiche cote JS et AUCUN compte cote Python, et le bloc « lecture
    commerciale » disparait sans que rien ne le signale -- defaut constate le
    26/08 en verifiant le rendu.

    Un test apparie compare les deux definitions."""
    cle = str(ent_cle or "").strip()
    return cle or str(nom or "").strip().lower()


def construire(leads, pays_aggraves=None, opportunites=None):
    """{cle: compte}. Fonction PURE.

    Regroupe sur la meme cle que la fiche entreprise du cockpit (cf.
    `cle_compte`). Ce module ne refait AUCUNE resolution d'entite : il
    consomme `ent_cle` quand elle existe, et son repli documente sinon."""
    aggraves = set(pays_aggraves or ())
    par_opp = {}
    for o in (opportunites or []):
        cle = cle_compte("", o.get("ent_cle"))
        if cle:
            par_opp.setdefault(cle, []).append(o)

    comptes = {}
    for l in (leads or []):
        nom = str(l.get("entreprise") or "").strip()
        if not nom:
            continue
        cle = cle_compte(nom, l.get("ent_cle"))
        if not cle:
            continue
        c = comptes.setdefault(cle, {
            "ent_cle": cle, "nom": "", "secteur": "", "nature": "",
            "zones": set(), "pays": set(), "projets": set(),
            "pays_aggraves": set(), "n_marches": 0, "n_signaux": 0,
            "etranger": False, "deploiement": False, "renouvellements": 0})
        if len(nom) > len(c["nom"]):
            c["nom"] = nom
        if l.get("src") == "ATTRIB":
            c["n_marches"] += 1
            if l.get("renouv"):
                c["renouvellements"] += 1
        else:
            c["n_signaux"] += 1
        if l.get("etranger_titulaire"):
            c["etranger"] = True
        if l.get("nature") in ("expatrie_significatif", "mixte"):
            c["deploiement"] = True
            if not c["nature"]:
                c["nature"] = l["nature"]
        if not c["secteur"]:
            c["secteur"] = str(l.get("sect") or "")
        for champ, cible in (("zone", "zones"), ("pays", "pays"),
                             ("projet_id", "projets")):
            v = str(l.get(champ) or "").strip()
            if v:
                c[cible].add(v)
        p = str(l.get("pays") or "").strip()
        if p in aggraves:
            c["pays_aggraves"].add(p)

    out = {}
    for cle, c in comptes.items():
        for k in ("zones", "pays", "projets", "pays_aggraves"):
            c[k] = sorted(c[k])
        note, motifs = priorite_compte(c)
        c["priorite"] = note
        c["motifs"] = motifs
        c["fonctions"] = fonctions_cibles(c["secteur"], c["nature"])
        c["angle"] = angle_approche(c)
        c["opportunites"] = sorted(
            [{"id": o["opportunity_id"], "titre": o.get("titre", ""),
              "priorite": o.get("priorite", 0),
              "action": (o.get("action") or {}).get("libelle", "")}
             for o in par_opp.get(cle, [])],
            key=lambda o: -o["priorite"])[:5]
        out[cle] = c
    return out
