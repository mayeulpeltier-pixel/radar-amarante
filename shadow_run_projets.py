# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- LIVE SHADOW RUN DE PROJECT INTELLIGENCE (lecture seule).
===============================================================================

CE QUE C'EST
------------
Une execution REELLE de la chaine de decouverte sur trois pays (Tanzanie, RDC,
Guinee), instrumentee de bout en bout, qui produit directement les sept
tableaux du rapport d'observation.

CE QU'IL NE FAIT PAS -- GARANTIES D'INNOCUITE
----------------------------------------------
  - AUCUNE ecriture Google Sheets (aucun appel a _ouvrir_classeur).
  - AUCUNE ecriture Postgres (radar_stockage n'est jamais importe).
  - AUCUNE ecriture de radar_etat.json (memoire inter-runs intacte).
  - AUCUNE modification de projets_reference.py.
  - AUCUNE promotion : `promouvable()` est evalue et AFFICHE, mais le registre
    maitre n'est jamais enrichi. Le mot DRY-RUN accompagne chaque promotion
    theorique.
Le seul effet de bord est la consommation de tokens Anthropic (mesuree).

POURQUOI UN APPEL LLM DEDIE
----------------------------
`bitd._appel_llm` plafonne la sortie a 400 tokens et ne renvoie pas la
comptabilite d'usage. Sur un lot de 10 objets JSON avec acteurs, 400 tokens
TRONQUENT probablement la reponse : le lot serait silencieusement perdu. Ce
harnais utilise donc son propre appel, avec un plafond adapte, et MESURE les
troncatures (`stop_reason == "max_tokens"`).

USAGE (GitHub Actions, secrets ANTHROPIC_API_KEY requis)
    python shadow_run_projets.py
    SHADOW_PAYS=TZA python shadow_run_projets.py      # un seul pays
    SHADOW_MAX_ART=15 SHADOW_JOURS=90 python shadow_run_projets.py
"""

import collections
import json
import os
import time
from datetime import datetime, timezone

import bitd_signaux as bitd
import decouverte_projets as dp
import pays_projets_reference as pref
import projets as pj
import projets_reference as ref
import sources_reference as sref
import ted_complet_v14 as ted


PAYS_CIBLES = [p.strip().upper() for p in
               os.environ.get("SHADOW_PAYS", "TZA,COD,GIN").split(",") if p.strip()]
MAX_ART = int(os.environ.get("SHADOW_MAX_ART", "20"))
JOURS = int(os.environ.get("SHADOW_JOURS", "60"))
TAILLE_LOT = int(os.environ.get("SHADOW_LOT", "10"))
MAX_LOTS = int(os.environ.get("SHADOW_MAX_LOTS", "20"))
MAX_TOKENS = int(os.environ.get("SHADOW_MAX_TOKENS", "2000"))
PAUSE = float(os.environ.get("SHADOW_PAUSE", "1.0"))
# Mode diagnostic. Le premier run a produit 0 candidat sans qu'on puisse dire
# si le modele avait REPONDU "aucun projet" ou si sa reponse n'avait pas ete
# COMPRISE : le harnais ne journalisait pas les reponses brutes. C'est un
# manque de l'instrumentation, corrige ici.
DEBUG = os.environ.get("SHADOW_DEBUG", "0") == "1"

# Tarif Haiku 4.5 verifie le 24/08/2026 : 1 $/MTok entree, 5 $/MTok sortie.
PRIX_IN, PRIX_OUT = 1.0, 5.0

M = collections.Counter()          # compteurs globaux
ERREURS = []                       # (etape, detail)
CHRONO = {}                        # duree par pays
USAGE = {"in": 0, "out": 0, "appels": 0, "tronques": 0, "dumps": 0}


# ===========================================================================
# APPEL LLM INSTRUMENTE
# ===========================================================================
def appel_llm_mesure(prompt):
    """Appel Haiku avec comptabilite d'usage et detection de troncature."""
    cle = os.environ.get("ANTHROPIC_API_KEY", "")
    if not cle:
        raise RuntimeError("ANTHROPIC_API_KEY absente")
    session = ted.session_robuste()
    corps = {"model": ted.MODELE, "max_tokens": MAX_TOKENS, "temperature": 0,
             "messages": [{"role": "user", "content": prompt}]}
    USAGE["appels"] += 1
    rep = session.post(ted.ANTHROPIC_ENDPOINT, timeout=90, json=corps,
                       headers={"x-api-key": cle,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json"})
    rep.raise_for_status()
    charge = rep.json()
    u = charge.get("usage") or {}
    USAGE["in"] += int(u.get("input_tokens") or 0)
    USAGE["out"] += int(u.get("output_tokens") or 0)
    if charge.get("stop_reason") == "max_tokens":
        USAGE["tronques"] += 1
        ERREURS.append(("llm", "reponse TRONQUEE (max_tokens) : lot perdu"))
    # CAUSE RACINE DU PREMIER RUN A 0 CANDIDAT : `texte_des_blocs` renvoie un
    # TUPLE (texte, motif_echec), pas une chaine. Le harnais renvoyait le tuple
    # tel quel, `parser_reponse` faisait str(tuple) -> JSON illisible -> chaque
    # lot retournait des entrees vides. Les collecteurs de production, eux,
    # depaquettent correctement (`texte, motif = ...`) : le defaut etait dans
    # l'instrument de mesure, pas dans le systeme mesure.
    texte, motif = ted.texte_des_blocs(charge)
    if texte is None:
        ERREURS.append(("llm", "reponse inexploitable : {}".format(motif)))
        texte = ""
    # AUTO-DIAGNOSTIC. Le flag DEBUG suppose que quelqu'un pense a l'activer,
    # et le premier run l'a justement oublie. Des qu'un lot ne produit AUCUN
    # projet, on dumpe la reponse brute d'office : c'est exactement le cas ou
    # l'on a besoin de savoir si le modele a repondu "aucun projet" ou si sa
    # reponse n'a pas ete comprise. Borne a 2 dumps pour ne pas noyer le log.
    interp = dp.parser_reponse(texte, TAILLE_LOT)
    nommes = sum(1 for e in interp if e["projet"])
    pays = sum(1 for e in interp if e["iso3"])
    muet = (nommes == 0 and pays == 0)
    if DEBUG or (muet and USAGE["dumps"] < 2):
        if muet:
            USAGE["dumps"] += 1
            print("\n    !!! LOT MUET (0 projet, 0 pays) -- reponse brute dumpee "
                  "automatiquement pour diagnostic !!!")
        print("    --- REPONSE BRUTE DU MODELE (appel {}) ---".format(USAGE["appels"]))
        print("    stop_reason={} | blocs={} | {} caracteres".format(
            charge.get("stop_reason"),
            [b.get("type") for b in (charge.get("content") or [])], len(texte or "")))
        print("    " + (texte or "(VIDE : aucun bloc texte)")[:1500].replace("\n", "\n    "))
        print("    --- INTERPRETATION : {}/{} avec nom de projet, {} avec pays ---"
              .format(nommes, TAILLE_LOT, pays))
        if muet and texte and texte.strip():
            print("    LECTURE : le modele A REPONDU. Si sa reponse cite des projets")
            print("    ci-dessus, le defaut est dans parser_reponse. Sinon, il rejette")
            print("    reellement ces articles et le defaut est dans la COLLECTE.")
    return texte


def cout_usd():
    return (USAGE["in"] / 1e6 * PRIX_IN) + (USAGE["out"] / 1e6 * PRIX_OUT)


# ===========================================================================
# COLLECTE INSTRUMENTEE (lecture seule)
# ===========================================================================
def collecter_pays(pays):
    """Articles bruts d'un pays, avec comptage fin des requetes et erreurs."""
    session = ted.session_robuste()
    articles, detail = [], []
    for langue, url in dp.urls_du_pays(pays):
        M["requetes"] += 1
        t0 = time.time()
        try:
            rep = session.get(url, timeout=30)
            rep.raise_for_status()
            M["reponses"] += 1
            lot = bitd.parser_rss(rep.text)[:MAX_ART]
        except Exception as e:
            M["erreurs_http"] += 1
            ERREURS.append(("collecte {} [{}]".format(pays["iso3"], langue),
                            str(e)[:90]))
            lot = []
        M["resultats_rss"] += len(lot)
        for a in lot:
            a["iso3_requete"] = pays["iso3"]
            a["langue_requete"] = langue
        articles.extend(lot)
        detail.append((langue, len(lot), round(time.time() - t0, 1)))
        time.sleep(PAUSE)
    return articles, detail


# ===========================================================================
# MESURE DE LA DEDUPLICATION (dont le bug connu)
# ===========================================================================
def mesurer_doublons(articles):
    """Doublons stricts (meme lien) et quasi-doublons (meme titre normalise).
    Les articles syndiques sont la principale source de gonflement."""
    liens = collections.Counter(a.get("lien", "") for a in articles if a.get("lien"))
    titres = collections.Counter(dp._norm(a.get("titre", "")) for a in articles)
    return {
        "liens_dupliques": sum(n - 1 for n in liens.values() if n > 1),
        "titres_dupliques": sum(n - 1 for n in titres.values() if n > 1),
        "liens_uniques": len(liens),
    }


def mesurer_bug_socle(signaux_classes):
    """Le bug identifie a l'audit : `projets.construire_projets` ne deduplique
    PAS par lien. On mesure son impact reel sur ce run plutot que de le
    contourner : on compare le nombre de signaux avec et sans doublons."""
    if not signaux_classes:
        return {"impact": 0, "note": "aucun signal classe"}
    vus, dedup = set(), []
    for s in signaux_classes:
        l = s.get("lien", "")
        if l and l in vus:
            continue
        vus.add(l)
        dedup.append(s)
    return {"avec_doublons": len(signaux_classes), "sans_doublons": len(dedup),
            "impact": len(signaux_classes) - len(dedup)}


# ===========================================================================
# AFFICHAGE
# ===========================================================================
def famille_source(signal):
    t = sref.type_du_signal(signal)
    return sref.LIBELLE_TYPE.get(t, t)


def afficher_candidat(c, n):
    print("\n" + "-" * 78)
    etat = "PROMOUVABLE (DRY-RUN, non promu)" if dp.promouvable(c) else "en attente"
    print("CANDIDAT {} : {}".format(n, c["nom"]))
    print("  identifiant   : {}".format(
        c.get("id_temporaire") or dp.generer_project_id(c["nom"], c.get("iso3"))))
    print("  pays / secteur: {} / {}".format(c.get("iso3", "?"), c.get("secteur", "?")))
    print("  sans nom      : {}".format("OUI (nom provisoire)" if c.get("sans_nom") else "non"))
    print("  phase         : {}".format(c.get("phase") or "(non determinee)"))
    print("  montant       : {}".format(
        "{} M$".format(int(c["montant_musd"])) if c.get("montant_musd") else "n.c."))
    print("  acteurs       : {}".format(", ".join(c.get("acteurs_top", [])) or "aucun"))
    print("  confiance     : {}/100  ({})".format(c["confiance"], etat))
    print("  poids preuve  : {} | meilleure source {} | {} source(s)".format(
        c.get("poids_sources"), c.get("meilleure_fiabilite"), c.get("nb_sources")))
    print("  motifs        : {}".format(" · ".join(dp.motifs_confiance(c))))
    print("  ARTICLES A L'ORIGINE ({}) :".format(c["nb_signaux"]))
    for s in sorted(c["signaux"], key=lambda x: x.get("date", "")):
        print("    [{}] {:<20} {}".format(
            s.get("date") or "date ?", (s.get("phase") or "-")[:20],
            (s.get("titre") or "")[:70]))
        print("        {}".format(s.get("lien", "")[:100]))


def tableaux(candidats, par_pays, doublons, bug):
    print("\n\n" + "=" * 78)
    print("TABLEAU 1 -- PERFORMANCE TECHNIQUE")
    print("=" * 78)
    print("{:<34} {:>12}   {}".format("Metrique", "Resultat", "Erreurs"))
    lignes = [
        ("Requetes envoyees", M["requetes"], M["erreurs_http"]),
        ("Reponses recues", M["reponses"], ""),
        ("Resultats RSS", M["resultats_rss"], ""),
        ("Articles uniques", M["articles_uniques"], ""),
        ("Articles rejetes (pre-filtres)", M["articles_rejetes"], ""),
        ("Signaux extraits (LLM)", M["signaux_extraits"], USAGE["tronques"]),
        ("Candidats", len(candidats), ""),
        ("dont sans nom", sum(1 for c in candidats if c.get("sans_nom")), ""),
        ("Projets fusionnes (variantes)", M["fusions"], ""),
        ("Doublons liens", doublons["liens_dupliques"], ""),
        ("Doublons titres (syndication)", doublons["titres_dupliques"], ""),
        ("Deja connus (ecartes)", M["deja_connus"], ""),
        ("Nouveaux projets (dry-run)", M["promouvables"], ""),
        ("Appels LLM", USAGE["appels"], USAGE["tronques"]),
        ("Tokens entree", USAGE["in"], ""),
        ("Tokens sortie", USAGE["out"], ""),
        ("Cout USD", "{:.4f}".format(cout_usd()), ""),
        ("Duree totale (s)", round(sum(CHRONO.values()), 1), ""),
    ]
    for lbl, val, err in lignes:
        print("{:<34} {:>12}   {}".format(lbl, val, err))

    print("\n" + "=" * 78)
    print("TABLEAU 2 -- CANDIDATS")
    print("=" * 78)
    print("{:<28} {:<5} {:<22} {:>4} {:>6} {:>5}".format(
        "Projet", "Pays", "Phase", "Src", "Conf", "Ama"))
    for c in candidats:
        ama = score_amarante(c)
        print("{:<28} {:<5} {:<22} {:>4} {:>6} {:>5}".format(
            c["nom"][:28], c.get("iso3", "?"), (c.get("phase") or "-")[:22],
            c.get("nb_sources", 0), c["confiance"], ama))

    print("\n" + "=" * 78)
    print("TABLEAU 6 -- SOURCES")
    print("=" * 78)
    fam = collections.Counter()
    fam_cand = collections.Counter()
    for c in candidats:
        familles = {famille_source(s) for s in c["signaux"]}
        for f in familles:
            fam_cand[f] += 1
        for s in c["signaux"]:
            fam[famille_source(s)] += 1
    print("{:<34} {:>9} {:>9}".format("Famille de source", "Signaux", "Candidats"))
    for f, n in fam.most_common():
        print("{:<34} {:>9} {:>9}".format(f, n, fam_cand[f]))
    if not fam:
        print("  (aucun candidat produit)")

    print("\n" + "=" * 78)
    print("TABLEAU 7 -- COUTS PAR PAYS")
    print("=" * 78)
    print("{:<6} {:>9} {:>9} {:>9} {:>10}".format(
        "Pays", "Requetes", "Articles", "Signaux", "Duree(s)"))
    for iso3, d in par_pays.items():
        print("{:<6} {:>9} {:>9} {:>9} {:>10}".format(
            iso3, d["requetes"], d["articles"], d["signaux"],
            round(CHRONO.get(iso3, 0), 1)))
    print("\nCout total du run : {:.4f} USD ({} appels, {} tokens in / {} out)".format(
        cout_usd(), USAGE["appels"], USAGE["in"], USAGE["out"]))
    if USAGE["tronques"]:
        print("ALERTE : {} reponse(s) TRONQUEE(S) -> lots perdus, "
              "augmenter SHADOW_MAX_TOKENS.".format(USAGE["tronques"]))

    print("\n" + "=" * 78)
    print("DEDUPLICATION -- impact du bug connu (socle sans dedup par lien)")
    print("=" * 78)
    print("  {}".format(bug))
    print("  Lecture : 'impact' = nombre de signaux comptes en double par")
    print("  projets.construire_projets. Chaque doublon gonfle maturite et confiance.")

    print("\n" + "=" * 78)
    print("TABLEAUX 3, 4, 5 (vrais positifs / faux positifs / faux negatifs)")
    print("=" * 78)
    print("  Ils exigent un JUGEMENT HUMAIN sur les candidats reels ci-dessus.")
    print("  Colle cette sortie dans la conversation : la classification A/B/C/D,")
    print("  les faux positifs et la recherche de faux negatifs seront faites")
    print("  a partir des candidats REELLEMENT produits, pas de fixtures.")


def score_amarante(c):
    """Score Amarante indicatif du candidat, via le socle, SANS l'inscrire au
    registre (on fabrique un projet ephemere en memoire)."""
    faux_projet = {
        "project_id": "SHADOW", "libelle": c["nom"], "iso3": c.get("iso3", ""),
        "pays": c.get("iso3", ""), "secteur": c.get("secteur", "infrastructure"),
        "valeur_musd": c.get("montant_musd", 0),
        "phase_courante": c.get("phase", ""), "nb_signaux": c.get("nb_signaux", 0),
        "derniere_maj": c.get("derniere_maj", ""),
        "acteurs_top": c.get("acteurs_top", []),
    }
    return pj.score_opportunite(faux_projet)["score"]


# ===========================================================================
# ORCHESTRATION
# ===========================================================================
def main():
    debut = datetime.now(timezone.utc)
    print("#" * 78)
    print("LIVE SHADOW RUN -- PROJECT INTELLIGENCE  ({} UTC)".format(
        debut.strftime("%Y-%m-%d %H:%M")))
    print("Pays : {} | fraicheur {} j | max {} articles/requete".format(
        ", ".join(PAYS_CIBLES), JOURS, MAX_ART))
    print("LECTURE SEULE : aucune ecriture Sheet/Postgres/etat, AUCUNE promotion.")
    print("#" * 78)

    dp.JOURS_FRAICHEUR = JOURS
    dp.MAX_ARTICLES = MAX_ART
    dp.TAILLE_LOT = TAILLE_LOT

    tous_articles, tous_signaux, par_pays = [], [], collections.OrderedDict()
    for iso3 in PAYS_CIBLES:
        pays = pref.pays_par_iso3(iso3)
        if not pays:
            ERREURS.append(("config", "pays inconnu au referentiel : " + iso3))
            continue
        t0 = time.time()
        print("\n>>> {} ({}) langues={} cadence={} j".format(
            pays["nom"], iso3, pays["langues"], pref.cadence_jours(pays)))
        articles, detail = collecter_pays(pays)
        for langue, n, sec in detail:
            print("    [{}] {} article(s) en {} s".format(langue, n, sec))
        signaux = dp.preparer(articles, vus=None)     # memoire NON consultee
        M["articles_rejetes"] += len(articles) - len(signaux)
        print("    -> {} article(s), {} retenu(s) apres pre-filtres".format(
            len(articles), len(signaux)))
        # Toujours affiches : sans eux, impossible de juger si la COLLECTE
        # ramene des annonces de projet ou du bruit generaliste.
        if signaux:
            print("    --- ARTICLES RETENUS ({}) : sont-ils des annonces de "
                  "projet ? ---".format(len(signaux)))
            for s in signaux:
                print("      [{}] {}".format(s.get("date") or "date ?",
                                             (s.get("titre") or "")[:90]))
        par_pays[iso3] = {"requetes": len(detail), "articles": len(articles),
                          "signaux": len(signaux)}
        CHRONO[iso3] = time.time() - t0
        tous_articles.extend(articles)
        tous_signaux.extend(signaux)

    doublons = mesurer_doublons(tous_articles)
    M["articles_uniques"] = doublons["liens_uniques"]

    print("\n>>> EXTRACTION LLM ({} signaux, lots de {})".format(
        len(tous_signaux), TAILLE_LOT))
    extraits, lots = dp.extraire_par_lots(tous_signaux, appel=appel_llm_mesure,
                                          max_lots=MAX_LOTS)
    M["signaux_extraits"] = sum(1 for s in extraits
                                if (s.get("extraction") or {}).get("projet")
                                or (s.get("extraction") or {}).get("iso3"))
    print("    {} lot(s) LLM, {} signal(aux) exploitable(s)".format(
        lots, M["signaux_extraits"]))

    # Comptage des projets deja connus (ecartes de la decouverte).
    for s in extraits:
        nom = ((s.get("extraction") or {}).get("projet") or "").strip()
        if nom and dp.deja_connu(nom):
            M["deja_connus"] += 1

    candidats = dp.regrouper(extraits)                # registre REEL, non modifie
    M["fusions"] = sum(len(c.get("alias_fusionnes") or []) for c in candidats)
    M["promouvables"] = sum(1 for c in candidats if dp.promouvable(c))

    print("\n>>> {} CANDIDAT(S) PRODUIT(S)".format(len(candidats)))
    for i, c in enumerate(candidats, 1):
        afficher_candidat(c, i)

    bug = mesurer_bug_socle([s for s in extraits
                             if (s.get("extraction") or {}).get("projet")])
    tableaux(candidats, par_pays, doublons, bug)

    print("\n" + "=" * 78)
    print("ERREURS RENCONTREES ({})".format(len(ERREURS)))
    print("=" * 78)
    for etape, detail in ERREURS[:25] or [("-", "aucune")]:
        print("  {:<26} {}".format(etape[:26], detail))

    print("\nGARANTIE : registre maitre inchange ({} projets), aucune ecriture, "
          "aucune promotion.".format(len(ref.charger_registre())))
    ted.sortie_selon_sante_llm("shadow_run")


if __name__ == "__main__":
    main()
