"""Boucle de retroaction (item 7) : apprend des issues gagne / perdu que tu
saisis a la main (colonne statut) pour NUANCER le scoring, secteur par secteur
et zone par zone.

Prudent par construction (le danger, avec peu de donnees, c'est le
surajustement) :
  - lissage bayesien (Beta-Binomial) : un a priori neutre tire les taux vers la
    moyenne globale, ce qui evite les 0% / 100% sur 2-3 observations ;
  - multiplicateur BORNE (par defaut 0.85 a 1.15) : la retroaction penche le
    score, elle ne le renverse jamais ;
  - seuil minimal (N_MIN) : tant qu'une categorie n'a pas assez d'issues, son
    multiplicateur reste neutre (1.0).

Ce module est PUR (aucun acces reseau / Sheet) : il recoit des "outcomes" deja
lus (des dicts {secteur, zone, statut}) et renvoie des multiplicateurs ou un
tableau de conversion. La lecture des donnees est faite par l'appelant
(signaux_prives pour le pipeline, radar_dashboard pour l'affichage).
"""

import os


def _f(env, defaut):
    try:
        return float(os.environ.get(env, defaut))
    except (TypeError, ValueError):
        return float(defaut)


N_MIN = int(_f("RADAR_RETRO_NMIN", 8))     # issues mini par categorie pour agir
MULT_MIN = _f("RADAR_RETRO_MIN", 0.85)     # borne basse du multiplicateur
MULT_MAX = _f("RADAR_RETRO_MAX", 1.15)     # borne haute
GAIN = _f("RADAR_RETRO_GAIN", 1.0)         # sensibilite (ecart de taux -> mult)
FORCE_PRIOR = _f("RADAR_RETRO_PRIOR", 8.0)  # k0 : poids de l'a priori (lissage)

DIMENSIONS = ("secteur", "zone")


def _issue(statut):
    """gagne / perdu / None (nouveau, contacte, relance ne sont pas des issues)."""
    s = (statut or "").strip().lower()
    if "gagn" in s:
        return "gagne"
    if "perd" in s:
        return "perdu"
    return None


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def agreger(outcomes):
    """outcomes : iterable de dicts {'secteur','zone','statut'}.
    Renvoie {'secteur':{val:(gagne,perdu)}, 'zone':{...}, 'base':(G,P)}."""
    dims = {d: {} for d in DIMENSIONS}
    g_tot = p_tot = 0
    for o in outcomes:
        issue = _issue(o.get("statut"))
        if issue is None:
            continue
        est_gagne = issue == "gagne"
        g_tot += est_gagne
        p_tot += not est_gagne
        for d in DIMENSIONS:
            val = (o.get(d) or "").strip()
            if not val:
                continue
            g, p = dims[d].get(val, (0, 0))
            dims[d][val] = (g + est_gagne, p + (not est_gagne))
    dims["base"] = (g_tot, p_tot)
    return dims


def _taux_lisse(g, p, p0):
    """Taux de succes lisse (Beta-Binomial) : tire vers p0 selon FORCE_PRIOR."""
    n = g + p
    return (g + FORCE_PRIOR * p0) / (n + FORCE_PRIOR) if (n + FORCE_PRIOR) else p0


def _mult(g, p, p0):
    """Multiplicateur d'une categorie : neutre si trop peu de donnees, sinon
    borne autour de l'ecart entre son taux lisse et le taux global."""
    if (g + p) < N_MIN:
        return 1.0
    p_hat = _taux_lisse(g, p, p0)
    return round(_clamp(1 + GAIN * (p_hat - p0), MULT_MIN, MULT_MAX), 3)


def multiplicateurs(outcomes):
    """Renvoie {'base':p0, 'n':total, 'secteur':{val:mult}, 'zone':{val:mult}}."""
    dims = agreger(outcomes)
    g_tot, p_tot = dims["base"]
    total = g_tot + p_tot
    p0 = (g_tot / total) if total else 0.0
    res = {"base": round(p0, 3), "n": total}
    for d in DIMENSIONS:
        res[d] = {val: _mult(g, p, p0) for val, (g, p) in dims[d].items()}
    return res


def mult_pour(mults, secteur, zone):
    """Multiplicateur combine (secteur x zone), lui-meme borne. 1.0 si absent."""
    if not mults:
        return 1.0
    ms = mults.get("secteur", {}).get((secteur or "").strip(), 1.0)
    mz = mults.get("zone", {}).get((zone or "").strip(), 1.0)
    return _clamp(ms * mz, MULT_MIN, MULT_MAX)


def table_conversion(outcomes):
    """Pour l'AFFICHAGE : par dimension, lignes triees (val, g, p, n, taux, actif).
    'actif' indique si la categorie a atteint N_MIN (donc si elle influe sur le
    scoring quand la retroaction est activee)."""
    dims = agreger(outcomes)
    g_tot, p_tot = dims["base"]
    total = g_tot + p_tot
    p0 = (g_tot / total) if total else 0.0
    res = {"base": round(p0, 3), "n": total, "n_min": N_MIN}
    for d in DIMENSIONS:
        lignes = []
        for val, (g, p) in dims[d].items():
            n = g + p
            lignes.append({"val": val, "g": g, "p": p, "n": n,
                           "taux": round(_taux_lisse(g, p, p0), 3),
                           "actif": n >= N_MIN})
        lignes.sort(key=lambda x: (-x["n"], -x["taux"]))
        res[d] = lignes
    return res
