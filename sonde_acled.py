# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE ACLED (jetable) : indice de conflit dynamique.
======================================================================

POURQUOI CETTE SOURCE
---------------------
ACLED (Armed Conflict Location & Event Data) recense les evenements de violence
politique et de troubles, GEOLOCALISES, avec le nombre de morts (fatalities).
Objectif radar (roadmap #11) : un INDICE DE CONFLIT PAR PAYS sur une fenetre
recente, qui BOOSTE le multiplicateur de risque (ta grille reste le plancher,
une flambee rechauffe). v1 = par pays ; le sous-national par GPS viendra apres.

ACCES (doc ACLED, verifiee le 14/08/2026 -- nouveau systeme post-sept 2025)
---------------------------------------------------------------------------
Compte myACLED (gratuit). Plus de cle API : OAuth.
  Token : POST https://acleddata.com/oauth/token
          body form : username=<email>, password=<mdp>, grant_type=password,
          client_id=acled, scope=authenticated
          -> { access_token (24h), refresh_token (14j) }
  Data  : GET https://acleddata.com/api/acled/read?_format=json
          header Authorization: Bearer <access_token>
          filtres : country, event_date=<debut>|<fin> + event_date_where=BETWEEN,
          fields (pipe), limit (defaut/max 5000, pagination au-dela).
Reponse : { "status": 200, "count": N, "data": [ {..evenement..}, ... ] }.

CE QUE CETTE SONDE ETABLIT, ET RIEN D'AUTRE
-------------------------------------------
  A. OAUTH : les identifiants obtiennent-ils un access_token ? (le seul point
     d'acces ; si ca marche, le reste suit.)
  B. DATA + SCHEMA : une requete pays + fenetre recente renvoie-t-elle des
     evenements avec `country`, `event_date`, `fatalities`, `event_type` ?
  C. AGREGATION : peut-on calculer l'indice (nb evenements + somme fatalities)
     sur l'echantillon ? Et le pattern MULTI-PAYS (OR) marche-t-il, pour batcher
     le perimetre au collecteur ?

IDENTIFIANTS : lus depuis l'env ACLED_EMAIL / ACLED_PASSWORD (secrets).
AUCUNE ECRITURE. Lecture seule. Sortie toujours en code 0.
    python sonde_acled.py
"""

import os
import re
import sys
from datetime import date, timedelta

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

TOKEN_URL = "https://acleddata.com/oauth/token"
DATA_URL = "https://acleddata.com/api/acled/read"
TIMEOUT = 60
FENETRE_JOURS = 90
EMAIL = os.environ.get("ACLED_EMAIL", "").strip()
MDP = os.environ.get("ACLED_PASSWORD", "").strip()

# Quelques pays a risque (noms ANGLAIS, comme ACLED) pour l'echantillon.
PAYS_TEST = "Ukraine"
PAYS_MULTI = ["Mali", "Niger"]

RESULTATS = []
# Schema d'autorisation annonce par le serveur (« Bearer » en principe).
# Conserve pour que le diagnostic du 403 puisse l'essayer tel quel plutot que
# de supposer.
TYPE_ANNONCE = {"valeur": ""}


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OK" if ok else "a creuser", detail))


def _plat(t, n=None):
    s = re.sub(r"\s+", " ", str(t if t is not None else "")).strip()
    return s[:n] if n else s


def obtenir_token(session):
    data = {
        "username": EMAIL, "password": MDP,
        "grant_type": "password", "client_id": "acled", "scope": "authenticated",
    }
    try:
        r = session.post(TOKEN_URL, data=data,
                         headers={"Content-Type": "application/x-www-form-urlencoded"},
                         timeout=TIMEOUT)
    except Exception as e:
        print("    exception reseau (token) : {}".format(_plat(e, 80)))
        return None
    print("    statut token HTTP {}".format(r.status_code))
    if r.status_code != 200:
        print("    corps (apercu) : {}".format(_plat(r.text, 200)))
        return None
    try:
        j = r.json()
    except Exception:
        print("    reponse token NON JSON : {}".format(_plat(r.text, 150)))
        return None
    tok = j.get("access_token")
    TYPE_ANNONCE["valeur"] = str(j.get("token_type") or "")
    print("    token_type={} | expires_in={} | access_token={}".format(
        j.get("token_type"), j.get("expires_in"),
        "recu ({} car.)".format(len(tok)) if tok else "ABSENT"))
    return tok


def interroger(session, token, schema="Bearer", **filtres):
    params = {"_format": "json"}
    params.update(filtres)
    try:
        r = session.get(DATA_URL, params=params,
                        headers={"Authorization": schema + " " + token},
                        timeout=TIMEOUT)
    except Exception as e:
        print("    exception reseau (data) : {}".format(_plat(e, 80)))
        return None
    print("    statut data HTTP {} | {}".format(r.status_code, _plat(r.url, 120)))
    if r.status_code >= 400:
        print("    corps (apercu) : {}".format(_plat(r.text, 200)))
        # EN-TETES DE REPONSE : c'est souvent la que l'API dit POURQUOI elle
        # refuse (WWW-Authenticate porte le scope manquant, X-* le quota).
        interessants = {k: v for k, v in r.headers.items()
                        if k.lower().startswith(("www-", "x-", "retry-"))}
        if interessants:
            print("    en-tetes : {}".format(_plat(interessants, 200)))
        return None
    try:
        return r.json()
    except Exception:
        print("    reponse data NON JSON : {}".format(_plat(r.text, 150)))
        return None


def diagnostiquer_403(session, token, token_type):
    """POURQUOI le 403 ? Fonction de DISCRIMINATION, pas de contournement.

    Un « Access denied » avec un token VALIDE ne veut pas dire la meme chose
    selon ce qui le declenche. Sans distinguer, on ne sait pas s'il faut
    corriger le code, la requete, ou le compte -- et on part reecrire du code
    qui n'a rien a se reprocher.

    Quatre hypotheses, de la moins couteuse a corriger a la plus :
      1. le SCHEMA d'autorisation n'est pas « Bearer » (le serveur a annonce
         un `token_type` : on l'essaie tel quel) ;
      2. ce sont les FILTRES qui sont refuses (requete minimale, sans pays ni
         dates : si elle passe, le probleme est dans les parametres) ;
      3. le compte n'a pas de DROIT DE LECTURE (tout est refuse, y compris la
         requete minimale) -- c'est alors une demarche a faire sur le site,
         pas une ligne de code a changer ;
      4. l'endpoint a bouge.

    Aucune ecriture, aucune tentative de contournement : on etablit un fait."""
    _titre("B-bis. DIAGNOSTIC DU REFUS (403)")
    constats = []

    # 1. Le serveur a annonce un token_type : le respecter plutot que
    #    supposer « Bearer ».
    if token_type and token_type.lower() != "bearer":
        print("  le serveur annonce token_type={!r}, on l'essaie".format(token_type))
        if interroger(session, token, schema=token_type, limit=1) is not None:
            constats.append("le schema d'autorisation n'est pas Bearer mais "
                            "{!r}".format(token_type))
            _verdict("403", True, "schema d'autorisation a corriger")
            return constats

    # 2. Requete MINIMALE : aucun filtre, un seul enregistrement.
    print("  requete minimale (aucun filtre, limit=1)")
    minimal = interroger(session, token, limit=1)
    if minimal is not None:
        constats.append("la requete MINIMALE passe : ce sont les filtres "
                        "(country / event_date) qui sont refuses")
        _verdict("403", True, "filtres a revoir, compte OK")
        return constats

    # 3. Tout est refuse, y compris sans filtre : c'est le COMPTE.
    constats.append(
        "meme sans aucun filtre, l'API refuse : le compte est authentifie "
        "mais n'a PAS de droit de lecture sur les donnees")
    constats.append(
        "ce n'est pas un probleme de code. Sur acleddata.com, verifie que le "
        "compte a bien une cle d'acces ACTIVE et que les conditions "
        "d'utilisation ont ete acceptees ; l'acces aux donnees est accorde "
        "separement de la creation du compte")
    _verdict("403", False, "compte sans droit de lecture (demarche cote ACLED)")
    return constats


LOGIN_URL = "https://acleddata.com/user/login?_format=json"


def sonde_cookie(session_neuve):
    """Voie d'authentification n°2 : session par COOKIE (doc ACLED).

    POURQUOI CE SECOND TEST
    -----------------------
    Le 403 sur toutes les requetes OAuth, meme sans filtre, laisse DEUX
    explications possibles, et elles n'appellent pas la meme action :

      a. le compte n'a pas de droit de lecture  -> demarche cote ACLED ;
      b. le jeton OAuth n'ouvre pas ce droit    -> on change de voie.

    L'authentification par cookie utilise les MEMES identifiants par une voie
    entierement differente (POST /user/login, puis session). Elle tranche donc
    entre les deux :
      - cookie refuse aussi  -> c'est le COMPTE, aucun code n'y changera rien ;
      - cookie accepte       -> c'est la voie OAuth, et on a deja la solution
                                de rechange sous la main.

    C'est aussi la voie que la doc ACLED recommande pour un acces « simple ».
    Si elle marche, le collecteur peut l'utiliser : une session vaut un jeton.

    Lecture seule, comme le reste."""
    _titre("D. VOIE DE SECOURS : authentification par COOKIE")
    try:
        r = session_neuve.post(LOGIN_URL, json={"name": EMAIL, "pass": MDP},
                               timeout=TIMEOUT)
    except Exception as e:
        print("    exception reseau (login) : {}".format(_plat(e, 80)))
        _verdict("cookie", False, "login injoignable")
        return None
    print("    statut login HTTP {}".format(r.status_code))
    if r.status_code != 200:
        print("    corps (apercu) : {}".format(_plat(r.text, 200)))
        _verdict("cookie", False,
                 "login refuse : les identifiants eux-memes sont en cause")
        return None
    try:
        uid = (r.json().get("current_user") or {}).get("uid")
        print("    session ouverte pour uid={}".format(uid))
    except Exception:
        print("    reponse login non JSON")

    try:
        d = session_neuve.get(DATA_URL, params={"_format": "json", "limit": 1},
                              timeout=TIMEOUT)
    except Exception as e:
        print("    exception reseau (data cookie) : {}".format(_plat(e, 80)))
        _verdict("cookie", False, "data injoignable")
        return None
    print("    statut data HTTP {} (via cookie)".format(d.status_code))
    if d.status_code >= 400:
        print("    corps (apercu) : {}".format(_plat(d.text, 200)))
        _verdict("cookie", False,
                 "refuse AUSSI par cookie : le compte n'a pas le droit de lire")
        return False
    _verdict("cookie", True,
             "la voie COOKIE fonctionne : le probleme est la voie OAuth")
    return True


def _fenetre():
    fin = date.today()
    debut = fin - timedelta(days=FENETRE_JOURS)
    return "{}|{}".format(debut.isoformat(), fin.isoformat())


def _evenements(charge):
    if isinstance(charge, dict):
        d = charge.get("data")
        if isinstance(d, list):
            return d, charge.get("status"), charge.get("count")
    if isinstance(charge, list):
        return charge, None, len(charge)
    return [], None, None


def sonde_a(session):
    _titre("A. OAUTH : obtention d'un access_token")
    if not EMAIL or not MDP:
        print("    ACLED_EMAIL / ACLED_PASSWORD absents de l'env.")
        _verdict("oauth", False, "identifiants manquants (secrets a definir)")
        return None
    token = obtenir_token(session)
    _verdict("oauth", bool(token),
             "token obtenu" if token else "echec OAuth (verifier email/mdp/compte)")
    return token


def sonde_b(session, token):
    _titre("B. DATA + SCHEMA : {} sur {} j".format(PAYS_TEST, FENETRE_JOURS))
    charge = interroger(session, token,
                        country=PAYS_TEST,
                        event_date=_fenetre(), event_date_where="BETWEEN",
                        fields="event_date|event_type|sub_event_type|country|iso|latitude|longitude|fatalities",
                        limit=50)
    evs, statut, count = _evenements(charge)
    print("    status={} | count={} | evenements dans le lot={}".format(statut, count, len(evs)))
    if not evs:
        _verdict("data", False, "aucun evenement (ou schema inattendu)")
        return None
    e0 = evs[0]
    print("\n    champs d'un evenement :")
    print("    {}".format(sorted(e0.keys()) if isinstance(e0, dict) else type(e0)))
    print("\n    3 evenements (date | type | pays | morts) :")
    for e in evs[:3]:
        print("      {} | {} | {} | morts={}".format(
            _plat(e.get("event_date"), 12), _plat(e.get("event_type"), 22),
            _plat(e.get("country"), 16), e.get("fatalities")))
    a_cles = all(k in e0 for k in ("country", "event_date", "fatalities", "event_type"))
    _verdict("data", a_cles,
             "champs cle presents (country/event_date/fatalities/event_type)" if a_cles
             else "champs cle manquants -- verifier le schema")
    return evs


def sonde_c(session, token):
    _titre("C. AGREGATION (indice) + pattern MULTI-PAYS")
    # Multi-pays via OR, fields minimaux : ce que fera le collecteur (batch perimetre).
    or_pays = ":OR:country=".join(PAYS_MULTI)
    charge = interroger(session, token,
                        country=or_pays,
                        event_date=_fenetre(), event_date_where="BETWEEN",
                        fields="country|fatalities|event_date",
                        limit=2000)
    evs, _statut, count = _evenements(charge)
    print("    multi-pays ({}) : {} evenements".format(" + ".join(PAYS_MULTI), len(evs)))
    if not evs:
        _verdict("agregation", False, "requete multi-pays vide")
        return
    # Indice par pays : nb evenements + somme fatalities (ce que lira le scoring)
    par_pays = {}
    for e in evs:
        p = e.get("country") or "?"
        try:
            morts = int(float(e.get("fatalities") or 0))
        except (ValueError, TypeError):
            morts = 0
        agg = par_pays.setdefault(p, {"evts": 0, "morts": 0})
        agg["evts"] += 1
        agg["morts"] += morts
    print("    indice brut par pays (fenetre {} j) :".format(FENETRE_JOURS))
    for p, agg in sorted(par_pays.items(), key=lambda x: -x[1]["morts"]):
        print("      {:14} : {:4} evenements | {:5} morts".format(p, agg["evts"], agg["morts"]))
    multi_ok = len(par_pays) >= 1
    _verdict("agregation", multi_ok,
             "indice calculable par pays, pattern OR fonctionnel" if multi_ok
             else "agregation impossible")


def main():
    print("SONDE ACLED -- lecture seule, aucune ecriture.")
    session = requests.Session()
    session.headers.update({"User-Agent": "radar-amarante-sonde/1.0"})
    token = sonde_a(session)
    diagnostic = []
    if token:
        sonde_b(session, token)
        sonde_c(session, token)
        # Si la lecture des donnees a echoue alors que le token est valide,
        # on ne s'arrete pas a « a creuser » : on cherche POURQUOI. Une sonde
        # qui constate sans discriminer laisse repartir vers du code qui n'a
        # rien a se reprocher.
        if any(nom in ("data", "agregation") and not ok
               for nom, ok, _ in RESULTATS):
            diagnostic = diagnostiquer_403(session, token,
                                           TYPE_ANNONCE["valeur"])
            # Session NEUVE : la precedente porte l'en-tete Authorization du
            # test OAuth, et on veut une voie vraiment independante.
            cookie = sonde_cookie(requests.Session())
            if cookie is True:
                diagnostic = [
                    "la voie COOKIE fonctionne avec les MEMES identifiants : "
                    "le compte a bien le droit de lire, c'est la voie OAuth "
                    "qui est refusee",
                    "le collecteur peut donc utiliser la session par cookie "
                    "(POST /user/login puis GET), c'est la voie que la doc "
                    "ACLED recommande pour un acces simple"]
            elif cookie is False:
                diagnostic.append(
                    "confirme par la voie COOKIE, independante d'OAuth : le "
                    "refus ne vient PAS du mode d'authentification")

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  {:12} {:12} {}".format(nom, "OK" if ok else "a creuser", detail))

    if diagnostic:
        _titre("CE QUE CE REFUS SIGNIFIE")
        for c in diagnostic:
            print("  - {}".format(c))

    print("\nSUITE : si OAuth + data + agregation sont verts, j'ecris")
    print("`acled_conflit.py` -- collecteur decorrele : OAuth (token cache +")
    print("refresh), batch des pays du perimetre (OR) sur {} j, indice par pays".format(FENETRE_JOURS))
    print("(evenements + fatalities, log-normalise, plafonne), ecrit dans une")
    print("table dediee. Puis integration au scoring : BOOST SEULEMENT sur")
    print("MULTIPLICATEUR_ZONE (ta grille = plancher). Test en paire.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Sonde interrompue : {}".format(e))
    sys.exit(0)
