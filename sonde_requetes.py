# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE DE CONCEPTION DE REQUETE (jetable, sans LLM).
===============================================================================

CE QUE LE SHADOW RUN A REVELE
------------------------------
La RDC et la Guinee ont renvoye EXACTEMENT les memes articles (Davis County,
PHC Bar, CBC, Al Jazeera). Deux pays differents, resultats identiques : le nom
du pays n'a AUCUN effet sur la requete. Les candidats produits etaient au
Royaume-Uni, aux Etats-Unis et au Pakistan -- trois pays qui n'etaient meme pas
interroges.

HYPOTHESE A TESTER
------------------
La requete actuelle vaut ~720 caracteres : un groupe de 38 declencheurs en OR,
suivi du pays entre guillemets. Google News plafonne la longueur utile d'une
requete ; le terme de pays, place EN DERNIER, serait tout simplement tronque.
S'y ajoute un second soupcon : en francais, les phrases exactes sont ecrites
SANS ACCENTS ("etude de faisabilite", "Guinee"), ce qui pourrait expliquer les
0 resultat des requetes FR.

CE QUE LA SONDE MESURE
----------------------
Cinq formes de requete, sur les memes pays, avec un indicateur de pertinence
calculable SANS LLM : la part des titres qui mentionnent reellement le pays (ou
un mot du pays). Une requete qui respecte le pays doit obtenir un taux eleve ;
la forme actuelle devrait s'effondrer.

Cout : ZERO appel LLM. Lecture seule, aucune ecriture.

USAGE
    python sonde_requetes.py
    SONDE_REQ_PAYS=TZA,COD,GIN python sonde_requetes.py
"""

import os
import re
import time
import unicodedata
from urllib.parse import unquote

import bitd_signaux as bitd
import decouverte_projets as dp
import pays_projets_reference as pref
import ted_complet_v14 as ted


PAYS = [p.strip().upper() for p in
        os.environ.get("SONDE_REQ_PAYS", "TZA,COD,GIN").split(",") if p.strip()]
MAX = int(os.environ.get("SONDE_REQ_MAX", "20"))
PAUSE = float(os.environ.get("SONDE_REQ_PAUSE", "1.0"))

# Familles courtes de declencheurs : l'alternative a un unique groupe geant.
FAMILLES = {
    "accords": '("memorandum of understanding" OR "concession agreement" OR '
               '"investment agreement" OR "host government agreement")',
    "financement": '("financing agreement" OR "funding approved" OR '
                   '"financial close" OR "final investment decision")',
    "etudes": '("feasibility study" OR "consultant selected" OR '
              '"preferred bidder" OR "master plan")',
    "travaux": '("EPC contract" OR "groundbreaking" OR "construction begins" OR '
               '"deepwater port" OR "power plant")',
}

# Mots par pays qui prouvent qu'un titre parle bien de ce pays.
INDICES_PAYS = {
    "TZA": ["tanzania", "tanzanie", "dodoma", "dar es salaam", "lindi", "mtwara"],
    "COD": ["congo", "drc", "rdc", "kinshasa", "katanga", "kolwezi", "lubumbashi"],
    "GIN": ["guinea", "guinee", "conakry", "boke", "simandou", "kamsar"],
    "MOZ": ["mozambique", "mocambique", "maputo", "cabo delgado", "afungi"],
    "NGA": ["nigeria", "lagos", "abuja", "niger delta"],
}


def _norm(t):
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def pertinence(titres, iso3):
    """Part des titres mentionnant reellement le pays. Fonction PURE.
    Indicateur grossier mais suffisant : une requete qui ignore le pays
    s'effondre a 0, une requete qui le respecte monte haut."""
    indices = [_norm(x) for x in INDICES_PAYS.get(iso3, [])]
    if not titres or not indices:
        return 0.0, 0
    n = sum(1 for t in titres if any(i in _norm(t) for i in indices))
    return round(100.0 * n / len(titres), 1), n


def formes(pays):
    """Les cinq formes de requete comparees. Retour : [(nom, requete, langue)]."""
    iso3 = pays["iso3"]
    nom_en = pays["nom"]
    nom_fr = pref.nom_pour_requete(pays, "fr")
    langue1 = (pays.get("langues") or ["en"])[0]
    out = [
        # A : la forme ACTUELLE, telle qu'en production.
        ("A actuelle (38 OR + pays en fin)",
         '{} "{}"'.format(dp.declencheurs("en"), nom_en), "en"),
        # B : pays EN TETE, meme groupe geant. Teste la troncature.
        ("B pays en tete + 38 OR",
         '"{}" {}'.format(nom_en, dp.declencheurs("en")), "en"),
        # C : pays en tete + UNE famille courte (4 termes).
        ("C pays + famille accords",
         '"{}" {}'.format(nom_en, FAMILLES["accords"]), "en"),
        ("C pays + famille financement",
         '"{}" {}'.format(nom_en, FAMILLES["financement"]), "en"),
        ("C pays + famille travaux",
         '"{}" {}'.format(nom_en, FAMILLES["travaux"]), "en"),
        # D : pays SANS guillemets + famille courte.
        ("D pays nu + famille etudes",
         '{} {}'.format(nom_en, FAMILLES["etudes"]), "en"),
    ]
    if langue1 == "fr" or "fr" in (pays.get("langues") or []):
        # E : francais, avec et sans accents, pour trancher le 0 resultat.
        out.append(("E francais actuel (sans accents)",
                    '{} "{}"'.format(dp.declencheurs("fr"), nom_fr), "fr"))
        out.append(("E francais court + accents",
                    '"{}" ("étude de faisabilité" OR "protocole d\'accord" OR '
                    '"accord de financement" OR "appel d\'offres")'.format(
                        _accentue(nom_fr)), "fr"))
    return out


def _accentue(nom):
    """Retablit les accents des noms de pays francophones les plus courants."""
    table = {"Guinee": "Guinée", "Senegal": "Sénégal",
             "Cote d'Ivoire": "Côte d'Ivoire", "RD Congo": "RD Congo",
             "Mocambique": "Moçambique"}
    return table.get(nom, nom)


def interroger(requete, langue, pays, fetch):
    hl, gl, ceid = pref.params_google_news(pays, langue)
    url = bitd.url_google_news("", requete_perso=requete, hl=hl, gl=gl, ceid=ceid)
    try:
        articles = bitd.parser_rss(fetch(url))[:MAX]
    except Exception as e:
        return None, str(e)[:60], len(unquote(url.split("q=")[1].split("&")[0]))
    return articles, "", len(unquote(url.split("q=")[1].split("&")[0]))


def main():
    session = ted.session_robuste()

    def fetch(url):
        rep = session.get(url, timeout=30)
        rep.raise_for_status()
        return rep.text

    print("#" * 78)
    print("SONDE DE CONCEPTION DE REQUETE -- aucun appel LLM, lecture seule")
    print("Question : quelle forme de requete respecte REELLEMENT le pays ?")
    print("#" * 78)

    resume = []
    for iso3 in PAYS:
        pays = pref.pays_par_iso3(iso3)
        if not pays:
            print("\n(pays inconnu : {})".format(iso3))
            continue
        print("\n" + "=" * 78)
        print("{} ({})".format(pays["nom"], iso3))
        print("=" * 78)
        for nom, requete, langue in formes(pays):
            articles, err, taille = interroger(requete, langue, pays, fetch)
            time.sleep(PAUSE)
            if articles is None:
                print("  {:<36} ERREUR {}".format(nom[:36], err))
                continue
            titres = [a.get("titre", "") for a in articles]
            taux, n = pertinence(titres, iso3)
            print("  {:<36} {:>3} art | {:>5}% pertinents ({}) | requete {} car".format(
                nom[:36], len(titres), taux, n, taille))
            for t in titres[:3]:
                marque = "OK " if any(_norm(i) in _norm(t)
                                      for i in INDICES_PAYS.get(iso3, [])) else "HS "
                print("        {}{}".format(marque, t[:66]))
            resume.append((iso3, nom, len(titres), taux))

    print("\n" + "#" * 78)
    print("SYNTHESE : taux de titres parlant reellement du pays")
    print("#" * 78)
    print("{:<6} {:<38} {:>5} {:>8}".format("Pays", "Forme", "Art", "Pertin."))
    for iso3, nom, n, taux in resume:
        print("{:<6} {:<38} {:>5} {:>7}%".format(iso3, nom[:38], n, taux))
    print("\nLECTURE : la forme A est celle en production. Si son taux est bas et")
    print("qu'une forme C/D monte nettement, la correction consiste a decouper le")
    print("groupe de declencheurs et a placer le pays EN TETE. Si toutes les")
    print("formes echouent, Google News ne convient pas et il faut des sources")
    print("directes (ministeres, DFI).")


if __name__ == "__main__":
    main()
