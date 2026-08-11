# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Moteur SIGNAUX PRIVES (refonte du module BITD).
=================================================================

Objectif : detecter le plus tot possible qu'une entreprise privee VA DEPLOYER
du personnel en zone a risque (donc un besoin de surete a venir), bien avant
tout appel d'offres. Remplace le module BITD (defense seule + GDELT).

CE QUI CHANGE PAR RAPPORT A BITD :
  1. WATCHLIST MULTI-SECTEURS : lit l'onglet 'watchlist_prives' (BTP, oil & gas,
     mines, ingenierie, agro, telecom, transport, luxe...) EN PLUS de la
     whitelist defense existante ('comptes_cibles_bitd').
  2. AUTO-ALIMENTATION MONDIALE : les titulaires gagnants du registre
     'attributions_radar' entrent automatiquement dans la veille (ils
     deploient deja). Couvre Ukraine, -stan, Sahel, etc. sans liste manuelle.
  3. NOUVELLE SOURCE ADZUNA (offres d'emploi) : une entreprise qui recrute
     pour un pays a risque = signal de deploiement fort. S'active des que
     ADZUNA_APP_ID / ADZUNA_APP_KEY sont presents.
  4. GDELT SUPPRIME (rate-limite, sans valeur, couteux en temps).

REUTILISATION : tout le moteur eprouve de bitd_signaux est reutilise tel quel
(Google News, fraicheur, anti-bruit, memoire, rotation, scoring, ecriture
Sheet dans 'prive_radar'). Seuls la watchlist, Adzuna et le prompt (neutre,
plus specifique defense) sont propres a ce module.

Ecrit dans le MEME onglet 'prive_radar' et le MEME schema que BITD : zero
impact sur le dashboard.

Interrupteurs :
  RADAR_SIGNAUX_PRIVES=0  -> desactive le moteur.
  ADZUNA_APP_ID / ADZUNA_APP_KEY -> activent la source offres d'emploi.
  RADAR_BITD_BUDGET       -> plafond d'analyses LLM par run (herite de BITD).
"""

import datetime
import email.utils
import os
import re
import time

import ted_complet_v14 as ted
import radar_resilience
import bitd_signaux as bitd
import radar_etat
import radar_retroaction


# ===========================================================================
# CONFIGURATION
# ===========================================================================
ACTIVER = os.environ.get("RADAR_SIGNAUX_PRIVES", "1") != "0"

# Anti rate-limit Google News (503) : pause entre entreprises + re-tentative.
PAUSE_ENTREPRISE = float(os.environ.get("RADAR_PRIVES_PAUSE", "0.7"))

# Garde-temps de la boucle entreprises (minutes). Le job GitHub est plafonne a
# 45 min et cette boucle en est l'etape la plus longue. On s'arrete AVANT pour
# que le run se termine normalement : etat commite, dashboard regenere, digest
# envoye. Les entreprises non traitees sont reprises au run suivant grace au
# curseur honnete. Reglable si tu elargis la fenetre (RADAR_PRIVES_ENTREPRISES).
MINUTES_MAX = float(os.environ.get("RADAR_PRIVES_MINUTES", "25"))
PAUSE_REPLI = 2.5   # attente avant la 2e (et derniere) tentative sur 503
PAUSE_LOCALE = float(os.environ.get("RADAR_PRIVES_PAUSE_LOCALE", "0.4"))  # entre 2 locales

# LOCALES Google News interrogees. Bug corrige : le moteur ne cherchait qu'en
# FR (hl=fr, gl=FR), ce qui rate la presse des majors etrangers de la
# watchlist (ukrainiens, kazakhs, africains anglophones...). On interroge
# desormais FR *et* EN et on fusionne. Reglable via RADAR_PRIVES_GNEWS_LOCALES
# ("hl:gl:pays:langue" separes par des virgules), ex. "fr:FR:FR:fr,en:US:US:en".
def _locales_gnews():
    brut = os.environ.get("RADAR_PRIVES_GNEWS_LOCALES", "fr:FR:FR:fr,en:US:US:en")
    locs = []
    for bloc in brut.split(","):
        p = [x.strip() for x in bloc.split(":")]
        if len(p) == 4 and all(p):
            locs.append((p[0], p[1], p[2] + ":" + p[3]))
    return locs or [("fr", "FR", "FR:fr")]

GNEWS_LOCALES = _locales_gnews()

# Plafond d'articles gardes par entreprise APRES fusion des locales (decouple
# du plafond BITD, qui reste a 6 pour le chemin defense mono-locale). Un peu
# plus haut ici pour ne pas ecraser la diversite FR+EN.
MAX_ARTICLES_PRIVE = int(os.environ.get("RADAR_PRIVES_MAX_ARTICLES", "10"))

# Nombre d'entreprises traitees par run (levier de couverture principal).
# Releve de 20 -> 35 : a ~865 entites surveillees, 20/run = un tour complet en
# ~5 mois (signal perissable). Surchargeable via RADAR_PRIVES_ENTREPRISES.
def taille_fenetre_pour(valeur_env=None, defaut=35):
    """Nombre d'entreprises a traiter ce run, borne a >=1. defaut si vide/illisible."""
    try:
        return max(1, int(valeur_env)) if valeur_env not in (None, "") else defaut
    except (TypeError, ValueError):
        return defaut


def _mois_attributaire_recent():
    """Fenetre de recence d'un attributaire pour entrer dans la tete prioritaire
    (defaut 12 mois). Reglable via RADAR_PRIVES_ATTRIB_MOIS (motif ADB)."""
    try:
        return max(1, int(os.environ.get("RADAR_PRIVES_ATTRIB_MOIS") or "12"))
    except (TypeError, ValueError):
        return 12


def _age_mois(date_str, aujourdhui=None):
    """Age en mois (approx) d'une date ISO (AAAA-MM-JJ) ou JJ/MM/AAAA. None si
    illisible. Jamais negatif (date future -> 0)."""
    s = str(date_str or "").strip()
    an = mo = jo = None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        an, mo, jo = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m2 = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
        if m2:
            jo, mo, an = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
    if an is None:
        return None
    try:
        d = datetime.date(an, mo, jo)
    except ValueError:
        return None
    ref = aujourdhui or datetime.date.today()
    return max(0, (ref - d).days) / 30.44


def _est_prioritaire(compte, aujourdhui=None):
    """Tete prioritaire = attributaire RECENT (marche gagne dans les
    RADAR_PRIVES_ATTRIB_MOIS derniers mois = deploiement encore en cours).

    POURQUOI LA RECENCE (constat du run du 11/08)
    ---------------------------------------------
    "priorite haute" seule mettait TOUS les attributaires jamais collectes en
    tete (175 pour une tete de 10 -> ~18 runs, plus lent que la queue :
    l'acceleration ne mordait pas). Un marche gagne il y a trois ans n'est plus
    "en cours". On restreint donc la tete aux attributaires recents : le pool
    redevient petit, donc la tete tourne vite. Un attributaire ancien, ou sans
    date exploitable, retombe dans la queue (cadence normale, il n'est pas
    perdu)."""
    if str(compte.get("priorite_socle", "")).strip().lower() != "haute":
        return False
    age = _age_mois(compte.get("date_attribution"), aujourdhui)
    if age is None:
        return False
    return age <= _mois_attributaire_recent()


def tete_pour(valeur_env, taille_fenetre, n_prio, defaut=10):
    """Taille de la tete prioritaire scannee a CHAQUE run. Bornée : jamais plus
    que le pool prioritaire, ni que la fenetre, et laisse toujours >=1 place a la
    queue tant qu'il reste des comptes non prioritaires. 0 si aucun prioritaire
    (comportement d'avant : toute la fenetre vient de la rotation normale)."""
    try:
        t = max(0, int(valeur_env)) if valeur_env not in (None, "") else defaut
    except (TypeError, ValueError):
        t = defaut
    return max(0, min(t, n_prio, taille_fenetre))


def composer_fenetre_deux_vitesses(comptes, taille_fenetre, taille_tete_env,
                                   curseur, curseur_prio):
    """Compose la fenetre du run : une TETE prioritaire (attributaires, curseur
    dedie) puis une QUEUE rotative (le reste, curseur general). PURE (aucun
    reseau) pour etre testable. La tete est en premier : l'avance des curseurs
    reste honnete meme sur un arret anticipe. Renvoie un dict avec la fenetre,
    les deux tranches, les tailles de pools et les positions de depart."""
    prioritaires, reste = [], []
    for c in (comptes or []):
        (prioritaires if _est_prioritaire(c) else reste).append(c)
    n_prio, n_reste = len(prioritaires), len(reste)
    taille_tete = tete_pour(taille_tete_env, taille_fenetre, n_prio)
    debut_prio = curseur_prio % n_prio if n_prio else 0
    debut_reste = curseur % n_reste if n_reste else 0
    slice_prio = [prioritaires[(debut_prio + i) % n_prio]
                  for i in range(min(taille_tete, n_prio))]
    reste_dispo = max(0, taille_fenetre - len(slice_prio))
    slice_reste = [reste[(debut_reste + i) % n_reste]
                   for i in range(min(reste_dispo, n_reste))]
    return {"fenetre": slice_prio + slice_reste,
            "slice_prio": slice_prio, "slice_reste": slice_reste,
            "n_prio": n_prio, "n_reste": n_reste,
            "debut_prio": debut_prio, "debut_reste": debut_reste}


def avancer_curseurs(comp, traitees):
    """(prochain_prio, prochain_queue) apres `traitees` comptes reellement faits.
    La tete etant en tete de fenetre, les premiers traites sont prioritaires."""
    prio_faits = min(traitees, len(comp["slice_prio"]))
    reste_faits = max(0, traitees - len(comp["slice_prio"]))
    prochain_prio = (comp["debut_prio"] + prio_faits) % comp["n_prio"] if comp["n_prio"] else 0
    prochain = (comp["debut_reste"] + reste_faits) % comp["n_reste"] if comp["n_reste"] else 0
    return prochain_prio, prochain

# Mode diagnostic (RADAR_PRIVES_DEBUG=1) : affiche la decision LLM pour chaque
# offre Adzuna analysee (gardee/rejetee + raison). N'ecrit rien de plus.
DEBUG = os.environ.get("RADAR_PRIVES_DEBUG", "0") == "1"


def _dbg(article, msg):
    """Trace de diagnostic, ciblee sur les offres d'emploi Adzuna."""
    if DEBUG and str(article.get("titre", "")).startswith("[Offre d'emploi]"):
        print("      [diag adzuna] {:60} | {}".format(
            article.get("titre", "")[:60], msg))

NOM_ONGLET_WATCHLIST = "watchlist_prives"     # nouvel onglet multi-secteurs
NOM_ONGLET_ATTRIBUTIONS = "attributions_radar"  # source d'auto-alimentation

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
ADZUNA_ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{}/search/1"
# Adzuna couvre surtout des pays developpes : on interroge les portails ou se
# trouvent les SIEGES qui recrutent pour l'etranger. Elargi aux pays des
# recruteurs internationaux (Canada, Australie, Afrique du Sud, Allemagne,
# Pologne) en plus de France/UK. Reglable via ADZUNA_PAYS.
ADZUNA_PAYS = [p.strip() for p in os.environ.get(
    "ADZUNA_PAYS", "fr,gb,ca,au,za,de,pl").split(",") if p.strip()]
ADZUNA_RESULTATS = 20
ADZUNA_PAUSE = float(os.environ.get("ADZUNA_PAUSE", "0.3"))      # entre appels
# Plafond d'appels par run : protege le quota gratuit meme avec 7 pays.
ADZUNA_MAX_APPELS = int(os.environ.get("ADZUNA_MAX_APPELS", "120"))

# Diagnostic : combien d'appels/offres/erreurs Adzuna sur le run (affiche a la
# fin). Permet de trancher : 0 appel = cles non lues ; appels mais 0 offre =
# actif mais rien trouve ; erreurs = probleme de cle/quota. 'coupe' = coupe-
# circuit apres un 429 (trop de requetes) pour proteger le quota.
_ADZUNA_STATS = {"appels": 0, "offres": 0, "erreurs": 0, "coupe": False}

# Noms de pays a risque (FR + quelques alias EN), pour reperer un signal de
# deploiement dans une offre d'emploi ou un article. Construit depuis la carte
# de risque du coeur (PAYS_ROUGE : nom_fr -> iso3).
def _noms_pays_risque():
    noms = set()
    for nom in getattr(ted, "PAYS_ROUGE", {}):
        n = str(nom).strip().lower()
        if len(n) >= 4:
            noms.add(n)
    # quelques alias anglais frequents dans les annonces
    noms.update(["ukraine", "mali", "niger", "nigeria", "chad", "sudan",
                 "south sudan", "somalia", "iraq", "libya", "yemen", "haiti",
                 "afghanistan", "burkina", "congo", "mozambique", "kazakhstan",
                 "uzbekistan", "tajikistan", "turkmenistan", "kyrgyzstan",
                 "moldova", "georgia", "armenia", "azerbaijan"])
    return noms

NOMS_PAYS_RISQUE = _noms_pays_risque()


def _mentionne_pays_risque(texte):
    """True si un pays a risque est cite dans le texte (signal de deploiement)."""
    t = " " + (texte or "").lower() + " "
    for nom in NOMS_PAYS_RISQUE:
        if nom in t:
            return True
    return False


# Formes juridiques quasi toujours associees a une PME purement locale (pas un
# prospect Amarante : pas de deploiement d'expatries). On NE bloque PAS les
# formes courantes de moyennes/grandes entreprises (GmbH, SAS, SA, Ltd, SpA,
# AG, BV, A/S, Inc) qui incluent de vrais prospects.
_FORMES_LOCALES = re.compile(
    r"\b(LLC|LLP|OOO|\u041e\u041e\u041e|TOV|\u0422\u041e\u0412|FOP|"
    r"S\.?\s?A\.?\s?de\s?C\.?\s?V\.?|Sp\.?\s*z\s*o\.?\s*o|"
    r"Additional Liability|Limited Liability Company|Private Enterprise)\b",
    re.I)


def _est_pme_locale(nom):
    """True si le nom porte une forme juridique de PME purement locale."""
    return bool(_FORMES_LOCALES.search(nom or ""))


# ===========================================================================
# PARTIE 1 -- WATCHLIST MULTI-SOURCES (le neuf)
# ===========================================================================

def _ouvrir_classeur(sheet_id, fichier_cs):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    return radar_resilience.avec_retry(lambda: gspread.authorize(creds).open_by_key(sheet_id), "ouverture classeur")


def lire_watchlist_multisecteurs(valeurs):
    """Transforme les lignes brutes de l'onglet 'watchlist_prives' en comptes.
    Colonnes attendues : entreprise, secteur, actif, requete_optionnelle.
    Ignore les lignes ou actif == 'non'."""
    if not valeurs or len(valeurs) < 2:
        return []
    entetes = [str(c).strip().lower() for c in valeurs[0]]
    def idx(nom):
        return entetes.index(nom) if nom in entetes else -1
    i_ent, i_sec = idx("entreprise"), idx("secteur")
    i_act, i_req = idx("actif"), idx("requete_optionnelle")
    # Colonne OPTIONNELLE : portails Adzuna a privilegier pour cette entreprise
    # (ex. "fr,za"). Absente -> repartition automatique, aucune saisie requise.
    i_pays = idx("pays_adzuna")
    comptes = []
    for row in valeurs[1:]:
        get = lambda i: (row[i].strip() if 0 <= i < len(row) else "")
        ent = get(i_ent)
        if not ent:
            continue
        if get(i_act).lower() == "non":
            continue
        comptes.append({
            "entreprise": ent,
            "secteur": get(i_sec) or "Autre",
            "requete_personnalisee": get(i_req),
            "pays_adzuna": get(i_pays),
            "priorite_socle": "moyenne",
            "angle_contact": "Entreprise deployant du personnel en zone a risque.",
        })
    return comptes


def seed_depuis_attributions(valeurs, max_comptes=150):
    """Auto-alimentation : chaque titulaire gagnant du registre d'attributions
    devient un compte a surveiller (il deploie deja). Couvre TOUTE zone a
    risque sans liste manuelle. Ignore les '(gagnant non publie)'.

    Capture la DATE d'attribution (date_publication), la PLUS RECENTE par
    societe : c'est elle qui decide si l'attributaire est "recent" (donc en tete
    prioritaire) ou ancien (queue). Voir _est_prioritaire."""
    if not valeurs or len(valeurs) < 2:
        return []
    entetes = [str(c).strip().lower() for c in valeurs[0]]
    if "gagnant" not in entetes:
        return []
    ig = entetes.index("gagnant")
    ipays = entetes.index("pays_execution") if "pays_execution" in entetes else -1
    idate = entetes.index("date_publication") if "date_publication" in entetes else -1
    par_nom, ordre = {}, []      # dedup par nom ; date_attribution = la plus recente
    for row in valeurs[1:]:
        brut = row[ig].strip() if ig < len(row) else ""
        if not brut or "non publie" in brut.lower():
            continue
        pays = (row[ipays].strip() if 0 <= ipays < len(row) else "")
        date_attr = (row[idate].strip() if 0 <= idate < len(row) else "")
        # un marche peut lister plusieurs gagnants separes par ';'
        for nom in brut.split(";"):
            nom = nom.strip()
            cle = nom.lower()
            if len(nom) < 3:
                continue
            if _est_pme_locale(nom):     # PME purement locale -> pas un prospect
                continue
            if cle in par_nom:
                # garder la date la PLUS RECENTE (age le plus petit) ; une date
                # lisible bat une illisible.
                a_new = _age_mois(date_attr)
                a_old = _age_mois(par_nom[cle].get("date_attribution"))
                if a_new is not None and (a_old is None or a_new < a_old):
                    par_nom[cle]["date_attribution"] = date_attr
                continue
            par_nom[cle] = {
                "entreprise": nom,
                "secteur": "Attributaire (marche gagne)",
                "requete_personnalisee": "",
                "priorite_socle": "haute",   # attributaire (recence tranchee par _est_prioritaire)
                "date_attribution": date_attr,
                "angle_contact": "Titulaire d'un marche en zone a risque{} : deploiement en cours.".format(
                    " (" + pays + ")" if pays else ""),
            }
            ordre.append(cle)
            if len(ordre) >= max_comptes:
                return [par_nom[c] for c in ordre]
    return [par_nom[c] for c in ordre]


def construire_watchlist(sheet_id, fichier_cs):
    """Fusionne : watchlist multi-secteurs + whitelist defense existante +
    titulaires d'attributions. Dedup par nom (insensible a la casse).
    La 1re occurrence gagne (watchlist curee prioritaire sur l'auto-seed)."""
    comptes = []
    if sheet_id and fichier_cs:
        try:
            classeur = _ouvrir_classeur(sheet_id, fichier_cs)
        except Exception as e:
            print("  (info) Sheet illisible ({}). Moteur prive inactif.".format(e))
            return []
        # 1. Watchlist multi-secteurs (curee)
        try:
            v = classeur.worksheet(NOM_ONGLET_WATCHLIST).get_all_values()
            comptes += lire_watchlist_multisecteurs(v)
        except Exception:
            print("  (info) Onglet '{}' absent : ajoute-le pour la veille multi-secteurs.".format(
                NOM_ONGLET_WATCHLIST))
        # 2. Whitelist defense existante (conventions d'origine conservees)
        try:
            v = classeur.worksheet(bitd.NOM_ONGLET_WHITELIST).get_all_values()
            for d in bitd._whitelist_depuis_valeurs(v):
                d.setdefault("secteur", "Defense")
                comptes.append(d)
        except Exception:
            pass
        # 3. Auto-alimentation par les attributions (couverture mondiale)
        try:
            v = classeur.worksheet(NOM_ONGLET_ATTRIBUTIONS).get_all_values()
            comptes += seed_depuis_attributions(v)
        except Exception:
            pass

    # Dedup par nom, 1re occurrence prioritaire.
    vus, uniques = set(), []
    for c in comptes:
        cle = c.get("entreprise", "").strip().lower()
        if cle and cle not in vus:
            vus.add(cle)
            uniques.append(c)
    return uniques


# ===========================================================================
# PARTIE 2 -- SOURCE ADZUNA (offres d'emploi = signal de deploiement)
# ===========================================================================

def _iso_vers_rfc822(iso):
    """'2026-06-15T12:00:00Z' -> date RFC822 (lisible par bitd.article_frais)."""
    s = (iso or "").strip().replace("Z", "+00:00")
    try:
        return email.utils.format_datetime(datetime.datetime.fromisoformat(s))
    except Exception:
        return ""


def quota_pays_adzuna(reste_appels, reste_entreprises, maxi=None):
    """Nombre de portails Adzuna a interroger pour l'entreprise courante.

    POURQUOI : le plafond ADZUNA_MAX_APPELS est GLOBAL au run. Avec 7 pays par
    entreprise et un plafond de 120, seules les ~17 premieres entreprises d'une
    fenetre de 35 recevaient une couverture Adzuna ; les 18 suivantes n'en
    avaient AUCUNE, silencieusement. On repartit donc le quota restant sur les
    entreprises restantes, pour que chacune ait au moins un portail.

    Toujours >= 1 tant qu'il reste des appels : mieux vaut un portail pour
    tout le monde que sept pour la moitie."""
    maxi = len(ADZUNA_PAYS) if maxi is None else maxi
    if reste_appels <= 0 or reste_entreprises <= 0:
        return 0
    return max(1, min(maxi, reste_appels // reste_entreprises))


def pays_pour_compte(compte, quota):
    """Portails Adzuna a interroger pour ce compte, dans l'ordre de pertinence.

    Priorite a la colonne OPTIONNELLE `pays_adzuna` de la watchlist (ex.
    "fr,za") quand l'analyste l'a renseignee : c'est lui qui sait ou une
    entreprise publie ses offres. Sinon on prend les premiers d'ADZUNA_PAYS,
    dont l'ordre est deja classe par rendement observe. Aucune saisie n'est
    requise : la colonne absente, le comportement reste automatique."""
    if quota <= 0:
        return []
    brut = str((compte or {}).get("pays_adzuna") or "").strip()
    if brut:
        choisis = [p.strip().lower() for p in brut.replace(";", ",").split(",") if p.strip()]
        if choisis:
            return choisis[:quota]
    return list(ADZUNA_PAYS)[:quota]


def collecter_adzuna(entreprise, fetch=None, session=None, pays=None):
    """Offres d'emploi mentionnant un pays a risque pour cette entreprise.
    Interroge les portails Adzuna des pays sieges (France, UK...). Renvoie des
    'articles' au meme format que Google News (titre/lien/date/resume), pour
    passer dans le meme pipeline d'analyse.

    `pays` : liste de portails a interroger POUR CETTE ENTREPRISE. Si None, on
    retombe sur ADZUNA_PAYS (comportement historique). Ce parametre existe car
    le plafond d'appels est GLOBAL au run : sans repartition, les premieres
    entreprises de la fenetre consommaient tout le quota et les suivantes
    n'avaient aucune couverture Adzuna (perte silencieuse du signal de
    deploiement le mieux date). Voir quota_pays_adzuna().

    `fetch` injectable pour tests : callable(pays, params) -> dict JSON.
    Sans cle et sans fetch : renvoie [] (source simplement inactive)."""
    if fetch is None and not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        return []
    session = session or ted.session_robuste()
    articles = []
    for pays in (ADZUNA_PAYS if pays is None else pays):
        if _ADZUNA_STATS["coupe"] or _ADZUNA_STATS["appels"] >= ADZUNA_MAX_APPELS:
            break
        params = {
            "app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY,
            "what_phrase": entreprise, "results_per_page": ADZUNA_RESULTATS,
            "content-type": "application/json",
        }
        _ADZUNA_STATS["appels"] += 1
        try:
            if fetch is not None:
                data = fetch(pays, params)
            else:
                rep = session.get(ADZUNA_ENDPOINT.format(pays), params=params, timeout=20)
                if rep.status_code == 429:   # trop de requetes -> coupe-circuit
                    _ADZUNA_STATS["coupe"] = True
                    print("  (info) Adzuna : quota/minute atteint (429), coupe pour ce run.")
                    break
                if rep.status_code >= 400:
                    _ADZUNA_STATS["erreurs"] += 1
                    detail = ""
                    try:
                        detail = rep.json().get("exception") or rep.text[:200]
                    except Exception:
                        detail = rep.text[:200]
                    print("  (info) Adzuna {} : HTTP {} -- {}".format(pays, rep.status_code, detail))
                    time.sleep(ADZUNA_PAUSE)
                    continue
                data = rep.json()
                time.sleep(ADZUNA_PAUSE)
        except Exception as e:
            _ADZUNA_STATS["erreurs"] += 1
            print("  (info) Adzuna {} indisponible ({}).".format(pays, e))
            continue

        for job in (data or {}).get("results", []):
            titre = (job.get("title") or "").strip()
            desc = (job.get("description") or "").strip()
            lieu = ((job.get("location") or {}).get("display_name") or "")
            zones = " ".join((job.get("location") or {}).get("area", []) or [])
            societe = ((job.get("company") or {}).get("display_name") or "")
            texte = " ".join([titre, desc, lieu, zones])
            # On ne garde que les offres evoquant un pays a risque (= deploiement).
            if not _mentionne_pays_risque(texte):
                continue
            _ADZUNA_STATS["offres"] += 1
            articles.append({
                "titre": "[Offre d'emploi] {} - {}".format(titre, lieu),
                "lien": job.get("redirect_url") or job.get("id", ""),
                "date": _iso_vers_rfc822(job.get("created", "")),
                "resume": "{}. Recruteur : {}. Lieu : {} {}. {}".format(
                    titre, societe or entreprise, lieu, zones, desc)[:800],
            })
    return articles


# ===========================================================================
# PARTIE 3 -- PROMPT NEUTRE (tous secteurs, meme schema que BITD)
# ===========================================================================
# Meme schema de sortie que bitd.PROMPT_SIGNAL, exploite par scorer_signal (ci-dessous).
PROMPT_NEUTRE = """Tu analyses une actualité ou une offre d'emploi concernant une entreprise privée, pour une société française de protection de personnes en zones à risque. Objectif : repérer si l'entreprise VA DÉPLOYER ou DÉPLOIE DÉJÀ des personnels (cadres, experts, techniciens, chefs de projet, équipes) DANS UN PAYS À RISQUE, ce qui crée un besoin probable de sûreté (escorte, protection rapprochée, chauffeur sécurité, sécurisation de déplacements).

Secteur de l'entreprise : {secteur}
Entreprise : {entreprise}
Titre : {titre}
Contenu (peut être tronqué) : {resume}

Sont des signaux : ouverture de site/filiale/chantier, contrat ou marché à l'étranger, recrutement d'un poste basé ou déployé dans un pays à risque, mission d'assistance technique, livraison/mise en service sur place, exploration minière/pétrolière, salon ou implantation à l'étranger.
NE sont PAS des signaux : résultats financiers, nominations internes, produits sans déploiement, actualité 100% domestique, poste télétravail/siège sans terrain, rumeur vague.

IMPORTANT sur la certitude : une simple INTENTION, ANNONCE, lettre d'intention ou PROJET (mots comme "intention", "envisage", "pourrait", "projette", "en discussion", "vraisemblablement") SANS présence physique confirmée ou datée => confiance FAIBLE (0.3 à 0.55) ET imminence "indetermine". Ne réserve "immediate" ou "court_terme" qu'aux déploiements confirmés ou déjà en cours sur le terrain.

Sois STRICT : dans le doute, signal=false. La confiance reflète ta certitude d'un vrai déploiement de personnel dans un pays à risque.

Réponds UNIQUEMENT en JSON valide, sans texte autour :
{{"signal": true/false, "iso3": "code ISO3 du pays de déploiement ou vide", "pays": "nom du pays ou vide", "type_activite": "formation_mco|implantation|essais_demonstration|livraison_mise_en_service|recrutement_local|incident|contrat_export|autre", "imminence": "immediate|court_terme|indetermine", "confiance": 0.0, "resume": "une phrase factuelle"}}"""


def analyser(entreprise, secteur, article, appel=None):
    """Tri LLM (Haiku) avec le prompt neutre. Meme schema de sortie que BITD,
    donc scorer_signal (local, unique) / bitd.normaliser_iso3 s'appliquent tels quels."""
    prompt = PROMPT_NEUTRE.format(
        secteur=secteur or "non precise", entreprise=entreprise,
        titre=article.get("titre", ""),
        resume=(article.get("resume") or article.get("titre", ""))[:800])
    try:
        appel_reel = appel or (lambda p: bitd._appel_llm(p, ted.MODELE))
        return bitd._parser_json(appel_reel(prompt))
    except Exception as e:
        print("  (info) Analyse LLM echouee ({}).".format(e))
        return None


# ===========================================================================
# SCORING CORRIGE (commercial vraiment variable + garde-fou confiance)
# ===========================================================================
# Bug constate : le score commercial etait fige a 8/10 (poids priorite mal
# calibres). On le rend variable et on ajoute un garde-fou : un signal peu sur
# ne peut pas etre promu en "contacter". Reutilise les autres poids de BITD.
POIDS_PRIORITE_V2 = {"haute": 0.8, "moyenne": 0.5, "basse": 0.3}

# Multiplicateurs de retroaction (item 7), charges dans main() selon le mode.
# TROIS ETATS (RADAR_RETROACTION) :
#   "0" (defaut) -> OFF   : rien n'est charge, scoring strictement inchange.
#   "ombre"      -> OMBRE : les multiplicateurs sont charges et on CALCULE ce que
#                   la retroaction FERAIT (combien d'actions changeraient), mais
#                   on n'applique RIEN au score. Sert a mesurer l'impact et a
#                   gagner en confiance avant de brancher en direct.
#   "1"/"actif"  -> ACTIF : charges ET appliques au score final (comportement
#                   historique).
# _RETRO = None quand OFF (garde le comportement par defaut, scoring inchange).
_RETRO = None
_RETRO_MODE = "off"        # "off" | "ombre" | "actif"
_RETRO_OMBRE = None        # accumulateur d'observation (rempli en mode OMBRE)
CONF_MIN_CONTACTER = 0.6   # sous ce niveau, action plafonnee a "surveiller"


def _mode_retroaction(valeur_env):
    """Traduit RADAR_RETROACTION en mode. Tolerant a la casse / aux synonymes."""
    v = (valeur_env or "0").strip().lower()
    if v in ("1", "actif", "on", "live"):
        return "actif"
    if v in ("ombre", "shadow", "observation", "dry"):
        return "ombre"
    return "off"


def _action_pour(final, conf):
    """Regle d'action UNIQUE (contacter / surveiller / ignorer), avec garde-fou
    confiance. Source de verite partagee par le score reel ET l'observation
    d'ombre, pour qu'ils ne divergent jamais."""
    action = ("contacter" if final >= bitd.SEUIL_CONTACTER
              else "surveiller" if final >= bitd.SEUIL_SURVEILLER else "ignorer")
    if action == "contacter" and conf < CONF_MIN_CONTACTER:
        action = "surveiller"
    return action


def nouvel_observateur_ombre():
    """Accumulateur des effets QU'AURAIT la retroaction si elle etait active."""
    return {"n": 0, "actions_changees": 0, "vers_contacter": 0,
            "quittent_contacter": 0, "somme_delta_abs": 0.0}


def _enregistrer_ombre(obs, action_reelle, action_ombre, final_reel, final_ombre):
    """Note, pour un signal, l'ecart entre l'action REELLE (retro non appliquee)
    et l'action QU'AURAIT donnee la retroaction. C'est le changement d'ACTION,
    pas le delta de score, qui mesure l'impact commercial reel."""
    if obs is None:
        return
    obs["n"] += 1
    obs["somme_delta_abs"] += abs(final_ombre - final_reel)
    if action_ombre != action_reelle:
        obs["actions_changees"] += 1
        if action_ombre == "contacter":
            obs["vers_contacter"] += 1
        elif action_reelle == "contacter":
            obs["quittent_contacter"] += 1


def resume_ombre(obs):
    """Phrase de journal (ou None si aucune observation). KPI = actions qui
    changeraient si la retroaction passait en direct."""
    if not obs or not obs["n"]:
        return None
    return ("Retroaction OMBRE : sur {} signal(aux) score(s), la retroaction "
            "aurait change {} action(s) ({} -> contacter, {} hors contacter) ; "
            "|delta score| moyen {:.2f}. (Rien n'a ete applique.)").format(
                obs["n"], obs["actions_changees"], obs["vers_contacter"],
                obs["quittent_contacter"], obs["somme_delta_abs"] / obs["n"])


def scorer_signal(extraction, priorite_compte, iso3=None):
    iso3 = (iso3 or extraction.get("iso3") or "").strip().upper()
    if not iso3 or iso3 not in ted.CODES_PAYS_SUIVIS:
        return None
    poids_zone = ted.MULTIPLICATEUR_ZONE.get(iso3, 0.3)
    poids_act = bitd.POIDS_ACTIVITE.get(extraction.get("type_activite"), 0.25)
    poids_prio = POIDS_PRIORITE_V2.get((priorite_compte or "").strip().lower(), 0.5)
    poids_imm = bitd.POIDS_IMMINENCE.get(extraction.get("imminence"), 0.7)
    surete = round(10 * poids_zone * poids_act, 1)
    commercial = round(10 * poids_prio, 1)
    final = round((0.6 * surete + 0.4 * commercial) * poids_imm, 1)
    # Zone (calculee ici car la retroaction en a besoin avant de fixer l'action).
    paire = bitd.ZONE_PAR_ISO3.get(iso3)
    if isinstance(paire, (tuple, list)) and len(paire) >= 2:
        nom_fr, zone = paire[0], paire[1]
    else:
        nom_fr, zone = (extraction.get("pays") or ""), "Non classe"
    # Retroaction (item 7) : nuance le score final selon la conversion observee
    # (secteur x zone). NEUTRE si desactivee (mode off) ou donnees insuffisantes.
    # On ne touche qu'au versant commercial via le score final, jamais a la
    # surete (evaluation de risque objective).
    mult = radar_retroaction.mult_pour(
        _RETRO, extraction.get("type_activite"), zone) if _RETRO else 1.0
    final_brut = final
    if _RETRO and _RETRO_MODE == "actif":
        final = round(final * mult, 1)
    try:
        conf = float(extraction.get("confiance") or 0)
    except (TypeError, ValueError):
        conf = 0
    action = _action_pour(final, conf)
    # OBSERVATION (mode ombre) : que FERAIT la retroaction si elle etait active ?
    # On calcule le score/action qu'elle DONNERAIT, sans jamais l'appliquer au
    # resultat renvoye. Seul le mode ombre remplit l'accumulateur.
    if _RETRO and _RETRO_MODE == "ombre":
        final_ombre = round(final_brut * mult, 1)
        _enregistrer_ombre(_RETRO_OMBRE, action,
                           _action_pour(final_ombre, conf), final_brut, final_ombre)
    return {"final": final, "surete": surete, "commercial": commercial,
            "zone": zone, "action": action, "nom": nom_fr}


# ===========================================================================
# PARTIE 4 -- TRAITEMENT D'UNE ENTREPRISE (Google News + Adzuna)
# ===========================================================================

# Declencheurs de DEPLOIEMENT (FR + EN) : cible l'actu de terrain plutot que
# l'actu boursiere. Les entreprises internationales ont surtout une presse en
# anglais, d'ou les termes bilingues.
TRIGGERS_NEWS = (
    'contrat OR chantier OR implantation OR filiale OR "nouveau site" OR '
    'deploiement OR expatrie OR mine OR forage OR '
    'contract OR "new site" OR expansion OR awarded OR drilling OR exploration OR '
    '"field operations" OR mobilization OR "site opening" OR deployment')


def _collecter_news_locale(url, entreprise, session):
    """Un flux Google News (une locale), resilient : sur 503, une seule
    nouvelle tentative apres pause, sinon on passe sans insister."""
    for tentative in range(2):
        try:
            rep = session.get(url, timeout=30)
            if rep.status_code == 503:
                if tentative == 0:
                    time.sleep(PAUSE_REPLI)
                    continue
                print("  (info) Google News sature (503) pour {} : locale ignoree.".format(entreprise))
                return []
            rep.raise_for_status()
            return bitd.parser_rss(rep.text)
        except Exception as e:
            if tentative == 0:
                time.sleep(PAUSE_REPLI)
                continue
            print("  (info) Flux indisponible pour {} ({}).".format(entreprise, str(e)[:70]))
            return []
    return []


def collecter_news(entreprise, requete="", session=None):
    """Google News RSS MULTI-LOCALE (FR + EN par defaut) : capte la presse
    francaise ET la presse internationale des majors etrangers de la watchlist.
    Fusionne les locales, deduplique par URL, plafonne au global. Requete
    enrichie de declencheurs de deploiement (FR + EN) si aucune requete
    personnalisee. Chaque locale est resiliente et isolee : une locale en echec
    n'empeche pas les autres."""
    session = session or ted.session_robuste()
    requete = requete or '"{}" ({})'.format(entreprise, TRIGGERS_NEWS)
    vus, articles = set(), []
    for i, (hl, gl, ceid) in enumerate(GNEWS_LOCALES):
        if i > 0:
            time.sleep(PAUSE_LOCALE)   # respiration anti rate-limit entre locales
        url = bitd.url_google_news(entreprise, requete, hl=hl, gl=gl, ceid=ceid)
        for a in _collecter_news_locale(url, entreprise, session):
            k = bitd.id_article(a.get("lien", ""))
            if k and k not in vus:
                vus.add(k)
                articles.append(a)
    return articles[:MAX_ARTICLES_PRIVE]


def collecter_signaux(entreprise, requete, session=None, fetch_adzuna=None,
                      pays_adzuna=None):
    """Fusionne les sources, ADZUNA EN PREMIER puis Google News multi-locale,
    dedup par URL. Adzuna est prioritaire car une offre d'emploi ("Country
    Manager Mali", "HSE Supervisor Iraq") est le signal de deploiement le plus
    net et le mieux date ; en le placant en tete, il passe l'analyse avant que
    le budget LLM du run ne s'epuise sur des articles de presse plus bruites."""
    articles = collecter_adzuna(entreprise, fetch=fetch_adzuna, session=session,
                                pays=pays_adzuna)
    articles += collecter_news(entreprise, requete, session=session)
    vus, uniques = set(), []
    for a in articles:
        k = bitd.id_article(a.get("lien", ""))
        if k and k not in vus:
            vus.add(k)
            uniques.append(a)
    return uniques


def traiter_entreprise(compte, deja_vus, cles_existantes, appel=None,
                       appel_verif=None, session=None, budget=None,
                       vus_ce_run=None, fetch_adzuna=None, pays_adzuna=None):
    """Renvoie les signaux retenus pour une entreprise (dedup par evenement).
    Reutilise le scoring, la verification Sonnet et la memoire de BITD."""
    entreprise = compte.get("entreprise", "").strip()
    if not entreprise:
        return []
    secteur = compte.get("secteur", "")
    requete = compte.get("requete_personnalisee", "")
    retenus = {}

    for article in collecter_signaux(entreprise, requete, session=session,
                                     fetch_adzuna=fetch_adzuna,
                                     pays_adzuna=pays_adzuna):
        if not bitd.article_frais(article):
            _dbg(article, "rejetee: annonce trop ancienne")
            continue
        pub = bitd.id_article(article.get("lien", ""))
        if pub in deja_vus:
            _dbg(article, "deja vue (run precedent)")
            continue
        if bitd.bruit_evident(article):
            _dbg(article, "rejetee: bruit evident (financier/RH/produit)")
            deja_vus.add(pub)
            if vus_ce_run is not None:
                vus_ce_run.add(pub)
            continue
        if budget is not None:
            if budget.get("reste", 0) <= 0:
                break
            budget["reste"] -= 1
        if vus_ce_run is not None:
            vus_ce_run.add(pub)
        deja_vus.add(pub)

        extraction = analyser(entreprise, secteur, article, appel=appel)
        if not extraction or not extraction.get("signal"):
            _dbg(article, "rejetee LLM: pas un signal de deploiement")
            continue
        iso3 = bitd.normaliser_iso3(extraction)
        extraction["iso3"] = iso3
        sc = scorer_signal(extraction, compte.get("priorite_socle"), iso3=iso3)
        if not sc or sc["action"] == "ignorer":
            _dbg(article, "rejetee: pays hors univers risque" if not sc
                 else "rejetee: score trop bas (final {})".format(sc["final"]))
            continue

        # Escalade Sonnet sur signaux a fort enjeu (reutilise BITD).
        if sc["final"] >= bitd.SEUIL_CONTACTER or float(extraction.get("confiance") or 0) < 0.7:
            verif = bitd.verifier_signal_sonnet(entreprise, article, appel=appel_verif)
            if verif is not None:
                if not verif.get("signal"):
                    _dbg(article, "rejetee (Sonnet): non confirme")
                    continue
                iso3 = bitd.normaliser_iso3(verif)
                verif["iso3"] = iso3
                sc2 = scorer_signal(verif, compte.get("priorite_socle"), iso3=iso3)
                if not sc2 or sc2["action"] == "ignorer":
                    _dbg(article, "rejetee (Sonnet): score trop bas")
                    continue
                extraction, sc = verif, sc2

        cle = bitd.clef_evenement(entreprise, sc["nom"], extraction.get("type_activite"))
        if cle in cles_existantes:
            _dbg(article, "rejetee: evenement deja connu (anti-doublon)")
            continue
        modele = ted.MODELE_RAFFINEMENT if sc["final"] >= bitd.SEUIL_CONTACTER else ted.MODELE
        ligne = bitd.ligne_prive(compte, article, extraction, sc, modele)
        _dbg(article, "GARDEE: final {} | action {} | {}".format(
            sc["final"], sc["action"], sc["nom"]))
        # on garde le meilleur signal par evenement
        if cle not in retenus or sc["final"] > retenus[cle]["final"]:
            retenus[cle] = {"final": sc["final"], "ligne": ligne, "sc": sc,
                            "entreprise": entreprise}
    return list(retenus.values())


# ===========================================================================
# PARTIE 5 -- POINT D'ENTREE
# ===========================================================================

def _charger_outcomes_prive(sheet_id, fichier_cs):
    """Lit l'onglet des signaux prives et extrait les issues (secteur, zone,
    statut) pour la boucle de retroaction. Lecture SEULE, best-effort : en cas
    d'echec, renvoie une liste vide (la retroaction reste alors neutre)."""
    try:
        classeur = _ouvrir_classeur(sheet_id, fichier_cs)
        valeurs = classeur.worksheet(bitd.NOM_ONGLET_PRIVE).get_all_values()
    except Exception as e:
        print("(retroaction) lecture '{}' impossible : {}".format(bitd.NOM_ONGLET_PRIVE, e))
        return []
    if not valeurs or len(valeurs) < 2:
        return []
    idx = {c: i for i, c in enumerate(valeurs[0])}

    def col(row, nom):
        i = idx.get(nom)
        return row[i] if (i is not None and i < len(row)) else ""

    return [{"secteur": col(r, "type_activite"), "zone": col(r, "zone"),
             "statut": col(r, bitd.COLONNE_STATUT)} for r in valeurs[1:]]


def main():
    if not ACTIVER:
        print("(info) Moteur signaux prives desactive (RADAR_SIGNAUX_PRIVES=0).")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return

    print("=" * 60)
    print("MOTEUR SIGNAUX PRIVES (multi-secteurs) - Radar Amarante")
    print("=" * 60)
    if DEBUG:
        print("(mode diagnostic actif : decision LLM affichee pour chaque offre Adzuna)")
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        print("(info) Adzuna inactif (ADZUNA_APP_ID / ADZUNA_APP_KEY absents) : "
              "Google News + attributions seulement.")

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    comptes = construire_watchlist(sheet_id, fichier)
    if not comptes:
        print("Watchlist vide. Rien a faire (verifie l'onglet '{}').".format(NOM_ONGLET_WATCHLIST))
        return
    print("Watchlist : {} entreprises (multi-secteurs + defense + attributaires).".format(len(comptes)))

    # Memoire inter-runs (curseur + articles vus) : DECOUPLEE du Sheet, lue
    # depuis radar_etat.json (versionne dans le depot). Au tout premier run, le
    # fichier n'existe pas encore : migration douce depuis l'ancien etat du
    # Sheet pour ne pas repartir de zero (et donc ne pas tout retraiter).
    curseur, vus_list = radar_etat.charger()
    curseur_prio = radar_etat.charger_prio()
    if curseur is None:
        if sheet_id and fichier:
            curseur = bitd.lire_curseur(sheet_id, fichier)
            vus_list = sorted(bitd.charger_vus(sheet_id, fichier))
            print("radar_etat : migration initiale depuis le Sheet "
                  "({} vus, curseur {}).".format(len(vus_list), curseur))
        else:
            curseur, vus_list = 0, []
            print("radar_etat : demarrage a froid (ni fichier d'etat ni Sheet).")
    deja_vus = set(vus_list)
    cles_existantes = bitd.cles_evenements_existantes(sheet_id, fichier)

    # Boucle de retroaction (item 7). TROIS modes via RADAR_RETROACTION :
    #   off (defaut) : rien. ombre : on MESURE l'impact sans l'appliquer. actif :
    #   on applique au score. Apprend des issues gagne/perdu, secteur x zone.
    #   Prudente : neutre tant qu'une categorie n'a pas assez d'issues. Lecture
    #   seule du Sheet, ne cree aucun point de defaillance.
    global _RETRO, _RETRO_MODE, _RETRO_OMBRE
    _RETRO_MODE = _mode_retroaction(os.environ.get("RADAR_RETROACTION", "0"))
    _RETRO = None
    _RETRO_OMBRE = nouvel_observateur_ombre() if _RETRO_MODE == "ombre" else None
    if _RETRO_MODE in ("actif", "ombre") and sheet_id and fichier:
        _RETRO = radar_retroaction.multiplicateurs(_charger_outcomes_prive(sheet_id, fichier))
        ajustees = sum(1 for d in radar_retroaction.DIMENSIONS
                       for m in _RETRO[d].values() if m != 1.0)
        label = "ACTIVE" if _RETRO_MODE == "actif" else "OMBRE (observation, non appliquee)"
        print("Retroaction {} : {} issue(s), taux de base {:.0%}, "
              "{} categorie(s) ajustee(s).".format(label, _RETRO["n"], _RETRO["base"], ajustees))

    # Rotation A DEUX VITESSES (item cadence). Le probleme : un balayage modulaire
    # unique met ~3 mois a couvrir ~865 entites, or un signal de deploiement
    # (offre "Country Manager Bamako") vit quelques semaines. On reserve donc une
    # TETE prioritaire (attributaires = deploiement en cours) scannee a CHAQUE
    # run, curseur dedie ; la QUEUE (watchlist curee + defense) tourne normalement,
    # curseur general. Latence de la tete : len(prio)/taille_tete runs au lieu du
    # cycle complet (ex. 60 attributaires / 10 = 6 runs ~ 3 semaines).
    taille_fenetre = taille_fenetre_pour(os.environ.get("RADAR_PRIVES_ENTREPRISES"))
    comp = composer_fenetre_deux_vitesses(
        comptes, taille_fenetre, os.environ.get("RADAR_PRIVES_TETE"),
        curseur, curseur_prio)
    fenetre = comp["fenetre"]     # la tete d'abord (curseur honnete en cas d'arret)
    print("Watchlist : {} compte(s) ({} attributaire(s) prioritaire(s), {} en queue). "
          "Ce run : {} en tete + {} en queue (curseurs prio {} / queue {}).".format(
              len(comptes), comp["n_prio"], comp["n_reste"], len(comp["slice_prio"]),
              len(comp["slice_reste"]), comp["debut_prio"], comp["debut_reste"]))

    budget = {"reste": bitd.MAX_ANALYSES_PAR_RUN}
    vus_ce_run = set()
    t0 = time.time()
    resultats = []
    traitees = 0                  # entreprises REELLEMENT traitees (curseur)
    arret = ""
    for i, compte in enumerate(fenetre, start=1):
        # Garde-temps : le job GitHub est plafonne (45 min) et cette boucle est
        # la plus longue du run. Sans cet arret propre, un depassement tuerait
        # TOUT le job : etat non commite, dashboard non regenere, digest non
        # envoye. On prefere traiter moins d'entreprises et finir le run.
        ecoule = (time.time() - t0) / 60.0
        if ecoule >= MINUTES_MAX:
            arret = "garde-temps ({:.0f} min)".format(ecoule)
            print("  (garde-temps atteint apres {:.0f} min, arret propre)".format(ecoule))
            break

        # Repartition du quota Adzuna sur les entreprises RESTANTES : chacune
        # garde une couverture, au lieu que les premieres epuisent le plafond.
        quota = quota_pays_adzuna(
            ADZUNA_MAX_APPELS - _ADZUNA_STATS["appels"], len(fenetre) - i + 1)
        signaux = traiter_entreprise(
            compte, deja_vus, cles_existantes, session=None,
            budget=budget, vus_ce_run=vus_ce_run,
            pays_adzuna=pays_pour_compte(compte, quota))
        traitees = i
        for s in signaux:
            cles_existantes.add(bitd.clef_evenement(
                s["entreprise"], s["sc"]["nom"], ""))  # anti-doublon intra-run
        resultats += signaux
        etat = "{} signal(aux)".format(len(signaux)) if signaux else "0 signal"
        print("  [{:>3}/{}] {:34} : {}".format(i, len(fenetre), compte["entreprise"][:34], etat))
        if budget["reste"] <= 0:
            arret = "budget d'analyses epuise"
            print("  (budget d'analyses epuise, on s'arrete proprement)")
            break
        time.sleep(PAUSE_ENTREPRISE)   # respiration anti rate-limit Google News

    # CURSEURS HONNETES : chacun n'avance que des comptes REELLEMENT traites.
    # La tete est en premier dans la fenetre : sur un arret anticipe (budget /
    # garde-temps), on sait combien de prioritaires ont ete faits (les premiers)
    # et combien de la queue (le reste). Les non-traites sont repris au run
    # suivant, sans saut.
    prochain_prio, prochain = avancer_curseurs(comp, traitees)
    if arret and traitees < len(fenetre):
        print("  -> {} compte(s) non traite(s) ({}) : repris au prochain run.".format(
            len(fenetre) - traitees, arret))

    print("Temps moteur : {:.0f}s. Signaux retenus : {}. Prochains curseurs : "
          "prio {}, queue {}.".format(time.time() - t0, len(resultats), prochain_prio, prochain))
    if ADZUNA_APP_ID and ADZUNA_APP_KEY:
        print("Adzuna : {appels} appel(s), {offres} offre(s) zone risque, "
              "{erreurs} erreur(s).".format(**_ADZUNA_STATS))
    else:
        print("Adzuna : inactif (ADZUNA_APP_ID / ADZUNA_APP_KEY absents).")

    # Ecriture des RESULTATS dans le Sheet (le livrable, lu par le dashboard et
    # le bouton "Je contacte"). L'ETAT inter-runs, lui, ne va plus dans le Sheet.
    if sheet_id and fichier:
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(
                fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            classeur_rw = radar_resilience.avec_retry(lambda: gspread.authorize(creds).open_by_key(sheet_id), "ouverture classeur")
            feuille = bitd.ouvrir_ou_creer_onglet(classeur_rw)
            n_ecrits = bitd.ecrire_resultats(feuille, resultats)
            print("-> {} signal(aux) ecrit(s) dans '{}'.".format(n_ecrits, bitd.NOM_ONGLET_PRIVE))
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("(dry-run : pas de Sheet configure)")

    # Retroaction en mode OMBRE : bilan de ce qu'elle AURAIT change ce run. C'est
    # la mesure a suivre run apres run avant de decider de passer en actif.
    phrase_ombre = resume_ombre(_RETRO_OMBRE)
    if phrase_ombre:
        print(phrase_ombre)
    # Persistance de la tendance (best-effort, ne casse jamais le run) : le KPI
    # d'ombre s'accumule dans 'runs_radar', pour decider plus tard sur donnees.
    if _RETRO_OMBRE is not None:
        try:
            import radar_runs
            print("(pg) " + radar_runs.enregistrer(
                "ombre", radar_runs.charge_ombre(_RETRO_OMBRE, _RETRO_MODE)))
        except Exception as e:
            print("(pg) stat de run ombre non persistee ({}) -- run non affecte".format(
                str(e)[:100]))

    # Persistance de l'ETAT inter-runs dans un fichier versionne, INDEPENDAMMENT
    # du Sheet (survit meme si le Sheet est indisponible). Le workflow commite
    # radar_etat.json en fin de run.
    n_vus = radar_etat.sauver(prochain, vus_list, vus_ce_run, curseur_prio=prochain_prio)
    print("radar_etat : {} vus memorises (plafond {}), curseurs -> prio {} / queue {} (fichier {}).".format(
        n_vus, radar_etat.MAX_VUS_MEMOIRE, prochain_prio, prochain, radar_etat.CHEMIN_ETAT))


if __name__ == "__main__":
    main()
