# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COLLECTEUR PROJECT INTELLIGENCE (couche 2, nourrit couche 3).
===============================================================================

CE QU'IL FAIT
-------------
Pour chaque projet du registre cure (`projets_reference.py`) :
  1. interroge Google News RSS sur ses ALIAS (requetes par PROJET, pas par
     entreprise : c'est tout le changement de doctrine) ;
  2. deduplique contre la memoire inter-runs (on ne repaie jamais deux fois
     l'analyse d'un meme article) ;
  3. classe la PHASE du cycle de vie par LLM Haiku, EN LOTS de 10 signaux ;
  4. passe le tout a `projets.construire_projets` (raisonnement pur) ;
  5. ecrit l'onglet `projets_radar` + miroir Postgres, best-effort.

POURQUOI LE LLM EN LOTS, ET PAS DE REGEX
-----------------------------------------
Mesure faite sur le corpus reel de la sonde : une grille regex ne classait que
5 signaux sur 187 (3 %). Les titres de presse sont NARRATIFS, pas declaratifs
("talks drag on", "Impairment at...", "pourrait debuter en octobre"). Un
pre-passage regex aurait economise 3 % d'appels tout en risquant de VERROUILLER
une phase fausse avant que le modele ne lise mieux. On classe donc au LLM, mais
par LOTS de 10 : le backfill complet (~200 signaux x 20 projets) tombe a ~400
appels Haiku, une seule fois. En regime courant, quelques signaux par semaine.

BUDGET ET SECURITE
------------------
  - Pre-filtres DETERMINISTES avant tout appel : fraicheur, bruit evident,
    rattachement effectif a un projet. Aucun appel sur un article non rattache.
  - Plafond dur d'appels par run (RADAR_PROJETS_MAX_LOTS).
  - Disjoncteur LLM partage : `ted.sortie_selon_sante_llm` en fin de run, et
    arret immediat si `STATS_LLM["arret"]` s'ouvre (solde epuise, cle revoquee).
  - Rotation : seuls RADAR_PROJETS_PAR_RUN projets sont interroges par run,
    curseur persiste. Un run reste borne en temps et en cout.

FLAG : RADAR_PROJETS=0 desactive entierement (defaut OFF le temps de la
validation en production). Additif : ne touche aucun collecteur existant.
"""

import collections
import json
import os
import time
from datetime import date

import bitd_signaux as bitd
import projets as pj
import projets_reference as ref
import radar_resilience
import ted_complet_v14 as ted


ACTIVER = os.environ.get("RADAR_PROJETS", "0") == "1"
NOM_ONGLET = "projets_radar"

PROJETS_PAR_RUN = int(os.environ.get("RADAR_PROJETS_PAR_RUN", "6"))
TAILLE_LOT = int(os.environ.get("RADAR_PROJETS_LOT", "10"))
MAX_LOTS = int(os.environ.get("RADAR_PROJETS_MAX_LOTS", "40"))
MAX_ARTICLES = int(os.environ.get("RADAR_PROJETS_MAX_ART", "25"))
JOURS_FRAICHEUR = int(os.environ.get("RADAR_PROJETS_JOURS", "45"))
# Backfill : fenetre large pour reconstruire l'historique (une fois), puis on
# repasse en fenetre courte. La sonde a montre 10 a 14 ans d'archive dispo.
BACKFILL = os.environ.get("RADAR_PROJETS_BACKFILL", "0") == "1"
PAUSE = float(os.environ.get("RADAR_PROJETS_PAUSE", "1.0"))
# Un lot de 10 produit ~870 tokens de sortie (mesure du shadow run).
# Le defaut historique de 400 tronquait la reponse et perdait le lot.
MAX_TOKENS_LOT = int(os.environ.get("RADAR_PROJETS_MAX_TOKENS", "2000"))

_LOCALES = {"fr": ("fr", "FR", "FR:fr"), "en": ("en", "US", "US:en")}


# ===========================================================================
# 1. REQUETES (pur)
# ===========================================================================
def requetes_du_projet(projet):
    """Requetes Google News pour un projet : ses alias FORTS, un par requete.
    On n'interroge PAS les alias faibles seuls (ils ramenerraient des
    homonymes) : ils ne servent qu'au rattachement d'un texte deja collecte.
    Fonction PURE."""
    return ['"{}"'.format(a) for a in (projet.get("alias") or [])[:4]]


def projets_du_run(registre, curseur=0, par_run=None):
    """Fenetre de projets a interroger ce run (rotation bornee). Fonction PURE."""
    par_run = PROJETS_PAR_RUN if par_run is None else par_run
    n = len(registre)
    if n == 0 or par_run <= 0:
        return []
    debut = curseur % n
    return (registre[debut:] + registre[:debut])[:par_run]


# ===========================================================================
# 2. PRE-FILTRE DETERMINISTE (pur) -- protege le budget LLM
# ===========================================================================
def signal_retenu(article, aujourd=None, backfill=None):
    """True si l'article merite d'aller plus loin. En BACKFILL on ne filtre pas
    sur la fraicheur (on reconstruit justement l'historique). Fonction PURE."""
    backfill = BACKFILL if backfill is None else backfill
    if bitd.bruit_evident(article):
        return False
    if backfill:
        return True
    return bitd.article_frais(article, aujourd=aujourd, jours=JOURS_FRAICHEUR)


def preparer_signaux(articles, projet, vus=None, aujourd=None, backfill=None):
    """Articles bruts -> signaux rattaches, dedupliques, prets a classer.
    Le project_id est pose ICI : la requete etait ciblee, donc le rattachement
    est connu par construction (c'est le chemin nominal documente dans
    test_projets.TestLimiteDuRattachementTextuel). Fonction PURE."""
    vus = set(vus or ())
    out, locaux = [], set()
    for a in articles or []:
        lien = a.get("lien", "")
        if not lien:
            continue
        ident = bitd.id_article(lien)
        if ident in vus or ident in locaux:
            continue
        if not signal_retenu(a, aujourd=aujourd, backfill=backfill):
            continue
        locaux.add(ident)
        out.append({
            "id": ident,
            "project_id": projet["project_id"],
            "titre": a.get("titre", ""),
            "resume": a.get("resume", ""),
            "date": _date_iso(a.get("date", "")),
            "lien": lien,
            "source": "news",
            "phase": "",
        })
    return out


def _date_iso(brut):
    """pubDate RFC 2822 -> 'YYYY-MM-DD', ou '' si illisible. Fonction PURE."""
    import email.utils
    if not brut:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(brut)
        return dt.date().isoformat() if dt else ""
    except Exception:
        return ""


# ===========================================================================
# 3. CLASSIFICATION DE PHASE PAR LOTS (LLM)
# ===========================================================================
PROMPT_LOT = """Tu classes des actualités concernant de GRANDS PROJETS d'infrastructure, d'énergie, de mines ou de transport, pour une société de sûreté qui veut anticiper les futurs déploiements de personnels.

Pour CHAQUE actualité numérotée, indique la PHASE du cycle de vie du projet qu'elle révèle, parmi exactement :
{phases}

Règles :
- Choisis la phase que l'actualité DÉMONTRE, pas celle que le projet atteindra plus tard.
- Une actualité qui ne révèle aucune phase (analyse, débat, opinion, rétrospective) reçoit "".
- Un retard, un enlisement, une dépréciation ou un retrait d'actionnaire NE FONT PAS avancer la phase : renvoie la phase réellement démontrée, ou "" si aucune.
- Relève aussi les ENTREPRISES et institutions citées (noms propres uniquement).

Actualités :
{items}

Réponds UNIQUEMENT par un tableau JSON, un objet par actualité, dans le même ordre :
[{{"n": 1, "phase": "FID", "acteurs": ["Shell"]}}, ...]
Aucun texte avant ou après."""


def construire_prompt_lot(signaux):
    """Prompt d'un lot. Fonction PURE (testable sans reseau)."""
    items = "\n".join(
        "{}. {} | {}".format(i + 1, s.get("titre", "")[:180],
                             (s.get("resume", "") or "")[:200])
        for i, s in enumerate(signaux))
    return PROMPT_LOT.format(phases=", ".join(pj.PHASES.keys()), items=items)


def parser_reponse_lot(texte, taille):
    """Reponse LLM -> liste de {phase, acteurs} de longueur `taille`.
    Tolerante : JSON casse, cles manquantes, phase inconnue, ordre incomplet
    -> on retombe sur des entrees vides plutot que de tout perdre.
    Fonction PURE."""
    vide = [{"phase": "", "acteurs": []} for _ in range(taille)]
    if not texte:
        return vide
    brut = texte.strip()
    debut, fin = brut.find("["), brut.rfind("]")
    if debut < 0 or fin <= debut:
        return vide
    try:
        data = json.loads(brut[debut:fin + 1])
    except ValueError:
        return vide
    if not isinstance(data, list):
        return vide
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n", i + 1)) - 1
        except (TypeError, ValueError):
            n = i
        if not (0 <= n < taille):
            continue
        phase = str(item.get("phase") or "").strip().upper()
        vide[n]["phase"] = phase if phase in pj.PHASES else ""
        acteurs = item.get("acteurs") or []
        if isinstance(acteurs, list):
            vide[n]["acteurs"] = [str(a).strip().lower()
                                  for a in acteurs if str(a).strip()][:10]
    return vide


def classer_lots(signaux, appel=None, max_lots=None):
    """Classe la phase de tous les signaux, par lots. Modifie une COPIE.
    `appel(prompt) -> texte` est injectable (tests offline).
    S'arrete net si le disjoncteur LLM s'ouvre. Retour : (signaux, nb_lots)."""
    max_lots = MAX_LOTS if max_lots is None else max_lots
    appel = appel or (lambda p: bitd._appel_llm(p, modele=ted.MODELE,
                                            max_tokens=MAX_TOKENS_LOT))
    out = [dict(s) for s in signaux]
    lots = 0
    for debut in range(0, len(out), TAILLE_LOT):
        if lots >= max_lots:
            print("  (budget) plafond de {} lots atteint, reste non classe.".format(max_lots))
            break
        if ted.STATS_LLM.get("arret"):
            print("  (disjoncteur) arret LLM : classification interrompue.")
            break
        lot = out[debut:debut + TAILLE_LOT]
        lots += 1
        try:
            reponse = appel(construire_prompt_lot(lot))
        except Exception as e:
            print("  (info) lot {} non classe ({}).".format(lots, str(e)[:70]))
            continue
        for s, r in zip(lot, parser_reponse_lot(reponse, len(lot))):
            s["phase"] = r["phase"]
            if r["acteurs"]:
                s["acteurs"] = r["acteurs"]
        time.sleep(PAUSE)
    return out, lots


# ===========================================================================
# 4. COLLECTE (I/O tolerant, injectable)
# ===========================================================================
def collecter_projet(projet, fetch=None, session=None):
    """Articles bruts d'un projet, toutes requetes. I/O tolerant : une requete
    qui echoue n'interrompt pas les autres."""
    if fetch is None:
        sess = session or ted.session_robuste()

        def fetch(url):
            rep = sess.get(url, timeout=30)
            rep.raise_for_status()
            return rep.text

    articles = []
    for requete in requetes_du_projet(projet):
        for loc in ("fr", "en"):
            hl, gl, ceid = _LOCALES[loc]
            url = bitd.url_google_news("", requete_perso=requete,
                                       hl=hl, gl=gl, ceid=ceid)
            try:
                articles.extend(bitd.parser_rss(fetch(url))[:MAX_ARTICLES])
            except Exception as e:
                print("  (info) requete {} ({}) echouee : {}".format(
                    requete[:30], loc, str(e)[:60]))
            time.sleep(PAUSE)
    return articles


# ===========================================================================
# 5. SORTIE (Sheet + miroir Postgres)
# ===========================================================================
COLONNES = [
    "date_maj", "project_id", "libelle", "pays", "iso3", "secteur",
    "phase_courante", "libelle_phase", "phase_max_atteinte", "recul",
    "maturite", "palier_maturite", "opportunite", "opportunite_phrase",
    "opportunite_motifs",
    "montee_vers", "montee_date", "montee_importance", "montee_message",
    "montee_recente",
    "alerte", "nb_signaux", "premiere_detection", "derniere_maj",
    "prochaine_etape", "fenetre_debut", "fenetre_fin", "fenetre_confiance",
    "valeur_musd", "acteurs", "services", "prospects", "timeline_json",
]


def ligne_depuis_projet(p):
    """Projet -> ligne du Sheet (ordre COLONNES). Fonction PURE."""
    f = p.get("fenetre") or {}
    v = {
        "date_maj": date.today().isoformat(),
        "project_id": p.get("project_id", ""),
        "libelle": p.get("libelle", ""),
        "pays": p.get("pays", ""),
        "iso3": p.get("iso3", ""),
        "secteur": p.get("secteur", ""),
        "phase_courante": p.get("phase_courante", ""),
        "libelle_phase": p.get("libelle_phase", ""),
        "phase_max_atteinte": p.get("phase_max_atteinte", ""),
        "recul": "oui" if p.get("recul") else "",
        "maturite": p.get("maturite", 0),
        "palier_maturite": pj.palier_maturite(p.get("maturite", 0)),
        "opportunite": (p.get("opportunite") or {}).get("score", 0),
        "opportunite_phrase": (p.get("opportunite") or {}).get("phrase", ""),
        # MOTIFS DU SCORE (P1.3, 26/08/2026). `score_opportunite` les
        # construisait depuis le debut et ne renvoyait que la phrase de
        # synthese : le detail « pourquoi 82 » n'atteignait jamais l'ecran.
        # Serialises en une chaine (le Sheet ne stocke pas de listes), separes
        # par « | » car les motifs contiennent deja des virgules.
        "opportunite_motifs": " | ".join(
            (p.get("opportunite") or {}).get("motifs", []) or []),
        # TRANSITION MONTANTE (P1.4). Le recul etait deja expose ; la montee,
        # qui est le signal d'ACTION, ne l'etait pas. On serialise la derniere
        # montee corroboree et sa fraicheur : c'est elle qui dit « ce projet
        # vient de franchir une etape », pas la phase du jour.
        "montee_vers": (p.get("montee") or {}).get("libelle_vers", ""),
        "montee_date": (p.get("montee") or {}).get("date", ""),
        "montee_importance": (p.get("montee") or {}).get("importance", ""),
        "montee_message": (p.get("montee") or {}).get("message", ""),
        "montee_recente": ("oui" if (p.get("montee") or {}).get("recente")
                           else "non"),
        "alerte": p.get("alerte", ""),
        "nb_signaux": p.get("nb_signaux", 0),
        "premiere_detection": p.get("premiere_detection", ""),
        "derniere_maj": p.get("derniere_maj", ""),
        "prochaine_etape": p.get("prochaine_etape", ""),
        "fenetre_debut": f.get("debut", ""),
        "fenetre_fin": f.get("fin", ""),
        "fenetre_confiance": f.get("confiance", ""),
        "valeur_musd": p.get("valeur_musd", 0),
        "acteurs": ", ".join(p.get("acteurs_top", [])),
        "services": ", ".join(p.get("services", [])),
        "prospects": ", ".join(x["entreprise"] for x in pj.prospects(p)),
        "timeline_json": json.dumps(pj.timeline(p), ensure_ascii=False),
    }
    return [str(v.get(c, "")) for c in COLONNES]


def fusionner_lignes(existantes, nouvelles):
    """Fusionne les lignes deja presentes avec celles du run. Fonction PURE.

    POURQUOI. Un projet est un ETAT COURANT, et l'ecriture etait donc un
    remplacement complet (clear + update). Mais le collecteur ne traite que
    RADAR_PROJETS_PAR_RUN projets par run (rotation) : chaque run effacait donc
    les projets des runs precedents. Mesure du 24/08/2026 : le run 1 avait
    ecrit INGA3, TANZLNG et MOZLNG ; le run 2 les a remplaces par CORALSUL,
    SIMANDOU et LOBITO. Le cockpit n'aurait jamais affiche plus de 3 projets
    sur 22 au registre.

    On remplace donc par project_id (le run recalcule fait foi pour SES
    projets) et on conserve les autres."""
    par_id = collections.OrderedDict()
    idx = COLONNES.index("project_id")
    for ligne in existantes or []:
        if len(ligne) > idx and str(ligne[idx]).strip():
            par_id[str(ligne[idx]).strip()] = list(ligne)
    for ligne in nouvelles or []:
        if len(ligne) > idx and str(ligne[idx]).strip():
            par_id[str(ligne[idx]).strip()] = list(ligne)
    return list(par_id.values())


def _lignes_existantes(feuille):
    """Lignes de donnees deja dans l'onglet (sans l'entete). Best-effort."""
    try:
        valeurs = radar_resilience.avec_retry(
            lambda: feuille.get_all_values(), "projets lecture")
    except Exception as e:
        print("  (info) lecture de l'existant impossible ({}) : "
              "ecriture du run seul.".format(str(e)[:60]))
        return []
    if not valeurs or len(valeurs) < 2:
        return []
    entetes = [str(c).strip() for c in valeurs[0]]
    if entetes != COLONNES:
        print("  (info) entetes differents : l'onglet est reecrit a neuf.")
        return []
    return valeurs[1:]


def ecrire(projets_calcules, sheet_id=None, fichier=None):
    """Ecrit l'onglet projets_radar (remplacement complet : un projet est un
    ETAT courant, pas un evenement) + miroir Postgres best-effort."""
    lignes = [ligne_depuis_projet(p) for p in projets_calcules]
    if sheet_id and fichier:
        try:
            # ECRITURE : il faut la portee `spreadsheets` (lecture-ecriture).
            # `sp._ouvrir_classeur` ouvre en spreadsheets.READONLY : l'employer
            # ici renvoyait "403 insufficient authentication scopes" (constate
            # au premier run de production du 24/08/2026).
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(
                fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            classeur = radar_resilience.avec_retry(
                lambda: gspread.authorize(creds).open_by_key(sheet_id),
                "ouverture classeur (ecriture)")
            try:
                feuille = classeur.worksheet(NOM_ONGLET)
            except Exception:
                feuille = classeur.add_worksheet(title=NOM_ONGLET, rows=200,
                                                 cols=len(COLONNES))
            # FUSION, pas remplacement : la rotation ne traite que quelques
            # projets par run, un clear() effacerait tous les autres.
            toutes = fusionner_lignes(_lignes_existantes(feuille), lignes)
            radar_resilience.avec_retry(lambda: feuille.clear(), "projets clear")
            radar_resilience.avec_retry(
                lambda: feuille.update(values=[COLONNES] + toutes,
                                       range_name="A1"), "projets update")
            print("  ecrit : {} projet(s) de ce run, {} au total dans "
                  "'{}'.".format(len(lignes), len(toutes), NOM_ONGLET))
        except Exception as e:
            print("  (info) ecriture Sheet impossible ({}).".format(str(e)[:80]))
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, l)) for l in lignes]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(str(e)[:60]))
    return len(lignes)


# ===========================================================================
# 6. ORCHESTRATION
# ===========================================================================
def main():
    if not ACTIVER:
        print("(info) Project Intelligence desactive (RADAR_PROJETS != 1).")
        return
    import radar_etat

    # ETAT SEPARE. `radar_etat` par defaut est PARTAGE avec
    # signaux_prives, qui y stocke son propre curseur de rotation et sa
    # liste de vus. Ecrire dedans depuis cette couche corromprait la
    # memoire du radar principal : deux curseurs de semantiques
    # differentes (pays ici, entreprises la-bas) dans le meme champ.
    # On utilise donc un fichier dedie.
    CHEMIN_ETAT = os.environ.get("RADAR_ETAT_PROJETS", "radar_etat_projets.json")

    print("=== PROJECT INTELLIGENCE -- collecte par PROJET ===")
    if BACKFILL:
        print("  MODE BACKFILL : fenetre de fraicheur desactivee (reconstruction "
              "de l'historique). A repasser a 0 apres le premier passage.")
    registre = ref.charger_registre()
    curseur, vus = radar_etat.charger(chemin=CHEMIN_ETAT)
    curseur, vus = (curseur or 0), list(vus or [])
    fenetre = projets_du_run(registre, curseur)
    print("  {} projet(s) interroges ce run (sur {} au registre).".format(
        len(fenetre), len(registre)))

    tous, nouveaux_vus = [], []
    for projet in fenetre:
        articles = collecter_projet(projet)
        signaux = preparer_signaux(articles, projet, vus=vus)
        print("  {:<22} {} article(s), {} nouveau(x)".format(
            projet["project_id"], len(articles), len(signaux)))
        tous.extend(signaux)
        nouveaux_vus.extend(s["id"] for s in signaux)

    if not tous:
        print("  aucun signal nouveau ce run.")
    classes, lots = classer_lots(tous)
    print("  {} signal(aux) classe(s) en {} lot(s) LLM.".format(len(classes), lots))

    calcules = pj.construire_projets(classes)
    for p in calcules[:10]:
        print("    [{:>3}/100] {:<26} {:<22} {}".format(
            (p["opportunite"] or {}).get("score", 0), p["project_id"],
            p["libelle_phase"], p["alerte"]))

    ecrire(calcules, os.environ.get("TED_SHEET_ID"),
           os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"))
    radar_etat.sauver(curseur + len(fenetre), vus, nouveaux_vus,
                      chemin=CHEMIN_ETAT)
    ted.sortie_selon_sante_llm("projets")


if __name__ == "__main__":
    main()
