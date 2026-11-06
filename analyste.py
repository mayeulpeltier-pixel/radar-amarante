"""Couche Analyste : une fiche d'approche par opportunite prioritaire.

POURQUOI (06/11/2026)
---------------------
Le Radar classe les avis (contacter / surveiller / ignorer) mais ne dit pas
QUOI FAIRE d'un lead. La sonde `sonde_fiches.py` a mesure sur 6 vrais leads
qu'une fiche redigee par le modele d'escalade AVEC recherche web (variante B)
est exploitable telle quelle par la BD : verdict de Mayeul, B preferee 6/6,
exploitable 6/6 (doc d'evaluation, projet claude/sonde_fiches_2026-11.md).
Cout mesure : ~0,17 $ par fiche.

DOCTRINE : « SOIT DE VRAIS PROSPECTS, SOIT RIEN »
-------------------------------------------------
Retour de Mayeul sur la sonde : la liste des soumissionnaires probables tiree
de la base (memes negociants pour la RDC, le Bangladesh et le Kirghizistan)
est hypothetique et n'apporte rien. Puis (06/11, 17 h) : « dans les fiches je
veux bien les candidats probables, mais je veux des vraies propositions ».
Donc :
  - candidats : UNIQUEMENT `candidats_probables.candidats_prouves`, qui exige
    un marche de services ou de travaux deja remporte (meme projet, meme
    acheteur reel dans le pays, ou meme pays et secteur precis) et le cite.
    Calcul deterministe en base, stocke tel quel dans la fiche : le modele
    ne peut pas en inventer. Jamais `soumissionnaires_probables` ;
  - « qui_deploiera » ne garde QUE des organisations dont la source est une
    page web reellement consultee pendant l'appel, le texte de l'avis, ou la
    liste de candidats prouves (verifie dans le CODE, pas seulement demande
    au modele) ;
  - sinon la liste reste vide.

CE QUE FAIT LE MODULE
---------------------
1. Lit les leads en base (meme chemin que l'application), lecture seule.
2. Les regroupe en OPPORTUNITES : un projet a plusieurs documents (avis
   general + plan de passation du PRECAPE, constate dans la sonde) ne recoit
   qu'UNE fiche.
3. Ne fiche que les opportunites jamais fichees (memoire : l'onglet
   `fiches_analyste` en base), les meilleures d'abord.
4. Ecrit chaque fiche dans `radar_lignes` (onglet `fiches_analyste`, cle =
   opportunite) via `ecrire_miroir` : aucun changement de schema.

GARDE-FOUS
----------
- Drapeau RADAR_ANALYSTE (defaut OFF) : sans lui, le module ne fait rien.
- Plafond de depense par run (RADAR_ANALYSTE_PLAFOND_USD, defaut 2 $) et
  nombre de fiches par run (RADAR_ANALYSTE_N, defaut 8).
- Ne leve jamais : une fiche ratee n'empeche ni les suivantes ni le run.
- Corps d'appel par `ted.corps_appel` (regle 12 de CLAUDE.md).
"""

import datetime
import json
import os
import re
import sys
import time
import unicodedata

import config_env
import ted_complet_v14 as ted

ONGLET = "fiches_analyste"
SOURCES_AVIS = {"TED", "BM", "UNGM", "EBRD", "AFDB", "ADB", "RW", "IDB"}
STATUTS_EXCLUS = {"non_pertinent", "gagne", "perdu"}  # radar_stockage.STATUTS_VALIDES

OUTIL_WEB = os.environ.get("RADAR_ANALYSTE_OUTIL_WEB") or "web_search_20250305"
MAX_RECHERCHES = 4
PRIX_RECHERCHE_USD = 0.01   # estimation ; les recherches reelles sont comptees
TARIFS_USD_MTOK = {"claude-sonnet-5-5": (2.00, 10.00), "claude-sonnet-4-6": (3.00, 15.00),
                   "claude-haiku-5-5": (0.10, 0.50)}
PLAFOND_TEXTE_NOTICE = 8000

# Prefixes documentaires des bailleurs : « AGPM - RDC - Projet X » et
# « PPM - RDC - Projet X (simplifie) » designent le MEME projet.
PREFIXES_DOC = r"(agpm|gpn|spn|ppm|ami|eoi|aoi|aon|acn|pn|rfp|rfq|ifb|ppn)"
MOTS_VIDES_TITRE = {"simplifie", "actualise", "revise", "addendum", "corrigendum"}

PROMPT = """Tu es analyste commercial pour Amarante International, société française de sûreté qui vend des prestations OPÉRATIONNELLES en zones à risque : escorte, protection rapprochée, chauffeurs sécurité, véhicules (blindés ou non), sécurisation de déplacements et de sites. Elle ne vend pas de conseil générique.

On te donne une opportunité détectée par le Radar Amarante. Rédige une FICHE D'APPROCHE pour le chargé d'affaires : ce qu'il doit savoir et faire, en 2 minutes de lecture.

Tu disposes d'une recherche web (quelques requêtes). Utilise-la pour ce qui change l'action : le projet ou le programme parent, l'agence d'exécution, les titulaires déjà connus, les acteurs déployés sur zone, le contexte sécuritaire local récent. Cite l'URL de chaque fait tiré du web.

RÈGLES ABSOLUES
- N'invente AUCUN nom d'entreprise, de personne ou de chiffre. Un nom n'est autorisé que s'il figure dans le texte de l'avis ci-dessous ou dans une source web que tu as réellement consultée.
- « qui_deploiera » ne contient QUE des prospects réels : des organisations nommées qui déploieront du personnel sur le terrain, avec l'URL consultée qui le prouve (ou « avis » si le nom figure dans l'avis, ou « radar » s'il figure dans la liste des titulaires prouvés ci-dessous). Pas de profil générique, pas d'hypothèse. Si aucun prospect réel n'est identifié, renvoie une liste vide.
- Les « titulaires prouvés » ont déjà remporté un marché comparable : ce sont de vrais candidats. Dis en une phrase, dans « angle_amarante » ou « prochaine_action », lequel démarcher en premier et pourquoi.
- Sois honnête : si l'opportunité n'a pas de vrai potentiel pour Amarante, dis-le (verdict « abandonner »).
- Prochaine action : concrète, réalisable par une personne, avec une cible et si possible une date butoir.
- Français, phrases courtes. N'insère aucune balise ni marque de citation dans le texte : mets les URL en clair.

OPPORTUNITÉ
{donnees}

Réponds UNIQUEMENT en JSON valide, sans texte autour :
{{
  "en_une_phrase": "l'opportunité pour Amarante, en une phrase",
  "pourquoi_maintenant": "le déclencheur et la fenêtre de temps",
  "acheteur": "qui paie, qui passe les marchés",
  "qui_deploiera": [{{"acteur": "nom réel", "source": "url ou 'avis'"}}],
  "besoin_surete": ["prestations Amarante concrètes probablement utiles"],
  "concurrence": "qui assure ou pourrait assurer la sûreté, ou 'inconnu'",
  "angle_amarante": "pourquoi Amarante et comment se différencier",
  "prochaine_action": {{"action": "...", "cible": "...", "avant_le": "AAAA-MM-JJ ou ''"}},
  "a_verifier": ["incertitudes à lever avant d'investir du temps"],
  "verdict": "poursuivre | surveiller | abandonner",
  "confiance": 0.0
}}"""


# ===========================================================================
# FONCTIONS PURES
# ===========================================================================

def _date(v):
    try:
        return datetime.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _sans_accents(t):
    return "".join(c for c in unicodedata.normalize("NFKD", str(t or ""))
                   if not unicodedata.combining(c))


def titre_canonique(titre):
    """Titre sans prefixe documentaire ni mots de revision, pour reconnaitre
    les documents d'un meme projet. Fonction PURE."""
    t = _sans_accents(titre).lower()
    t = re.sub(r"&#0?39;|&apos;", "'", t)
    t = re.sub(r"^\s*" + PREFIXES_DOC + r"\b\s*[-:]\s*", "", t)
    t = re.sub(r"\([^)]*\)", " ", t)
    mots = [m for m in re.findall(r"[a-z0-9]+", t) if m not in MOTS_VIDES_TITRE]
    return " ".join(mots)[:80]


def cle_fiche(lead):
    """Cle d'opportunite pour la fiche : projet ou entreprise si connus
    (`opportunites.cle_opportunite`), sinon pays + titre canonique."""
    try:
        import opportunites
        cle = opportunites.cle_opportunite(lead)
    except Exception:  # noqa: BLE001
        cle = ""
    if cle.startswith(("PROJ:", "ENT:")):
        return cle
    canon = titre_canonique(lead.get("titre"))
    if not canon:
        return cle
    return "TITRE:{}:{}".format(_sans_accents(lead.get("pays")).lower().strip(), canon)


def selectionner(leads, n, deja, aujourd=None, jours_max=60):
    """Les n opportunites a ficher : meilleur lead de chaque opportunite non
    encore fichee, sources d'avis, non close par la BD, detectee recemment,
    cloture non depassee. Tri par score final decroissant. PURE."""
    auj = aujourd or datetime.date.today()
    meilleurs = {}
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
        cle = cle_fiche(l)
        if not cle or cle in deja:
            continue
        actuel = meilleurs.get(cle)
        if actuel is None or float(l.get("final") or 0) > float(actuel.get("final") or 0):
            meilleurs[cle] = l
    ordre = sorted(meilleurs.items(),
                   key=lambda kv: (-float(kv[1].get("final") or 0), kv[0]))
    return ordre[:max(0, n)]


CHAMPS = (("src", "Source"), ("pub", "Référence"), ("titre", "Titre"),
          ("agence", "Acheteur"), ("pays", "Pays"), ("final", "Score Radar /10"),
          ("action", "Action recommandée par le tri"), ("valeur", "Montant estimé"),
          ("deadline", "Clôture"), ("date_det", "Détecté le"),
          ("justif", "Justification du tri"), ("lien", "Lien"))


def donnees(lead, texte_notice="", candidats=None):
    lignes = []
    for cle, libelle in CHAMPS:
        v = lead.get(cle)
        if v not in (None, "", "n.c.", "indetermine"):
            lignes.append("- {} : {}".format(libelle, v))
    if candidats:
        lignes.append("- Titulaires prouvés (ont déjà remporté un marché comparable, preuve en base) :")
        for c in candidats:
            m = (c.get("marches") or [{}])[0]
            lignes.append("    - {} ({}{}) : « {} », {}, {}".format(
                c.get("entreprise"), c.get("libelle_preuve"),
                ", étranger" if c.get("etranger") else "",
                m.get("titre", ""), m.get("pays", ""), m.get("date", "")))
    if texte_notice:
        lignes.append("- Texte de l'avis (extrait) :\n" + texte_notice[:PLAFOND_TEXTE_NOTICE])
    return "\n".join(lignes)


def corps(modele, prompt):
    c = ted.corps_appel(modele, prompt, 2500)
    c["tools"] = [{"type": OUTIL_WEB, "name": "web_search", "max_uses": MAX_RECHERCHES}]
    return c


def sources_web(charge):
    urls, vus = [], set()
    for b in (charge or {}).get("content") or []:
        if not isinstance(b, dict):
            continue
        trouves = []
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            trouves += [r.get("url") for r in b["content"] if isinstance(r, dict)]
        trouves += [c.get("url") for c in b.get("citations") or [] if isinstance(c, dict)]
        for u in trouves:
            if u and u not in vus:
                vus.add(u)
                urls.append(u)
    return urls


def cout(modele, usage):
    usage = usage or {}
    p_in, p_out = TARIFS_USD_MTOK.get(modele, (0.0, 0.0))
    t_in = sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    t_out = int(usage.get("output_tokens") or 0)
    n = int(((usage.get("server_tool_use") or {}).get("web_search_requests")) or 0)
    return (t_in * p_in + t_out * p_out) / 1_000_000 + n * PRIX_RECHERCHE_USD, n


_CITE = re.compile(r"[(<]?cite index=[\"'][^\"']*[\"']>|</cite>")


def nettoyer(v):
    """Retire les balises de citation que le modele glisse parfois dans le
    texte (constate dans la sonde : `<cite index="22-4">...</cite>`)."""
    if isinstance(v, str):
        return re.sub(r"\s{2,}", " ", _CITE.sub("", v)).strip()
    if isinstance(v, list):
        return [nettoyer(x) for x in v]
    if isinstance(v, dict):
        return {k: nettoyer(x) for k, x in v.items()}
    return v


def prospects_reels(fiche, urls, texte_avis, candidats=None):
    """Applique la doctrine dans le CODE : ne garde que les acteurs dont la
    source est une URL reellement consultee, ou « avis » quand le nom figure
    dans le texte de l'avis. Le reste est ecarte, sans remplacement."""
    garde = []
    bas = _sans_accents(texte_avis).lower()
    prouves = {_sans_accents(c.get("entreprise")).lower() for c in (candidats or [])}
    for a in (fiche or {}).get("qui_deploiera") or []:
        if not isinstance(a, dict):
            continue
        nom = str(a.get("acteur") or "").strip()
        src = str(a.get("source") or "").strip()
        if not nom:
            continue
        cle_nom = _sans_accents(nom).lower()
        if (src in urls or (src.lower() == "avis" and cle_nom in bas)
                or (src.lower() == "radar" and cle_nom in prouves)):
            garde.append({"acteur": nom, "source": src})
    fiche = dict(fiche or {})
    fiche["qui_deploiera"] = garde
    return fiche


def parser(texte):
    t = re.sub(r"```(?:json)?", "", texte or "")
    debut, fin = t.find("{"), t.rfind("}")
    if debut == -1 or fin <= debut:
        return None
    try:
        v = json.loads(t[debut:fin + 1])
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def ligne_fiche(cle, lead, fiche, urls, usd, modele, aujourd=None, candidats=None):
    """Ligne stockee dans radar_lignes (onglet fiches_analyste)."""
    return {"candidats": list(candidats or []),"publication_number": cle, "date": (aujourd or datetime.date.today()).isoformat(),
            "src": lead.get("src"), "pub": lead.get("pub"), "titre": lead.get("titre"),
            "pays": lead.get("pays"), "lien": lead.get("lien"), "score": lead.get("final"),
            "modele": modele, "cout_usd": round(usd, 4), "sources": urls[:30], "fiche": fiche}


# ===========================================================================
# RESEAU (injectable)
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


def appel_http(c):
    import requests
    debut = time.time()
    try:
        rep = requests.post(ted.ANTHROPIC_ENDPOINT, json=c, timeout=240, headers={
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01", "content-type": "application/json"})
        charge = rep.json()
    except Exception as e:  # noqa: BLE001
        return {"code": None, "charge": None, "texte": str(e)[:300],
                "latence_s": round(time.time() - debut, 1)}
    return {"code": rep.status_code, "charge": charge, "texte": "",
            "latence_s": round(time.time() - debut, 1)}


def produire(cle, lead, modele, texte_notice="", appel=appel_http, aujourd=None, candidats=None):
    """(ligne|None, cout_usd, message). Ne leve jamais."""
    try:
        r = appel(corps(modele, PROMPT.format(donnees=donnees(lead, texte_notice, candidats))))
    except Exception as e:  # noqa: BLE001
        return None, 0.0, "appel en echec : {}".format(str(e)[:120])
    charge = r.get("charge") if isinstance(r.get("charge"), dict) else {}
    usd, _ = cout(modele, charge.get("usage"))
    if r.get("code") != 200:
        detail = (charge.get("error") or {}).get("message") if charge else r.get("texte")
        return None, usd, "HTTP {} : {}".format(r.get("code"), str(detail)[:160])
    texte, motif = ted.texte_des_blocs(charge)
    fiche = parser(texte) if texte else None
    if fiche is None:
        return None, usd, "reponse inexploitable : {}".format(motif or (texte or "")[:120])
    urls = sources_web(charge)
    fiche = prospects_reels(nettoyer(fiche), urls, texte_notice + " " + str(lead.get("titre") or ""),
                            candidats)
    return ligne_fiche(cle, lead, fiche, urls, usd, modele, aujourd, candidats), usd, "ok"


def executer(selection, modele, plafond, appel=appel_http, texte_notice=None, pause=1.0,
             leads=None, candidats_pour=None):
    """Une fiche par opportunite selectionnee, arret au plafond."""
    if candidats_pour is None:
        def candidats_pour(lead):
            import candidats_probables as cp
            return cp.candidats_prouves(lead, leads or [], n=3)
    if texte_notice is None:
        def texte_notice(lead):
            if lead.get("src") != "TED" or not lead.get("pub"):
                return ""
            return ted.texte_integral_notice(lead["pub"])
    lignes, depense = [], 0.0
    for i, (cle, lead) in enumerate(selection, 1):
        if depense >= plafond:
            print("  plafond de depense atteint ({:.2f} $) : arret.".format(depense))
            break
        try:
            notice = texte_notice(lead) or ""
        except Exception:  # noqa: BLE001
            notice = ""
        try:
            cands = candidats_pour(lead) or []
        except Exception:  # noqa: BLE001
            cands = []
        ligne, usd, msg = produire(cle, lead, modele, notice, appel=appel, candidats=cands)
        depense += usd
        verdict = (ligne or {}).get("fiche", {}).get("verdict", "-")
        print("  [{}/{}] {} | {} | {} candidat(s) prouve(s) | {:.3f} $ | {}".format(
            i, len(selection), str(lead.get("titre"))[:60], verdict, len(cands), usd, msg))
        if ligne:
            lignes.append(ligne)
        if pause:
            time.sleep(pause)
    return lignes, round(depense, 4)


def main():
    if not config_env.booleen("RADAR_ANALYSTE", False):
        print("Couche Analyste desactivee (RADAR_ANALYSTE absent ou 0).")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY") or not os.environ.get("DATABASE_URL"):
        print("Couche Analyste : ANTHROPIC_API_KEY et DATABASE_URL requis, rien fait.")
        return 0
    n = config_env.entier("RADAR_ANALYSTE_N", 8)
    plafond = config_env.reel("RADAR_ANALYSTE_PLAFOND_USD", 2.0)
    modele = config_env.texte("RADAR_ANALYSTE_MODELE", ted.MODELE_RAFFINEMENT)
    try:
        import radar_stockage as st
        leads = charger_leads()
        with st.connexion() as conn:
            deja = st.publications_existantes(conn, ONGLET)
        if config_env.booleen("RADAR_ANALYSTE_REFAIRE", False):
            # Regenere les fiches existantes (upsert en base) : sert quand la
            # doctrine ou la matiere change, comme l'ajout des candidats prouves.
            print("Couche Analyste : REFAIRE actif, les fiches existantes seront regenerees.")
            deja = set()
    except Exception as e:  # noqa: BLE001
        print("Couche Analyste : lecture de la base impossible ({}), rien fait.".format(str(e)[:120]))
        return 0
    selection = selectionner(leads, n, deja)
    print("Couche Analyste : {} leads en base, {} opportunites deja fichees, {} a ficher "
          "(modele {}, plafond {} $).".format(len(leads), len(deja), len(selection), modele, plafond))
    if not selection:
        return 0
    if config_env.booleen("RADAR_ANALYSTE_DIAG", False):
        # Diagnostic GRATUIT : candidats prouves par opportunite, aucun appel
        # au modele, aucune ecriture. Sert a mesurer avant de payer.
        import candidats_probables as cp
        for cle, lead in selection:
            cands = cp.candidats_prouves(lead, leads, n=3)
            print("  - {} | {} | {} candidat(s)".format(
                str(lead.get("titre"))[:70], lead.get("pays"), len(cands)))
            for c in cands:
                m = (c.get("marches") or [{}])[0]
                print("      * {} [{}{}] « {} » {} {}".format(
                    c["entreprise"], c["preuve"], ", etranger" if c["etranger"] else "",
                    str(m.get("titre"))[:70], m.get("pays"), m.get("date")))
        return 0
    lignes, depense = executer(selection, modele, plafond, leads=leads)
    print("  " + st.ecrire_miroir(ONGLET, lignes))
    print("Couche Analyste : {} fiche(s) ecrite(s), depense {} $.".format(len(lignes), depense))
    return 0


if __name__ == "__main__":
    sys.exit(main())
