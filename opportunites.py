# -*- coding: utf-8 -*-
"""L'objet OPPORTUNITY (P2.2) — l'unité de travail qui survit à ses sources.

LE PROBLEME
-----------
Aujourd'hui l'unité de travail est le LEAD : une ligne d'une source. Or une
même occasion commerciale se manifeste plusieurs fois, par des canaux
différents et à des moments différents :

    un projet BM detecte en mars
      -> un signal de recrutement en juin (l'entreprise mobilise)
        -> un avis d'appel d'offres en aout (le marche existe enfin)

Trois lignes, trois onglets, trois scores. Aucun lien. Le commercial les
decouvre separement et ne voit pas qu'il regarde trois fois la meme histoire.

CE QUE FAIT CE MODULE
---------------------
Il regroupe les leads en OPPORTUNITES sur une cle stable, et exprime chacune
selon cinq dimensions au lieu d'un chiffre unique.

CE QU'IL NE FAIT PAS, ET POURQUOI
---------------------------------
Il ne produit AUCUN renseignement nouveau. Les cinq dimensions sont des
re-expressions de signaux deja collectes et deja ponderes ailleurs. C'est
volontaire : inventer un score a partir de champs non mesures donnerait un
chiffre d'apparence savante et sans contenu -- exactement le reproche fait a
l'idee d'un « potentiel en euros » tant que les effectifs expatries ne sont
pas sources.

Et surtout : ces dimensions ne sont PAS CALIBREES. Elles le seront quand la
boucle de retroaction disposera d'assez d'issues gagne/perdu (P1.1). D'ici la,
elles ordonnent, elles ne predisent pas. `confiance` sert justement a dire a
quel point on peut s'y fier.
"""

import collections
import datetime
import os
import re

# ---------------------------------------------------------------------------
# Reglages
# ---------------------------------------------------------------------------
# Une opportunite dont plus rien ne bouge depuis ce delai n'est plus une
# occasion, c'est un dossier a archiver.
JOURS_DORMANCE = int(os.environ.get("RADAR_OPP_DORMANCE", "365"))

# Sources dont les liens resolvent tous vers le meme domaine : elles comptent
# pour UNE seule source dans le calcul de corroboration. Le piege est connu et
# documente -- N flux d'actualite differents pointent tous news.google.com,
# et les compter separement gonflerait artificiellement la confiance, ce qui
# est l'inverse exact de l'objectif.
DOMAINES_FUSIONNES = ("news.google.com", "google.com/url")


# ===========================================================================
# 1. IDENTITE DE L'OPPORTUNITE
# ===========================================================================
def cle_opportunite(lead):
    """Cle stable d'une opportunite. Fonction PURE.

    Trois niveaux, du plus englobant au plus etroit :

      PROJ:<projet_id>      un projet identifie rassemble TOUT ce qui le
                            concerne : l'avis, le titulaire, les signaux. C'est
                            le regroupement le plus utile, et `projet_id` est
                            une identite deja stable.
      ENT:<ent_cle>:<zone>  a defaut de projet, une entreprise qui se deploie
                            sur un theatre. La zone est dans la cle a dessein :
                            le meme groupe au Sahel et en Asie centrale, ce
                            sont deux conversations commerciales differentes.
      AVIS:<src>:<pub>      a defaut des deux, l'avis reste une opportunite a
                            lui seul. On ne perd donc jamais un lead au
                            regroupement.

    Renvoie "" si le lead ne porte meme pas d'identifiant de publication : il
    n'y a alors rien de stable a quoi se raccrocher."""
    pid = str(lead.get("projet_id") or "").strip()
    if pid:
        return "PROJ:" + pid
    ent = str(lead.get("ent_cle") or "").strip()
    if ent:
        return "ENT:{}:{}".format(ent, str(lead.get("zone") or "?").strip())
    pub = str(lead.get("pub") or "").strip()
    if pub:
        return "AVIS:{}:{}".format(str(lead.get("src") or "?").strip(), pub)
    return ""


def _domaine(lien):
    m = re.match(r"https?://([^/]+)", str(lien or "").strip().lower())
    return m.group(1) if m else ""


def sources_distinctes(leads):
    """Nombre de sources REELLEMENT independantes. Fonction PURE.

    Deux corrections par rapport a un simple `len(set(src))` :
      - les liens Google News resolvent tous vers le meme domaine et comptent
        pour UNE source, quel que soit le nombre de flux d'origine ;
      - une source qui n'apporte aucun lien exploitable ne compte pas comme
        une corroboration independante.

    Sans ces deux regles, la « convergence de signaux » serait un compteur de
    doublons deguise en preuve."""
    vues = set()
    for l in (leads or []):
        dom = _domaine(l.get("lien"))
        if any(d in dom for d in DOMAINES_FUSIONNES):
            vues.add("news.google")
            continue
        vues.add(str(l.get("src") or "?"))
    return len(vues)


# ===========================================================================
# 2. LES CINQ DIMENSIONS
# ===========================================================================
# Chacune renvoie (note sur 100, motifs). Les motifs sont la partie utile :
# une note sans justification n'est pas auditable, et c'est precisement le
# reproche qui a mene a P1.3.

def _val(leads, champ):
    return max([float(l.get(champ) or 0) for l in leads] or [0.0])


def attractivite(leads):
    """« Est-ce un beau business ? » Taille et intensite du besoin surete."""
    motifs = []
    valeur = _val(leads, "valeur") or _val(leads, "enveloppe")
    if valeur >= 50:
        n, m = 40, "marché ou enveloppe supérieure à 50 M€"
    elif valeur >= 10:
        n, m = 30, "marché de 10 à 50 M€"
    elif valeur >= 1:
        n, m = 20, "marché de 1 à 10 M€"
    elif valeur > 0:
        n, m = 10, "marché inférieur à 1 M€"
    else:
        n, m = 12, "montant non chiffré (ni pénalisé ni récompensé)"
    motifs.append(m)
    surete = _val(leads, "surete")
    n += min(40, surete * 4)
    motifs.append("besoin de sûreté analysé à {:.1f}/10".format(surete))
    if any(l.get("etranger_titulaire") for l in leads):
        n += 20
        motifs.append("titulaire étranger : déploiement d'expatriés probable")
    return (min(100, round(n)), motifs)


def timing(leads, aujourd=None):
    """« Est-ce le bon moment ? » Echeance, fenetre, franchissement d'etape."""
    auj = aujourd or datetime.date.today()
    motifs, n = [], 30
    jours = None
    for l in leads:
        d = str(l.get("deadline") or "")[:10]
        try:
            j = (datetime.date.fromisoformat(d) - auj).days
        except ValueError:
            continue
        jours = j if jours is None else min(jours, j)
    if jours is not None:
        if jours < 0:
            n, motifs = 5, ["avis clôturé : l'occasion directe est passée"]
            return (n, motifs)
        if jours <= 7:
            n, m = 95, "clôture dans moins d'une semaine"
        elif jours <= 30:
            n, m = 85, "clôture dans le mois"
        elif jours <= 90:
            n, m = 65, "clôture dans le trimestre"
        else:
            n, m = 45, "échéance lointaine"
        motifs.append(m)
    fen = {l.get("win") for l in leads}
    if "immediate" in fen:
        n = max(n, 90)
        motifs.append("fenêtre d'action immédiate")
    elif "court_terme" in fen:
        n = max(n, 70)
        motifs.append("fenêtre d'action à court terme")
    if not motifs:
        motifs.append("aucune échéance connue : timing indéterminé")
    return (min(100, round(n)), motifs)


def winability(leads):
    """« Est-ce realiste de gagner ? »

    C'est la dimension que l'audit externe reclamait en croyant qu'elle
    n'existait pas. Elle existe : `accessibilite_commerciale` est ponderee
    depuis toujours dans le score commercial (+3 / +1,5 / 0). Elle etait
    simplement fondue dans un chiffre unique. Ici on la sort, on la nomme, et
    on lui adjoint les deux autres signaux deja collectes."""
    motifs, n = [], 50
    acces = {l.get("acces") for l in leads if l.get("acces")}
    if "facile" in acces:
        n += 25
        motifs.append("accès commercial facile")
    elif "moyenne" in acces:
        n += 10
        motifs.append("accès commercial moyen")
    elif "difficile" in acces:
        n -= 20
        motifs.append("accès commercial difficile")
    if any(l.get("secu") for l in leads):
        n -= 25
        motifs.append("sûreté déjà en place chez le client : titulaire à déloger")
    client = {l.get("client") for l in leads if l.get("client")}
    if "etat_administration_locale" in client:
        n -= 15
        motifs.append("acheteur public local : procédure longue et peu accessible")
    elif client & {"entreprise_privee", "bailleur_donateur"}:
        n += 10
        motifs.append("acheteur privé ou bailleur : décision plus directe")
    if not motifs:
        motifs.append("aucun signal d'accessibilité : winability indéterminée")
    return (max(0, min(100, round(n))), motifs)


def fit(leads):
    """« Est-ce dans notre coeur de metier ? » Nature du deploiement."""
    motifs, n = [], 40
    natures = {l.get("nature") for l in leads if l.get("nature")}
    if "expatrie_significatif" in natures:
        n += 45
        motifs.append("déploiement d'expatriés significatif")
    elif "mixte" in natures:
        n += 30
        motifs.append("encadrement international, main-d'œuvre locale")
    elif "local_uniquement" in natures:
        n -= 10
        motifs.append("personnel local uniquement : besoin d'expatriation faible")
    elif "aucun_deploiement" in natures:
        n -= 30
        motifs.append("aucun déploiement humain détecté")
    besoins = {l.get("besoin") for l in leads if l.get("besoin")}
    if "fort" in besoins:
        n += 20
        motifs.append("besoin de sûreté fort")
    elif "faible" in besoins:
        n -= 10
        motifs.append("besoin de sûreté faible")
    if not motifs:
        motifs.append("nature du déploiement non analysée")
    return (max(0, min(100, round(n))), motifs)


def confiance(leads):
    """« Quelle est la qualite des preuves ? »

    Deux facteurs seulement, et les deux sont mesurables : le nombre de
    sources INDEPENDANTES (cf. `sources_distinctes`) et la fraicheur du signal
    le plus recent. Une opportunite attestee par une seule source de l'an
    dernier ne merite pas la meme confiance qu'une opportunite corroboree par
    trois canaux ce mois-ci."""
    motifs = []
    n_src = sources_distinctes(leads)
    n = {0: 10, 1: 35}.get(n_src, min(85, 35 + 25 * (n_src - 1)))
    motifs.append("{} source{} indépendante{}".format(
        n_src, "s" if n_src > 1 else "", "s" if n_src > 1 else ""))
    if n_src == 1:
        motifs.append("non corroborée : à vérifier avant d'engager du temps")
    dates = sorted(str(l.get("date_det") or "")[:10] for l in leads
                   if str(l.get("date_det") or "")[:10])
    if dates:
        motifs.append("signal le plus récent : {}".format(dates[-1]))
    else:
        n -= 15
        motifs.append("aucune date de détection : ancienneté inconnue")
    return (max(0, min(100, round(n))), motifs)


# ===========================================================================
# 3. PRIORITE COMMERCIALE
# ===========================================================================
# Une note unique reste necessaire pour TRIER. La difference avec avant, c'est
# qu'elle est desormais decomposee et que ses composantes sont affichees : le
# commercial voit POURQUOI un dossier est haut ou bas.
POIDS = {"attractivite": 0.30, "timing": 0.25, "winability": 0.25, "fit": 0.20}


def priorite(dims):
    """Moyenne ponderee, ATTENUEE par la confiance. Fonction PURE.

    L'attenuation est le point important : une opportunite magnifique attestee
    par une seule source de l'an dernier ne doit pas trôner en tête de liste.
    Elle est bornee a 0,7 pour qu'une confiance faible degrade sans effacer --
    sinon on cacherait des dossiers qu'il faut justement aller verifier."""
    base = sum(dims.get(k, (0, []))[0] * p for k, p in POIDS.items())
    conf = dims.get("confiance", (50, []))[0]
    return round(base * (0.7 + 0.3 * conf / 100))


# ===========================================================================
# 4. CONSTRUCTION
# ===========================================================================
def construire(leads, aujourd=None):
    """[opportunites] triees par priorite decroissante. Fonction PURE.

    Aucun appel reseau ni base : la persistance est le travail de
    `radar_stockage.enregistrer_opportunites`."""
    auj = aujourd or datetime.date.today()
    groupes = collections.OrderedDict()
    for l in (leads or []):
        # Une attribution deja gagnee par un tiers n'est pas une opportunite a
        # saisir. Elle reste utile (le titulaire est un prospect) mais elle a
        # son propre onglet : la compter ici regonflerait le pipeline, ce que
        # la separation du 25/08 a precisement supprime.
        if l.get("src") == "ATTRIB":
            continue
        if l.get("statut") in ("non_pertinent", "écarté", "perdu"):
            continue
        cle = cle_opportunite(l)
        if cle:
            groupes.setdefault(cle, []).append(l)

    out = []
    for cle, groupe in groupes.items():
        dims = {"attractivite": attractivite(groupe),
                "timing": timing(groupe, auj),
                "winability": winability(groupe),
                "fit": fit(groupe),
                "confiance": confiance(groupe)}
        dates = sorted(str(l.get("date_det") or "")[:10] for l in groupe
                       if str(l.get("date_det") or "")[:10])
        derniere = dates[-1] if dates else ""
        try:
            dormante = (auj - datetime.date.fromisoformat(derniere)).days > JOURS_DORMANCE
        except ValueError:
            dormante = False
        principal = max(groupe, key=lambda l: float(l.get("final") or 0))
        out.append({
            "opportunity_id": cle,
            "titre": principal.get("titre", ""),
            "pays": principal.get("pays", ""),
            "zone": principal.get("zone", ""),
            "secteur": principal.get("sect") or principal.get("grp") or "",
            "ent_cle": next((l.get("ent_cle") for l in groupe
                             if l.get("ent_cle")), ""),
            "projet_id": next((l.get("projet_id") for l in groupe
                               if l.get("projet_id")), ""),
            "sources": sorted({str(l.get("src") or "?") for l in groupe}),
            "n_leads": len(groupe),
            "premiere_vue": dates[0] if dates else "",
            "derniere_vue": derniere,
            "dormante": dormante,
            "dimensions": {k: {"note": v[0], "motifs": v[1]}
                           for k, v in dims.items()},
            "priorite": priorite(dims),
            "statut": principal.get("statut", "nouveau"),
        })
    out.sort(key=lambda o: (-o["priorite"], o["opportunity_id"]))
    return out


def serialiser(opportunites):
    """Aplati pour le stockage et le front (ni dict imbrique ni liste)."""
    lignes = []
    for o in opportunites:
        d = o["dimensions"]
        lignes.append({
            "opportunity_id": o["opportunity_id"], "titre": o["titre"],
            "pays": o["pays"], "zone": o["zone"], "secteur": o["secteur"],
            "ent_cle": o["ent_cle"], "projet_id": o["projet_id"],
            "sources": ", ".join(o["sources"]), "n_leads": o["n_leads"],
            "premiere_vue": o["premiere_vue"], "derniere_vue": o["derniere_vue"],
            "dormante": "oui" if o["dormante"] else "non",
            "priorite": o["priorite"], "statut": o["statut"],
            "attractivite": d["attractivite"]["note"],
            "timing": d["timing"]["note"],
            "winability": d["winability"]["note"],
            "fit": d["fit"]["note"],
            "confiance": d["confiance"]["note"],
            # « | » et non « , » : les motifs contiennent deja des virgules.
            "motifs": " | ".join(
                "{}: {}".format(k, "; ".join(v["motifs"]))
                for k, v in d.items()),
        })
    return lignes
