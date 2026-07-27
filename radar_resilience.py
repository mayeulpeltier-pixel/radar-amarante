# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- Resilience Google Sheets (brique partagee).
=========================================================================

Cause : les incidents Google "APIError [503] service is currently unavailable"
frappent au hasard n'importe quel collecteur au moment d'OUVRIR ou d'ECRIRE le
Sheet. Constat du 27/07/2026 :
  - IDB plantait (exit 1, run rouge) faute d'encaisser l'erreur ;
  - IsDB encaissait mais PERDAIT l'ecriture (aucun retry) ;
  - aucun collecteur ne reessayait, alors qu'un 503 se dissipe en 1-2 s.

Un seul point de verite, importe par tous les collecteurs :
  - est_transitoire(e)   : l'erreur merite-t-elle un retry ? (429/500/502/503,
                           quotas, "service is currently unavailable")
  - avec_retry(op)       : backoff exponentiel ; relance si non-transitoire ou
                           apres epuisement des tentatives.
  - ouvrir_classeur(...) : authorize + open_by_key avec retry ; remplace le
                           pattern inline duplique dans chaque ouvrir_feuille*.

Volontairement sans dependance a ted_complet_v14 (brique bas niveau). gspread
et google.oauth2 ne sont importes qu'a l'appel d'ouvrir_classeur (les tests de
retry n'en ont pas besoin).
"""

import time

# Codes HTTP transitoires cote Google (a reessayer). 429 = quota/rate limit ;
# 500/502/503 = indisponibilite momentanee.
CODES_TRANSITOIRES = {429, 500, 502, 503}
TENTATIVES_DEFAUT = 4
BASE_ATTENTE = 2.0        # secondes ; backoff 2, 4, 8


def _code_http(e):
    """Extrait le code HTTP d'une exception gspread APIError si present."""
    resp = getattr(e, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    code = getattr(e, "code", None)
    return code if isinstance(code, int) else None


def est_transitoire(e):
    """Vrai si l'erreur est un hoquet reessayable (et non une vraie erreur)."""
    code = _code_http(e)
    if code in CODES_TRANSITOIRES:
        return True
    msg = str(e).lower()
    if "service is currently unavailable" in msg:
        return True
    if "rate limit" in msg or "quota exceeded" in msg or "quota exceeded" in msg:
        return True
    # Repli sur le code entre crochets du message gspread, ex "APIError: [503]".
    return any("[{}]".format(c) in msg for c in CODES_TRANSITOIRES)


def avec_retry(operation, description="operation Sheet",
               tentatives=TENTATIVES_DEFAUT, base=BASE_ATTENTE, dormir=time.sleep):
    """Execute `operation` (callable sans argument) avec backoff exponentiel sur
    erreur transitoire. Relance immediatement toute erreur non-transitoire, et
    relance la derniere erreur si toutes les tentatives echouent.
    `dormir` est injectable pour les tests (pas d'attente reelle)."""
    derniere = None
    for i in range(tentatives):
        try:
            return operation()
        except Exception as e:
            derniere = e
            if not est_transitoire(e) or i == tentatives - 1:
                raise
            attente = base * (2 ** i)
            print("  (retry) {} : erreur transitoire ({}), tentative {}/{} dans "
                  "{:.0f}s...".format(description, e, i + 2, tentatives, attente))
            dormir(attente)
    if derniere is not None:                          # securite (jamais atteint)
        raise derniere


def ouvrir_classeur(sheet_id, fichier_compte_service, portee=None):
    """authorize + open_by_key, protege par retry (corrige le 503 d'ouverture,
    la cause du crash IDB). Remplace le pattern inline duplique partout."""
    import gspread
    from google.oauth2.service_account import Credentials
    portee = portee or ["https://www.googleapis.com/auth/spreadsheets"]

    def _ouvrir():
        creds = Credentials.from_service_account_file(fichier_compte_service, scopes=portee)
        return gspread.authorize(creds).open_by_key(sheet_id)

    return avec_retry(_ouvrir, description="ouverture du classeur")
