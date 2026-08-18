# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE DIPLOMATIE ECONOMIQUE (jetable) : Google News renvoie-t-il
des signaux exploitables de type delegation / mission economique / MEDEF ?
===============================================================================

POURQUOI
--------
signaux_prives + signaux_deploiement captent deja implantation, contrat export,
recrutement, mise en service... via Google News + Adzuna. MANQUE l'angle
"diplomatie economique" (voyage MEDEF, mission economique, delegation, visite
officielle) : un signal PRECOCE de deploiement (l'entreprise prepare, n'a pas
encore deploye). Avant de cabler un collecteur, on VERIFIE que ces requetes
donnent du volume, de la fraicheur et pas trop de bruit.

CE QUE LA SONDE MESURE
----------------------
  - VOLUME : combien d'articles par requete.
  - FRAICHEUR : combien dans la fenetre (defaut 60 j).
  - PERTINENCE : les titres sont affiches -> jugement a l'oeil (signal vs bruit).
Deux fronts :
  A. DECOUVERTE par pays a risque : "delegation / mission eco / MEDEF" + pays.
  B. PAR ENTREPRISE : quelques grands deployeurs francais (exemples de
     faisabilite, pas la vraie watchlist) x angle diplomatie.

Reutilise le mecanisme exact de bitd_signaux (URL RSS, parsing, fraicheur), mais
en autonome (sonde jetable, aucune dependance au depot).

USAGE
-----
    python sonde_delegation.py
    SONDE_DELEG_JOURS=90 python sonde_delegation.py     # elargir la fenetre
    SONDE_DELEG_DRYRUN=1 python sonde_delegation.py      # afficher les URL

AUCUNE ECRITURE. Aucun secret. Aucun LLM. Sortie toujours en code 0.
"""

import datetime
import email.utils
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:
    print("requests indisponible : pip install requests")
    sys.exit(0)


GNEWS_BASE = "https://news.google.com/rss/search"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
JOURS = int(os.environ.get("SONDE_DELEG_JOURS", "60"))
DRYRUN = os.environ.get("SONDE_DELEG_DRYRUN") == "1"
TIMEOUT = 30

# Angle "diplomatie economique" : formulations a eprouver (OR booleen GNews).
DECLENCHEURS = ('délégation OR "mission économique" OR MEDEF OR '
                '"mission patronale" OR "forum économique" OR "visite d\'État"')

# A. Decouverte par PAYS a risque (echantillon zones Amarante).
PAYS = ["Mali", "Niger", "Sénégal", "Côte d'Ivoire", "Ukraine", "RDC"]

# B. Par ENTREPRISE : exemples de grands deployeurs FR (test de faisabilite,
# PAS la watchlist reelle). On veut juste voir si l'angle renvoie qqch de cible.
ENTREPRISES = ["Vinci", "Bouygues", "Eiffage", "Thales", "TotalEnergies", "Bolloré"]

MOTIFS_BRUIT = re.compile(
    r"\b(football|match|transfert|people|série|film|météo|horoscope)\b", re.I)


def url_gnews(requete, hl="fr", gl="FR", ceid="FR:fr"):
    params = {"q": requete, "hl": hl, "gl": gl, "ceid": ceid}
    return GNEWS_BASE + "?" + urllib.parse.urlencode(params)


def parser_rss(xml_texte):
    out = []
    try:
        racine = ET.fromstring(xml_texte)
    except ET.ParseError:
        return out
    for item in racine.iter("item"):
        titre = (item.findtext("title") or "").strip()
        lien = (item.findtext("link") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        if titre and lien:
            out.append({"titre": titre, "lien": lien, "date": date})
    return out


def frais(article, jours=JOURS):
    brut = article.get("date", "")
    if not brut:
        return True
    try:
        dt = email.utils.parsedate_to_datetime(brut)
        if dt is None:
            return True
        ref = datetime.datetime.now(dt.tzinfo)
        return (ref - dt).days <= jours
    except Exception:
        return True


def interroger(requete):
    url = url_gnews(requete)
    if DRYRUN:
        print("      [DRYRUN]", url)
        return None
    try:
        rep = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    except requests.exceptions.RequestException as e:
        print("      (reseau) echec :", type(e).__name__)
        return None
    if rep.status_code == 429:
        print("      (429) quota Google News atteint -- pause conseillee.")
        return None
    if rep.status_code != 200:
        print("      (HTTP {})".format(rep.status_code))
        return None
    return parser_rss(rep.text)


def evaluer(libelle, requete, total, frais_ct, bruit_ct):
    arts = interroger(requete)
    if arts is None:
        return total, frais_ct, bruit_ct
    fr = [a for a in arts if frais(a)]
    br = [a for a in fr if MOTIFS_BRUIT.search(a["titre"])]
    total += len(arts)
    frais_ct += len(fr)
    bruit_ct += len(br)
    print("  {:<28} {:>2} articles | {:>2} frais (<{}j) | {} bruit".format(
        libelle[:28], len(arts), len(fr), JOURS, len(br)))
    for a in fr[:3]:
        print("       - {}".format(a["titre"][:88]))
    return total, frais_ct, bruit_ct


def main():
    print("SONDE DIPLOMATIE ECONOMIQUE -- Google News RSS")
    print("Fenetre fraicheur :", JOURS, "j | Dry-run :", DRYRUN)
    print("Angle :", DECLENCHEURS)
    total = frais_ct = bruit_ct = 0

    print("\n" + "=" * 74)
    print("A. DECOUVERTE PAR PAYS A RISQUE")
    print("=" * 74)
    for p in PAYS:
        req = '({}) "{}"'.format(DECLENCHEURS, p)
        total, frais_ct, bruit_ct = evaluer(p, req, total, frais_ct, bruit_ct)

    print("\n" + "=" * 74)
    print("B. PAR ENTREPRISE (exemples de faisabilite)")
    print("=" * 74)
    for e in ENTREPRISES:
        req = '"{}" ({})'.format(e, DECLENCHEURS)
        total, frais_ct, bruit_ct = evaluer(e, req, total, frais_ct, bruit_ct)

    print("\n" + "=" * 74)
    print("BILAN")
    print("=" * 74)
    if DRYRUN:
        print("  (dry-run : aucune requete envoyee)")
        sys.exit(0)
    print("  Articles totaux      :", total)
    print("  Dont frais (<{}j)     : {}".format(JOURS, frais_ct))
    print("  Dont bruit evident   :", bruit_ct)
    print()
    print("  LECTURE :")
    if frais_ct == 0:
        print("  - Volume frais NUL : l'angle diplomatie eco ne remonte rien de")
        print("    recent sur cet echantillon. Ne PAS cabler ; reessayer avec une")
        print("    fenetre plus large ou d'autres formulations avant de conclure.")
    elif bruit_ct > frais_ct * 0.5:
        print("  - Beaucoup de bruit : angle exploitable mais pre-filtre + LLM")
        print("    indispensables. Cabler avec prudence (budget plafonne).")
    else:
        print("  - Volume frais correct et bruit maitrisable : angle a cabler")
        print("    (nouveau type_activite 'delegation_mission' + requetes dediees).")
    print("  Juger la PERTINENCE des titres ci-dessus : parlent-ils vraiment de")
    print("  deploiement/mission a l'etranger, ou de politique interne ?")
    sys.exit(0)


if __name__ == "__main__":
    main()
