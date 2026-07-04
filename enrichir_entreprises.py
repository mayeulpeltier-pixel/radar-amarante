# -*- coding: utf-8 -*-
"""
Radar Amarante - Enrichissement firmographique des entreprises (Phase 1, #2).

Pour chaque entreprise de la whitelist (onglet 'comptes_cibles_bitd'), recupere
son identite officielle et ses dirigeants, et ecrit le resultat dans l'onglet
'entreprises_enrichies'. Objectif : commencer a repondre a "qui appeler".

Sources (coût quasi nul) :
- API Recherche d'entreprises (recherche-entreprises.api.gouv.fr) : gratuite,
  sans cle. SIREN, dirigeants, NAF, effectifs, ville. Entreprises FRANCAISES.
- Pappers (api.pappers.fr) : OPTIONNEL. Si la variable PAPPERS_API_KEY est
  definie, ajoute le chiffre d'affaires. Sinon, ignore (le module tourne quand
  meme sur la source publique).

Auto-limite : n'enrichit que les entreprises nouvelles ou anciennes (> DELAI),
jamais deux fois de suite -> reste dans les quotas gratuits. Tolerant aux pannes.
"""

import os
import re
import datetime
import unicodedata

import ted_complet_v14 as ted

try:
    import bitd_signaux as bs
    NOM_ONGLET_WHITELIST = bs.NOM_ONGLET_WHITELIST
    _lire_whitelist = bs.lire_whitelist
except Exception:
    NOM_ONGLET_WHITELIST = "comptes_cibles_bitd"
    _lire_whitelist = None

# ===========================================================================
# CONFIGURATION
# ===========================================================================
NOM_ONGLET_ENRICHIES = "entreprises_enrichies"
API_GOUV = "https://recherche-entreprises.api.gouv.fr/search"
API_PAPPERS = "https://api.pappers.fr/v2/entreprise"
# Repli international (societes absentes du registre FR) : GLEIF, gratuit, sans
# cle. Identite officielle + pays + siege. https://api.gleif.org/api/v1/
API_GLEIF = "https://api.gleif.org/api/v1/lei-records"
DELAI_RAFRAICHISSEMENT_JOURS = 120     # au-dela, on re-enrichit
PAUSE = 0.4                            # politesse API (7 req/s max cote gouv)

# --- Recherche de contacts (Hunter.io), OPTIONNEL et frugal en quota ---------
# Palier gratuit Hunter : 25 recherches/mois. On cible donc les seules
# entreprises "Haute" priorite, on plafonne par run, et on verifie le quota
# restant avant d'appeler. Emails PROFESSIONNELS uniquement (pas de webmail).
# RGPD : email pro nominatif = donnee personnelle. Prospection B2B en France
# autorisee si la personne est informee et peut s'opposer AU PREMIER CONTACT.
API_HUNTER_FINDER = "https://api.hunter.io/v2/email-finder"
API_HUNTER_DOMAIN = "https://api.hunter.io/v2/domain-search"   # par nom d'entreprise (international)
API_HUNTER_ACCOUNT = "https://api.hunter.io/v2/account"
NOM_ONGLET_CONTACTS = "contacts_bitd"
COLONNES_CONTACTS = ["entreprise", "email_pro", "confiance", "source", "date_contact"]
RADAR_HUNTER_MAX = int(os.environ.get("RADAR_HUNTER_MAX", "6"))   # appels/run max
SEUIL_CONF_EMAIL = 50                  # sous ce score, email marque "a verifier"

COLONNES_ENRICHIES = [
    "entreprise", "siren", "nom_officiel", "dirigeant_principal",
    "autres_dirigeants", "activite_naf", "effectif", "ville",
    "chiffre_affaires", "source", "date_enrichissement",
]

# Libelles des tranches d'effectif INSEE.
TRANCHES_EFFECTIF = {
    "00": "0 sal.", "01": "1-2", "02": "3-5", "03": "6-9", "11": "10-19",
    "12": "20-49", "21": "50-99", "22": "100-199", "31": "200-249",
    "32": "250-499", "41": "500-999", "42": "1000-1999", "51": "2000-4999",
    "52": "5000-9999", "53": "10000+",
}
QUALITES_DIRIGEANTES = ("président", "directeur général", "directrice générale",
                        "gérant", "gérante", "directeur", "administrateur")


# ===========================================================================
# API RECHERCHE D'ENTREPRISES (gouv, gratuite)
# ===========================================================================
def rechercher_entreprise_gouv(nom, session=None, fetch=None):
    """Renvoie un dict firmographique pour `nom`, ou None si introuvable."""
    donnees = _get_json(API_GOUV, {"q": nom, "page": 1, "per_page": 1},
                        session=session, fetch=fetch)
    if not donnees:
        return None
    resultats = donnees.get("results") or []
    if not resultats:
        return None
    r = resultats[0]
    siege = r.get("siege") or {}
    principal, autres = _extraire_dirigeants(r.get("dirigeants") or [])
    tranche = r.get("tranche_effectif_salarie")
    return {
        "siren": r.get("siren", ""),
        "nom_officiel": r.get("nom_complet") or r.get("nom_raison_sociale") or "",
        "dirigeant_principal": principal,
        "autres_dirigeants": " ; ".join(autres),
        "activite_naf": r.get("libelle_activite_principale")
        or r.get("activite_principale", ""),
        "effectif": TRANCHES_EFFECTIF.get(tranche, tranche or ""),
        "ville": siege.get("libelle_commune", ""),
    }


def _extraire_dirigeants(dirigeants):
    """(dirigeant_principal, [autres]) a partir de la liste API. Personnes
    physiques d'abord, en privilegiant les qualites dirigeantes."""
    personnes = []
    for d in dirigeants:
        if (d.get("type_dirigeant") or "").lower().startswith("personne mor"):
            nom = d.get("denomination") or d.get("nom", "")
            if nom:
                personnes.append((nom.strip(), (d.get("qualite") or "").strip(), 0))
            continue
        prenom = (d.get("prenoms") or "").strip().split(" ")[0]
        nom = (d.get("nom") or "").strip()
        qualite = (d.get("qualite") or "").strip()
        complet = (prenom + " " + nom).strip()
        if complet:
            poids = 2 if any(q in qualite.lower() for q in QUALITES_DIRIGEANTES) else 1
            personnes.append((complet, qualite, poids))
    if not personnes:
        return "", []
    personnes.sort(key=lambda p: p[2], reverse=True)
    def libelle(p):
        return "{} ({})".format(p[0], p[1]) if p[1] else p[0]
    return libelle(personnes[0]), [libelle(p) for p in personnes[1:3]]


# ===========================================================================
# GLEIF (repli international, gratuit, sans cle)
# ===========================================================================
def _norm_nom(s):
    """Normalisation PRUDENTE d'un nom d'entreprise pour le matching :
    minuscules, sans accents, ponctuation et formes juridiques (FR + courantes
    a l'international) retirees."""
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.,'()]", " ", s)
    s = re.sub(r"\b(sa|sas|sarl|sasu|eurl|spa|srl|gmbh|ltd|llc|inc|plc|bv|nv|ag|"
               r"co|company|corp|group|groupe|holding|international|intl|as|"
               r"anonim|sirketi|ticaret|ve|jsc|ooo|pjsc|llp|pvt)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def rechercher_entreprise_gleif(nom, session=None, fetch=None):
    """Identite officielle d'une entreprise via GLEIF (mondial). Renvoie un dict
    {nom_officiel, ville, pays, lei, statut} ou None. Matching PRUDENT : le nom
    officiel doit correspondre (egalite normalisee ou l'un prefixe l'autre),
    sinon on renvoie None plutot que de deviner (cf. decision B de l'item 6)."""
    if not nom:
        return None
    donnees = _get_json(API_GLEIF,
                        {"filter[entity.legalName]": nom, "page[size]": 10},
                        session=session, fetch=fetch)
    if not donnees:
        return None
    records = donnees.get("data") or []
    if not records:
        return None
    cible = _norm_nom(nom)
    if not cible:
        return None
    candidats = []
    for r in records:
        ent = (r.get("attributes") or {}).get("entity") or {}
        nom_off = ((ent.get("legalName") or {}).get("name") or "").strip()
        n = _norm_nom(nom_off)
        if not n:
            continue
        if n == cible or n.startswith(cible + " ") or cible.startswith(n + " "):
            candidats.append((len(n), r, nom_off, ent))
    if not candidats:
        return None
    candidats.sort(key=lambda c: c[0])          # le plus court = le plus proche
    _, r, nom_off, ent = candidats[0]
    hq = ent.get("headquartersAddress") or {}
    la = ent.get("legalAddress") or {}
    return {
        "nom_officiel": nom_off,
        "ville": hq.get("city") or la.get("city") or "",
        "pays": hq.get("country") or la.get("country") or ent.get("jurisdiction") or "",
        "lei": (r.get("attributes") or {}).get("lei") or r.get("id") or "",
        "statut": ent.get("status") or "",
    }


# ===========================================================================
# PAPPERS (optionnel : chiffre d'affaires)
# ===========================================================================
def enrichir_pappers(siren, session=None, fetch=None):
    cle = os.environ.get("PAPPERS_API_KEY", "")
    if not (cle and siren):
        return ""
    donnees = _get_json(API_PAPPERS, {"api_token": cle, "siren": siren},
                        session=session, fetch=fetch)
    if not donnees:
        return ""
    ca = donnees.get("chiffre_affaires")
    if ca is None:
        finances = donnees.get("finances") or []
        if finances and isinstance(finances[0], dict):
            ca = finances[0].get("chiffre_affaires")
    return str(ca) if ca not in (None, "") else ""


# ===========================================================================
# HTTP tolerant (injectable pour tests)
# ===========================================================================
def _get_json(url, params, session=None, fetch=None):
    if fetch is not None:
        try:
            return fetch(url, params)
        except Exception:
            return None
    session = session or ted.session_robuste()
    try:
        rep = session.get(url, params=params, timeout=30)
        rep.raise_for_status()
        return rep.json()
    except Exception as e:
        print("  (info) API indisponible ({}) : {}".format(url, e))
        return None


# ===========================================================================
# ORCHESTRATION
# ===========================================================================
def enrichir_une(entreprise_row, session=None, fetch_gouv=None, fetch_pappers=None,
                 fetch_gleif=None):
    nom = entreprise_row.get("entreprise", "").strip()
    if not nom:
        return None
    gouv = rechercher_entreprise_gouv(nom, session=session, fetch=fetch_gouv)
    aujourd = datetime.date.today().isoformat()
    if not gouv:
        # Repli international : GLEIF (gratuit, sans cle). Donne l'identite
        # officielle, le pays et le siege des societes absentes du registre FR.
        base = {c: "" for c in COLONNES_ENRICHIES}
        gleif = rechercher_entreprise_gleif(nom, session=session, fetch=fetch_gleif)
        if gleif:
            ville = gleif["ville"]
            if gleif["pays"]:                    # pays code dans la ville (decision 2A)
                ville = "{} ({})".format(ville, gleif["pays"]) if ville else "({})".format(gleif["pays"])
            base.update({"entreprise": nom, "nom_officiel": gleif["nom_officiel"],
                         "ville": ville, "source": "gleif",
                         "date_enrichissement": aujourd})
        else:
            base.update({"entreprise": nom, "source": "non trouvée",
                         "date_enrichissement": aujourd})
        return [str(base.get(c, "")) for c in COLONNES_ENRICHIES]
    ca = enrichir_pappers(gouv.get("siren", ""), session=session, fetch=fetch_pappers)
    valeurs = {
        "entreprise": nom, "siren": gouv["siren"], "nom_officiel": gouv["nom_officiel"],
        "dirigeant_principal": gouv["dirigeant_principal"],
        "autres_dirigeants": gouv["autres_dirigeants"], "activite_naf": gouv["activite_naf"],
        "effectif": gouv["effectif"], "ville": gouv["ville"], "chiffre_affaires": ca,
        "source": "gouv+pappers" if ca else "gouv",
        "date_enrichissement": aujourd,
    }
    return [str(valeurs.get(c, "")) for c in COLONNES_ENRICHIES]


def entreprises_deja_enrichies(sheet_id, fichier_cs):
    """Noms deja enrichis recemment (pour ne pas re-hitter l'API). Renvoie un set."""
    if not (sheet_id and fichier_cs):
        return set()
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
        classeur = gspread.authorize(creds).open_by_key(sheet_id)
        valeurs = classeur.worksheet(NOM_ONGLET_ENRICHIES).get_all_values()
    except Exception:
        return set()
    return _recentes_depuis_valeurs(valeurs)


def _recentes_depuis_valeurs(valeurs, aujourd=None):
    if not valeurs:
        return set()
    aujourd = aujourd or datetime.date.today()
    i_ent = COLONNES_ENRICHIES.index("entreprise")
    i_date = COLONNES_ENRICHIES.index("date_enrichissement")
    debut = 1 if "entreprise" in [str(c).strip() for c in valeurs[0]] else 0
    recentes = set()
    for row in valeurs[debut:]:
        if max(i_ent, i_date) >= len(row):
            continue
        nom = str(row[i_ent]).strip()
        try:
            d = datetime.date.fromisoformat(str(row[i_date]).strip())
            frais = (aujourd - d).days <= DELAI_RAFRAICHISSEMENT_JOURS
        except ValueError:
            frais = True  # date illisible mais deja presente -> on ne re-hit pas
        if nom and frais:
            recentes.add(nom.lower())
    return recentes


def ouvrir_ou_creer_onglet(classeur):
    import gspread
    try:
        return classeur.worksheet(NOM_ONGLET_ENRICHIES)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET_ENRICHIES, rows=500,
                                   cols=len(COLONNES_ENRICHIES))
        f.update([COLONNES_ENRICHIES])
        return f


def credits_hunter(fetch=None):
    """Credits de recherche restants (palier gratuit = 25/mois). 0 si pas de cle."""
    if not os.environ.get("HUNTER_API_KEY", ""):
        return 0
    donnees = _get_json(API_HUNTER_ACCOUNT,
                        {"api_key": os.environ["HUNTER_API_KEY"]}, fetch=fetch)
    try:
        r = donnees["data"]["requests"]["searches"]
        return int(r["available"]) - int(r["used"])
    except Exception:
        return 0


def _nettoyer_nom(dirigeant):
    """'PATRICE CAINE (Président ...)' -> 'PATRICE CAINE'."""
    import re
    return re.sub(r"\s*\(.*?\)\s*", "", dirigeant or "").strip()


def trouver_contact_hunter(entreprise, dirigeant, fetch=None):
    """Email pro le plus probable via Hunter Email Finder. {} si rien / pas de cle."""
    cle = os.environ.get("HUNTER_API_KEY", "")
    nom = _nettoyer_nom(dirigeant)
    if not (cle and nom):
        return {}
    donnees = _get_json(API_HUNTER_FINDER,
                        {"company": entreprise, "full_name": nom, "api_key": cle},
                        fetch=fetch)
    data = (donnees or {}).get("data") or {}
    email = data.get("email")
    if not email:
        return {}
    score = data.get("score")
    statut = ((data.get("verification") or {}).get("status")) or ""
    source = "hunter" + ("/" + statut if statut else "")
    if score is not None and score < SEUIL_CONF_EMAIL:
        source += " (a verifier)"
    return {"email": email, "confiance": "" if score is None else str(score),
            "source": source}


def meilleur_email_domaine(emails):
    """Choisit le meilleur email d'une liste Hunter Domain Search : email
    NOMINATIF (personnel) de plus haute confiance en priorite, sinon email
    generique (contact@...). {} si liste vide."""
    if not emails:
        return {}
    persos = [e for e in emails if e.get("type") == "personal" and e.get("value")]
    generiques = [e for e in emails if e.get("type") != "personal" and e.get("value")]
    pool = persos or generiques
    if not pool:
        return {}

    def conf(e):
        c = e.get("confidence")
        return c if isinstance(c, (int, float)) else -1
    best = max(pool, key=conf)
    contact = ((best.get("first_name") or "").strip() + " "
               + (best.get("last_name") or "").strip()).strip()
    return {"email": best.get("value", ""), "type": best.get("type", ""),
            "confiance": "" if best.get("confidence") is None else str(best.get("confidence")),
            "contact": contact}


def trouver_contact_domaine_hunter(entreprise, fetch=None):
    """Email de contact via Hunter Domain Search par NOM d'entreprise. Marche a
    l'international (Hunter resout le domaine lui-meme). {} si rien / pas de cle.
    Cout : 1 credit de recherche par entreprise (1 a 10 emails renvoyes)."""
    cle = os.environ.get("HUNTER_API_KEY", "")
    if not (cle and entreprise):
        return {}
    donnees = _get_json(API_HUNTER_DOMAIN,
                        {"company": entreprise, "api_key": cle, "limit": 10}, fetch=fetch)
    data = (donnees or {}).get("data") or {}
    meilleur = meilleur_email_domaine(data.get("emails") or [])
    if not meilleur.get("email"):
        return {}
    statut = "hunter/domaine" + ("/" + meilleur["type"] if meilleur.get("type") else "")
    conf = meilleur.get("confiance", "")
    if conf.isdigit() and int(conf) < SEUIL_CONF_EMAIL:
        statut += " (a verifier)"
    return {"email": meilleur["email"], "confiance": conf, "source": statut,
            "contact": meilleur.get("contact", ""), "domaine": data.get("domain", "")}


def contacts_existants(classeur):
    """Entreprises deja tentees (evite de re-consommer du quota)."""
    import gspread
    try:
        valeurs = classeur.worksheet(NOM_ONGLET_CONTACTS).get_all_values()
    except gspread.WorksheetNotFound:
        return set()
    debut = 1 if valeurs and str(valeurs[0][:1]) == str(["entreprise"]) else 0
    return {str(r[0]).strip().lower() for r in valeurs[debut:] if r and str(r[0]).strip()}


def _infos_par_entreprise(classeur):
    """Mappe entreprise -> (dirigeant_principal, source) depuis
    entreprises_enrichies. La source ('gouv', 'gleif', 'non trouvée'...) permet
    de choisir la bonne methode Hunter : Email Finder (FR, dirigeant connu) ou
    Domain Search (etranger identifie par GLEIF)."""
    import gspread
    try:
        valeurs = classeur.worksheet(NOM_ONGLET_ENRICHIES).get_all_values()
    except gspread.WorksheetNotFound:
        return {}
    i_ent = COLONNES_ENRICHIES.index("entreprise")
    i_dir = COLONNES_ENRICHIES.index("dirigeant_principal")
    i_src = COLONNES_ENRICHIES.index("source")
    m = {}
    for r in valeurs:
        if len(r) > max(i_ent, i_dir, i_src) and str(r[i_ent]).strip().lower() != "entreprise":
            m[str(r[i_ent]).strip().lower()] = (str(r[i_dir]).strip(), str(r[i_src]).strip())
    return m


def selectionner_cibles_hunter(whitelist, infos, deja, budget):
    """Repartit le budget Hunter (plafond global) entre :
    - cibles FR : dirigeant connu -> Email Finder (contact nominatif, prioritaire) ;
    - cibles etrangeres : identifiees par GLEIF, sans dirigeant -> Domain Search.
    Ne cible QUE la priorite 'Haute', jamais deja tentee. Renvoie une liste de
    tuples (nom, methode, dirigeant) plafonnee au budget. Fonction pure (testable)."""
    cibles = []
    for w in whitelist:
        if w.get("priorite_socle", "").strip() != "Haute":
            continue
        nom = w.get("entreprise", "").strip()
        if not nom or nom.lower() in deja:
            continue
        dirigeant, source = infos.get(nom.lower(), ("", ""))
        if dirigeant:
            cibles.append((nom, "finder", dirigeant))
        elif source == "gleif":
            cibles.append((nom, "domaine", ""))
        # sinon (pas encore enrichie, ou 'non trouvée' pure) : on ne gaspille pas de credit
    cibles.sort(key=lambda c: 0 if c[1] == "finder" else 1)   # FR d'abord
    return cibles[:max(0, budget)]


def pass_contacts_hunter(classeur, whitelist, fetch=None):
    """Cherche l'email pro des entreprises Haute priorite sans contact connu.
    Frugal : plafond par run + verification du quota Hunter restant."""
    import time
    if not os.environ.get("HUNTER_API_KEY", ""):
        print("Hunter inactif (HUNTER_API_KEY absente).")
        return
    budget = min(RADAR_HUNTER_MAX, credits_hunter(fetch=fetch))
    if budget <= 0:
        print("Hunter : quota epuise ou indisponible, passe ignoree.")
        return
    deja = contacts_existants(classeur)
    infos = _infos_par_entreprise(classeur)
    cibles = selectionner_cibles_hunter(whitelist, infos, deja, budget)
    n_fr = sum(1 for c in cibles if c[1] == "finder")
    n_int = sum(1 for c in cibles if c[1] == "domaine")
    print("Hunter : {} credit(s) utilisable(s), {} cible(s) : {} FR (finder), "
          "{} etranger (domaine).".format(budget, len(cibles), n_fr, n_int))
    aujourd = datetime.date.today().isoformat()
    nouveaux = []
    for nom, methode, dirigeant in cibles:
        if methode == "finder":
            c = trouver_contact_hunter(nom, dirigeant, fetch=fetch)
        else:
            c = trouver_contact_domaine_hunter(nom, fetch=fetch)
        nouveaux.append([nom, c.get("email", ""), c.get("confiance", ""),
                         c.get("source", "non trouve"), aujourd])
        print("  [{}] {} : {}".format(methode, nom, c.get("email") or "aucun email trouve"))
        time.sleep(0.3)
    if not nouveaux:
        return
    import gspread
    try:
        feuille = classeur.worksheet(NOM_ONGLET_CONTACTS)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(title=NOM_ONGLET_CONTACTS, rows=500,
                                         cols=len(COLONNES_CONTACTS))
        feuille.update([COLONNES_CONTACTS])
    feuille.append_rows(nouveaux, value_input_option="RAW")
    trouves = sum(1 for r in nouveaux if r[1])
    print("Hunter : {} tentative(s), {} email(s) trouve(s), ecrits dans '{}'.".format(
        len(nouveaux), trouves, NOM_ONGLET_CONTACTS))


def main():
    import time
    print("=" * 60)
    print("ENRICHISSEMENT ENTREPRISES - Radar Amarante")
    print("=" * 60)
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    if _lire_whitelist is None:
        print("Module whitelist indisponible. Rien a faire.")
        return
    whitelist = _lire_whitelist(sheet_id, fichier)
    if not whitelist:
        print("Whitelist vide ou absente. Rien a faire.")
        return

    deja = entreprises_deja_enrichies(sheet_id, fichier)
    a_faire = [w for w in whitelist if w.get("entreprise", "").strip().lower() not in deja]
    print("Whitelist : {} | deja enrichies : {} | a enrichir : {}".format(
        len(whitelist), len(deja), len(a_faire)))

    session = ted.session_robuste()
    lignes = []
    for w in a_faire:
        ligne = enrichir_une(w, session=session)
        if ligne:
            lignes.append(ligne)
        time.sleep(PAUSE)

    if not (sheet_id and fichier):
        print("(dry-run) {} entreprises enrichies (pas de Sheet, non ecrit).".format(len(lignes)))
        return
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier, scopes=portee)
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    if lignes:
        feuille = ouvrir_ou_creer_onglet(classeur)
        feuille.append_rows(lignes, value_input_option="RAW")
        print("{} entreprises enrichies et ecrites dans '{}'.".format(
            len(lignes), NOM_ONGLET_ENRICHIES))
    else:
        print("Enrichissement firmographique : rien de nouveau.")

    # Passe contacts (Hunter), independante du cache d'enrichissement.
    pass_contacts_hunter(classeur, whitelist)


if __name__ == "__main__":
    main()
