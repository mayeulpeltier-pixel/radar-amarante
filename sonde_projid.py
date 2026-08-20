# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE PROJ_ID (jetable) : l'API BM procnotices expose-t-elle
un identifiant de projet, utilisable comme ancre du "dossier vivant" ?
===============================================================================

CONTEXTE
--------
Voie A du Lot 2 : relier amont -> avis -> attribution BM par le proj_id (fiable,
sans matching textuel cross-langue). L'amont (bm_projets) porte deja le proj_id
(publication_number = "BMP-{proj_id}"). Les avis ET attributions BM viennent du
MEME endpoint (search.worldbank.org/api/v2/procnotices). Cette sonde inspecte un
record brut de cet endpoint : contient-il un champ project_id / proj_id / id
projet, coherent avec l'amont ? Si oui, la Voie A tient et on l'expose. Sinon,
il faudra se rabattre sur project_name (textuel).

AUCUNE ECRITURE. Lancer en Actions (acces reseau worldbank). Code 0.
"""

import sys

try:
    import ted_complet_bm as bm
except Exception as e:
    print("Import ted_complet_bm impossible :", e)
    sys.exit(0)


def main():
    print("SONDE PROJ_ID -- inspection d'un record BM procnotices")
    print("Endpoint :", getattr(bm, "BM_ENDPOINT", "?"))
    try:
        bruts, retenus, total = bm.collecte_bm()
    except Exception as e:
        print("(reseau) collecte_bm a echoue :", type(e).__name__, str(e)[:100])
        sys.exit(0)
    print("Records bruts :", len(bruts), "| retenus (fenetre) :", len(retenus))
    if not bruts:
        print("Aucun record : impossible d'inspecter les champs.")
        sys.exit(0)

    rec = bruts[0]
    print("\n" + "=" * 68)
    print("CHAMPS D'UN RECORD (cles)")
    print("=" * 68)
    for k in sorted(rec.keys()):
        v = rec[k]
        apercu = str(v).replace("\n", " ")[:55]
        print("  {:<28} {}".format(k, apercu))

    print("\n" + "=" * 68)
    print("CHAMPS RESSEMBLANT A UN IDENTIFIANT DE PROJET")
    print("=" * 68)
    cands = [k for k in rec
             if "proj" in k.lower() or k.lower() in ("id", "projectid",
                                                     "project_id", "projid",
                                                     "project_num")]
    if cands:
        for k in cands:
            print("  TROUVE : {} = {}".format(k, str(rec[k])[:70]))
    else:
        print("  Aucun champ d'ID projet evident.")

    # Un proj_id BM ressemble a 'P' + 6 chiffres (ex P163401). On le cherche
    # dans toutes les valeurs, au cas ou il serait sous un nom inattendu.
    import re
    motif = re.compile(r"\bP\d{6}\b")
    print("\n  Valeurs contenant un motif 'P######' (proj_id BM typique) :")
    trouve = False
    for k, v in rec.items():
        m = motif.search(str(v))
        if m:
            print("    {} -> {}".format(k, m.group(0)))
            trouve = True
    if not trouve:
        print("    (aucune)")

    print("\n" + "=" * 68)
    print("LECTURE")
    print("=" * 68)
    print("  Un champ project_id (ou un motif P###### exploitable) -> Voie A")
    print("  tient : on l'expose dans avis + attributions, ancre = proj_id.")
    print("  Rien d'exploitable -> se rabattre sur project_name (textuel).")
    sys.exit(0)


if __name__ == "__main__":
    main()
