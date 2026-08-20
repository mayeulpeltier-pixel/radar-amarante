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
