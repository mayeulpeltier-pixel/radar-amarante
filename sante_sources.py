# -*- coding: utf-8 -*-
"""
Radar Amarante - TABLEAU DE SANTE DES SOURCES (chantier audit, 01/11/2026).

Un collecteur peut passer tous ses tests et ne plus rien ramener depuis des
semaines : les tests ne mesurent pas la PRODUCTION. Ce module lit la base et
donne, par source, le volume, la date du dernier enregistrement, la fraicheur
(jours ecoules) et un etat (OK / VIEILLIT / MUET). Aucun LLM, lecture seule.

Couvre les deux piles : Operations (`radar_lignes`, par onglet) et IE
(`radar_ie_*`). Sortie markdown (resume de job) + CSV. Best-effort : une table
absente ou illisible devient une ligne « indisponible », jamais une erreur.

L'analyse (fraicheur, etat) est PURE et testable ; la couche base est injectable.
"""

import csv
import datetime
import os

import config_env

# (libelle, table, colonne_date, colonne_groupe|None)
SOURCES = [
    ("Operations", "radar_lignes", "date_detection", "onglet"),
    ("IE leads", "radar_ie_leads", "date_detection", "source"),
    ("IE declencheurs", "radar_ie_declencheurs", "cree_le", "type"),
    ("IE vigilance", "radar_ie_vigilance", "detecte_le", None),
    ("IE sanctions", "radar_ie_sanctions", "detecte_le", None),
    ("IE enrichissement", "radar_ie_enrichissement", "enrichi_le", None),
    ("IE echeances", "radar_ie_echeances", "cree_le", None),
]

SEUIL_VIEILLIT = config_env.entier("SANTE_SEUIL_VIEILLIT", 10)   # jours
SEUIL_MUET = config_env.entier("SANTE_SEUIL_MUET", 30)           # jours
SORTIE = os.environ.get("SANTE_SORTIE") or "sante_sources_sortie"


# ===========================================================================
# ANALYSE (pur, testable)
# ===========================================================================
def jours_depuis(date_iso, aujourd):
    """Jours entre `date_iso` (AAAA-MM-JJ...) et aujourd, ou None si illisible."""
    if not date_iso:
        return None
    try:
        d = datetime.date.fromisoformat(str(date_iso)[:10])
    except ValueError:
        return None
    return (aujourd - d).days


def etat(fraicheur_jours, lignes, vieillit=SEUIL_VIEILLIT, muet=SEUIL_MUET):
    """OK / VIEILLIT / MUET / VIDE / INDISPONIBLE selon fraicheur et volume."""
    if lignes is None:
        return "INDISPONIBLE"
    if lignes == 0:
        return "VIDE"
    if fraicheur_jours is None:
        return "SANS DATE"
    if fraicheur_jours > muet:
        return "MUET"
    if fraicheur_jours > vieillit:
        return "VIEILLIT"
    return "OK"


def enrichir(stats, aujourd, vieillit=SEUIL_VIEILLIT, muet=SEUIL_MUET):
    """Ajoute fraicheur et etat a chaque stat {source, lignes, dernier}."""
    sortie = []
    for s in stats:
        fr = jours_depuis(s.get("dernier"), aujourd)
        sortie.append(dict(s, fraicheur=fr,
                           etat=etat(fr, s.get("lignes"), vieillit, muet)))
    # Les problemes d'abord (MUET/VIDE/INDISPONIBLE), puis par fraicheur.
    rang = {"INDISPONIBLE": 0, "MUET": 1, "VIDE": 2, "SANS DATE": 3, "VIEILLIT": 4, "OK": 5}
    sortie.sort(key=lambda x: (rang.get(x["etat"], 9), -(x["fraicheur"] or 0)))
    return sortie


def resume(lignes_sante):
    c = {}
    for l in lignes_sante:
        c[l["etat"]] = c.get(l["etat"], 0) + 1
    return c


# ===========================================================================
# COLLECTE BASE (injectable)
# ===========================================================================
def _stats_table(cur, libelle, table, col_date, col_groupe):
    """Renvoie une liste de stats pour une table (groupee ou non). Best-effort."""
    try:
        if col_groupe:
            cur.execute("SELECT {g}, COUNT(*), MAX({d}) FROM {t} GROUP BY {g} ORDER BY {g}"
                        .format(g=col_groupe, d=col_date, t=table))
            out = []
            for grp, n, dernier in cur.fetchall():
                out.append({"source": "{} / {}".format(libelle, grp or "?"),
                            "lignes": int(n), "dernier": _iso(dernier)})
            return out or [{"source": libelle, "lignes": 0, "dernier": None}]
        cur.execute("SELECT COUNT(*), MAX({d}) FROM {t}".format(d=col_date, t=table))
        n, dernier = cur.fetchone()
        return [{"source": libelle, "lignes": int(n), "dernier": _iso(dernier)}]
    except Exception:  # noqa: BLE001 (table absente / colonne manquante)
        return [{"source": libelle, "lignes": None, "dernier": None}]


def _iso(v):
    if v is None:
        return None
    return str(v)[:10]


def collecter(url, sources=SOURCES):
    """Lit la base et renvoie la liste brute de stats. [] si base injoignable."""
    if not url:
        return []
    try:
        import psycopg
        stats = []
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                for libelle, table, col_date, col_groupe in sources:
                    conn.rollback()
                    stats += _stats_table(cur, libelle, table, col_date, col_groupe)
        return stats
    except Exception as e:  # noqa: BLE001
        print("  (info) base indisponible ({}).".format(str(e)[:120]))
        return []


# ===========================================================================
# RAPPORT
# ===========================================================================
def rapport_markdown(lignes_sante, aujourd):
    c = resume(lignes_sante)
    tete = ["# Tableau de santé des sources", "",
            "Généré le {}. Seuils : vieillit > {} j, muet > {} j.".format(
                aujourd.isoformat(), SEUIL_VIEILLIT, SEUIL_MUET),
            "", "Résumé : " + (", ".join("{} {}".format(v, k) for k, v in sorted(c.items()))
                               or "aucune source"),
            "", "| État | Source | Lignes | Dernier | Fraîcheur (j) |",
            "|---|---|---|---|---|"]
    for l in lignes_sante:
        tete.append("| {} | {} | {} | {} | {} |".format(
            l["etat"], l["source"], "-" if l["lignes"] is None else l["lignes"],
            l["dernier"] or "-", "-" if l["fraicheur"] is None else l["fraicheur"]))
    if not lignes_sante:
        tete.append("| INDISPONIBLE | (base injoignable) | - | - | - |")
    return "\n".join(tete) + "\n"


def main():
    aujourd = datetime.date.today()
    url = os.environ.get("DATABASE_URL")
    lignes_sante = enrichir(collecter(url), aujourd)
    md = rapport_markdown(lignes_sante, aujourd)
    print(md)
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "sante.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(SORTIE, "sante.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["etat", "source", "lignes", "dernier", "fraicheur_jours"])
        for l in lignes_sante:
            w.writerow([l["etat"], l["source"], l["lignes"], l["dernier"], l["fraicheur"]])
    resume_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume_env:
        with open(resume_env, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
