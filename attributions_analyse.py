# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- ANALYSE DES ATTRIBUTIONS (titulaires de marches).
===================================================================

CE QUE CE MODULE CORRIGE (23/07/2026)
-------------------------------------
Les quatre collecteurs d'attributions (TED, Banque Mondiale, UNGM, IsDB, IDB)
ecrivent 13 colonnes brutes dans l'onglet partage `attributions_radar`. AUCUN
n'appelle le modele. Consequence, visible dans le dashboard :

  - `attribution_vers_lead()` renvoie `"final": score, "surete": score,
    "comm": score` -- LE MEME CHIFFRE TROIS FOIS. Ce n'est pas une analyse,
    c'est un calcul deterministe zone + secteur + valeur ;
  - la justification est un GABARIT FIXE : « Titulaire d'un marche gagne (X).
    Prospect a demarcher. » La meme phrase pour les centaines de lignes ;
  - `nom`, `email`, `tel` valent "n.c." sauf si l'enrichissement francais
    matche, ce qui n'arrive presque jamais sur un titulaire turc ou chinois.

Autrement dit, l'onglet Titulaires est un ANNUAIRE. Ce module en fait une
liste de prospection : qui est cette entreprise, d'ou vient-elle, quel
personnel va-t-elle deployer, ou, combien de temps, et a qui parler.

POURQUOI UNE TABLE SEPAREE PLUTOT QUE 5 COLONNES DE PLUS
---------------------------------------------------------
C'est LA decision de conception, et elle merite d'etre justifiee.

L'onglet `attributions_radar` est PARTAGE par quatre collecteurs, lu
POSITIONNELLEMENT, et suivi de deux colonnes de saisie humaine
(`statut_prospection`, `date_detection`). Y inserer des colonnes desalignerait
toutes les lignes deja ecrites : c'est exactement l'incident `bm_radar`, en
pire, puisqu'il toucherait la zone humaine.

Et ce n'est pas theorique : `ouvrir_feuille` ne reecrit l'en-tete QUE si
`date_detection` est absent de la ligne 1. Ajouter des colonnes au schema
laisserait donc l'en-tete inchange pendant que les nouvelles lignes
adopteraient la nouvelle disposition. Desalignement SILENCIEUX.

La table separee, jointe sur `publication_number`, evite tout cela :

  1. risque nul sur l'onglet partage et sur la saisie humaine ;
  2. UN analyseur au lieu de quatre : les collecteurs ne bougent pas ;
  3. le dashboard sait deja faire ce type de jointure (il le fait pour
     `entreprises_enrichies`) : on reutilise un mecanisme eprouve ;
  4. l'analyse est rejouable et rattrapable sans recollecter ;
  5. le budget d'appels au modele vit a UN seul endroit.

Le pays d'origine du titulaire, que les collecteurs calculent (`_origine`)
puis JETTENT avant l'ecriture, est ici re-etabli par le modele, qui le fait
mieux que la comparaison de chaines des collecteurs.

COUT
----
Haiku 4.5 : 1 $ / M tokens en entree, 5 $ / M en sortie. Une attribution
coute environ 850 tokens en entree et 300 en sortie, soit ~0,24 centime.
Le budget par defaut (150 par run, 2 runs/semaine) plafonne donc la depense
autour de 3 $/mois. La memoire inter-runs fait qu'on ne paie jamais deux fois
la meme attribution.

USAGE
-----
    python attributions_analyse.py
    RADAR_ATTRIB_DEBUG=1 python attributions_analyse.py   # aucune ecriture

VARIABLES
---------
    RADAR_ATTRIB_BUDGET   nombre max d'analyses par run (defaut 150)
    RADAR_ATTRIB_DEBUG    1 = affiche ce qui serait ecrit, n'ecrit rien
    RADAR_MODELE          modele utilise (defaut : celui du coeur)
"""

import os
import sys
from datetime import date

import ted_complet_v14 as ted
import radar_resilience
import bm_attributions as bma


# ===========================================================================
# CONFIGURATION
# ===========================================================================

NOM_ONGLET = "attributions_analyse"
ONGLET_SOURCE = bma.NOM_ONGLET                 # attributions_radar
COLONNES_SOURCE = bma.COLONNES                 # les 13 colonnes brutes

# Schema de la table d'analyse. `publication_number` en TETE : c'est la clef
# de jointure, et la mettre en premier rend l'onglet lisible a l'oeil nu.
COLONNES = [
    "publication_number", "date_analyse",
    "score_final", "score_surete", "score_commercial", "action_recommandee",
    "pays_origine_titulaire", "titulaire_etranger",
    "nature_deploiement", "profils_deployes", "duree_chantier",
    "exposition_terrain", "besoin_surete_probable",
    "interlocuteur_vise", "justification", "confiance",
    "modele",
]

BUDGET = int(os.environ.get("RADAR_ATTRIB_BUDGET", "150"))
DEBUG = os.environ.get("RADAR_ATTRIB_DEBUG", "0") == "1"

# Vocabulaires fermes. Le modele doit choisir DANS ces listes : une valeur
# libre serait impossible a filtrer dans le dashboard et a scorer ici.
NATURES = ("expatrie_significatif", "mixte", "local_uniquement",
           "aucun_deploiement", "inconnue")
DUREES = ("courte", "moyenne", "longue_ou_residente", "inconnue")
EXPOSITIONS = ("site_isole", "chantier_urbain", "bureau", "inconnue")
BESOINS = ("fort", "moyen", "faible", "inconnu")


# ===========================================================================
# PROMPT
# ===========================================================================
# Cadrage DIFFERENT de celui des avis, et c'est tout l'interet. Un avis pose
# la question « ce marche va-t-il exister ? ». Une attribution pose une
# question deja tranchee : le marche EST attribue, une entreprise identifiee
# va deployer du personnel. La seule inconnue est operationnelle -- qui,
# combien de temps, ou -- et commerciale : a qui parler.

PROMPT_ATTRIBUTION = """Tu analyses une ATTRIBUTION de marche public international pour Amarante International, societe francaise de securite privee operant en zones a risque (Sahel, MENA, Ukraine, Asie centrale).

CONTEXTE DECISIF : ce marche est DEJA ATTRIBUE. L'entreprise ci-dessous a gagne. Elle va donc mobiliser du personnel pour l'executer, dans un pays a risque. Ta tache n'est PAS d'evaluer si l'opportunite existe : elle existe. Ta tache est de dire QUI va etre expose, COMBIEN DE TEMPS, et A QUI Amarante doit parler.

DONNEES DE L'ATTRIBUTION
Titulaire : {gagnant}
Pays d'adresse enregistree du titulaire (donnee officielle TED, peut differer de l'origine reelle du groupe) : {pays_adresse}
Pays d'execution : {pays_execution}
Acheteur / bailleur : {acheteur}
Objet du marche : {titre}
Secteur : {secteur}
Montant attribue : {valeur}
Codes CPV : {cpv}
Sous-traitance : {sous_traitance}

CE QUE TU DOIS DETERMINER

1. ORIGINE DU TITULAIRE. D'apres sa raison sociale et ta connaissance du tissu economique, de quel pays vient cette entreprise ? Reponds par le nom du pays, ou "inconnu" si tu n'as pas d'element. Puis dis si elle est ETRANGERE au pays d'execution.
   C'est le signal le plus important : une entreprise turque qui gagne un marche de travaux au Mali expatrie des ingenieurs et des chefs de chantier. Une entreprise malienne, non.
   Le "pays d'adresse enregistree" ci-dessus est un INDICE factuel fiable, mais l'adresse officielle peut masquer l'origine reelle : une filiale locale d'un groupe etranger a une adresse locale. Recoupe-le avec la raison sociale plutot que de le recopier aveuglement.

2. NATURE DU DEPLOIEMENT, une valeur parmi :
   - "expatrie_significatif" : personnel international deploye durablement (encadrement, ingenierie, supervision)
   - "mixte" : encadrement international, main-d'oeuvre locale
   - "local_uniquement" : execute par du personnel du pays
   - "aucun_deploiement" : fourniture livree, sans presence sur place (achat de materiel, licence, transport)
   - "inconnue"

3. PROFILS DEPLOYES. En une ligne, les fonctions reellement exposees (ex : "chefs de chantier et ingenieurs geotechniques sur site isole"). Sois CONCRET, pas generique.

4. DUREE DU CHANTIER : "courte" (moins de 6 mois), "moyenne" (6 a 18 mois), "longue_ou_residente" (plus de 18 mois ou presence permanente), "inconnue". Deduis-la du montant, du secteur et de l'objet.

5. EXPOSITION TERRAIN : "site_isole" (hors agglomeration, deplacements routiers), "chantier_urbain", "bureau", "inconnue".

6. BESOIN DE SURETE PROBABLE : "fort", "moyen", "faible", "inconnu". Croise l'exposition, la duree et le profil du personnel.

7. INTERLOCUTEUR VISE. Chez ce titulaire precisement, quelle fonction Amarante doit-elle contacter (ex : "directeur des operations Afrique de l'Ouest", "responsable HSE groupe", "chef de projet pays") ? Adapte a la taille et a la nature de l'entreprise.

8. JUSTIFICATION. Deux a trois phrases SPECIFIQUES a ce marche. Interdiction de repeter le contenu des champs : explique le raisonnement. Une justification qui pourrait s'appliquer a n'importe quelle autre attribution est une mauvaise justification.

REGLES
- Une fourniture pure (materiel livre, vehicules, medicaments, licences) vaut "aucun_deploiement" : personne n'est expose, ce n'est pas un prospect.
- Travaux, genie civil, forage, supervision, assistance technique, logistique terrain impliquent presque toujours une presence.
- Si le titulaire est LOCAL et le marche modeste, dis-le franchement : mieux vaut une piste ecartee qu'une liste diluee.
- N'invente aucun fait sur l'entreprise. En cas de doute, "inconnu" et une confiance basse.

Reponds UNIQUEMENT par un objet JSON, sans texte autour, sans balises Markdown :
{{
  "pays_origine_titulaire": "nom du pays ou inconnu",
  "titulaire_etranger": true | false,
  "nature_deploiement": "expatrie_significatif | mixte | local_uniquement | aucun_deploiement | inconnue",
  "profils_deployes": "une ligne concrete",
  "duree_chantier": "courte | moyenne | longue_ou_residente | inconnue",
  "exposition_terrain": "site_isole | chantier_urbain | bureau | inconnue",
  "besoin_surete_probable": "fort | moyen | faible | inconnu",
  "interlocuteur_vise": "fonction a contacter",
  "justification": "deux a trois phrases specifiques",
  "confiance": 0.0 a 1.0
}}"""


def construire_prompt(a):
    """Attribution brute -> prompt. Fonction PURE."""
    def champ(cle, defaut="non precise"):
        return str(a.get(cle) or "").strip() or defaut
    return PROMPT_ATTRIBUTION.format(
        gagnant=champ("gagnant", "titulaire non nomme"),
        pays_adresse=champ("pays_titulaire", "non renseigne"),
        pays_execution=champ("pays_execution"),
        acheteur=champ("acheteur"),
        titre=champ("titre"),
        secteur=champ("secteur"),
        valeur=champ("valeur_attribuee", "montant non publie"),
        cpv=champ("cpv"),
        sous_traitance=champ("sous_traitance", "non precisee"))


# ===========================================================================
# NORMALISATION
# ===========================================================================

def normaliser(extraction):
    """Ramene l'extraction dans les vocabulaires fermes. Ne leve jamais.

    Un modele qui repond a cote (valeur libre, synonyme, majuscules) ne doit
    pas produire une ligne infiltrable dans le dashboard : on replie sur
    "inconnu(e)" plutot que de laisser passer une valeur fantaisiste."""
    if not isinstance(extraction, dict):
        return None

    def dans(cle, valeurs, defaut):
        v = str(extraction.get(cle) or "").strip().lower()
        extraction[cle] = v if v in valeurs else defaut

    dans("nature_deploiement", NATURES, "inconnue")
    dans("duree_chantier", DUREES, "inconnue")
    dans("exposition_terrain", EXPOSITIONS, "inconnue")
    dans("besoin_surete_probable", BESOINS, "inconnu")

    extraction["titulaire_etranger"] = bool(extraction.get("titulaire_etranger"))
    for cle in ("pays_origine_titulaire", "profils_deployes",
                "interlocuteur_vise", "justification"):
        extraction[cle] = str(extraction.get(cle) or "").strip()
    try:
        conf = float(extraction.get("confiance", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    extraction["confiance"] = max(0.0, min(1.0, conf))
    return extraction


# ===========================================================================
# SCORING
# ===========================================================================
# Deux axes distincts, comme pour les avis, et surtout PAS un chiffre unique
# recopie trois fois. C'est precisement ce que faisait le dashboard.

POIDS_NATURE = {"expatrie_significatif": 1.0, "mixte": 0.75,
                "local_uniquement": 0.15, "aucun_deploiement": 0.0,
                "inconnue": 0.4}
POIDS_EXPOSITION = {"site_isole": 1.0, "chantier_urbain": 0.6,
                    "bureau": 0.2, "inconnue": 0.4}
POIDS_DUREE = {"longue_ou_residente": 1.0, "moyenne": 0.7,
               "courte": 0.4, "inconnue": 0.5}
POIDS_BESOIN = {"fort": 1.0, "moyen": 0.6, "faible": 0.2, "inconnu": 0.4}


def _montant_usd(valeur):
    """Extrait un ordre de grandeur en USD depuis '(USD) 8 000 000'.
    Renvoie 0.0 si illisible : un montant absent ne doit ni favoriser ni
    condamner (meme prudence que les dates ailleurs dans le projet)."""
    import re
    texte = str(valeur or "").replace("\u00a0", " ")
    chiffres = re.sub(r"[^\d.,]", "", texte).replace(" ", "")
    if not chiffres:
        return 0.0
    chiffres = chiffres.replace(",", "") if chiffres.count(",") > 1 else chiffres
    chiffres = chiffres.replace(",", ".") if chiffres.count(".") == 0 else \
        chiffres.replace(",", "")
    try:
        return float(chiffres)
    except ValueError:
        return 0.0


def poids_valeur(valeur):
    """Un gros marche mobilise plus de monde, plus longtemps. Echelle
    volontairement plate au-dela de 50 M USD : au-dela, ce n'est plus le
    montant qui discrimine."""
    montant = _montant_usd(valeur)
    if montant >= 50_000_000:
        return 1.0
    if montant >= 10_000_000:
        return 0.85
    if montant >= 2_000_000:
        return 0.65
    if montant >= 500_000:
        return 0.45
    if montant > 0:
        return 0.25
    return 0.35                      # montant non publie : ni prime ni peine


def calculer_scores(attribution, extraction):
    """(surete, commercial, final) sur 10. Fonction PURE.

    SURETE     : a quel point du personnel est expose. Zone x nature du
                 deploiement x exposition x duree.
    COMMERCIAL : a quel point c'est une cible atteignable et rentable.
                 Titulaire etranger (il achete de la protection
                 internationale) x valeur du marche x besoin exprime.

    Les deux restent SEPARES a l'affichage : un chantier tres expose chez un
    titulaire local n'est pas la meme affaire qu'un bureau chez un groupe
    international."""
    if not isinstance(extraction, dict):
        return 0.0, 0.0, 0.0
    zone = ted.MULTIPLICATEUR_ZONE.get(
        str(attribution.get("pays_execution") or "").upper(), 0.3)

    nature = POIDS_NATURE.get(extraction.get("nature_deploiement"), 0.4)
    expo = POIDS_EXPOSITION.get(extraction.get("exposition_terrain"), 0.4)
    duree = POIDS_DUREE.get(extraction.get("duree_chantier"), 0.5)
    besoin = POIDS_BESOIN.get(extraction.get("besoin_surete_probable"), 0.4)

    surete = 10.0 * zone * nature * (0.6 * expo + 0.4 * duree)

    etranger = 1.0 if extraction.get("titulaire_etranger") else 0.45
    commercial = 10.0 * etranger * (0.5 * poids_valeur(
        attribution.get("valeur_attribuee")) + 0.5 * besoin)

    # GARDE-FOU : sans deploiement, il n'y a personne a proteger. Le score
    # commercial ne doit pas maquiller une fourniture en prospect.
    if extraction.get("nature_deploiement") == "aucun_deploiement":
        surete, commercial = 0.0, min(commercial, 2.0)

    # Confiance basse : on n'ecrase pas, on tempere.
    conf = float(extraction.get("confiance") or 0.5)
    if conf < 0.4:
        surete, commercial = surete * 0.8, commercial * 0.8

    final = 0.55 * surete + 0.45 * commercial
    return (round(min(10.0, surete), 1), round(min(10.0, commercial), 1),
            round(min(10.0, final), 1))


def action_recommandee(final, extraction):
    """contacter / surveiller / ignorer."""
    if not isinstance(extraction, dict):
        return "ignorer"
    if extraction.get("nature_deploiement") in ("aucun_deploiement",
                                                "local_uniquement"):
        return "ignorer"
    if final >= 6.0 and extraction.get("besoin_surete_probable") in ("fort",
                                                                    "moyen"):
        return "contacter"
    if final >= 3.5:
        return "surveiller"
    return "ignorer"


# ===========================================================================
# PRIORISATION
# ===========================================================================

def prioriser(attributions):
    """Ordonne AVANT le plafond de budget : sinon le plafond tronquerait au
    hasard. Meme invariant que cote avis (test_budget).

    Critere : risque de la zone d'abord, valeur du marche ensuite. On ne
    connait pas encore la nature du deploiement -- c'est justement ce que
    l'analyse va determiner."""
    def clef(a):
        zone = ted.MULTIPLICATEUR_ZONE.get(
            str(a.get("pays_execution") or "").upper(), 0.3)
        return (-zone, -poids_valeur(a.get("valeur_attribuee")),
                str(a.get("date_publication") or ""))
    return sorted(attributions, key=clef)


# ===========================================================================
# ANALYSE
# ===========================================================================

def analyser_une(attribution, modele=None):
    """Attribution -> extraction normalisee, ou None. Ne leve jamais.

    Meme cascade de recuperation JSON que le coeur : lecture directe,
    sous-chaine, puis reparation par le modele de raffinement."""
    import json
    texte = ted.appeler_modele(construire_prompt(attribution),
                               modele=modele or ted.MODELE)
    if texte is None:
        return None
    try:
        return normaliser(json.loads(texte))
    except json.JSONDecodeError:
        pass
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin > debut:
        try:
            return normaliser(json.loads(texte[debut:fin + 1]))
        except json.JSONDecodeError:
            pass
    repare = ted.reparer_json(texte, modele=ted.MODELE_RAFFINEMENT)
    if repare is None:
        return None
    try:
        return normaliser(json.loads(repare))
    except json.JSONDecodeError:
        return None


def ligne_pour_sheet(attribution, extraction, scores, modele=None):
    """Ligne rangee selon COLONNES. Fonction PURE."""
    surete, commercial, final = scores
    valeurs = {
        "publication_number": attribution.get("publication_number", ""),
        "date_analyse": date.today().isoformat(),
        "score_final": final, "score_surete": surete,
        "score_commercial": commercial,
        "action_recommandee": action_recommandee(final, extraction),
        "pays_origine_titulaire": extraction.get("pays_origine_titulaire", ""),
        "titulaire_etranger": extraction.get("titulaire_etranger", False),
        "nature_deploiement": extraction.get("nature_deploiement", ""),
        "profils_deployes": extraction.get("profils_deployes", ""),
        "duree_chantier": extraction.get("duree_chantier", ""),
        "exposition_terrain": extraction.get("exposition_terrain", ""),
        "besoin_surete_probable": extraction.get("besoin_surete_probable", ""),
        "interlocuteur_vise": extraction.get("interlocuteur_vise", ""),
        "justification": extraction.get("justification", ""),
        "confiance": extraction.get("confiance", ""),
        "modele": modele or ted.MODELE,
    }
    return [str(valeurs.get(c, "")) for c in COLONNES]


# ===========================================================================
# GOOGLE SHEETS
# ===========================================================================

def ouvrir_feuille(sheet_id, fichier, nom_onglet=NOM_ONGLET, colonnes=None):
    import gspread
    from google.oauth2.service_account import Credentials
    colonnes = colonnes or COLONNES
    # Ouverture protegee par retry (503/429).
    classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier)
    try:
        return classeur.worksheet(nom_onglet)
    except Exception:
        f = classeur.add_worksheet(title=nom_onglet, rows=4000,
                                   cols=len(colonnes))
        f.append_row(colonnes)
        return f


def lire_attributions(feuille_source):
    """Lit `attributions_radar` en LECTURE POSITIONNELLE (regle 4).

    L'en-tete de la feuille n'est jamais consulte pour localiser une colonne :
    la position vient de `bm_attributions.COLONNES`. Un en-tete desaligne ne
    peut donc pas ranger un montant sous `gagnant`."""
    valeurs = feuille_source.get_all_values()
    if not valeurs:
        return []
    premiere = [str(c).strip() for c in (valeurs[0] or [])]
    debut = 1 if "publication_number" in premiere else 0
    attributions = []
    for ligne in valeurs[debut:]:
        if not ligne:
            continue
        a = {}
        for i, nom in enumerate(COLONNES_SOURCE):
            a[nom] = str(ligne[i]).strip() if i < len(ligne) else ""
        if a.get("publication_number"):
            attributions.append(a)
    return attributions


def ecrire(feuille, lignes):
    """Ajout seul. Une analyse deja presente n'est jamais reecrite : la
    memoire l'a de toute facon ecartee en amont."""
    if not lignes:
        return 0
    radar_resilience.avec_retry(lambda: feuille.append_rows(lignes, value_input_option="RAW"), "ecriture append_rows")
    return len(lignes)


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    sheet_id = os.environ.get("TED_SHEET_ID", "")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

    print("=" * 62)
    print("ANALYSE DES ATTRIBUTIONS - Radar Amarante")
    print("  budget : {} analyse(s) | modele : {}{}".format(
        BUDGET, ted.MODELE, " | MODE DEBUG (aucune ecriture)" if DEBUG else ""))
    print("=" * 62)

    if not sheet_id:
        print("(info) TED_SHEET_ID absent : rien a faire.")
        return

    source = ouvrir_feuille(sheet_id, fichier, ONGLET_SOURCE, COLONNES_SOURCE)
    attributions = lire_attributions(source)
    print("  {} attribution(s) dans '{}'.".format(len(attributions),
                                                  ONGLET_SOURCE))

    # MEMOIRE AVANT PLAFOND : le budget doit servir a DECOUVRIR, jamais a
    # repayer ce qu'on connait deja. Invariant partage avec les avis.
    deja = ted.numeros_publication_existants(sheet_id, fichier, NOM_ONGLET,
                                             COLONNES)
    nouvelles = [a for a in attributions
                 if a.get("publication_number") not in deja]
    print("  memoire : {} deja analysee(s), {} nouvelle(s).".format(
        len(attributions) - len(nouvelles), len(nouvelles)))

    nouvelles = prioriser(nouvelles)
    if len(nouvelles) > BUDGET:
        print("  plafond : {} analysee(s) ce run, {} en attente du prochain."
              .format(BUDGET, len(nouvelles) - BUDGET))
        nouvelles = nouvelles[:BUDGET]

    lignes, apercu = [], []
    for a in nouvelles:
        extraction = analyser_une(a)
        if not extraction:
            continue
        scores = calculer_scores(a, extraction)
        lignes.append(ligne_pour_sheet(a, extraction, scores))
        apercu.append((scores[2], a, extraction))

    print("  {} analyse(s) aboutie(s) sur {} tentee(s).".format(
        len(lignes), len(nouvelles)))

    # Apercu trie : c'est ce qui permet de juger la QUALITE avant d'ecrire.
    for final, a, e in sorted(apercu, key=lambda x: -x[0])[:10]:
        print("\n  [{:4.1f}] {} | {} | {}".format(
            final, (a.get("gagnant") or "?")[:38],
            a.get("pays_execution"), e.get("nature_deploiement")))
        print("        origine : {} ({}) | duree : {} | besoin : {}".format(
            e.get("pays_origine_titulaire") or "?",
            "etranger" if e.get("titulaire_etranger") else "local",
            e.get("duree_chantier"), e.get("besoin_surete_probable")))
        print("        a contacter : {}".format(e.get("interlocuteur_vise")))
        print("        {}".format((e.get("justification") or "")[:200]))

    if DEBUG:
        print("\n  MODE DEBUG : {} ligne(s) NON ecrite(s).".format(len(lignes)))
        return

    try:
        cible = ouvrir_feuille(sheet_id, fichier)
        print("\n  {} ligne(s) ecrite(s) dans '{}'.".format(
            ecrire(cible, lignes), NOM_ONGLET))
    except Exception as e:
        print("\n(attrib-analyse) ecriture impossible ({}). Le run continue."
              .format(e))

    try:
        import radar_stockage
        plates = [dict(zip(COLONNES, l)) for l in lignes]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))


if __name__ == "__main__":
    main()
    # Un run qui n'a rien pu analyser doit sortir en echec plutot que de
    # laisser l'etape GitHub verte (cf. solde de credits epuise du 23/07).
    ted.sortie_selon_sante_llm("attrib-analyse")
