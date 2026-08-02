# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Digest hebdomadaire push (e-mail).
====================================================

POURQUOI CE MODULE
------------------
Le dashboard est "pull" : il faut aller le consulter. Ce module ajoute un
"push" : apres chaque run, il envoie par e-mail la liste des NOUVELLES
opportunites "a contacter", pour que le radar te sollicite au lieu d'attendre
que tu l'ouvres.

COMMENT (option B validee)
--------------------------
Ce module NE fait AUCUN envoi d'e-mail lui-meme (pas de SMTP, pas de secret
mail dans le depot). Il POST la liste courante des leads "a contacter" vers le
MEME webapp Google Apps Script que le bouton "Je contacte" (SUIVI_WEBAPP_URL).
C'est le script Google, cote toi, qui :
  - deduplique (onglet 'digest_envoyes' : n'e-maile que les leads jamais
    envoyes),
  - envoie l'e-mail HTML a ton adresse (codee en dur cote Apps Script).
Ainsi le "nouveau depuis la derniere fois" et le destinataire vivent chez toi,
pas dans le depot, et l'ordre de commit de radar_etat.json n'entre pas en jeu.

ISOLATION : best-effort, exactement comme les collecteurs. Si le webapp n'est
pas configure, si le Sheet est illisible ou si le POST echoue, le module le
signale et sort proprement (code 0) : il ne fait jamais echouer le run.

REUTILISATION : lit le Sheet et construit les leads via radar_dashboard
(lire_onglets + construire_leads), donc EXACTEMENT la meme vue que la page.
La session HTTP resiliente vient du coeur ted_complet_v14.

Interrupteur : RADAR_DIGEST=0 desactive l'envoi.
ENV attendues : SUIVI_WEBAPP_URL, SUIVI_TOKEN (secrets, comme le bouton),
                TED_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE.

LANCEMENT :  python radar_digest.py
"""

import json
import os
import sys
from datetime import date


ACTIVER = os.environ.get("RADAR_DIGEST", "1") != "0"

# Plafond de leads envoyes dans un POST (protege la taille du corps HTTP et le
# temps d'execution Apps Script). Le tri par score garde les plus importants.
MAX_LEADS_DIGEST = int(os.environ.get("RADAR_DIGEST_MAX", "200"))

# Statuts de suivi (CRM) qui signifient "deja pris en charge" : on ne remet pas
# ces leads dans un digest de NOUVELLES choses a contacter. 'nouveau' et vide
# passent. Compare en minuscules, avec et sans accent (saisie manuelle).
_STATUTS_ACTIFS = {
    "contacte", "contacté", "gagne", "gagné", "perdu", "relance", "relancé",
    "en cours", "en_cours", "ignore", "ignoré",
}


def _sans_accent(s):
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def lead_id(lead):
    """Identifiant stable d'un lead, MIROIR de leadId() du dashboard JS :
    publication_number, sinon lien, sinon composite src|pays|agence|titre.
    Sert de cle de deduplication cote Apps Script."""
    pub = str(lead.get("pub") or "").strip()
    if pub:
        return pub
    lien = str(lead.get("lien") or "").strip()
    if lien:
        return lien
    return "|".join([str(lead.get("src") or ""), str(lead.get("pays") or ""),
                     str(lead.get("agence") or ""), str(lead.get("titre") or "")])


def _statut_actif(lead):
    st = _sans_accent(lead.get("statut"))
    st_brut = str(lead.get("statut") or "").strip().lower()
    return st in {_sans_accent(x) for x in _STATUTS_ACTIFS} or st_brut in _STATUTS_ACTIFS


def construire_payload(leads, genere=None, max_leads=MAX_LEADS_DIGEST):
    """Fonction PURE (testable, sans reseau). Selectionne les leads a pousser :
    action == 'contacter', hors ceux deja pris en charge dans le CRM. Trie par
    score decroissant, plafonne, et projette un dict compact par lead.

    Renvoie un dict pret a serialiser en JSON pour le webapp Apps Script."""
    retenus = []
    for l in leads or []:
        if str(l.get("action") or "").lower() != "contacter":
            continue
        if _statut_actif(l):
            continue
        retenus.append(l)
    retenus.sort(key=lambda l: l.get("final", 0) or 0, reverse=True)
    retenus = retenus[:max(0, max_leads)]

    items = []
    for l in retenus:
        nom = l.get("nom")
        email = l.get("email")
        items.append({
            "id": lead_id(l),
            "src": l.get("src", ""),
            "pays": l.get("pays", ""),
            "zone": l.get("zone", ""),
            "titre": l.get("titre", ""),
            "agence": l.get("agence", ""),
            "score": l.get("final", 0),
            "surete": l.get("surete", 0),
            "comm": l.get("comm", 0),
            "fenetre": l.get("win", ""),
            "lien": l.get("lien", ""),
            "contact": "" if (nom in (None, "", "n.c.")) else nom,
            "email": "" if (email in (None, "", "n.c.")) else email,
            "date_det": l.get("date_det", ""),
        })
    return {
        "type": "digest",
        "genere": genere or date.today().strftime("%d/%m/%Y"),
        "leads": items,
    }


def envoyer(url, token, payload, session=None):
    """POST best-effort du payload vers le webapp. Renvoie True si le POST a
    abouti (code < 400), False sinon. N'exception JAMAIS : le digest ne doit
    pas faire echouer le run. Le webapp repond en 'no-cors' style ; on ne lit
    pas la reponse au-dela du code HTTP."""
    corps = dict(payload)
    corps["token"] = token
    try:
        import ted_complet_v14 as ted
        session = session or ted.session_robuste()
    except Exception:
        import requests
        session = session or requests.Session()
    try:
        rep = session.post(
            url,
            data=json.dumps(corps).encode("utf-8"),
            headers={"Content-Type": "text/plain;charset=utf-8"},
            timeout=30,
        )
        if rep.status_code >= 400:
            print("(digest) le webapp a repondu HTTP {} : {}".format(
                rep.status_code, (rep.text or "")[:200]))
            return False
        print("(digest) POST envoye au webapp (HTTP {}).".format(rep.status_code))
        return True
    except Exception as e:
        print("(digest) envoi impossible ({}). Le run continue normalement.".format(e))
        return False


def _config_suivi():
    """URL + token du webapp, priorite aux variables d'environnement (secrets
    GitHub Actions), repli sur suivi_config.py pour un usage local. Meme source
    que le bouton 'Je contacte' du dashboard."""
    url = os.environ.get("SUIVI_WEBAPP_URL", "") or ""
    token = os.environ.get("SUIVI_TOKEN", "") or ""
    if not (url and token):
        try:
            import suivi_config
            url = url or (getattr(suivi_config, "SUIVI_WEBAPP_URL", "") or "")
            token = token or (getattr(suivi_config, "SUIVI_TOKEN", "") or "")
        except Exception:
            pass
    return url, token


def _charger_leads(sheet_id, fichier_cs):
    """Lit le Sheet et reconstruit EXACTEMENT les memes leads que le dashboard,
    en reutilisant sa plomberie (aucune duplication de logique).

    Passe par `charger_leads`, le point d'entree UNIQUE du chemin Sheet : c'est
    lui, et lui seul, qui deballe `lire_onglets` et cable `construire_leads`. Le
    digest ne recable donc plus rien a la main -- c'est ce recablage, laisse en
    arriere quand `lire_onglets` a grossi (10 -> 15 valeurs), qui cassait le
    digest a chaque run ("too many values to unpack", avale en "lecture du Sheet
    impossible", aucun e-mail envoye)."""
    import radar_dashboard as dash
    leads, _onglets = dash.charger_leads(sheet_id, fichier_cs)
    return leads


def main():
    if not ACTIVER:
        print("(info) Digest desactive (RADAR_DIGEST=0).")
        return
    url, token = _config_suivi()
    if not (url and token):
        print("(info) Digest inactif : SUIVI_WEBAPP_URL / SUIVI_TOKEN absents. "
              "Definis-les (secrets) pour activer l'e-mail hebdo.")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not (sheet_id and fichier_cs):
        print("(info) Digest inactif : TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents.")
        return

    try:
        leads = _charger_leads(sheet_id, fichier_cs)
    except Exception as e:
        print("(digest) lecture du Sheet impossible ({}). Digest ignore ce run.".format(e))
        return

    payload = construire_payload(leads)
    n = len(payload["leads"])
    if n == 0:
        print("(digest) aucun lead 'a contacter' a pousser ce run. Rien a envoyer.")
        return
    print("(digest) {} lead(s) 'a contacter' envoye(s) au webapp "
          "(il n'e-mailera que les nouveaux).".format(n))
    envoyer(url, token, payload)


if __name__ == "__main__":
    main()
