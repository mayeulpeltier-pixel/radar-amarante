# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE ATTRIBUTIONS PROZORRO (jetable).
=========================================================================

Pourquoi cette sonde
--------------------
Le premier run reel des AVIS Prozorro l'a montre : les avis ouverts sont du
marche public DOMESTIQUE (acheteur toujours public, titulaire probable = PME
ukrainienne, ouvriers locaux). Score plafonne, hors cible Amarante. La seule
valeur ICP plausible de Prozorro est dans les ATTRIBUTIONS, a UNE condition :
qu'un gagnant ETRANGER (donc deployant des expatries) soit distinguable d'un
gagnant ukrainien. C'est exactement la doctrine IsDB (nom ET pays du titulaire
-> le filtre local/etranger fonctionne).

LA QUESTION UNIQUE (aucune supposition, on dumpe le reel) :
  Dans awards[].suppliers[], peut-on distinguer un gagnant etranger d'un
  gagnant ukrainien, et quelle est la part d'etrangers sur un echantillon
  d'attributions ICP-pertinentes ?

Ce qu'on inspecte par fournisseur :
  - identifier.scheme (UA-EDR / UA-IPN = ukrainien ; autre = suspect etranger)
  - identifier.id     (code EDRPOU ukrainien vs identifiant etranger)
  - address.countryName (Україна vs pays etranger)
  - name / legalName, scale (sme/large), et la valeur du contrat attribue

Reutilise le scan DEJA TESTE de prozorro_radar.collecte() : memes 3 cribles
(methode, categorie works/services, valeur, CPV), donc on regarde les
attributions ICP-PERTINENTES, pas le bruit municipal. Aucune ecriture, aucun
LLM. Sortie toujours en code 0. Jetable : a supprimer une fois la decision
prise (batir la tranche attributions filtree, ou abandonner Prozorro).

REGLAGE (env, defauts poses par le workflow) :
  PROZORRO_JOURS         fenetre d'echantillon (defaut prozorro_radar : 4)
  PROZORRO_MINUTES_MAX   garde-fou temps du scan
"""

import json
import sys

try:
    import prozorro_radar as pz
except Exception as e:                               # pragma: no cover
    print("import prozorro_radar impossible : {}".format(e))
    sys.exit(0)


SCHEMES_UA = ("UA-EDR", "UA-IPN", "UA-")   # prefixes d'identifiant ukrainien
PAYS_UA = ("україна", "ukraine")


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _plat(t, n=None):
    s = " ".join(str(t or "").split())
    return s[:n] if n else s


def _infos_fournisseur(s):
    ident = s.get("identifier") or {}
    adr = s.get("address") or {}
    return {
        "nom": s.get("name") or ident.get("legalName") or "",
        "scheme": (ident.get("scheme") or ""),
        "id": (ident.get("id") or ""),
        "pays": (adr.get("countryName") or ""),
        "scale": (s.get("scale") or ""),
    }


def _est_etranger(infos):
    """True (etranger) / False (ukrainien) / None (indetermine).
    Le pays prime (plus fiable), le scheme corrobore."""
    pays = infos["pays"].strip().lower()
    scheme = infos["scheme"].upper()
    if pays in PAYS_UA:
        return False
    if scheme.startswith("UA-"):
        return False
    if pays and pays not in PAYS_UA:
        return True
    if scheme and not scheme.startswith("UA-"):
        return True
    return None


def main():
    print("SONDE ATTRIBUTIONS PROZORRO -- diagnostic, aucune ecriture, aucun LLM.")
    print("Fenetre {} j | seuil {:.0f} UAH | cribles works/services actifs.".format(
        pz.PROZORRO_JOURS, pz.SEUIL_UAH))

    # Reutilise le scan teste : on ne recupere que les attributions
    # ICP-pertinentes (crible categorie + valeur + CPV deja appliques).
    _avis, attributions, compteurs = pz.collecte()
    pz._afficher_entonnoir(compteurs)

    _titre("A -- Structure brute des gagnants (3 premiers, verite terrain)")
    montres = 0
    for detail in attributions:
        for a in (detail.get("awards") or []):
            if a.get("status") == "active" and a.get("suppliers"):
                print("\n  Marche : {}".format(_plat(detail.get("title"), 90)))
                print("  award.value : {}".format(
                    json.dumps(a.get("value") or {}, ensure_ascii=False)))
                for s in a["suppliers"]:
                    print("  supplier BRUT : {}".format(
                        json.dumps(s, ensure_ascii=False)[:500]))
                montres += 1
                break
        if montres >= 3:
            break
    if not montres:
        print("  (aucune attribution avec fournisseur dans l'echantillon)")

    _titre("B -- Nationalite des gagnants (le verdict)")
    total_fourn = 0
    par_scheme, par_pays = {}, {}
    n_ua, n_etranger, n_indetermine = 0, 0, 0
    etrangers = []

    for detail in attributions:
        for a in (detail.get("awards") or []):
            if a.get("status") != "active" or not a.get("suppliers"):
                continue
            for s in a["suppliers"]:
                infos = _infos_fournisseur(s)
                total_fourn += 1
                par_scheme[infos["scheme"] or "(vide)"] = par_scheme.get(infos["scheme"] or "(vide)", 0) + 1
                par_pays[infos["pays"] or "(vide)"] = par_pays.get(infos["pays"] or "(vide)", 0) + 1
                verdict = _est_etranger(infos)
                if verdict is True:
                    n_etranger += 1
                    etrangers.append((detail.get("title"), infos))
                elif verdict is False:
                    n_ua += 1
                else:
                    n_indetermine += 1

    print("Attributions ICP-pertinentes vues : {}".format(len(attributions)))
    print("Fournisseurs (gagnants) au total  : {}".format(total_fourn))
    print("  ukrainiens   : {}".format(n_ua))
    print("  ETRANGERS    : {}".format(n_etranger))
    print("  indetermines : {}".format(n_indetermine))
    print("\nRepartition par identifier.scheme :", dict(sorted(par_scheme.items())))
    print("Repartition par address.countryName :", dict(sorted(par_pays.items())))

    if etrangers:
        print("\n  GAGNANTS ETRANGERS reperes :")
        for titre, infos in etrangers[:15]:
            print("   - {} | pays={} scheme={} id={} | {}".format(
                infos["nom"][:40], infos["pays"], infos["scheme"],
                infos["id"], _plat(titre, 50)))

    _titre("SYNTHESE")
    detectable = (n_ua + n_etranger) > 0 and (total_fourn > 0)
    print("  [{}] Nationalite DETECTABLE : {}".format(
        "OK " if detectable else "?? ",
        "oui (pays et/ou scheme exploitables)" if detectable
        else "non : ni pays ni scheme fiables -> filtre etranger impossible"))
    if total_fourn:
        part = 100.0 * n_etranger / total_fourn
        print("  [{}] Part d'ETRANGERS : {}/{} soit {:.1f}%".format(
            "OK " if n_etranger else "?? ", n_etranger, total_fourn, part))
    print("\n  LECTURE :")
    print("   - part etrangere significative  -> batir la tranche attributions")
    print("     filtree sur gagnant etranger (seul gisement ICP de Prozorro).")
    print("   - part quasi nulle              -> Prozorro marginal pour Amarante,")
    print("     on capitalise l'apprentissage et on passe a MIGA / IFC / IDB.")
    print("\nSonde jetable : a supprimer une fois la decision prise.")
    sys.exit(0)


if __name__ == "__main__":
    main()
