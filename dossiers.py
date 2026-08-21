# -*- coding: utf-8 -*-
"""
Radar Amarante -- RESOLVEUR DE DOSSIERS (etape 3 du chantier ecosysteme).
===============================================================================

IDEE
----
Un "dossier vivant" regroupe, sous une meme entite, toutes les phases d'un meme
projet Banque Mondiale a travers le temps : amont (bm_projets) -> avis d'appel
d'offres -> attribution. L'ancre est le proj_id BM (motif P######), fiable et
sans matching textuel :
  - amont            : publication_number = "BMP-P######"
  - avis / attribution : champ projet_id = "P######"

Le resolveur produit, pour chaque proj_id rencontre, un dossier avec sa timeline
datee, sa phase courante (la plus avancee presente) et les leads de chaque
phase. Fonction PURE : testable sans reseau.

NB : le rattachement ne concerne que la Banque Mondiale (seule source portant le
proj_id). Les autres sources restent des leads independants. Un dossier reduit a
une seule phase reste utile (projet identifie), mais les dossiers MULTI-PHASES
sont le coeur de la valeur (on suit l'evolution).
"""

import re
from collections import defaultdict

_PROJ = re.compile(r"\bP\d{6}\b")

# Ordre chronologique des phases du cycle de vie d'un marche.
ORDRE_PHASES = ["amont", "avis", "attribution"]


def proj_id_du_lead(lead):
    """proj_id BM (P######) d'un lead, ou '' si aucun. Cherche d'abord le champ
    projet_id (avis/attribution BM), puis publication_number 'BMP-P######'
    (amont)."""
    for champ in (lead.get("projet_id"), lead.get("pub"),
                  lead.get("publication_number")):
        if champ:
            m = _PROJ.search(str(champ))
            if m:
                return m.group(0)
    return ""


def phase_du_lead(lead):
    """Phase du cycle de vie a laquelle appartient un lead."""
    src = (lead.get("src") or "").upper()
    if src == "ATTRIB":
        return "attribution"
    if src == "BMP":
        return "amont"
    return "avis"


def _date(lead):
    return (lead.get("date_pub") or lead.get("date") or lead.get("mois") or "")


def _meta(leads):
    """Metadonnees du dossier : pays, secteur, libelle. On prend le lead le plus
    informatif (titre le plus long) comme reference d'affichage."""
    ref = max(leads, key=lambda l: len(str(l.get("titre") or "")))
    return {
        "pays": ref.get("pays") or ref.get("zone") or "",
        "zone": ref.get("zone") or "",
        "secteur": ref.get("sect") or ref.get("secteur") or "",
        "titre": ref.get("titre") or "",
    }


def construire_dossiers(leads, multi_phases_seulement=False):
    """Regroupe les leads par proj_id BM en dossiers.
    multi_phases_seulement : ne retient que les dossiers presents dans au moins
    deux phases (le cas a vraie valeur de suivi). Defaut False (tous).
    Retour : liste de dossiers, trie par richesse (nb de phases puis nb de leads)
    decroissante. Fonction PURE."""
    par_pid = defaultdict(list)
    for l in leads:
        pid = proj_id_du_lead(l)
        if pid:
            par_pid[pid].append(l)

    dossiers = []
    for pid, items in par_pid.items():
        phases = defaultdict(list)
        for l in items:
            phases[phase_du_lead(l)].append(l)
        if multi_phases_seulement and len(phases) < 2:
            continue
        presentes = [p for p in ORDRE_PHASES if p in phases]
        timeline = sorted(items, key=lambda l: (_date(l), ORDRE_PHASES.index(
            phase_du_lead(l))))
        courante = presentes[-1] if presentes else "avis"
        dossiers.append({
            "proj_id": pid,
            **_meta(items),
            "phases_presentes": presentes,
            "phase_courante": courante,
            "n_phases": len(presentes),
            "n_leads": len(items),
            "phases": {p: phases[p] for p in presentes},
            "timeline": timeline,
        })

    dossiers.sort(key=lambda d: (d["n_phases"], d["n_leads"]), reverse=True)
    return dossiers


def index_par_proj_id(dossiers):
    """{proj_id: dossier} pour rattacher un lead a son dossier cote front."""
    return {d["proj_id"]: d for d in dossiers}
