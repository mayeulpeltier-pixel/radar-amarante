# -*- coding: utf-8 -*-
"""
Radar Amarante - TABLEAU DE SANTE DES SOURCES (chantier audit, 01/11/2026).

Un collecteur peut passer tous ses tests et ne plus rien ramener depuis des
semaines : les tests ne mesurent pas la PRODUCTION. Ce module lit la base et
donne, par source, le volume, la date du dernier enregistrement, la fraicheur
(jours ecoules) et un etat. Aucun LLM, lecture seule.

Couvre les deux piles : Operations (`radar_lignes`, par onglet) et IE
(`radar_ie_*`). Sortie markdown (resume de job) + CSV. Best-effort : une table
absente ou illisible devient une ligne « indisponible », jamais une erreur.

HONNETETE DU SIGNAL (correction 01/11/2026). La premiere version criait au
loup : elle marquait MUET toute source sans nouveaute depuis 30 jours, sans
distinguer une PANNE d'une source naturellement LENTE. Diagnostic terrain :
DFC (Excel annuel), Proparco (fenetre 730 j) et IFC tournent a chaque run,
memoire intacte, et ne trouvent simplement rien de neuf -- c'est normal, pas
une panne. Prozorro n'a meme pas de collecteur de production (sonde seule).
Chaque source porte donc desormais une CLASSE qui regle son seuil et son alarme :

  - live      collecteur a cadence normale          -> seuils standards (10/30 j)
  - lent      source a faible cadence (annuel, ...)  -> seuils elargis (90/200 j)
  - reference donnee de reference / etat, pas une collecte -> jamais d'alarme
  - sonde     jamais branche en production           -> jamais d'alarme
  - inactif   desactive/bloque/mort, code conserve   -> jamais d'alarme (motif ADB)

Seul signaux_prives / enrichir_entreprises etait une vraie panne (oubli de
branchement Postgres au 22/07) : corrige par ailleurs. Le reste etait du bruit.

L'analyse (fraicheur, classe, etat) est PURE et testable ; la couche base est
injectable.
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
    ("IE echeances", "radar_ie_echeances", "maj_le", None),   # (01/11) etait `cree_le` (inexistante) -> INDISPONIBLE
]

SEUIL_VIEILLIT = config_env.entier("SANTE_SEUIL_VIEILLIT", 10)        # jours, source vive
SEUIL_MUET = config_env.entier("SANTE_SEUIL_MUET", 30)               # jours, source vive
SEUIL_VIEILLIT_LENT = config_env.entier("SANTE_SEUIL_VIEILLIT_LENT", 90)   # source lente
SEUIL_MUET_LENT = config_env.entier("SANTE_SEUIL_MUET_LENT", 200)          # source lente
SORTIE = os.environ.get("SANTE_SORTIE") or "sante_sources_sortie"

# Classe par source, cle = dernier segment du libelle (onglet pour Operations,
# libelle entier pour les tables IE scalaires). Absent => "live".
# La classe se lit sur l'inventaire CLAUDE.md et le terrain, pas sur la fraicheur.
PROFILS = {
    # --- Operations : sources a faible cadence (nouveaute rare = normal) ---
    "dfc_radar": "lent",            # Excel annuel (FY >= 2021)
    "proparco_radar": "lent",       # fenetre 730 j, portail lent
    "ifc_radar": "lent",            # divulgations espacees
    "miga_radar": "lent",           # garanties espacees
    "afdb_projets_radar": "lent",   # projets amont IATI
    "bm_projets_radar": "lent",     # projets amont
    "projets_candidats": "lent",    # decouverte projets (opt-in)
    # --- Operations : code conserve mais pas de flux (motif ADB) ---
    "boamp_radar": "inactif",       # mort (rendement nul), code conserve
    "adb_radar": "inactif",         # desactive RADAR_ADB=0 (portail JS)
    "afdb_radar": "inactif",        # bloque Cloudflare 403 depuis le 29/10
    # --- Operations : jamais un collecteur de production ---
    "prozorro_radar": "sonde",      # existe en sonde seule, aucun prozorro*.py
    # --- Operations : donnees de reference / etat, pas des collectes ---
    "comptes_cibles_bitd": "reference",
    "watchlist_prives": "reference",
    "suivi_prospects": "reference",
    "radar_etat": "reference",
    "prive_vus": "reference",
    # --- IE : enumeration periodique, pas un flux quotidien ---
    "IE vigilance": "lent",         # cartographie SIRENE >= 5000, refaite espacee
}

# Classes dont on ne surveille PAS la fraicheur : etat informatif, jamais alarme.
ETIQUETTE_SANS_ALARME = {"reference": "RÉFÉRENCE", "sonde": "SONDE", "inactif": "INACTIF"}

# Ordre d'affichage : vraies pannes d'abord, informatif en bas.
RANG = {"INDISPONIBLE": 0, "MUET": 1, "VIDE": 2, "SANS DATE": 3,
        "VIEILLIT": 4, "OK": 5, "RÉFÉRENCE": 6, "SONDE": 7, "INACTIF": 8}


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


def classe(source):
    """Classe d'une source (live par defaut). `source` = libelle complet."""
    cle = str(source).split(" / ")[-1]
    return PROFILS.get(cle) or PROFILS.get(source) or "live"


def seuils(cl):
    """(vieillit, muet) selon la classe. Les classes sans alarme n'en ont pas."""
    if cl == "lent":
        return SEUIL_VIEILLIT_LENT, SEUIL_MUET_LENT
    return SEUIL_VIEILLIT, SEUIL_MUET


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


def etat_source(source, fraicheur_jours, lignes):
    """Etat tenant compte de la CLASSE : une source sans alarme reste informative,
    une source lente a des seuils elargis, une source vive les seuils standards."""
    cl = classe(source)
    if cl in ETIQUETTE_SANS_ALARME and lignes:      # presente avec des donnees
        return cl, ETIQUETTE_SANS_ALARME[cl]
    v, m = seuils(cl)
    return cl, etat(fraicheur_jours, lignes, v, m)


def enrichir(stats, aujourd):
    """Ajoute classe, fraicheur et etat a chaque stat {source, lignes, dernier}."""
    sortie = []
    for s in stats:
        fr = jours_depuis(s.get("dernier"), aujourd)
        cl, et = etat_source(s.get("source"), fr, s.get("lignes"))
        sortie.append(dict(s, classe=cl, fraicheur=fr, etat=et))
    # Vraies pannes d'abord (INDISPONIBLE/MUET/VIDE), informatif en bas ;
    # a etat egal, le plus ancien remonte.
    sortie.sort(key=lambda x: (RANG.get(x["etat"], 9), -(x["fraicheur"] or 0)))
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
            "Généré le {}.".format(aujourd.isoformat()),
            "Seuils : vive, vieillit > {} j / muet > {} j ; lente, > {} j / > {} j.".format(
                SEUIL_VIEILLIT, SEUIL_MUET, SEUIL_VIEILLIT_LENT, SEUIL_MUET_LENT),
            "Classes sans alarme (réf. / sonde / inactif) : informatives, jamais « muet ».",
            "", "Résumé : " + (", ".join("{} {}".format(v, k) for k, v in sorted(c.items()))
                               or "aucune source"),
            "", "| État | Source | Classe | Lignes | Dernier | Fraîcheur (j) |",
            "|---|---|---|---|---|---|"]
    for l in lignes_sante:
        tete.append("| {} | {} | {} | {} | {} | {} |".format(
            l["etat"], l["source"], l.get("classe", "live"),
            "-" if l["lignes"] is None else l["lignes"],
            l["dernier"] or "-", "-" if l["fraicheur"] is None else l["fraicheur"]))
    if not lignes_sante:
        tete.append("| INDISPONIBLE | (base injoignable) | - | - | - | - |")
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
        w.writerow(["etat", "source", "classe", "lignes", "dernier", "fraicheur_jours"])
        for l in lignes_sante:
            w.writerow([l["etat"], l["source"], l.get("classe", "live"),
                        l["lignes"], l["dernier"], l["fraicheur"]])
    resume_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume_env:
        with open(resume_env, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
