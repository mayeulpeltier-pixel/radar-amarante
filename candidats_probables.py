# -*- coding: utf-8 -*-
"""
Radar Amarante -- CANDIDATS PROBABLES (Lot 1 du chantier ecosysteme).
===============================================================================

IDEE
----
Pour un nouvel avis de marche, quelles entreprises vont probablement postuler ?
Le meilleur predicteur deterministe, ce sont les INCUMBENTS : les titulaires qui
ont deja gagne des marches SIMILAIRES (meme secteur, meme zone) dans l'historique
des attributions deja collectees. Aucune collecte nouvelle : pure exploitation
des donnees existantes (onglet attributions -> leads src=ATTRIB).

Un incumbent ETRANGER est doublement interessant pour Amarante : il gagne ce
type de marche ET il deploie du personnel expatrie a securiser. Le score de
pertinence bonifie donc les titulaires etrangers.

CE QUE PRODUIT LE MODULE
------------------------
construire_index(leads) -> index a 3 granularites (du plus precis au plus large)
    {
      "secteur_zone": { "Genie civil|Sahel": [ {entreprise, nb, derniere,
                         origine, etranger}, ... ] },
      "secteur":      { "Genie civil": [...] },
      "zone":         { "Sahel": [...] },
    }
Le front (cockpit) cherche d'abord secteur_zone, puis retombe sur secteur, puis
zone. Fonction PURE : testable sans reseau, sans etat.
"""

from collections import defaultdict


def _est_etranger(v):
    """etranger_titulaire peut arriver en bool ou en 'oui'/'non' (string)."""
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("oui", "true", "1", "yes")


def _slot():
    return {"nb": 0, "derniere": "", "origine": "", "etranger": False}


def _pertinence(c):
    """Score de tri : frequence + bonus etranger (prospect Amarante par
    excellence). La recence departage a frequence egale."""
    return (c["nb"] + (2 if c["etranger"] else 0), c["derniere"])


def construire_index(leads, top=8):
    """leads (schema dashboard) -> index des incumbents par secteur/zone.
    N'utilise que les leads d'attribution (src=ATTRIB) avec un titulaire nomme.
    Fonction PURE."""
    par_sz = defaultdict(lambda: defaultdict(_slot))   # (secteur, zone)
    par_s = defaultdict(lambda: defaultdict(_slot))    # secteur
    par_z = defaultdict(lambda: defaultdict(_slot))    # zone

    for l in leads:
        if l.get("src") != "ATTRIB":
            continue
        ent = (l.get("entreprise") or "").strip()
        if len(ent) < 3:
            continue
        sect = (l.get("sect") or "").strip() or "Autre"
        zone = (l.get("zone") or "").strip() or "Non classe"
        origine = (l.get("origine") or "").strip()
        etr = _est_etranger(l.get("etranger_titulaire"))
        date = (l.get("mois") or l.get("date_det") or "").strip()
        for table, key in ((par_sz, (sect, zone)), (par_s, sect), (par_z, zone)):
            s = table[key][ent]
            s["nb"] += 1
            if date > s["derniere"]:
                s["derniere"] = date
            if origine and not s["origine"]:
                s["origine"] = origine
            if etr:
                s["etranger"] = True

    def finaliser(table):
        out = {}
        for key, ents in table.items():
            lst = [dict(entreprise=e, **st) for e, st in ents.items()]
            lst.sort(key=_pertinence, reverse=True)
            cle = "|".join(key) if isinstance(key, tuple) else key
            out[cle] = lst[:top]
        return out

    return {
        "secteur_zone": finaliser(par_sz),
        "secteur": finaliser(par_s),
        "zone": finaliser(par_z),
    }


# ===========================================================================
# BIDDER INTELLIGENCE (P2.6, 26/08/2026)
# ===========================================================================
# CE QUE LA v1 CONFOND
# --------------------
# `_pertinence` additionne la frequence ET un bonus de +2 aux titulaires
# ETRANGERS, sous le nom de « candidats probables ». Or ce sont DEUX questions
# differentes :
#
#   « qui va soumissionner ? »        -> une prevision, fondee sur l'historique
#   « qui interesse Amarante ? »      -> une preference commerciale
#
# Etre etranger ne rend PAS une entreprise plus susceptible de soumissionner.
# Cela la rend plus interessante A DEMARCHER, ce qui est autre chose. Melanges,
# les deux produisent un classement qui se presente comme une prevision et n'en
# est pas une : une entreprise etrangere a 3 marches passe devant une locale a
# 5 (verifie le 26/08).
#
# On separe donc les deux scores. La v1 reste en place -- d'autres appels en
# dependent -- mais elle n'est plus le dernier mot.
#
# CE QU'ON N'AJOUTE PAS
# ---------------------
# L'audit externe proposait consortiums, implantation locale, prequalification.
# Aucun de ces champs n'est collecte : les inventer donnerait une probabilite
# d'apparence savante et sans fondement. On n'utilise QUE ce qui est en base :
# acheteur, secteur, zone, recence, ordre de grandeur du montant.

import datetime as _dt

# Un titulaire vu il y a plus de ce delai n'est plus un indice fiable de
# participation : les equipes changent, les agrements expirent.
MOIS_MEMOIRE = 48


def _mois_ecart(date_ref, aujourd):
    """Nombre de mois entre une date « AAAA-MM » (ou ISO) et aujourd'hui."""
    txt = str(date_ref or "")[:7]
    try:
        an, mois = int(txt[:4]), int(txt[5:7])
    except (ValueError, IndexError):
        return None
    return (aujourd.year - an) * 12 + (aujourd.month - mois)


def _bande(valeur):
    """Ordre de grandeur d'un marche. Deux marches de la meme bande relevent
    des memes capacites : c'est un indice de participation, pas une preuve.

    MEME PIEGE QUE DANS `opportunites` (26/08) : selon l'endroit de la chaine,
    un lead porte `valeur` en CHAINE brute (« 10000000 EUR ») ou `valeur_meur`
    deja convertie. On delegue la lecture au module qui a deja resolu le
    probleme, plutot que d'en ecrire une troisieme version -- trois
    conversions divergentes seraient trois bugs a venir."""
    try:
        from opportunites import _nombre
        v = _nombre(valeur)
    except Exception:
        try:
            v = float(valeur or 0)
        except (TypeError, ValueError):
            return ""
    if v > 10000:                 # brut en euros, pas en millions
        v = v / 1e6
    if v <= 0:
        return ""
    if v < 1:
        return "<1M"
    if v < 10:
        return "1-10M"
    if v < 50:
        return "10-50M"
    return ">50M"


def historique_titulaires(leads, aujourd=None):
    """Historique par titulaire, tous axes confondus. Fonction PURE.

    {entreprise: {secteurs, zones, acheteurs, bandes, derniere, nb, origine,
                  etranger}}"""
    auj = aujourd or _dt.date.today()
    out = {}
    for l in (leads or []):
        if l.get("src") != "ATTRIB":
            continue
        ent = (l.get("entreprise") or "").strip()
        if len(ent) < 3:
            continue
        h = out.setdefault(ent, {
            "entreprise": ent, "secteurs": set(), "zones": set(),
            "acheteurs": set(), "bandes": set(), "derniere": "", "nb": 0,
            "origine": "", "etranger": False})
        h["nb"] += 1
        h["secteurs"].add((l.get("sect") or "").strip() or "Autre")
        h["zones"].add((l.get("zone") or "").strip() or "Non classe")
        ach = (l.get("agence") or l.get("acheteur") or "").strip().lower()
        if ach:
            h["acheteurs"].add(ach)
        b = _bande(l.get("valeur_meur") or l.get("valeur"))
        if b:
            h["bandes"].add(b)
        d = (l.get("mois") or l.get("date_det") or "").strip()
        if d > h["derniere"]:
            h["derniere"] = d
        if (l.get("origine") or "").strip() and not h["origine"]:
            h["origine"] = l["origine"].strip()
        if _est_etranger(l.get("etranger_titulaire")):
            h["etranger"] = True
    return out


def probabilite_participation(avis, hist, aujourd=None):
    """(0-100, motifs) : quelle chance que CE titulaire soumissionne sur CET
    avis. Fonction PURE.

    Ce score ne dit RIEN de l'interet commercial pour Amarante : c'est
    volontaire, voir l'encadre en tete de section. Toutes les composantes sont
    des faits deja en base, aucune n'est extrapolee."""
    auj = aujourd or _dt.date.today()
    motifs, n = [], 5
    sect = (avis.get("sect") or "").strip() or "Autre"
    zone = (avis.get("zone") or "").strip() or "Non classe"
    ach = (avis.get("agence") or avis.get("acheteur") or "").strip().lower()

    # L'acheteur est le signal le plus fort : une administration reconduit
    # souvent des titulaires qu'elle connait deja.
    if ach and ach in hist.get("acheteurs", set()):
        n += 35
        motifs.append("a déjà remporté un marché de cet acheteur")
    memes = (sect in hist.get("secteurs", set()),
             zone in hist.get("zones", set()))
    if all(memes):
        n += 25
        motifs.append("actif sur ce secteur ET ce théâtre")
    elif memes[0]:
        n += 12
        motifs.append("actif sur ce secteur")
    elif memes[1]:
        n += 10
        motifs.append("actif sur ce théâtre")
    # Recence : un titulaire de 2019 n'est pas un indice de 2026. La v1 ne
    # s'en servait que pour departager les ex aequo.
    mois = _mois_ecart(hist.get("derniere"), auj)
    if mois is None:
        motifs.append("dernière attribution non datée")
    elif mois <= 12:
        n += 15
        motifs.append("actif dans les 12 derniers mois")
    elif mois <= 24:
        n += 8
        motifs.append("actif dans les 24 derniers mois")
    elif mois > MOIS_MEMOIRE:
        n -= 15
        motifs.append("aucune attribution depuis plus de {} ans".format(
            MOIS_MEMOIRE // 12))
    b = _bande(avis.get("valeur_meur") or avis.get("valeur"))
    if b and b in hist.get("bandes", set()):
        n += 8
        motifs.append("déjà positionné sur des marchés de cet ordre ({})".format(b))
    nb = hist.get("nb", 0)
    if nb >= 3:
        n += 10
        motifs.append("{} marchés remportés au total".format(nb))
    return (max(0, min(95, n)), motifs)


def interet_amarante(hist):
    """(0-100, motifs) : ce titulaire vaut-il d'etre demarche ?

    SEPARE de la probabilite a dessein. Une entreprise peut etre tres probable
    et sans interet (locale, sans expatries), ou peu probable et tres
    interessante (etrangere, deployant des equipes)."""
    motifs, n = 30, []
    n, motifs = 30, []
    if hist.get("etranger"):
        n += 40
        motifs.append("titulaire étranger : expatriés probables à protéger")
    if len(hist.get("zones", set())) >= 2:
        n += 15
        motifs.append("présent sur {} théâtres".format(len(hist["zones"])))
    if hist.get("nb", 0) >= 3:
        n += 15
        motifs.append("titulaire récurrent en zone à risque")
    if hist.get("origine"):
        motifs.append("origine : {}".format(hist["origine"]))
    if not motifs:
        motifs.append("aucun signal d'intérêt particulier")
    return (min(100, n), motifs)


def soumissionnaires_probables(avis, leads, n=5, aujourd=None, hists=None):
    """[{entreprise, probabilite, interet, motifs, ...}] pour un avis donne.

    Trie par PROBABILITE, pas par interet : la question posee est « qui va
    soumissionner », et melanger les deux etait le defaut de la v1. L'interet
    est rendu a cote pour que l'arbitrage reste possible a l'oeil."""
    # `hists` est le MEME pour tous les avis d'un run : l'appelant qui boucle
    # sur des centaines d'avis doit le construire UNE fois et le passer ici.
    # Sans ce parametre, on reparcourait tout le corpus a chaque appel.
    if hists is None:
        hists = historique_titulaires(leads, aujourd)
    out = []
    for ent, h in hists.items():
        p, mp = probabilite_participation(avis, h, aujourd)
        if p < 20:                      # en dessous, ce n'est plus un indice
            continue
        i, mi = interet_amarante(h)
        out.append({"entreprise": ent, "probabilite": p, "interet": i,
                    "motifs": mp, "motifs_interet": mi,
                    "origine": h["origine"], "etranger": h["etranger"],
                    "nb": h["nb"], "derniere": h["derniere"]})
    out.sort(key=lambda c: (-c["probabilite"], -c["interet"], c["entreprise"]))
    return out[:n]


def candidats_pour(secteur, zone, index, n=6):
    """Meilleurs candidats pour un (secteur, zone) : du plus precis au plus
    large. Sert de reference cote Python ; le cockpit fait la meme cascade en
    JS. Ne renvoie jamais None."""
    if not index:
        return []
    sect = (secteur or "").strip() or "Autre"
    zn = (zone or "").strip() or "Non classe"
    return (index.get("secteur_zone", {}).get(sect + "|" + zn)
            or index.get("secteur", {}).get(sect)
            or index.get("zone", {}).get(zn)
            or [])[:n]
