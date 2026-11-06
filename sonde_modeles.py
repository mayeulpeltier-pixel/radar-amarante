"""Sonde jetable : migration des modeles LLM (Haiku/Sonnet 4.x -> 5.5).

QUESTION POSEE (06/11/2026)
---------------------------
Le pipeline tourne sur `claude-haiku-4-5-20251001` (volume) et
`claude-sonnet-4-6` (escalade), aucune surcharge RADAR_MODELE dans les
workflows. Les successeurs annonces sont moins chers et plus recents :
  - Haiku 5.5  : ~0,10 $ / 0,50 $ par MTok (prompts < 100k tokens)
  - Sonnet 5.5 : 2 $ / 10 $ par MTok (contre 3 $ / 15 $ pour Sonnet 4.6)
Avant de basculer quoi que ce soit, trois questions :
  1. COMPATIBILITE : les nouveaux modeles acceptent-ils l'appel tel qu'il est
     construit aujourd'hui (`temperature: 0` en dur dans `appeler_modele`) ?
     Le code previent deja que Sonnet 5 rejette ce parametre.
  2. QUALITE : sur de VRAIS avis TED, leurs extractions menent-elles aux memes
     decisions (score final, niveau d'opportunite, securite en place,
     declenchement de l'escalade) que les modeles actuels ?
  3. COUT : combien coute reellement un avis, tokens mesures, pas estimes ?

CE QUE LA SONDE NE FAIT PAS
---------------------------
Elle n'ecrit RIEN : ni Sheet, ni Postgres, ni etat, ni dashboard. Elle ne
modifie aucun collecteur. Elle lit TED (meme requete que la production),
appelle les modeles, et produit un rapport console + un JSON d'artefact.
Sonde jetable : a supprimer une fois la bascule decidee.

GARDE-FOUS
----------
- Plafond de depense `SONDE_PLAFOND_USD` (defaut 3 $) : au-dela, plus aucun
  appel. Un echantillon de 25 avis x 4 modeles coute de l'ordre de 1 $.
- `SONDE_DRYRUN=1` : collecte + echantillon + estimation, ZERO appel modele.
- Les appels passent par une fonction injectable : tout le calcul (parsing,
  comparaison, couts) est pur et teste sans reseau (test_sonde_modeles.py).

UTILISATION : Actions > "Sonde migration modeles" > Run workflow.
"""

import json
import os
import sys
import time

import config_env
import ted_complet_v14 as ted

# ===========================================================================
# CONFIGURATION
# ===========================================================================

# Tarifs list Anthropic en $ par million de tokens (entree, sortie), releves
# le 06/11/2026 sur des sources publiques recoupees. Un modele absent de la
# table est compte a 0 et SIGNALE dans le rapport (jamais un cout invente).
TARIFS_USD_MTOK = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}

# Paires (reference actuelle, candidat). La troisieme paire repond a une
# question differente : le NOUVEAU modele rapide approche-t-il l'ACTUEL modele
# d'escalade ? Si oui, on peut escalader moins souvent.
ROLES = ("volume_actuel", "volume_candidat", "escalade_actuelle", "escalade_candidate")


def modeles_configures(env=None):
    """Les 4 modeles compares, surchargeables par variables d'environnement."""
    env = os.environ if env is None else env
    return {
        "volume_actuel": env.get("SONDE_VOLUME_ACTUEL") or ted.MODELE,
        "volume_candidat": env.get("SONDE_VOLUME_CANDIDAT") or "claude-haiku-5-5",
        "escalade_actuelle": env.get("SONDE_ESCALADE_ACTUELLE") or ted.MODELE_RAFFINEMENT,
        "escalade_candidate": env.get("SONDE_ESCALADE_CANDIDATE") or "claude-sonnet-5-5",
    }


PAIRES = (
    ("volume_actuel", "volume_candidat"),
    ("escalade_actuelle", "escalade_candidate"),
    ("escalade_actuelle", "volume_candidat"),
)

# Champs categoriels compares un a un (ceux qui pilotent le score et l'action).
CHAMPS_COMPARES = (
    "deploiement_terrain_reel",
    "niveau_opportunite_amarante",
    "securite_existante",
    "type_mobilite",
    "profil_personnes_exposees",
    "type_client",
    "accessibilite_commerciale",
    "besoin_securite_operationnel_probable",
)

ECART_SCORE_NOTABLE = 2.0  # meme seuil que `divergence` dans le coeur TED


# ===========================================================================
# FONCTIONS PURES (testees sans reseau)
# ===========================================================================

def cout_usd(modele, tokens_entree, tokens_sortie, tarifs=None):
    """Cout d'un appel en $. (cout, connu). Modele inconnu -> (0.0, False)."""
    tarifs = TARIFS_USD_MTOK if tarifs is None else tarifs
    prix = tarifs.get(modele)
    if prix is None:
        return 0.0, False
    return (tokens_entree * prix[0] + tokens_sortie * prix[1]) / 1_000_000, True


def est_refus_temperature(code_http, corps):
    """Vrai si l'API refuse la requete A CAUSE du parametre temperature.

    On exige un 400 ET la mention du parametre : un 400 generique (prompt trop
    long, JSON mal forme) ne doit pas etre pris pour une incompatibilite."""
    if code_http != 400:
        return False
    bas = str(corps or "").lower()
    return "temperature" in bas


def parser_extraction(texte):
    """Parsing GRATUIT uniquement (niveaux 1 et 2 de `ted.appeler_llm`).

    La reparation payante par Sonnet est volontairement exclue : on mesure ici
    la capacite du modele a produire du JSON valide du premier coup. Un echec
    ici = une reparation payante en production. (extraction|None, niveau)
    avec niveau in {"direct", "extrait", "echec"}."""
    if not texte:
        return None, "echec"
    t = texte.strip()
    if t.startswith("```"):
        morceaux = t.split("```")
        t = morceaux[1] if len(morceaux) > 1 else t
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        v = json.loads(t)
        if isinstance(v, dict):
            return ted.normaliser_securite(v), "direct"
    except (json.JSONDecodeError, ValueError):
        pass
    debut, fin = t.find("{"), t.rfind("}")
    if debut != -1 and fin > debut:
        try:
            v = json.loads(t[debut:fin + 1])
            if isinstance(v, dict):
                return ted.normaliser_securite(v), "extrait"
        except (json.JSONDecodeError, ValueError):
            pass
    return None, "echec"


def merite_escalade(extraction, score_final):
    """Reproduit le critere d'escalade du coeur TED (main, V8) :
    score >= 5, ou confiance < 0,7, ou securite deja en place."""
    if extraction is None:
        return False
    if score_final >= 5:
        return True
    try:
        if float(extraction.get("confiance", 1.0)) < 0.7:
            return True
    except (TypeError, ValueError):
        pass
    return ted.escalade_pour_securite(extraction)


def classe_action(score_final):
    """Classe d'action telle que la voit la BD (seuils du coeur)."""
    if score_final >= ted.SEUIL_ALERTE:
        return "contacter"
    if score_final >= ted.SEUIL_SURVEILLANCE:
        return "surveiller"
    return "ignorer"


def _norm(v):
    if isinstance(v, str):
        return v.strip().lower()
    return v


def comparer_paire(resultats, role_a, role_b):
    """Compare deux modeles sur les avis ou LES DEUX ont produit une extraction.

    `resultats` : liste de dicts {"avis": ..., "par_role": {role: r}} ou r
    porte "extraction", "score". Renvoie un resume chiffre + la liste des
    desaccords de classe d'action (ceux qui changent ce que voit la BD)."""
    communs = [x for x in resultats
               if x["par_role"].get(role_a, {}).get("extraction") is not None
               and x["par_role"].get(role_b, {}).get("extraction") is not None]
    n = len(communs)
    resume = {"paire": "{} vs {}".format(role_a, role_b), "avis_communs": n,
              "accord_champs": {}, "ecart_score_moyen": None,
              "ecarts_notables": 0, "accord_classe_action": None,
              "accord_escalade": None, "desaccords_action": []}
    if not n:
        return resume
    for champ in CHAMPS_COMPARES:
        egaux = sum(1 for x in communs
                    if _norm(x["par_role"][role_a]["extraction"].get(champ))
                    == _norm(x["par_role"][role_b]["extraction"].get(champ)))
        resume["accord_champs"][champ] = round(egaux / n, 3)
    ecarts, classes_ok, escalade_ok = [], 0, 0
    for x in communs:
        ra, rb = x["par_role"][role_a], x["par_role"][role_b]
        ecart = abs(ra["score"] - rb["score"])
        ecarts.append(ecart)
        if ecart >= ECART_SCORE_NOTABLE:
            resume["ecarts_notables"] += 1
        ca, cb = classe_action(ra["score"]), classe_action(rb["score"])
        if ca == cb:
            classes_ok += 1
        else:
            resume["desaccords_action"].append({
                "publication_number": x["avis"].get("publication_number", ""),
                "titre": str(x["avis"].get("titre", ""))[:90],
                role_a: {"score": ra["score"], "classe": ca,
                         "justification": str(ra["extraction"].get("justification", ""))[:220]},
                role_b: {"score": rb["score"], "classe": cb,
                         "justification": str(rb["extraction"].get("justification", ""))[:220]},
            })
        if (merite_escalade(ra["extraction"], ra["score"])
                == merite_escalade(rb["extraction"], rb["score"])):
            escalade_ok += 1
    resume["ecart_score_moyen"] = round(sum(ecarts) / n, 2)
    resume["accord_classe_action"] = round(classes_ok / n, 3)
    resume["accord_escalade"] = round(escalade_ok / n, 3)
    return resume


def resume_modele(resultats, role, modele):
    """Fiabilite et cout d'un modele sur l'echantillon."""
    lignes = [x["par_role"].get(role) for x in resultats if x["par_role"].get(role)]
    n = len(lignes)
    ok = [r for r in lignes if r.get("extraction") is not None]
    t_in = sum(r.get("tokens_entree", 0) for r in lignes)
    t_out = sum(r.get("tokens_sortie", 0) for r in lignes)
    cout = sum(r.get("cout_usd", 0.0) for r in lignes)
    lat = [r["latence_s"] for r in lignes if r.get("latence_s") is not None]
    niveaux = {}
    for r in lignes:
        niveaux[r.get("niveau_parsing", "echec")] = niveaux.get(r.get("niveau_parsing", "echec"), 0) + 1
    escalades = sum(1 for r in ok if merite_escalade(r["extraction"], r["score"]))
    return {
        "role": role, "modele": modele, "appels": n,
        "extractions_ok": len(ok),
        "taux_json_direct": round(niveaux.get("direct", 0) / n, 3) if n else None,
        "niveaux_parsing": niveaux,
        "erreurs_api": sum(1 for r in lignes if r.get("erreur")),
        "tronquees": sum(1 for r in lignes if r.get("stop_reason") == "max_tokens"),
        "tokens_entree": t_in, "tokens_sortie": t_out,
        "cout_total_usd": round(cout, 4),
        "cout_par_avis_usd": round(cout / n, 5) if n else None,
        "latence_mediane_s": round(sorted(lat)[len(lat) // 2], 2) if lat else None,
        "taux_escalade": round(escalades / len(ok), 3) if ok else None,
        "score_moyen": round(sum(r["score"] for r in ok) / len(ok), 2) if ok else None,
    }


def echantillonner(avis_normalises, n):
    """Echantillon deterministe : avis AVEC description d'abord (c'est le cas
    le plus riche et le plus representatif de la production), puis les
    autres ; ordre stable par publication_number pour rejouer a l'identique."""
    tries = sorted(avis_normalises,
                   key=lambda a: (0 if a.get("description") else 1,
                                  str(a.get("publication_number", ""))))
    return tries[:max(0, n)]


def estimer_cout(nb_avis, modeles, tokens_entree=3000, tokens_sortie=600):
    """Estimation a priori (dry-run) : hypothese ~3k tokens en entree (prompt
    + description bornee a 2000 car.) et ~600 en sortie par avis."""
    total = 0.0
    for m in modeles:
        c, _ = cout_usd(m, tokens_entree, tokens_sortie)
        total += c * nb_avis
    return round(total, 3)


# ===========================================================================
# APPELS RESEAU (isoles, injectables)
# ===========================================================================

def appel_http(modele, prompt, temperature, max_tokens=1000):
    """Un appel Messages API. Renvoie un dict brut :
    {code, charge|None, corps_texte, latence_s}. Ne leve jamais."""
    import requests
    cle = os.environ.get("ANTHROPIC_API_KEY", "")
    corps = {"model": modele, "max_tokens": max_tokens,
             "messages": [{"role": "user", "content": prompt}]}
    if temperature is not None:
        corps["temperature"] = temperature
    debut = time.time()
    try:
        rep = requests.post(ted.ANTHROPIC_ENDPOINT, timeout=90, json=corps, headers={
            "x-api-key": cle, "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
    except Exception as e:  # reseau : on rapporte, on ne casse pas la sonde
        return {"code": None, "charge": None, "corps_texte": str(e)[:300],
                "latence_s": round(time.time() - debut, 2)}
    try:
        charge = rep.json()
    except ValueError:
        charge = None
    return {"code": rep.status_code, "charge": charge,
            "corps_texte": (rep.text or "")[:400],
            "latence_s": round(time.time() - debut, 2)}


def tester_compatibilite(modele, appel=appel_http):
    """Le modele accepte-t-il `temperature: 0` (forme actuelle de l'appel) ?

    Renvoie {"modele", "disponible", "temperature_0", "detail"} :
      - temperature_0 True  : la production peut basculer par simple variable
      - temperature_0 False : il faudra retirer le parametre dans le code
      - disponible False    : modele inconnu/retire/sans droit (voir detail)"""
    sonde = 'Reponds uniquement par le JSON {"ok": true}'
    r = appel(modele, sonde, 0, 20)
    if r["code"] == 200:
        return {"modele": modele, "disponible": True, "temperature_0": True, "detail": ""}
    if est_refus_temperature(r["code"], r["corps_texte"]):
        r2 = appel(modele, sonde, None, 20)
        return {"modele": modele, "disponible": r2["code"] == 200,
                "temperature_0": False,
                "detail": "temperature refusee : " + r["corps_texte"][:200]
                + ("" if r2["code"] == 200 else " | sans temperature : " + r2["corps_texte"][:200])}
    return {"modele": modele, "disponible": False, "temperature_0": None,
            "detail": "HTTP {} : {}".format(r["code"], r["corps_texte"][:250])}


def analyser_avis(avis, modele, temperature, appel=appel_http):
    """Une extraction complete : meme prompt que la production, parsing
    gratuit, score calcule par le VRAI `calculer_scores` du coeur."""
    prompt = ted.PROMPT_TEMPLATE.format(
        acheteur=avis.get("acheteur", ""),
        pays_acheteur=avis.get("pays_acheteur", ""),
        pays_execution=avis.get("pays_execution", ""),
        titre=avis.get("titre", ""),
        cpv=avis.get("cpv", ""),
        description=avis.get("description", "") or "(non fournie par l'avis)",
    )
    r = appel(modele, prompt, temperature, 1000)
    ligne = {"modele": modele, "latence_s": r.get("latence_s"), "extraction": None,
             "score": None, "niveau_parsing": "echec", "erreur": "",
             "tokens_entree": 0, "tokens_sortie": 0, "cout_usd": 0.0,
             "stop_reason": None}
    charge = r.get("charge") if isinstance(r.get("charge"), dict) else {}
    usage = charge.get("usage") or {}
    ligne["tokens_entree"] = int(usage.get("input_tokens") or 0)
    ligne["tokens_sortie"] = int(usage.get("output_tokens") or 0)
    ligne["cout_usd"], _ = cout_usd(modele, ligne["tokens_entree"], ligne["tokens_sortie"])
    ligne["stop_reason"] = charge.get("stop_reason")
    if r.get("code") != 200:
        ligne["erreur"] = "HTTP {} : {}".format(r.get("code"), r.get("corps_texte", "")[:200])
        return ligne
    texte, motif = ted.texte_des_blocs(charge)
    if texte is None:
        ligne["erreur"] = motif
        return ligne
    extraction, niveau = parser_extraction(texte)
    ligne["niveau_parsing"] = niveau
    if extraction is not None:
        ligne["extraction"] = extraction
        ligne["score"] = ted.calculer_scores(avis, extraction)[2]
    return ligne


# ===========================================================================
# ORCHESTRATION
# ===========================================================================

def collecter_echantillon(n):
    """Meme chaine que la production TED : requete, filtre client, fenetre,
    normalisation, dedup. Aucune memoire inter-runs : on veut des avis
    reels, deja vus ou non."""
    bruts = ted.interroger_ted()
    pertinents = [a for a in bruts if ted.avis_correspond(a)]
    normalises = [ted.normaliser(a) for a in pertinents]
    normalises, _ = ted.dedupliquer_quasi_doublons(normalises)
    print("TED : {} bruts, {} pertinents, {} apres dedup.".format(
        len(bruts), len(pertinents), len(normalises)))
    return echantillonner(normalises, n)


def executer(echantillon, modeles, plafond_usd, appel=appel_http, pause=0.3):
    """Compatibilite puis extraction de chaque avis par chaque modele.
    S'arrete net (sans lever) des que le plafond de depense est atteint."""
    compat = {}
    for role in ROLES:
        m = modeles[role]
        if m not in compat:
            compat[m] = tester_compatibilite(m, appel=appel)
    resultats, depense, coupe = [], 0.0, False
    for i, avis in enumerate(echantillon, start=1):
        print("[{}/{}] {}".format(i, len(echantillon), str(avis.get("titre", ""))[:70]))
        entree = {"avis": {k: avis.get(k) for k in (
            "publication_number", "titre", "acheteur", "pays_execution", "cpv")},
            "par_role": {}}
        for role in ROLES:
            m = modeles[role]
            c = compat[m]
            if not c["disponible"]:
                continue
            if depense >= plafond_usd:
                coupe = True
                break
            temperature = 0 if c["temperature_0"] else None
            ligne = analyser_avis(avis, m, temperature, appel=appel)
            depense += ligne["cout_usd"]
            entree["par_role"][role] = ligne
            if pause:
                time.sleep(pause)
        resultats.append(entree)
        if coupe:
            print("PLAFOND DE DEPENSE ATTEINT ({:.2f} $) : arret de la sonde.".format(depense))
            break
    return {"compatibilite": compat, "resultats": resultats,
            "depense_usd": round(depense, 4), "plafond_atteint": coupe}


def rapport(bilan, modeles):
    """Synthese chiffree : fiabilite et cout par modele, accords par paire."""
    resultats = bilan["resultats"]
    return {
        "modeles": modeles,
        "compatibilite": bilan["compatibilite"],
        "avis_analyses": len(resultats),
        "depense_usd": bilan["depense_usd"],
        "plafond_atteint": bilan["plafond_atteint"],
        "par_modele": [resume_modele(resultats, role, modeles[role]) for role in ROLES],
        "par_paire": [comparer_paire(resultats, a, b) for a, b in PAIRES],
    }


def afficher(synthese):
    print("\n" + "=" * 72)
    print("SONDE MIGRATION MODELES -- {} avis, depense {} $".format(
        synthese["avis_analyses"], synthese["depense_usd"]))
    print("=" * 72)
    print("\n1. COMPATIBILITE (temperature: 0 tel qu'en production)")
    for m, c in synthese["compatibilite"].items():
        etat = ("OK" if c["temperature_0"] else
                "A ADAPTER (retirer temperature)" if c["disponible"] else "INDISPONIBLE")
        print("  - {:<28} {}{}".format(m, etat, ("  | " + c["detail"]) if c["detail"] else ""))
    print("\n2. FIABILITE ET COUT PAR MODELE")
    for r in synthese["par_modele"]:
        print("  - {:<19} {:<28} ok {}/{} | JSON direct {} | tronq {} | "
              "{} $/avis | lat {} s | escalade {} | score moy {}".format(
                  r["role"], r["modele"], r["extractions_ok"], r["appels"],
                  r["taux_json_direct"], r["tronquees"], r["cout_par_avis_usd"],
                  r["latence_mediane_s"], r["taux_escalade"], r["score_moyen"]))
    inconnus = [m for m in synthese["modeles"].values() if m not in TARIFS_USD_MTOK]
    if inconnus:
        print("  (tarif inconnu, compte a 0 : {})".format(", ".join(sorted(set(inconnus)))))
    print("\n3. ACCORD DE DECISION PAR PAIRE")
    for p in synthese["par_paire"]:
        print("  - {} ({} avis communs)".format(p["paire"], p["avis_communs"]))
        if not p["avis_communs"]:
            continue
        print("      classe d'action identique : {} | escalade identique : {} | "
              "ecart score moyen : {} | ecarts >= {} : {}".format(
                  p["accord_classe_action"], p["accord_escalade"],
                  p["ecart_score_moyen"], ECART_SCORE_NOTABLE, p["ecarts_notables"]))
        print("      accord par champ : " + ", ".join(
            "{} {}".format(k, v) for k, v in p["accord_champs"].items()))
        for d in p["desaccords_action"][:8]:
            a, b = p["paire"].split(" vs ")
            print("      * {} | {} {} ({}) vs {} {} ({})".format(
                d["titre"][:60], a, d[a]["classe"], d[a]["score"],
                b, d[b]["classe"], d[b]["score"]))


def main():
    # config_env : une saisie vide dans le formulaire du workflow vaut le
    # defaut, et une valeur illisible ne tue pas la sonde (garde test_config_env).
    n = config_env.entier("SONDE_N", 25)
    plafond = config_env.reel("SONDE_PLAFOND_USD", 3.0)
    dryrun = config_env.booleen("SONDE_DRYRUN", False)
    sortie = config_env.texte("SONDE_SORTIE", "sonde_modeles_resultats.json")
    modeles = modeles_configures()
    print("Modeles compares : " + json.dumps(modeles))
    echantillon = collecter_echantillon(n)
    nb_desc = sum(1 for a in echantillon if a.get("description"))
    print("Echantillon : {} avis ({} avec description).".format(len(echantillon), nb_desc))
    print("Estimation a priori : ~{} $ (plafond {} $).".format(
        estimer_cout(len(echantillon), list(modeles.values())), plafond))
    if dryrun:
        for a in echantillon:
            print("  - {} | {} | {}".format(a.get("publication_number"),
                                            a.get("pays_execution"), str(a.get("titre"))[:70]))
        print("DRY-RUN : aucun appel modele.")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return 1
    if not echantillon:
        print("Aucun avis TED exploitable : rien a comparer.")
        return 1
    bilan = executer(echantillon, modeles, plafond)
    synthese = rapport(bilan, modeles)
    afficher(synthese)
    with open(sortie, "w", encoding="utf-8") as f:
        json.dump({"synthese": synthese, "detail": bilan["resultats"]}, f,
                  ensure_ascii=False, indent=1, default=str)
    print("\nDetail complet : {} (artefact du workflow).".format(sortie))
    return 0


if __name__ == "__main__":
    sys.exit(main())
