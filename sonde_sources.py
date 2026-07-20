# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE v3 (jetable) : obtenir les titulaires ONU autrement.
=============================================================================

CONTEXTE
--------
L'endpoint public des attributions UNGM repond mais renvoie le NOM DE TYPE .NET
de sa liste au lieu des donnees :
    System.Collections.Generic.List`1[UNGM.Models.ViewModels.ContractAward.
    ContractAwardGeneralInfoModel]
Quatorze formes de requete ont ete essayees en vain. Avant d'abandonner, trois
pistes restaient inexplorees. Cette sonde les traite en un seul run.

  A. LE JAVASCRIPT EXTERNE. On avait constate que l'appel de recherche n'est
     pas dans le HTML... sans jamais aller telecharger les fichiers .js de la
     page pour l'y chercher. C'est la piste la plus directe.

  B. LES EN-TETES. Un controleur ASP.NET peut changer de serialisation selon
     Accept ou X-Requested-With. On fait varier ces en-tetes sur l'endpoint.

  C. IATI (International Aid Transparency Initiative). PNUD, UNICEF, PAM,
     UNOPS, HCR y publient leurs transactions, avec le detail du fournisseur
     et du beneficiaire, via une API publique et standardisee. Si cela marche,
     UNGM devient inutile pour les titulaires : on aurait du JSON propre,
     requetable par pays, plutot qu'un portail a gratter.

Aucune ecriture. Sortie toujours en code 0. Supprimable apres usage.
LANCEMENT : workflow "Sonde sources" (declenchement manuel).
"""

import json
import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible : pip install requests")
    sys.exit(0)


TIMEOUT = 45
NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGE_AWARDS = "https://www.ungm.org/Public/ContractAward"
ENDPOINT_AWARDS = "https://www.ungm.org/Public/ContractAward/Search"
RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("PISTE OUVERTE" if ok else "impasse", detail))


def _apercu(t, n=280):
    return " ".join(str(t or "").split())[:n]


# ===========================================================================
# A -- Chercher l'appel dans les fichiers JavaScript EXTERNES
# ===========================================================================

def sonde_a_javascript(s):
    _titre("A -- L'appel de recherche est-il dans un fichier JS externe ?")
    try:
        rep = s.get(PAGE_AWARDS, timeout=TIMEOUT)
        html = rep.text or ""
        print("Page : HTTP {} ({} octets)".format(rep.status_code, len(rep.content)))
    except Exception as e:
        _verdict("A javascript", False, "page illisible : {}".format(e))
        return

    srcs = re.findall(r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']', html, re.I)
    # On resout les URL relatives et on ecarte les librairies tierces, qui ne
    # contiennent evidemment pas le code metier d'UNGM.
    tiers = ("jquery", "bootstrap", "modernizr", "popper", "moment",
             "googletagmanager", "google-analytics", "recaptcha", "polyfill")
    urls = []
    for src in srcs:
        if any(t in src.lower() for t in tiers):
            continue
        if src.startswith("//"):
            u = "https:" + src
        elif src.startswith("http"):
            u = src
        else:
            u = "https://www.ungm.org" + ("" if src.startswith("/") else "/") + src
        if "ungm.org" in u and u not in urls:
            urls.append(u)
    print("{} fichier(s) JS propres a UNGM a inspecter.".format(len(urls)))

    motifs = [
        (r'url\s*:\s*[\'"]([^\'"]{4,140})[\'"]', "url:"),
        (r'\$\.(?:post|get|ajax)\s*\(\s*[\'"]([^\'"]{4,140})[\'"]', "$.post/get"),
        (r'[\'"](\/Public\/[A-Za-z0-9_/]*(?:Award|Search)[A-Za-z0-9_/]*)[\'"]', "chemin"),
    ]
    trouvailles = []
    for u in urls[:25]:
        try:
            r = s.get(u, timeout=TIMEOUT)
            if r.status_code >= 400:
                continue
            code = r.text or ""
        except Exception:
            continue
        if "contractaward" not in code.lower():
            continue
        print("\n  --- {} ({} octets) contient 'ContractAward' ---".format(
            u.split("/")[-1][:60], len(r.content)))
        for motif, libelle in motifs:
            for m in re.findall(motif, code, re.I):
                if re.search(r"(?i)award|search", m):
                    trouvailles.append(m.strip())
                    print("      [{}] {}".format(libelle, m.strip()[:110]))
        # Contexte autour des appels : revele souvent les noms de champs.
        for m in list(re.finditer(r"(?i)contractaward\w*search|search\w*contractaward",
                                  code))[:4]:
            deb, fin = max(0, m.start() - 160), min(len(code), m.end() + 260)
            print("      contexte : ...{}...".format(_apercu(code[deb:fin], 300)))

    uniques = sorted(set(trouvailles))
    if uniques:
        _verdict("A javascript", True,
                 "{} chemin(s) candidat(s) trouve(s) : {}".format(
                     len(uniques), uniques[:6]))
    else:
        _verdict("A javascript", False,
                 "aucun fichier JS d'UNGM ne revele d'appel de recherche.")


# ===========================================================================
# B -- Faire varier les en-tetes sur l'endpoint qui repond
# ===========================================================================

def sonde_b_entetes(s):
    _titre("B -- La serialisation change-t-elle selon les en-tetes ?")
    charge = {"PageIndex": 0, "PageSize": 15, "Title": "", "Description": "",
              "Reference": "", "Supplier": "", "Agencies": [], "Countries": [],
              "SortField": "AwardDate", "SortAscending": False}
    variantes = [
        ("JSON + Accept json", {"Content-Type": "application/json",
                                "Accept": "application/json",
                                "X-Requested-With": "XMLHttpRequest"}),
        ("JSON sans X-Requested-With", {"Content-Type": "application/json",
                                        "Accept": "application/json"}),
        ("JSON + Accept html", {"Content-Type": "application/json",
                                "Accept": "text/html,*/*",
                                "X-Requested-With": "XMLHttpRequest"}),
        ("form + Accept json", {"Content-Type": "application/x-www-form-urlencoded",
                                "Accept": "application/json",
                                "X-Requested-With": "XMLHttpRequest"}),
    ]
    ouvert = False
    for nom, entetes in variantes:
        h = dict(entetes)
        h["User-Agent"] = NAVIGATEUR
        h["Referer"] = PAGE_AWARDS
        corps = (json.dumps(charge) if "json" in h["Content-Type"] else charge)
        try:
            r = s.post(ENDPOINT_AWARDS, data=corps, headers=h, timeout=TIMEOUT)
            txt = r.text or ""
            print("  {:30} -> HTTP {} ({} octets)".format(nom, r.status_code, len(txt)))
            print("      {}".format(_apercu(txt, 200)))
            # Une reponse differente du nom de type .NET est un progres.
            if r.status_code < 400 and txt.strip() and "System.Collections" not in txt:
                ouvert = True
                _verdict("B entetes", True,
                         "la variante {!r} change la reponse : a exploiter.".format(nom))
                return
        except Exception as e:
            print("  {:30} -> exception {}".format(nom, e))
    if not ouvert:
        _verdict("B entetes", False,
                 "toutes les variantes renvoient le meme nom de type .NET.")


# ===========================================================================
# C -- IATI : les agences ONU publient-elles leurs fournisseurs ?
# ===========================================================================

def sonde_c_iati(s):
    _titre("C -- IATI : obtenir les fournisseurs des agences ONU par API")

    # C1. API officielle. Elle exige souvent une cle d'abonnement gratuite
    #     (en-tete Ocp-Apim-Subscription-Key) : un 401/403 ici n'est donc PAS
    #     une impasse, c'est une inscription a faire.
    base = "https://api.iatistandard.org/datastore/transaction/select"
    params = {
        "q": 'transaction_recipient_country_code:ML AND '
             '(transaction_transaction_type_code:3 OR transaction_transaction_type_code:4)',
        "fl": ("iati_identifier,title_narrative,reporting_org_narrative,"
               "transaction_receiver_org_narrative,transaction_provider_org_narrative,"
               "transaction_value,transaction_value_currency,"
               "transaction_transaction_date_iso_date"),
        "rows": 5, "format": "json",
    }
    besoin_cle = False
    try:
        r = s.get(base, params=params, timeout=TIMEOUT)
        print("C1. API officielle -> HTTP {} ({} octets)".format(
            r.status_code, len(r.content)))
        if r.status_code in (401, 403):
            besoin_cle = True
            print("    {}".format(_apercu(r.text, 200)))
            print("    -> cle d'abonnement gratuite probablement requise "
                  "(portail developpeur IATI).")
        elif r.status_code < 400:
            data = r.json()
            docs = (data.get("response") or {}).get("docs") or []
            total = (data.get("response") or {}).get("numFound", "?")
            print("    {} transaction(s) au total, {} rapatriee(s).".format(
                total, len(docs)))
            nommes = _afficher_transactions(docs)
            if docs:
                print("    champs : {}".format(sorted(docs[0].keys())))
                _verdict("C IATI", True,
                         "{}/{} transactions nomment un beneficiaire "
                         "(API officielle).".format(nommes, len(docs)))
                return
    except Exception as e:
        print("C1. exception : {}".format(e))

    # C2. Miroir ouvert de Code for IATI : meme donnee, SANS cle. C'est le
    #     repli si l'API officielle demande une inscription.
    base2 = "https://datastore.codeforiati.org/api/1/access/transaction.csv"
    params2 = {"recipient-country": "ML", "limit": 5}
    try:
        r = s.get(base2, params=params2, timeout=TIMEOUT)
        print("\nC2. miroir Code for IATI -> HTTP {} ({} octets)".format(
            r.status_code, len(r.content)))
        txt = r.text or ""
        lignes = [l for l in txt.splitlines() if l.strip()][:6]
        for l in lignes:
            print("    {}".format(_apercu(l, 220)))
        if r.status_code < 400 and len(lignes) > 1:
            entete = lignes[0].lower()
            a_fournisseur = any(m in entete for m in
                                ("receiver", "provider", "participating"))
            _verdict("C IATI", True,
                     "miroir ouvert exploitable SANS cle ; colonnes "
                     "fournisseur presentes : {}".format(a_fournisseur))
            return
    except Exception as e:
        print("C2. exception : {}".format(e))

    _verdict("C IATI", False,
             "API officielle {} et miroir ouvert indisponibles.".format(
                 "necessite une cle" if besoin_cle else "en echec"))


def _afficher_transactions(docs):
    nommes = 0
    for d in docs:
        recv = d.get("transaction_receiver_org_narrative")
        org = d.get("reporting_org_narrative")
        print("      - bailleur : {} | beneficiaire : {} | {} {}".format(
            _apercu(org, 30) or "n.c.", _apercu(recv, 38) or "NON NOMME",
            d.get("transaction_value"), d.get("transaction_value_currency") or ""))
        if recv:
            nommes += 1
    return nommes


def main():
    print("SONDE v3 -- titulaires ONU : trois pistes restantes (aucune ecriture)")
    s = requests.Session()
    s.headers.update({"User-Agent": NAVIGATEUR, "Accept-Language": "en-US,en;q=0.9"})
    for f in (sonde_a_javascript, sonde_b_entetes, sonde_c_iati):
        try:
            f(s)
        except Exception as e:
            _verdict(f.__name__, False, "exception non rattrapee : {}".format(e))

    _titre("VERDICT v3")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {} -- {}".format("OUI" if ok else "NON", nom, detail))
    print("\nUne seule piste ouverte suffit : A ou B debloquerait UNGM,")
    print("C le rendrait inutile en fournissant les memes noms par API propre.")


if __name__ == "__main__":
    main()
