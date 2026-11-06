"""Sonde jetable : la couche Analyste produit-elle des fiches UTILES a la BD ?

QUESTION POSEE (06/11/2026)
---------------------------
Le Radar classe les avis (contacter / surveiller / ignorer) mais ne dit pas
QUOI FAIRE d'un lead : qui appeler, avec quel angle, avant quelle date. La
couche Analyste proposee : pour chaque lead prioritaire, une fiche courte
redigee par Sonnet 5.5 a partir de tout ce que le Radar sait deja.

Avant de construire quoi que ce soit (affichage, stockage, planification),
on mesure sur de VRAIS leads en base :
  1. la fiche est-elle exploitable par la BD ? (jugement humain, a l'aveugle) ;
  2. la recherche web (outil serveur Anthropic) apporte-t-elle assez pour
     justifier son cout ? Chaque lead recoit DEUX fiches : A sans web, B avec ;
  3. combien coute une fiche, mesure en tokens et en recherches reelles.

CE QUE LA SONDE NE FAIT PAS
---------------------------
Elle LIT Postgres (meme chemin que l'application) et n'ecrit RIEN : ni base,
ni Sheet, ni etat. Plafond de depense dur. Sortie : JSON + Markdown en
artefact du workflow. Sonde jetable : a supprimer une fois la decision prise.

GARDE-FOU ANTI-INVENTION
------------------------
Meme regle que le prompt du coeur : aucun nom d'entreprise ou de personne
qui ne vienne pas des donnees fournies ou d'une source web citee. Une fiche
sans matiere doit le dire ("abandonner"), pas broder.
"""

import datetime
import json
import os
import re
import sys
import time

import config_env
import ted_complet_v14 as ted

# ===========================================================================
# CONFIGURATION
# ===========================================================================

# Sources d'AVIS (lentille Opportunites) : celles ou une fiche d'approche a un
# sens. Les signaux prives et projets amont ont leur propre logique.
SOURCES_AVIS = {"TED", "BM", "UNGM", "EBRD", "AFDB", "ADB", "RW", "IDB"}
STATUTS_EXCLUS = {"non_pertinent", "gagne", "perdu"}  # vocabulaire de radar_stockage.STATUTS_VALIDES

# Recherche web : outil serveur Anthropic. Type surchargeable (la version de
# l'outil evolue) ; un refus est rapporte, jamais masque.
OUTIL_WEB = os.environ.get("SONDE_OUTIL_WEB") or "web_search_20250305"
MAX_RECHERCHES = 4
# Prix d'une recherche web cote API ($). A confirmer sur la facture : la sonde
# compte les recherches reelles, le prix unitaire n'est qu'une estimation.
PRIX_RECHERCHE_USD = 0.01
TARIFS_USD_MTOK = {"claude-sonnet-5-5": (2.00, 10.00), "claude-sonnet-4-6": (3.00, 15.00),
                   "claude-haiku-5-5": (0.10, 0.50)}

PLAFOND_TEXTE_NOTICE = 8000   # caracteres de texte integral injectes

PROMPT_FICHE = """Tu es analyste commercial pour Amarante International, société française de sûreté qui vend des prestations OPÉRATIONNELLES en zones à risque : escorte, protection rapprochée, chauffeurs sécurité, véhicules (blindés ou non), sécurisation de déplacements et de sites. Elle ne vend pas de conseil générique.

On te donne un lead détecté par le Radar Amarante. Rédige une FICHE D'APPROCHE pour le chargé d'affaires : ce qu'il doit savoir et faire, en 2 minutes de lecture.

RÈGLES ABSOLUES
- N'invente AUCUN nom d'entreprise, de personne ou de chiffre. Un nom n'est autorisé que s'il figure dans le texte de l'avis ci-dessous ou dans une source web que tu as réellement consultée (indique alors son URL).
- « qui_deploiera » ne contient QUE des prospects réels : des organisations nommées, identifiées dans l'avis ou dans une source citée, qui déploieront du personnel sur le terrain. Pas de profil générique, pas d'hypothèse. Si aucun prospect réel n'est identifié, renvoie une liste vide.
- Sois honnête : si le lead n'a pas de vrai potentiel pour Amarante, dis-le (verdict « abandonner ») plutôt que de broder.
- Prochaine action : concrète, réalisable par une personne, avec une cible et si possible une date butoir tirée des données (clôture, attribution).
- Français, phrases courtes, pas de jargon creux.{consigne_web}

DONNÉES RADAR
{donnees}

Réponds UNIQUEMENT en JSON valide, sans texte autour :
{{
  "en_une_phrase": "l'opportunité pour Amarante, en une phrase",
  "pourquoi_maintenant": "le déclencheur et la fenêtre de temps",
  "acheteur": "qui paie et ce qu'on sait de lui",
  "qui_deploiera": [{{"acteur": "nom réel", "source": "url ou 'avis'"}}],
  "besoin_surete": ["prestations Amarante concrètes probablement utiles"],
  "concurrence": "qui assure ou pourrait assurer la sûreté, ou 'inconnu'",
  "angle_amarante": "pourquoi Amarante et comment se différencier",
  "prochaine_action": {{"action": "...", "cible": "...", "avant_le": "AAAA-MM-JJ ou ''"}},
  "a_verifier": ["incertitudes à lever avant d'investir du temps"],
  "verdict": "poursuivre | surveiller | abandonner",
  "confiance": 0.0
}}"""

CONSIGNE_WEB = """
- Tu disposes d'une recherche web (quelques requêtes). Utilise-la pour ce qui change l'action : le projet ou le programme parent, le titulaire pressenti ou en place, les acteurs déjà déployés sur zone, le contexte sécuritaire local récent. Cite l'URL de chaque fait tiré du web."""


# ===========================================================================
# FONCTIONS PURES (testees sans reseau)
# ===========================================================================

def _date(v):
    try:
        return datetime.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def selectionner_leads(leads, n, aujourd=None, jours_max=60):
    """Les n leads a ficher : sources d'avis, non clos par la BD, detectes
    recemment, cloture non depassee, tries par score final decroissant.
    Fonction PURE et deterministe (departage par numero de publication)."""
    auj = aujourd or datetime.date.today()
    retenus = []
    for l in leads:
        if l.get("src") not in SOURCES_AVIS:
            continue
        if str(l.get("statut") or "").strip().lower() in STATUTS_EXCLUS:
            continue
        det = _date(l.get("date_det"))
        if det is None or (auj - det).days > jours_max:
            continue
        clot = _date(l.get("deadline"))
        if clot is not None and clot < auj:
            continue
        retenus.append(l)
    retenus.sort(key=lambda l: (-float(l.get("final") or 0), str(l.get("pub") or "")))
    return retenus[:max(0, n)]


CHAMPS_LEAD = (("src", "Source"), ("pub", "Référence"), ("titre", "Titre"),
               ("agence", "Acheteur"), ("pays", "Pays"), ("zone", "Zone"),
               ("sect", "Secteur"), ("final", "Score final /10"),
               ("surete", "Score sûreté /10"), ("comm", "Score commercial /10"),
               ("action", "Action recommandée"), ("acces", "Accessibilité commerciale"),
               ("duree", "Durée estimée"), ("client", "Type de client"),
               ("valeur", "Montant estimé"), ("deadline", "Clôture"),
               ("date_det", "Détecté le"), ("justif", "Justification du tri"),
               ("lien", "Lien"))


def donnees_pour_prompt(lead, candidats=None, texte_notice=""):
    """Bloc texte des DONNEES RADAR : champs utiles du lead, soumissionnaires
    probables (calcules a partir des attributions en base) et texte integral
    de la notice quand il existe. Fonction PURE."""
    lignes = []
    for cle, libelle in CHAMPS_LEAD:
        v = lead.get(cle)
        if v not in (None, "", "n.c.", "indetermine"):
            lignes.append("- {} : {}".format(libelle, v))
    if candidats:
        lignes.append("- Soumissionnaires probables (historique d'attributions en base) :")
        for c in candidats:
            lignes.append("    - {} (probabilité {} %, {}{})".format(
                c.get("entreprise"), c.get("probabilite"),
                "; ".join(c.get("motifs") or []),
                ", étranger" if c.get("etranger") else ""))
    if texte_notice:
        lignes.append("- Texte de la notice (extrait) :\n" + texte_notice[:PLAFOND_TEXTE_NOTICE])
    return "\n".join(lignes)


def construire_prompt(lead, candidats=None, texte_notice="", web=False):
    return PROMPT_FICHE.format(
        consigne_web=CONSIGNE_WEB if web else "",
        donnees=donnees_pour_prompt(lead, candidats, texte_notice))


def corps_fiche(modele, prompt, web=False):
    """Corps d'appel : le constructeur UNIQUE du coeur (regle 12 de CLAUDE.md),
    plus l'outil de recherche web pour la variante B."""
    corps = ted.corps_appel(modele, prompt, 2500)
    if web:
        corps["tools"] = [{"type": OUTIL_WEB, "name": "web_search", "max_uses": MAX_RECHERCHES}]
    return corps


def sources_web(charge):
    """URLs reellement consultees : resultats de recherche et citations."""
    urls = []
    for b in (charge or {}).get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            for r in b["content"]:
                if isinstance(r, dict) and r.get("url"):
                    urls.append(r["url"])
        for c in b.get("citations") or []:
            if isinstance(c, dict) and c.get("url"):
                urls.append(c["url"])
    vus, uniques = set(), []
    for u in urls:
        if u not in vus:
            vus.add(u)
            uniques.append(u)
    return uniques


def cout(modele, usage):
    """(cout_usd, nb_recherches) a partir de l'objet `usage` de la reponse."""
    usage = usage or {}
    p_in, p_out = TARIFS_USD_MTOK.get(modele, (0.0, 0.0))
    t_in = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) \
        + int(usage.get("cache_creation_input_tokens") or 0)
    t_out = int(usage.get("output_tokens") or 0)
    recherches = int(((usage.get("server_tool_use") or {}).get("web_search_requests")) or 0)
    return (t_in * p_in + t_out * p_out) / 1_000_000 + recherches * PRIX_RECHERCHE_USD, recherches


def noms_non_sources(fiche, donnees, urls):
    """Controle anti-invention : acteurs nommes sans source ni presence dans
    les donnees. Heuristique volontairement simple (un acteur "sourcé" porte
    une URL consultee ou la mention 'données radar', ou figure tel quel dans
    les donnees) : elle signale, elle ne corrige pas."""
    suspects = []
    bas = (donnees or "").lower()
    for a in (fiche or {}).get("qui_deploiera") or []:
        if not isinstance(a, dict):
            continue
        nom = str(a.get("acteur") or "").strip()
        src = str(a.get("source") or "").strip()
        if not nom:
            continue
        if src.startswith("http") and src in urls:
            continue
        if nom.lower() in bas:
            continue
        # Un profil (description generique) n'est pas un nom propre.
        if not re.search(r"\b[A-Z][A-Za-z&\-]{1,}\b.*\b[A-Z]", nom) and not re.search(r"[A-Z]{2,}", nom):
            continue
        suspects.append(nom)
    return suspects


def fiche_markdown(fiche, titre_lead, lien=""):
    """Rendu lisible d'une fiche (pour le jugement humain)."""
    if not fiche:
        return "_Fiche indisponible._"
    pa = fiche.get("prochaine_action") or {}
    l = ["**En une phrase** : {}".format(fiche.get("en_une_phrase", "")),
         "**Pourquoi maintenant** : {}".format(fiche.get("pourquoi_maintenant", "")),
         "**Acheteur** : {}".format(fiche.get("acheteur", "")),
         "**Qui déploiera** : " + "; ".join(
             "{}{}".format(a.get("acteur", ""), " ({})".format(a["source"]) if a.get("source") else "")
             for a in fiche.get("qui_deploiera") or [] if isinstance(a, dict)),
         "**Besoin sûreté** : " + "; ".join(fiche.get("besoin_surete") or []),
         "**Concurrence** : {}".format(fiche.get("concurrence", "")),
         "**Angle Amarante** : {}".format(fiche.get("angle_amarante", "")),
         "**Prochaine action** : {} ({}{})".format(
             pa.get("action", ""), pa.get("cible", ""),
             ", avant le " + pa["avant_le"] if pa.get("avant_le") else ""),
         "**À vérifier** : " + "; ".join(fiche.get("a_verifier") or []),
         "**Verdict** : {} (confiance {})".format(fiche.get("verdict", ""), fiche.get("confiance", ""))]
    return "\n\n".join(l)


# ===========================================================================
# APPELS RESEAU (isoles, injectables)
# ===========================================================================

def appel_http(corps):
    """Messages API. {code, charge, texte, latence_s}. Ne leve jamais."""
    import requests
    debut = time.time()
    try:
        rep = requests.post(ted.ANTHROPIC_ENDPOINT, json=corps, timeout=240, headers={
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01", "content-type": "application/json"})
    except Exception as e:  # noqa: BLE001
        return {"code": None, "charge": None, "texte": str(e)[:300],
                "latence_s": round(time.time() - debut, 1)}
    try:
        charge = rep.json()
    except ValueError:
        charge = None
    return {"code": rep.status_code, "charge": charge, "texte": (rep.text or "")[:400],
            "latence_s": round(time.time() - debut, 1)}


def produire_fiche(lead, modele, candidats=None, texte_notice="", web=False, appel=appel_http):
    """Une fiche : appel, parsing JSON gratuit, cout, sources, controle des noms."""
    prompt = construire_prompt(lead, candidats, texte_notice, web)
    r = appel(corps_fiche(modele, prompt, web))
    charge = r.get("charge") if isinstance(r.get("charge"), dict) else {}
    usd, recherches = cout(modele, charge.get("usage"))
    sortie = {"variante": "B_web" if web else "A_radar", "modele": modele,
              "latence_s": r.get("latence_s"), "cout_usd": round(usd, 4),
              "recherches": recherches, "sources": sources_web(charge),
              "fiche": None, "erreur": "", "stop_reason": charge.get("stop_reason"),
              "noms_non_sources": []}
    if r.get("code") != 200:
        sortie["erreur"] = "HTTP {} : {}".format(r.get("code"), r.get("texte", "")[:250])
        return sortie
    texte, motif = ted.texte_des_blocs(charge)
    if texte is None:
        sortie["erreur"] = motif
        return sortie
    t = re.sub(r"```(?:json)?", "", texte)
    debut, fin = t.find("{"), t.rfind("}")
    try:
        sortie["fiche"] = json.loads(t[debut:fin + 1]) if debut != -1 and fin > debut else None
    except ValueError:
        sortie["fiche"] = None
    if sortie["fiche"] is None:
        sortie["erreur"] = "JSON illisible : " + texte[:200]
    else:
        sortie["noms_non_sources"] = noms_non_sources(
            sortie["fiche"], donnees_pour_prompt(lead, candidats, texte_notice), sortie["sources"])
    return sortie


# ===========================================================================
# ORCHESTRATION
# ===========================================================================

def charger_leads():
    """Meme lecture que l'application (radar_app.lire_donnees), lecture seule."""
    import radar_app
    import radar_dashboard as dash
    import radar_stockage as st
    with st.connexion() as conn:
        lignes = radar_app.lire_donnees(conn)
    (l_ted, l_bm, l_prive, l_attrib, enrich, l_rw, l_afdb, l_adb, l_ebrd, _watch,
     l_ungm, an_attrib, _alertes, l_miga, l_ifc, l_idb, l_bmp, l_prop, l_dfc,
     _projets, _cand) = lignes
    return dash.construire_leads(
        l_ted, l_bm, l_prive, enrich, l_attrib, l_rw, l_afdb, l_adb, l_ebrd, l_ungm,
        an_attrib, lignes_miga=l_miga, lignes_ifc=l_ifc, lignes_idb=l_idb,
        lignes_bmp=l_bmp, lignes_proparco=l_prop, lignes_dfc=l_dfc)


def executer(selection, leads, modele, plafond, appel=appel_http, texte_notice=None,
             candidats_pour=None, pause=1.0):
    """Deux fiches par lead (A puis B). Arret net au plafond de depense.

    SOUMISSIONNAIRES PROBABLES RETIRES (06/11/2026, retour de Mayeul) : la
    liste calculee en base (`candidats_probables`) proposait les memes
    negociants pour la RDC, le Bangladesh et le Kirghizistan. Hypothetique,
    couteuse en tokens, sans valeur pour la BD. Doctrine : « soit de vrais
    prospects, soit rien ». Par defaut, plus aucune lecture de ce module ;
    `candidats_pour` reste injectable pour les tests uniquement."""
    if candidats_pour is None:
        def candidats_pour(lead):
            return []
    if texte_notice is None:
        def texte_notice(lead):
            if lead.get("src") != "TED" or not lead.get("pub"):
                return ""
            return ted.texte_integral_notice(lead["pub"])
    depense, resultats, coupe = 0.0, [], False
    for i, lead in enumerate(selection, 1):
        print("[{}/{}] {} | {} | {}".format(i, len(selection), lead.get("src"),
                                            lead.get("final"), str(lead.get("titre"))[:70]))
        try:
            cands = candidats_pour(lead)
        except Exception as e:  # noqa: BLE001
            print("  (info) candidats indisponibles : {}".format(str(e)[:80]))
            cands = []
        try:
            notice = texte_notice(lead) or ""
        except Exception as e:  # noqa: BLE001
            print("  (info) texte de notice indisponible : {}".format(str(e)[:80]))
            notice = ""
        entree = {"lead": {k: lead.get(k) for k, _ in CHAMPS_LEAD},
                  "candidats": cands, "texte_notice_car": len(notice), "fiches": []}
        for web in (False, True):
            if depense >= plafond:
                coupe = True
                break
            f = produire_fiche(lead, modele, cands, notice, web, appel=appel)
            depense += f["cout_usd"]
            entree["fiches"].append(f)
            print("  - {} : {} | {} $ | {} recherche(s) | {} s{}".format(
                f["variante"], (f["fiche"] or {}).get("verdict", "ECHEC"), f["cout_usd"],
                f["recherches"], f["latence_s"], (" | " + f["erreur"][:120]) if f["erreur"] else ""))
            if pause:
                time.sleep(pause)
        resultats.append(entree)
        if coupe:
            print("PLAFOND DE DEPENSE ATTEINT ({:.2f} $) : arret.".format(depense))
            break
    return {"resultats": resultats, "depense_usd": round(depense, 4), "plafond_atteint": coupe}


def synthese(bilan):
    par_var = {}
    for e in bilan["resultats"]:
        for f in e["fiches"]:
            v = par_var.setdefault(f["variante"], {"n": 0, "ok": 0, "cout": 0.0, "recherches": 0,
                                                    "sources": 0, "noms_suspects": 0, "verdicts": {}})
            v["n"] += 1
            v["cout"] += f["cout_usd"]
            v["recherches"] += f["recherches"]
            v["sources"] += len(f["sources"])
            v["noms_suspects"] += len(f["noms_non_sources"])
            if f["fiche"]:
                v["ok"] += 1
                verdict = str(f["fiche"].get("verdict", "?"))
                v["verdicts"][verdict] = v["verdicts"].get(verdict, 0) + 1
    for v in par_var.values():
        v["cout_par_fiche"] = round(v["cout"] / v["n"], 4) if v["n"] else None
        v["cout"] = round(v["cout"], 4)
    return par_var


def main():
    n = config_env.entier("SONDE_FICHES_N", 6)
    plafond = config_env.reel("SONDE_FICHES_PLAFOND_USD", 3.0)
    modele = config_env.texte("SONDE_FICHES_MODELE", ted.MODELE_RAFFINEMENT)
    sortie = config_env.texte("SONDE_FICHES_SORTIE", "sonde_fiches_resultats")
    if not os.environ.get("ANTHROPIC_API_KEY") or not os.environ.get("DATABASE_URL"):
        print("ERREUR : ANTHROPIC_API_KEY et DATABASE_URL requis.")
        return 1
    leads = charger_leads()
    selection = selectionner_leads(leads, n)
    print("Leads en base : {} | retenus pour fiche : {} | modele {} | outil web {}".format(
        len(leads), len(selection), modele, OUTIL_WEB))
    if not selection:
        print("Aucun lead eligible (sources d'avis, < 60 jours, non clos).")
        return 1
    bilan = executer(selection, leads, modele, plafond)
    s = synthese(bilan)
    print("\nSYNTHESE -- depense {} $".format(bilan["depense_usd"]))
    for var, v in sorted(s.items()):
        print("  - {} : {}/{} fiches | {} $/fiche | {} recherches | {} sources | "
              "noms non sources {} | verdicts {}".format(
                  var, v["ok"], v["n"], v["cout_par_fiche"], v["recherches"],
                  v["sources"], v["noms_suspects"], v["verdicts"]))
    with open(sortie + ".json", "w", encoding="utf-8") as f:
        json.dump({"synthese": s, **bilan}, f, ensure_ascii=False, indent=1, default=str)
    with open(sortie + ".md", "w", encoding="utf-8") as f:
        for i, e in enumerate(bilan["resultats"], 1):
            f.write("# {}. {}\n\n{}\n\n".format(i, e["lead"].get("titre"), e["lead"].get("lien") or ""))
            for fi in e["fiches"]:
                f.write("## Variante {}\n\n{}\n\nSources : {}\n\n".format(
                    fi["variante"], fiche_markdown(fi["fiche"], e["lead"].get("titre")),
                    ", ".join(fi["sources"][:8]) or "aucune"))
    print("Detail : {0}.json et {0}.md (artefact du workflow).".format(sortie))
    return 0


if __name__ == "__main__":
    sys.exit(main())
