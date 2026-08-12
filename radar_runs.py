# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- PERSISTANCE DES STATS DE RUN.
===============================================

POURQUOI CE MODULE (02/08/2026)
-------------------------------
Deux mesures utiles ne vivaient que dans les LOGS GitHub (ephemeres, non
agreges) :
  - la SANTE par source (volume + fraicheur), qui montre une source qui
    decline lentement ou qui s'est tue ;
  - le KPI de la retroaction en OMBRE (combien d'actions changeraient si on la
    branchait en direct), qu'il faut accumuler avant de decider de l'activer.

Ce module les PERSISTE, une ligne par run, pour tracer la tendance dans le temps.

MODELE DE DONNEES : AUCUN NOUVEAU SCHEMA
----------------------------------------
On reutilise la table generique de `radar_stockage` (radar_lignes, JSONB) via un
onglet synthetique `runs_radar`. Chaque run ajoute une ou deux lignes (une par
type : "sante", "ombre"), identifiees par `type|horodatage`. L'ecriture passe par
`ecrire_miroir` : BEST-EFFORT, elle ne leve jamais -- une base absente ou en
panne ne coute pas un run (meme doctrine que tout le reste du radar).

Interrupteurs herites de radar_stockage : RADAR_PG=0 ou DATABASE_URL absent ->
inerte (inactif), sans bruit.
"""

import datetime
import os

import radar_stockage


NOM_ONGLET = "runs_radar"
# Une source en REGRESSION (elle produisait, puis 0 pendant N runs consecutifs)
# est alertee. Surchargeable via RADAR_MUET_RUNS. N=3 -> ~1,5 semaine a 2 runs/sem.
SEUIL_MUET_RUNS = int(os.environ.get("RADAR_MUET_RUNS", "3"))


def horodatage():
    """Instant du run, ISO UTC a la seconde. Sert d'identifiant de ligne."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def construire_enregistrement(type_, charge, horo=None):
    """Ligne de stat de run (PURE, testable sans base).

    publication_number = "type|horodatage" : unique par (type, run). Deux types
    coexistent donc sans conflit, et chaque run AJOUTE une ligne (accumulation,
    jamais d'ecrasement d'un run precedent)."""
    horo = horo or horodatage()
    enr = {"type": str(type_), "horodatage": horo,
           "publication_number": "{}|{}".format(type_, horo)}
    enr.update(charge or {})
    return enr


def enregistrer(type_, charge, horo=None):
    """Persiste une stat de run. BEST-EFFORT : renvoie une phrase de journal,
    ne leve JAMAIS (delegue a radar_stockage.ecrire_miroir)."""
    enr = construire_enregistrement(type_, charge, horo)
    return radar_stockage.ecrire_miroir(NOM_ONGLET, [enr])


def historique(limite=30, type_=None):
    """Derniers enregistrements (plus recents d'abord), best-effort.

    Renvoie [] si la base est inactive ou indisponible (jamais d'exception).
    Filtre optionnel par type ("sante" ou "ombre")."""
    if not radar_stockage.actif():
        return []
    try:
        with radar_stockage.connexion() as conn:
            radar_stockage.initialiser(conn)
            lignes = radar_stockage.lire_onglet(conn, NOM_ONGLET)
    except Exception:
        return []
    lignes = [l for l in (lignes or []) if isinstance(l, dict)]
    if type_:
        lignes = [l for l in lignes if l.get("type") == type_]
    lignes.sort(key=lambda l: l.get("horodatage", ""), reverse=True)
    try:
        limite = max(0, int(limite))
    except (TypeError, ValueError):
        limite = 30
    return lignes[:limite]


# ===========================================================================
# DETECTION DES SOURCES MUETTES (regression silencieuse, 12/08/2026)
# ===========================================================================
# Le mode d'echec le plus vicieux : une source qui produisait s'arrete sans
# erreur (endpoint change, quota epuise, structure modifiee) et le radar continue
# comme si de rien n'etait. Le panneau "Etat du run" le montre passivement ; ici
# on le detecte ACTIVEMENT sur la tendance persistee et on alerte fort.

def sources_muettes(hist_sante, seuil_runs=None):
    """Sources en REGRESSION : presentes et a 0 sur les `seuil_runs` derniers
    runs, ALORS qu'elles produisaient avant. Pur (aucune base).

    On exige une activite passee (n>0 au-dela de la fenetre) pour distinguer une
    panne d'une source chroniquement vide : on n'alerte que sur une vraie
    regression, pas sur un bruit de fond. `hist_sante` = enregistrements 'sante'
    (l'ordre est renormalise ici, plus recent d'abord). Renvoie
    [{src, runs_muets}] trie par source."""
    seuil = seuil_runs or SEUIL_MUET_RUNS
    sante = [r for r in (hist_sante or []) if isinstance(r, dict)
             and r.get("type") == "sante"]
    sante.sort(key=lambda r: r.get("horodatage", ""), reverse=True)
    fenetre = sante[:seuil]
    if len(fenetre) < seuil:
        return []                      # pas assez d'historique pour conclure
    # Sources qui ont deja produit (n>0) AU-DELA de la fenetre.
    actives_avant = set()
    for run in sante[seuil:]:
        for s in (run.get("sources") or []):
            if (s.get("n") or 0) > 0:
                actives_avant.add(s.get("src"))
    # Volumes par source DANS la fenetre.
    vols = {}
    for run in fenetre:
        for s in (run.get("sources") or []):
            vols.setdefault(s.get("src"), []).append(s.get("n") or 0)
    muettes = []
    for src, ns in vols.items():
        if len(ns) >= seuil and all(n == 0 for n in ns) and src in actives_avant:
            muettes.append({"src": src, "runs_muets": len(ns)})
    return sorted(muettes, key=lambda m: m["src"] or "")


def alerter_sources_muettes(seuil_runs=None, emettre=None, hist=None):
    """Detecte et ALERTE fort les sources muettes. Best-effort (ne leve jamais).

    Emet, par source : une ligne lisible ET une annotation GitHub Actions
    (`::warning::`) visible sur le run, sans dependance mail/webapp. Renvoie la
    liste (utile aux tests / a un futur badge dashboard)."""
    emettre = emettre or print
    seuil = seuil_runs or SEUIL_MUET_RUNS
    try:
        if hist is None:
            hist = historique(limite=max(30, seuil * 5), type_="sante")
        muettes = sources_muettes(hist, seuil_runs=seuil)
    except Exception:
        return []
    if not muettes:
        return []
    emettre("  " + "!" * 58)
    for m in muettes:
        emettre("  /!\\ SOURCE MUETTE : {} = 0 resultat depuis {} runs.".format(
            m["src"], m["runs_muets"]))
        emettre("::warning title=Source muette::{} n'a rien produit depuis {} "
                "runs consecutifs -- verifier collecteur, endpoint ou quota."
                .format(m["src"], m["runs_muets"]))
    emettre("  " + "!" * 58)
    return muettes


# ===========================================================================
# ASSEMBLAGE DES CHARGES (pur : ce que chaque appelant persiste)
# ===========================================================================

def charge_sante(sante):
    """Extrait de `radar_dashboard.sante_run(leads)` ce qu'on garde en tendance :
    le compte + l'age par source, et les agregats. Pur."""
    sante = sante or {}
    return {"actives": sante.get("actives", 0),
            "a_verifier": sante.get("a_verifier", 0),
            "sources": [{"src": s.get("src"), "n": s.get("n"),
                         "age": s.get("age"), "etat": s.get("etat")}
                        for s in sante.get("sources", [])]}


def charge_ombre(observateur, mode="ombre"):
    """Extrait de l'observateur d'ombre (signaux_prives) le KPI a suivre. Pur."""
    o = observateur or {}
    n = o.get("n", 0)
    return {"mode": mode, "n": n,
            "actions_changees": o.get("actions_changees", 0),
            "vers_contacter": o.get("vers_contacter", 0),
            "quittent_contacter": o.get("quittent_contacter", 0),
            "delta_moyen": round(o.get("somme_delta_abs", 0.0) / n, 3) if n else 0.0}


# ===========================================================================
# MAIN : lecture autonome de la tendance (verification / coup d'oeil)
# ===========================================================================

def main():
    hist = historique(limite=15)
    if not hist:
        print("(info) Aucune stat de run (base inactive, absente ou vide).")
        return
    print("Dernieres stats de run (plus recentes d'abord) :")
    for r in hist:
        h = r.get("horodatage", "")
        if r.get("type") == "sante":
            print("  [{}] sante : {} source(s) active(s), {} a verifier".format(
                h, r.get("actives"), r.get("a_verifier")))
        elif r.get("type") == "ombre":
            print("  [{}] ombre ({}) : {} signal(aux), {} action(s) changerai(en)t, "
                  "delta moyen {}".format(h, r.get("mode"), r.get("n"),
                                          r.get("actions_changees"), r.get("delta_moyen")))
    muettes = alerter_sources_muettes(hist=[r for r in hist if r.get("type") == "sante"])
    if not muettes:
        print("  (ok) aucune source en regression silencieuse.")


if __name__ == "__main__":
    main()
