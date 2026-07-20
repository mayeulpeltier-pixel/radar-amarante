# -*- coding: utf-8 -*-
"""
TED -- Pipeline complet autonome : collecte + extraction LLM + score
========================================================================

UNE SEULE CELLULE, AUTOSUFFISANTE. Ce fichier fusionne ce qui etait avant
deux scripts separes (ted_collecte.py pour le Sprint 1, puis un script
Sprint 2 qui dependait des variables globales du premier). Cette
dependance entre cellules a casse quand le runtime Colab a ete redemarre
entre deux sessions (NameError sur PAYS_ROUGE). Plutot que de demander de
rejouer les cellules dans le bon ordre a chaque fois, ce fichier ne
depend plus de rien d'autre que lui-meme.

CE QU'IL FAIT :
1. Collecte (Sprint 1) : interroge TED, filtre par CPV+pays, deduplique.
   Strictement identique a la logique validee dans ted_collecte.py.
2. Extraction LLM (Sprint 2) : pour chaque avis retenu, appelle un
   modele Claude pour extraire des faits structures (pas un score -- le
   modele lit, le code calcule, cadrage section 7-8).
3. Score : calcule en code a partir des faits extraits, 0 a 10.
4. Affichage console, classe par score. Rien n'est ecrit automatiquement
   nulle part (pas de Sheet, pas de contact client) -- relecture humaine
   obligatoire avant toute action commerciale.

CORRECTION CLE DU PROMPT (issue du run reel) :
Le code buyer-country ne suffit pas a juger si l'acheteur est un bailleur
etranger : "European Union, represented by the European Commission on
behalf of and for the account of Jordan" a un buyer-country = JOR, alors
que l'acheteur reel est la Commission europeenne. Le prompt demande
explicitement au modele de juger sur le NOM de l'acheteur, jamais sur le
seul code pays -- la nuance que le mecanique ne pouvait pas attraper.

Variable d'environnement requise : ANTHROPIC_API_KEY
Dependances : pip install requests
"""

import json
import os
import time
import html
import re
from datetime import date, timedelta

import requests

# ===========================================================================
# PARTIE 1 -- CONFIGURATION COLLECTE (identique a ted_collecte.py)
# ===========================================================================

TED_ENDPOINT = "https://api.ted.europa.eu/v3/notices/search"

CODES_CPV = [
    "71520000", "71247000", "71521000", "71300000", "71351000",
    "45250000", "76000000", "90710000", "65100000", "09300000",
    "14000000", "32500000",
    "75211100",  # Services lies a l'aide militaire etrangere
    "75211200",  # Services lies a l'aide economique etrangere
    "79421000",  # Project management services -- voir DIVISIONS_CPV_CONDITIONNELLES
]

# Trois niveaux d'admission CPV (V8, suite a une revue critique qui a
# trouve une vraie faille). LARGEMENT_ADMISES : divisions deja prouvees
# sur des runs reels (71 = assistance technique, porteur de la majorite
# des vrais leads). CONDITIONNELLES : divisions larges et generiques
# (79 = conseil en gestion, 75 = administration publique/defense/
# securite sociale) admises SEULEMENT si le titre contient un signal
# explicite de deploiement terrain -- la division 75 avait ete ajoutee
# pour deux codes precis (aide militaire/economique etrangere) mais
# admise au niveau de la division entiere a 2 chiffres, ce qui ouvrait
# aussi l'administration publique generique, la securite sociale, la
# justice... le meme risque que "maitrise d'oeuvre" sur BOAMP.
# CODES_PRECIS_TOUJOURS_ADMIS : les codes exacts qui ont motive l'ajout
# de leur division restent admis sans condition de titre, meme quand
# leur division est par ailleurs conditionnelle.
DIVISIONS_CPV_LARGEMENT_ADMISES = {"09", "14", "32", "45", "65", "71", "76", "90"}
DIVISIONS_CPV_CONDITIONNELLES = {"75", "79"}
CODES_PRECIS_TOUJOURS_ADMIS = {"75211100", "75211200"}
MOTS_CLES_DEPLOIEMENT_TERRAIN = [
    "resident expert", "field mission", "site visit", "implementation unit",
    "international staff", "remote area", "project management unit",
    "field office", "field team",
]

PAYS_ROUGE = {
    "Libye": "LBY", "Mali": "MLI", "Niger": "NER", "Burkina Faso": "BFA",
    "RDC": "COD", "Soudan du Sud": "SSD", "Yemen": "YEM", "Somalie": "SOM",
    "Irak": "IRQ", "Ukraine": "UKR", "Mexique": "MEX",
    "Palestine": "PSE",  # CORRECTION (run reel) : restait en couverture
    # large (poids 0.3) alors que le texte du modele lui-meme decrit un
    # "contexte de tension securitaire elevee" -- incoherence entre la
    # justification qualitative et le score mecanique, repere precisement
    # grace a la double lecture Haiku/Sonnet.
    "Afghanistan": "AFG",  # ELARGI (sur demande) : absent alors qu'objectivement
    "Haiti": "HTI",         # parmi les zones les plus a risque au monde.
}
PAYS_ORANGE = {
    "Ethiopie": "ETH", "Nigeria": "NGA", "Cameroun": "CMR",
    "Mozambique": "MOZ", "Bangladesh": "BGD", "Pakistan": "PAK",
    "Egypte": "EGY", "Ouzbekistan": "UZB", "Moldavie": "MDA",
    "Jamaique": "JAM", "Armenie": "ARM", "Jordanie": "JOR",
    "Papouasie-Nouvelle-Guinee": "PNG", "Montenegro": "MNE",
    "Albanie": "ALB", "Madagascar": "MDG", "Oman": "OMN",
    "Emirats Arabes Unis": "ARE", "Turquie": "TUR", "Afrique du Sud": "ZAF",
}
AFRIQUE = {
    "Algerie": "DZA", "Angola": "AGO", "Benin": "BEN", "Botswana": "BWA",
    "Burkina Faso": "BFA", "Burundi": "BDI", "Cap-Vert": "CPV",
    "Cameroun": "CMR", "Republique Centrafricaine": "CAF", "Tchad": "TCD",
    "Comores": "COM", "Congo": "COG", "RDC": "COD", "Cote d'Ivoire": "CIV",
    "Djibouti": "DJI", "Egypte": "EGY", "Guinee Equatoriale": "GNQ",
    "Erythree": "ERI", "Eswatini": "SWZ", "Ethiopie": "ETH", "Gabon": "GAB",
    "Gambie": "GMB", "Ghana": "GHA", "Guinee": "GIN", "Guinee-Bissau": "GNB",
    "Kenya": "KEN", "Lesotho": "LSO", "Liberia": "LBR", "Libye": "LBY",
    "Madagascar": "MDG", "Malawi": "MWI", "Mali": "MLI", "Mauritanie": "MRT",
    "Maurice": "MUS", "Maroc": "MAR", "Mozambique": "MOZ", "Namibie": "NAM",
    "Niger": "NER", "Nigeria": "NGA", "Rwanda": "RWA",
    "Sao Tome-et-Principe": "STP", "Senegal": "SEN", "Seychelles": "SYC",
    "Sierra Leone": "SLE", "Somalie": "SOM", "Afrique du Sud": "ZAF",
    "Soudan du Sud": "SSD", "Soudan": "SDN", "Tanzanie": "TZA",
    "Togo": "TGO", "Tunisie": "TUN", "Ouganda": "UGA", "Zambie": "ZMB",
    "Zimbabwe": "ZWE",
}
MOYEN_ORIENT = {
    "Bahrein": "BHR", "Iran": "IRN", "Irak": "IRQ", "Israel": "ISR",
    "Jordanie": "JOR", "Koweit": "KWT", "Liban": "LBN", "Oman": "OMN",
    "Palestine": "PSE", "Qatar": "QAT", "Arabie Saoudite": "SAU",
    "Syrie": "SYR", "Turquie": "TUR", "Emirats Arabes Unis": "ARE",
    "Yemen": "YEM",
}
AMERIQUE_DU_SUD = {
    "Argentine": "ARG", "Bolivie": "BOL", "Bresil": "BRA", "Chili": "CHL",
    "Colombie": "COL", "Equateur": "ECU", "Guyana": "GUY",
    "Paraguay": "PRY", "Perou": "PER", "Suriname": "SUR",
    "Uruguay": "URY", "Venezuela": "VEN",
}

# ELARGISSEMENT (sur demande explicite : "Europe de l'Est, Ukraine,
# Ouzbekistan... Pakistan et pays d'Asie a risque... iles a risque...
# Guyane francaise... bref tous les pays a risque"). Meme logique que
# l'elargissement continental precedent : couverture large (poids 0.3),
# pas une cartographie de risque fine pays par pays -- a affiner avec la
# connaissance terrain d'Amarante, comme deja note pour AFRIQUE/MOYEN_ORIENT.
EUROPE_EST_CAUCASE_ASIE_CENTRALE = {
    "Russie": "RUS", "Bielorussie": "BLR", "Georgie": "GEO",
    "Azerbaidjan": "AZE", "Kosovo": "XKX", "Bosnie-Herzegovine": "BIH",
    "Serbie": "SRB", "Macedoine du Nord": "MKD", "Kazakhstan": "KAZ",
    "Kirghizistan": "KGZ", "Tadjikistan": "TJK", "Turkmenistan": "TKM",
}
ASIE_A_RISQUE = {
    "Myanmar": "MMR", "Sri Lanka": "LKA", "Nepal": "NPL",
    "Philippines": "PHL", "Indonesie": "IDN", "Cambodge": "KHM",
    "Laos": "LAO",
}
ILES_A_RISQUE = {
    "Trinite-et-Tobago": "TTO", "Iles Salomon": "SLB", "Fidji": "FJI",
    "Vanuatu": "VUT",
    # Haiti et PNG deja couverts plus haut (rouge / orange) ; Comores et
    # Madagascar deja dans AFRIQUE/PAYS_ORANGE.
}
TERRITOIRES_FRANCAIS_OUTRE_MER_A_RISQUE = {
    "Guyane": "GUF", "Mayotte": "MYT", "Nouvelle-Caledonie": "NCL",
    # Ces territoires sont juridiquement UE/France : un avis qui les
    # mentionne aura tres probablement un acheteur francais local
    # (type_client=etat_administration_locale), ce que le score
    # commercial penalise deja correctement. On les garde ici pour le
    # volet RISQUE (orpaillage illegal en Guyane, tensions a Mayotte,
    # troubles civils en Nouvelle-Caledonie), pas pour l'accessibilite.
}

CODES_PAYS_SUIVIS = sorted(set(
    list(PAYS_ROUGE.values()) + list(PAYS_ORANGE.values())
    + list(AFRIQUE.values()) + list(MOYEN_ORIENT.values())
    + list(AMERIQUE_DU_SUD.values())
    + list(EUROPE_EST_CAUCASE_ASIE_CENTRALE.values())
    + list(ASIE_A_RISQUE.values())
    + list(ILES_A_RISQUE.values())
    + list(TERRITOIRES_FRANCAIS_OUTRE_MER_A_RISQUE.values())
))

NB_JOURS_FENETRE = 14
LIMITE_RESULTATS = 250

# Multiplicateur de zone pour le score (cadrage section 8.1, adapte) :
# rouge = risque fort confirme, orange = risque moyen confirme,
# couverture large = pays inclus par l'elargissement continental sans
# classification de risque individuelle -- poids plus faible, moins fiable.
MULTIPLICATEUR_ZONE = {}
for _code in PAYS_ROUGE.values():
    MULTIPLICATEUR_ZONE[_code] = 1.0
for _code in PAYS_ORANGE.values():
    MULTIPLICATEUR_ZONE.setdefault(_code, 0.6)
for _code in (
    list(AFRIQUE.values()) + list(MOYEN_ORIENT.values()) + list(AMERIQUE_DU_SUD.values())
    + list(EUROPE_EST_CAUCASE_ASIE_CENTRALE.values()) + list(ASIE_A_RISQUE.values())
    + list(ILES_A_RISQUE.values()) + list(TERRITOIRES_FRANCAIS_OUTRE_MER_A_RISQUE.values())
):
    MULTIPLICATEUR_ZONE.setdefault(_code, 0.3)


# ===========================================================================
# PARTIE 2 -- CONFIGURATION LLM (Sprint 2)
# ===========================================================================

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
# Modeles surchargeables par env (upgrade intentionnel sans toucher au code, et
# rollback instantane). Defauts = chaines VALIDES et actives, verifiees sur la
# doc Anthropic : Haiku 4.5 (volume) et Sonnet 4.6 (raffinement). La forme `or`
# tolere une variable vide (retombe sur le defaut). ATTENTION : passer le
# raffinement a "claude-sonnet-5" exige AUSSI de retirer "temperature": 0 de
# l'appel API -- Sonnet 5 rejette les parametres d'echantillonnage non-defaut.
# Ne pas swapper a l'aveugle.
MODELE = os.environ.get("RADAR_MODELE") or "claude-haiku-4-5-20251001"  # modele rapide/economique pour le volume
MODELE_RAFFINEMENT = os.environ.get("RADAR_MODELE_RAFFINEMENT") or "claude-sonnet-4-6"  # escalade sur les cas au-dessus du

# --- Sante des appels au modele --------------------------------------------
# Un modele retire fait echouer TOUS les appels : chaque avis est ignore et le
# run se termine "vert" avec zero analyse. Le radar semble tourner alors qu'il
# ne produit plus rien. On compte donc les appels et les echecs pour pouvoir
# marquer le run en ECHEC (radar_run sort alors en code 1, ce qui declenche
# l'alerte e-mail de GitHub Actions).
# Horizons de retrait au 18/07/2026, verifies sur la table officielle des
# depreciations : haiku-4-5-20251001 pas avant le 15/10/2026, sonnet-4-6 pas
# avant le 17/02/2027. Les deux se surchargent sans toucher au code, via
# RADAR_MODELE et RADAR_MODELE_RAFFINEMENT.
STATS_LLM = {"appels": 0, "echecs": 0, "modele_invalide": 0, "detail": ""}

# Proportion d'echecs au-dela de laquelle on considere l'API cassee (et non
# quelques avis malchanceux). Sous ce seuil, le pipeline degrade en silence
# comme avant : un avis rate n'est pas un incident.
SEUIL_ECHEC_LLM = float(os.environ.get("RADAR_SEUIL_ECHEC_LLM", "0.8"))
MINI_APPELS_LLM = int(os.environ.get("RADAR_MINI_APPELS_LLM", "10"))


def _marquer_echec_llm(detail):
    """Enregistre un echec d'appel et repere les erreurs de MODELE (retrait,
    nom invalide), qui exigent une action et non une simple patience."""
    STATS_LLM["echecs"] += 1
    bas = str(detail or "").lower()
    if ("not_found_error" in bas or "model" in bas and
            ("not found" in bas or "invalid" in bas or "does not exist" in bas)):
        STATS_LLM["modele_invalide"] += 1
        STATS_LLM["detail"] = str(detail)[:300]


def sante_llm():
    """(ok, message) sur l'etat des appels au modele pour ce run.

    Renvoie ok=False si un modele est refuse par l'API, ou si la quasi-totalite
    des appels a echoue. Dans les deux cas le run n'a rien produit d'utile et
    doit etre signale bruyamment plutot que passer pour un succes."""
    appels, echecs = STATS_LLM["appels"], STATS_LLM["echecs"]
    if STATS_LLM["modele_invalide"]:
        return False, (
            "MODELE REFUSE PAR L'API ({} fois). Modeles configures : {} (volume) "
            "et {} (raffinement). Verifie la table des depreciations et surcharge "
            "RADAR_MODELE / RADAR_MODELE_RAFFINEMENT. Detail : {}".format(
                STATS_LLM["modele_invalide"], MODELE, MODELE_RAFFINEMENT,
                STATS_LLM["detail"] or "n.c."))
    if appels >= MINI_APPELS_LLM and echecs >= appels * SEUIL_ECHEC_LLM:
        return False, (
            "APPELS AU MODELE MASSIVEMENT EN ECHEC : {}/{}. Cle API, quota ou "
            "disponibilite du service a verifier.".format(echecs, appels))
    if echecs:
        return True, "{} echec(s) d'appel sur {} (tolerable).".format(echecs, appels)
    return True, "{} appel(s) au modele, aucun echec.".format(appels)
    # seuil de surveillance (deja prevu au cadrage initial section 7.3,
    # jamais cable jusqu'ici : "n'escalader que sur les cas limites").
SEUIL_ALERTE = 6     # "fort", a contacter
SEUIL_SURVEILLANCE = 4  # "a surveiller", ne pas perdre dans le bruit
CHAMPS_VALEUR_TED = ["total-value", "estimated-value"]
# V12 : description de la procedure et des lots. Le titre TED est souvent
# pauvre ; le vrai signal surete (visites terrain, sites isoles, experts
# residents, conditions d'execution) vit dans la description. Ces champs
# sont DIRECTEMENT requetables dans la recherche TED, pas besoin d'aller
# chercher la notice complete avis par avis. REMARQUE : ils existent pour
# les notices eForms, pas pour l'ancien schema TED -- d'ou un repli propre
# sur le titre seul quand la description est absente (jamais de plantage).
CHAMPS_DESCRIPTION_TED = ["description-proc", "description-lot"]
# Tous les champs d'enrichissement optionnels, injectes ENSEMBLE dans la
# requete. Si TED en rejette un (400), interroger_ted les retire TOUS et
# poursuit : la collecte ne doit jamais casser pour un champ d'affichage
# ou d'enrichissement. (Le 400 ne dit pas QUEL champ est en cause, d'ou le
# retrait groupe ; en pratique ces champs sont documentes comme valides.)
CHAMPS_OPTIONNELS_TED = CHAMPS_VALEUR_TED + CHAMPS_DESCRIPTION_TED
# V14 : niveau d'enrichissement determine UNE fois (au 1er appel reel a
# TED), puis reutilise pour toutes les pages et pour le Mode B. Valeurs :
# None (non determine), "complet" (valeur + description), "desc"
# (description seule, la valeur a ete refusee), "base" (titre seul).
# CORRECTIF (run reel V13) : on DECOUPLE valeur et description. Un champ
# valeur invalide ne doit plus faire perdre la description (documentee
# comme valide), ce qui etait le defaut du repli groupe : un seul 400 sur
# la valeur retirait tout, d'ou le 0/23 avis enrichis observe.
_NIVEAU_ENRICHISSEMENT = None

# Longueur max de description injectee au modele (caracteres). Borne le
# cout par appel : description-lot peut etre long et multi-lots.
MAX_CARACTERES_DESCRIPTION = 2000

SEUIL_SURETE_POUR_FORT = 5.0  # V9 : un lead "FORT" doit avoir un besoin
# surete reel (>= ce seuil), pas seulement un bon score commercial. En
# dessous (ou si le modele juge l'opportunite faible), le score final est
# plafonne juste sous SEUIL_ALERTE -- voir le garde-fou dans
# calculer_scores. Valeur a affiner apres quelques runs reels (4.5 plus
# permissif, 5.5 plus strict) ; calee ici sur le constat qu'un score
# commercial eleve hissait au rang FORT des avis eau/energie AFD en pays
# stable dont le modele lui-meme jugeait l'opportunite faible.

# NOTE : la cle API n'est plus lue ici, au chargement du module, mais a
# chaque appel dans appeler_llm() -- voir plus bas. Une lecture figee au
# chargement gardait une valeur vide si la cle etait definie APRES coup
# dans une autre cellule, meme correctement : observe en conditions
# reelles (treize erreurs malgre une cle apparemment definie).

# Divisions CPV correspondant a une infrastructure critique (energie, eau,
# mines, transport/construction lourde, telecom). Calcule MECANIQUEMENT,
# pas demande au modele : le CPV est deja connu, pas la peine de faire
# rejuger une information deja structuree (meme principe que tout le
# pipeline : le LLM lit le contexte ambigu, le code calcule ce qui est
# deja factuel). Exclut 71 (ingenierie generique, applicable a tout
# secteur) et 90 (etude environnementale generique) : ce sont des TYPES
# D'ACTIVITE, pas des secteurs, et ne doivent pas activer le bonus seuls.
DIVISIONS_INFRASTRUCTURE_CRITIQUE = {"09", "14", "32", "45", "65", "76"}

PROMPT_TEMPLATE = """Tu es analyste sûreté pour une société française de protection de personnes en zones à risque. La société vend des prestations OPÉRATIONNELLES concrètes : escorte, protection rapprochée (CPO/CPD), chauffeur sécurité, véhicule blindé ou non, sécurisation de déplacements terrain. Elle ne vend PAS de conseil voyage générique ni de simple briefing sécurité.

On te donne un avis de marché public européen (TED). Détermine s'il implique probablement un déploiement PHYSIQUE et RÉGULIER de personnel sur le terrain à l'étranger, créant un besoin probable de prestations opérationnelles de sûreté (et pas seulement un besoin théorique ou un conseil ponctuel).

RÈGLE CRITIQUE SUR L'ACHETEUR (lis bien avant de répondre) :
Juge si l'acheteur est un bailleur/institution étrangère sur la base du NOM de l'acheteur, JAMAIS sur le seul code pays associé. Des formulations comme "European Union, represented by the European Commission on behalf of and for the account of [pays]" désignent un acheteur européen réel, même si le code pays technique correspond au pays bénéficiaire. De même pour l'AFD, Expertise France, la Banque Mondiale, la BAD, les agences ONU, USAID, GIZ, KfW. À l'inverse, une administration ou un opérateur portant le nom du pays d'exécution lui-même (ex: "Administrația Națională a Drumurilor" pour la Moldavie) est une administration LOCALE, même si le marché est publié sur TED.

RÈGLE SUR LA DURÉE :
Distingue une mission courte et ponctuelle (étude de faisabilité de quelques jours à quelques semaines, audit unique) d'une présence longue ou résidente (assistance technique continue, expert résident, appui à une unité de gestion de projet sur plusieurs mois ou années).

RÈGLE SUR LA MOBILITÉ TERRAIN (distincte de la durée -- lis bien) :
Une présence longue n'implique pas forcément un vrai besoin opérationnel : un expert résident basé en capitale, qui travaille principalement au bureau et se déplace peu, a un profil de risque faible même sur 18 mois. À l'inverse, une mission courte mais réellement mobile (plusieurs sites en une semaine) peut justifier un accompagnement plus fort qu'une présence longue mais sédentaire. Classe le profil de mobilité terrain dans UNE seule des catégories suivantes (la plus représentative) :
- aucune : pas de présence physique notable (travail documentaire, réunion à distance).
- capitale : présence dans la capitale ou un grand centre urbain sécurisé, déplacements limités.
- multi_sites : déplacements entre plusieurs sites ou plusieurs provinces.
- chantier : présence régulière sur un chantier ou une installation industrielle, souvent hors zone urbaine.
- terrain_isole : zones rurales ou isolées, accès difficile, infrastructures éloignées.
- frontiere : zone frontalière ou zone de tension active.

RÈGLE SUR LA SÉCURITÉ DÉJÀ EN PLACE (lis bien -- ça change la conclusion ET la nature de l'opportunité) :
Détermine QUI assure déjà la sécurité, car cela distingue un marché fermé d'une opportunité de conquête. Renseigne "securite_existante" avec l'une des quatre valeurs :
- "interne_client" : la sécurité est visiblement gérée EN INTERNE par le client lui-même (son propre personnel, un dispositif militaire ou étatique intégré, une force onusienne organique). Là, il n'y a généralement PAS d'ouverture pour Amarante -> l'avis peut être écarté.
- "prestataire_tiers" : la sécurité est déjà fournie par un PRESTATAIRE EXTERNE (société privée de sécurité, titulaire d'un autre marché d'escorte ou de gardiennage). Ce N'EST PAS une raison d'écarter : c'est au contraire une OPPORTUNITÉ DE DÉPLACEMENT concurrentiel -- un contrat existe, un concurrent est en place, il peut être disputé (renouvellement, extension, sous-traitance). À CONSERVER et signaler.
- "aucune" : aucune sécurité mentionnée, besoin potentiellement ouvert.
- "inconnu" : impossible à déterminer depuis l'avis.
Cherche des formulations comme "security services provided", "armed escort", "convoy security", "guard services", "protection personnel", ou équivalent en français, puis juge si c'est le client lui-même (interne) ou un tiers (prestataire).

RÈGLE SUR LE TYPE DE CLIENT :
Un bailleur international (AFD, UE, Banque Mondiale...) n'est pas le seul type de client pertinent. Une entreprise privée internationale (minière, énergie, industrielle) déployant du personnel à l'étranger est un prospect tout aussi réel pour Amarante, parfois plus accessible commercialement qu'un marché institutionnel verrouillé.

RÈGLE SUR LE PROFIL DES PERSONNES EXPOSÉES (distincte du type de client -- lis bien) :
Le type de client (qui paie) ne dit pas QUI est physiquement présent sur le terrain. Une entreprise privée peut déployer surtout du personnel local avec un ou deux superviseurs étrangers ; un bailleur peut au contraire envoyer une équipe d'experts internationaux nombreuse. Juge séparément qui est probablement exposé physiquement :
- expert_international : expert, consultant ou cadre étranger/expatrié, individuel ou petite équipe.
- executive : dirigeant, cadre de haut niveau, délégation en visite.
- technicien : personnel technique étranger ou mixte (encadrement étranger, exécution mixte).
- ouvrier_local : main d'oeuvre majoritairement locale, encadrement local.
- aucun : aucune personne identifiable comme exposée (travail purement documentaire ou à distance).
20 ouvriers locaux sur un chantier n'ont pas le même profil de besoin que 3 experts européens, même sur le même chantier.

RÈGLE SUR L'ACCESSIBILITÉ COMMERCIALE (distincte du besoin sûreté -- lis bien) :
Le besoin de sûreté et la possibilité réelle pour Amarante de vendre une prestation sont deux choses différentes. Un déploiement onusien dans une zone à très haut risque peut avoir un besoin sûreté élevé mais un marché verrouillé (sécurité gérée en interne, appels d'offres réservés à des prestataires déjà référencés). Une entreprise privée internationale sur un appel ouvert est généralement plus accessible. Signaux d'accessibilité facile : appel d'offres ouvert, mission internationale classique, pas de mention de prestataire de sécurité déjà intégré. Signaux d'accessibilité difficile : marché réservé, acheteur étatique ou militaire, sécurité visiblement gérée en interne ou par un dispositif existant.

RÈGLE SUR LES PROFILS D'ACTEURS PROBABLES :
Ne cite JAMAIS le nom d'une entreprise réelle, même si tu penses la connaître : à ce stade (avant attribution), aucune certitude n'est possible sur qui répondra, et un nom incorrect afficherait une fausse information comme un fait vérifié. Décris uniquement des PROFILS de type d'acteur (ex: "bureau d'études en ingénierie hydraulique", "consortium d'ingénierie européen", "opérateur industriel local avec appui technique étranger").

Le texte peut être dans n'importe quelle langue. Raisonne en anglais pour la précision, mais cite les indices dans leur langue d'origine.

Réponds UNIQUEMENT en JSON valide, sans texte avant ni après, sans balises Markdown, et SANS AUCUN COMMENTAIRE ni remarque entre parenthèses à l'intérieur des valeurs JSON (ex: ne jamais écrire "Kpalimé" (localisation spécifique) -- une chaîne JSON doit être une chaîne et rien d'autre). Si une précision est nécessaire, intègre-la dans le texte de la chaîne elle-même.

Schéma de sortie :
{{
  "deploiement_terrain_reel": true | false,
  "type_mobilite": "aucune | capitale | multi_sites | chantier | terrain_isole | frontiere",
  "profil_personnes_exposees": "expert_international | executive | technicien | ouvrier_local | aucun",
  "securite_existante": "aucune | interne_client | prestataire_tiers | inconnu",
  "indices_deploiement": ["courtes citations textuelles"],
  "type_activite": "assistance_technique | supervision_chantier | etude_terrain | fourniture_equipement | formation | autre",
  "type_client": "bailleur_donateur | institution_ue_onu | etat_administration_locale | entreprise_privee | autre",
  "duree_estimee": "courte_ponctuelle | longue_ou_residente | indetermine",
  "accessibilite_commerciale": "facile | moyenne | difficile",
  "profils_acteurs_probables": ["types de profils, JAMAIS de noms d'entreprises reelles"],
  "besoin_securite_operationnel_probable": true | false,
  "niveau_opportunite_amarante": "fort | moyen | faible",
  "justification": "une à deux phrases, en lien avec un besoin opérationnel concret (escorte, CPO, véhicule sécurisé), pas un conseil générique",
  "confiance": 0.0 à 1.0
}}

Avis à analyser :
Acheteur : {acheteur}
Pays acheteur (code, à ne pas sur-interpréter seul) : {pays_acheteur}
Pays d'exécution : {pays_execution}
Titre : {titre}
Codes CPV : {cpv}
Description (source la plus riche quand elle est présente : conditions d'exécution, mobilité terrain, profils déployés ; peut être vide, dans ce cas raisonne sur le titre et les CPV) : {description}
"""


# ===========================================================================
# PARTIE 3 -- COLLECTE (Sprint 1, logique inchangee)
# ===========================================================================

def construire_requete():
    clause_cpv = " ".join(CODES_CPV)
    clause_pays = " ".join(CODES_PAYS_SUIVIS)
    query = "classification-cpv IN ({}) AND place-of-performance IN ({})".format(
        clause_cpv, clause_pays
    )
    return {
        "query": query,
        "fields": [
            "publication-number", "notice-title", "buyer-name",
            "buyer-country", "place-of-performance", "classification-cpv",
            "publication-date", "deadline", "notice-type",
        ],
        "page": 1,
        "limit": LIMITE_RESULTATS,
        "scope": "ACTIVE",
        "checkQuerySyntax": False,
        "paginationMode": "PAGE_NUMBER",
    }


MAX_PAGES = 5  # plafond de securite (jusqu'a 5 x LIMITE_RESULTATS resultats
                # par mode). Empeche une boucle infinie si la pagination
                # TED se comporte de facon inattendue, tout en couvrant un
                # volume largement suffisant pour la fenetre de jours visee.


_SESSION_ROBUSTE = None


def session_robuste():
    """V10 (point C) : session requests avec reessais automatiques et
    delai exponentiel sur les erreurs TRANSITOIRES. Couvre 429 (rate
    limit) et 529 (overloaded) cote Anthropic, plus 500/502/503/504 cote
    serveur (TED ou Anthropic).

    PIEGE EVITE : urllib3 ne reessaie PAS les requetes POST par defaut
    (allowed_methods exclut POST). Or les deux appels du pipeline (TED et
    Anthropic) sont des POST. Il faut donc autoriser POST explicitement,
    sinon les retries seraient silencieusement inactifs -- defaut des
    snippets generiques copies tels quels.

    Les erreurs NON transitoires (ex: 401 cle invalide) ne sont pas dans
    la liste : elles echouent immediatement, sans 4 tentatives inutiles,
    et restent attrapees par le try/except existant de chaque appelant."""
    global _SESSION_ROBUSTE
    if _SESSION_ROBUSTE is not None:
        return _SESSION_ROBUSTE
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry
    retry = Retry(
        total=4,
        connect=3,
        read=3,
        backoff_factor=1.0,  # delais ~1s, 2s, 4s, 8s
        status_forcelist=[429, 500, 502, 503, 504, 529],
        allowed_methods=frozenset(["GET", "POST"]),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adaptateur = HTTPAdapter(max_retries=retry)
    session.mount("https://", adaptateur)
    session.mount("http://", adaptateur)
    _SESSION_ROBUSTE = session
    return session


def interroger_ted(corps_requete=None, max_pages=MAX_PAGES):
    """Recupere TOUTES les pages disponibles (jusqu'a max_pages), pas
    seulement la premiere. CORRECTION (observe en conditions reelles) :
    avec l'elargissement des pays/CPV, Mode A et Mode B ont tous les deux
    renvoye exactement LIMITE_RESULTATS avis bruts -- signe certain qu'on
    plafonnait sur la premiere page et qu'il existe probablement des avis
    plus anciens jamais vus. Boucle jusqu'a ce qu'une page renvoie moins
    que LIMITE_RESULTATS (derniere page atteinte) ou jusqu'au plafond de
    securite."""
    corps_base = corps_requete or construire_requete()
    global _NIVEAU_ENRICHISSEMENT
    # Jeu de champs de base "pur" (sans aucun champ optionnel deja ajoute),
    # pour reconstruire proprement quel que soit l'etat de corps_base.
    champs_base = [c for c in corps_base.get("fields", []) if c not in CHAMPS_OPTIONNELS_TED]
    limite = corps_base.get("limit", LIMITE_RESULTATS)

    def corps_pour(niveau, page):
        champs = list(champs_base)
        if niveau == "complet":
            champs = champs + CHAMPS_VALEUR_TED + CHAMPS_DESCRIPTION_TED
        elif niveau == "desc":
            champs = champs + CHAMPS_DESCRIPTION_TED
        # niveau "base" : champs de base seuls
        c = dict(corps_base)
        c["fields"] = champs
        c["page"] = page
        return c

    tous_resultats = []
    for page in range(1, max_pages + 1):
        if _NIVEAU_ENRICHISSEMENT is None:
            # Premier appel reel : on degrade progressivement (complet ->
            # desc -> base) jusqu'a ce que TED accepte, puis on VERROUILLE le
            # niveau trouve pour toutes les pages suivantes et le Mode B.
            # Decouple valeur et description : si seule la valeur est
            # refusee, on garde la description.
            reponse = None
            for niveau in ("complet", "desc", "base"):
                try:
                    reponse = session_robuste().post(TED_ENDPOINT, json=corps_pour(niveau, page), timeout=30)
                    reponse.raise_for_status()
                    _NIVEAU_ENRICHISSEMENT = niveau
                    if niveau == "desc":
                        print("  (info) Champs valeur refuses par TED -- description "
                              "conservee, valeur estimee ignoree.")
                    elif niveau == "base":
                        print("  (info) Champs valeur ET description refuses par TED "
                              "-- analyse sur le titre seul (comme avant la V12).")
                    break
                except requests.exceptions.HTTPError as e:
                    statut = getattr(getattr(e, "response", None), "status_code", None)
                    if statut == 400:
                        continue  # on tente le niveau suivant, avec moins de champs
                    raise
            if reponse is None:
                # Meme le jeu de base echoue : ce n'est pas un probleme de champ.
                raise RuntimeError("Requete TED en echec meme sans champ optionnel.")
        else:
            reponse = session_robuste().post(
                TED_ENDPOINT, json=corps_pour(_NIVEAU_ENRICHISSEMENT, page), timeout=30
            )
            reponse.raise_for_status()
        corps = reponse.json()

        resultats_page = None
        for cle in ("notices", "results", "items"):
            if cle in corps and isinstance(corps[cle], list):
                resultats_page = corps[cle]
                break
        if resultats_page is None:
            print("ATTENTION : cle de reponse TED introuvable (page {}). Cles presentes : {}".format(
                page, list(corps.keys())
            ))
            break

        tous_resultats.extend(resultats_page)
        if len(resultats_page) < limite:
            break  # derniere page atteinte, pas la peine de continuer
        time.sleep(0.3)
    else:
        print("ATTENTION : plafond de {} pages atteint ({} avis recuperes). "
              "Des avis plus anciens existent peut-etre encore -- augmenter "
              "MAX_PAGES si ce message apparait regulierement.".format(
                  max_pages, len(tous_resultats)
              ))

    return tous_resultats


def extraire_texte(valeur):
    """Coerce n'importe quel format de champ TED (chaine, dict
    multilingue, liste) en une chaine simple."""
    if valeur is None:
        return ""
    if isinstance(valeur, str):
        return valeur
    if isinstance(valeur, dict):
        for cle_langue in ("eng", "en", "fra", "fr"):
            if cle_langue in valeur and valeur[cle_langue]:
                # CORRECTIF : on RECURSE au lieu de renvoyer brut. Certains
                # champs TED (ex: description-lot) sont des dicts dont la
                # valeur de langue est elle-meme une LISTE (plusieurs lots) ;
                # renvoyer cette liste cassait _nettoyer_html (attend une
                # chaine). extraire_texte garantit desormais une chaine.
                return extraire_texte(valeur[cle_langue])
        for v in valeur.values():
            if v:
                return extraire_texte(v)
        return ""
    if isinstance(valeur, list):
        for v in valeur:
            texte = extraire_texte(v)
            if texte:
                return texte
        return ""
    return str(valeur)


def extraire_valeur(avis):
    """V11 : extrait la valeur estimee du marche depuis un enregistrement
    TED, de facon DEFENSIVE car le format n'est pas garanti (nombre brut,
    chaine, dict {amount, currency}, liste de lots...). Renvoie une chaine
    lisible (ex: '850000 EUR') ou 'inconnu' si rien d'exploitable. Le
    champ est frequemment absent sur les avis d'assistance technique : un
    'inconnu' n'est donc PAS une erreur, c'est le cas le plus courant."""
    brut = None
    for cle in CHAMPS_VALEUR_TED + ["total-value", "estimated-value", "value"]:
        if isinstance(avis, dict) and avis.get(cle) not in (None, "", [], {}):
            brut = avis.get(cle)
            break
    if brut is None:
        return "inconnu"

    # Si liste de lots : on prend la somme des montants trouvables, sinon le max.
    if isinstance(brut, list):
        montants = []
        for element in brut:
            m = _montant_depuis(element)
            if m is not None:
                montants.append(m)
        if not montants:
            return "inconnu"
        return _formater_montant(sum(montants))

    montant = _montant_depuis(brut)
    if montant is None:
        # dernier recours : on renvoie la representation texte brute, tronquee
        texte = extraire_texte(brut).strip()
        return texte[:40] if texte else "inconnu"
    devise = _devise_depuis(brut)
    return _formater_montant(montant, devise)


def _montant_depuis(valeur):
    """Tente d'extraire un nombre d'une valeur de format inconnu."""
    if isinstance(valeur, (int, float)):
        return float(valeur)
    if isinstance(valeur, str):
        nettoye = valeur.replace(" ", "").replace(",", "").strip()
        try:
            return float(nettoye)
        except ValueError:
            return None
    if isinstance(valeur, dict):
        for cle in ("amount", "value", "total", "estimated-value", "total-value"):
            if cle in valeur and valeur[cle] not in (None, ""):
                return _montant_depuis(valeur[cle])
    return None


def _devise_depuis(valeur):
    if isinstance(valeur, dict):
        for cle in ("currency", "cur", "devise"):
            if valeur.get(cle):
                return str(valeur[cle])
    return "EUR"  # defaut raisonnable pour des marches publies au Journal UE


def _formater_montant(montant, devise="EUR"):
    try:
        return "{:,.0f} {}".format(float(montant), devise).replace(",", " ")
    except (ValueError, TypeError):
        return "inconnu"


def _nettoyer_html(texte):
    """V13 : retire les balises HTML residuelles et decode les entites
    (&amp;, &eacute;, &#39;...) que TED laisse parfois dans description-proc.
    Sans dependance externe (pas de BeautifulSoup) : une regex suffit pour
    ce besoin simple, et on evite d'installer une lib lourde sur Colab.
    Objectif : ne pas gaspiller de tokens (donc de budget) en balises."""
    if not texte:
        return ""
    sans_balises = re.sub(r"<[^>]+>", " ", texte)   # <p>, <ul>, <br/>...
    decode = html.unescape(sans_balises)             # entites -> caracteres
    return re.sub(r"\s+", " ", decode).strip()        # espaces multiples -> un seul


def _morceaux_texte(valeur):
    """Renvoie une LISTE de chaines a partir d'un champ TED de format
    inconnu (chaine, liste, dict multilingue dont la valeur peut etre une
    liste de lots). Contrairement a extraire_texte qui renvoie UNE seule
    chaine (la 1re trouvee), on collecte ici TOUS les morceaux : utile pour
    la description, ou plusieurs lots coexistent et ou ne garder que le
    premier perdrait de l'information."""
    if valeur is None:
        return []
    if isinstance(valeur, str):
        return [valeur]
    if isinstance(valeur, list):
        out = []
        for v in valeur:
            out.extend(_morceaux_texte(v))
        return out
    if isinstance(valeur, dict):
        for cle in ("eng", "en", "fra", "fr"):
            if cle in valeur and valeur[cle]:
                return _morceaux_texte(valeur[cle])
        for v in valeur.values():
            if v:
                return _morceaux_texte(v)
        return []
    return [str(valeur)]


def extraire_description(avis):
    """V12 : assemble la description procedure + lots en un texte unique,
    coerce tout format TED (chaine, dict multilingue, liste de lots) et
    tronque a MAX_CARACTERES_DESCRIPTION pour borner le cout LLM. Renvoie
    une chaine vide si rien d'exploitable (notice ancien schema, ou avis
    sans description) : l'appelant retombe alors sur le titre seul.
    V13 : nettoyage HTML applique a chaque morceau avant assemblage.
    V14 : via _morceaux_texte, capture TOUS les lots (et non plus le seul
    premier) et gere les dicts multilingues dont la valeur est une liste,
    qui faisaient planter le parsing en conditions reelles."""
    morceaux = []
    for cle in CHAMPS_DESCRIPTION_TED:
        brut = avis.get(cle) if isinstance(avis, dict) else None
        if not brut:
            continue
        for texte in _morceaux_texte(brut):
            texte = _nettoyer_html(texte)
            if texte:
                morceaux.append(texte)

    # Dedoublonnage simple (description-proc reprend parfois un lot a l'identique)
    vus, uniques = set(), []
    for m in morceaux:
        cle = m[:120]
        if cle not in vus:
            vus.add(cle)
            uniques.append(m)

    description = " | ".join(uniques).strip()
    if len(description) > MAX_CARACTERES_DESCRIPTION:
        description = description[:MAX_CARACTERES_DESCRIPTION].rstrip() + " [...]"
    return description


def extraire_codes(valeur):
    if valeur is None:
        return []
    if isinstance(valeur, str):
        return [valeur]
    if isinstance(valeur, dict):
        for cle in ("code", "value", "id"):
            if cle in valeur:
                return [valeur[cle]]
        return []
    if isinstance(valeur, list):
        codes = []
        for item in valeur:
            codes.extend(extraire_codes(item))
        return codes
    return [str(valeur)]


def avis_correspond(avis):
    """Validation client-side, independante du filtrage serveur.

    V8 : admission CPV a trois niveaux (la division 75 etait admise
    entierement alors que seuls deux codes precis la justifiaient --
    corrige). Codes precis (CODES_PRECIS_TOUJOURS_ADMIS) admis sans
    condition. Divisions deja prouvees sur des runs reels
    (DIVISIONS_CPV_LARGEMENT_ADMISES) admises sur la seule base de la
    division. Divisions larges et generiques (CONDITIONNELLES, ex:
    conseil en gestion, administration publique) admises SEULEMENT si
    le titre contient un signal explicite de deploiement terrain --
    sinon elles ouvriraient du conseil/administration generique, le
    meme risque que "maitrise d'oeuvre" sur BOAMP."""
    cpv_recus = extraire_codes(avis.get("classification-cpv"))
    pays_recus = extraire_codes(avis.get("place-of-performance")) or extraire_codes(avis.get("buyer-country"))
    pays_ok = any(p in CODES_PAYS_SUIVIS for p in pays_recus)
    if not pays_ok:
        return False
    if not cpv_recus:
        return True

    if set(cpv_recus) & CODES_PRECIS_TOUJOURS_ADMIS:
        return True

    divisions_recues = {c[:2] for c in cpv_recus}
    if divisions_recues & DIVISIONS_CPV_LARGEMENT_ADMISES:
        return True

    if divisions_recues & DIVISIONS_CPV_CONDITIONNELLES:
        titre = extraire_texte(avis.get("notice-title")).lower()
        return any(mot in titre for mot in MOTS_CLES_DEPLOIEMENT_TERRAIN)

    return False


def acheteur_etranger(avis):
    """Signal mecanique (limite -- voir le prompt LLM qui corrige ce que
    cet indicateur rate, ex: avis Jordanie/Commission europeenne)."""
    pays_exec = set(extraire_codes(avis.get("place-of-performance")))
    pays_acheteur = set(extraire_codes(avis.get("buyer-country")))
    if not pays_exec or not pays_acheteur:
        return "inconnu"
    return "oui" if not (pays_acheteur & pays_exec) else "non"


def normaliser(avis, pays_detecte_titre=None):
    """pays_detecte_titre : ancien parametre du Mode B (retire). Conserve
    pour compatibilite du schema de sortie ; en pratique toujours None,
    donc source_mode_b reste False."""
    cpv = extraire_codes(avis.get("classification-cpv"))
    pays_exec = extraire_codes(avis.get("place-of-performance"))
    pays_ach = extraire_codes(avis.get("buyer-country"))
    pub_number = extraire_texte(avis.get("publication-number"))
    # CORRECTION (revue critique) : si place-of-performance est vide, le
    # repli sur pays_ach suppose silencieusement que le pays de l'acheteur
    # = le pays d'execution -- faux par construction dans les cas memes
    # qu'on cible (un bailleur europeen qui agit POUR un pays tiers). On
    # garde le repli (mieux que rien), mais on signale qu'il a ete utilise.
    if pays_detecte_titre:
        pays_exec_affiche = pays_detecte_titre
        incertitude_pays = True
    else:
        pays_exec_affiche = ", ".join(pays_exec) or ", ".join(pays_ach)
        incertitude_pays = not pays_exec
    return {
        "publication_number": pub_number,
        "titre": extraire_texte(avis.get("notice-title"))[:300],
        "acheteur": extraire_texte(avis.get("buyer-name")),
        "pays_acheteur": ", ".join(pays_ach),
        "pays_execution": pays_exec_affiche,
        "pays_execution_incertitude": incertitude_pays,
        "acheteur_etranger": acheteur_etranger(avis),
        "cpv": ", ".join(cpv),
        "date_publication": extraire_texte(avis.get("publication-date")),
        "deadline": extraire_texte(avis.get("deadline")),
        "valeur_estimee": extraire_valeur(avis),
        "description": extraire_description(avis),
        "source_mode_b": bool(pays_detecte_titre),
        "lien_avis": "https://ted.europa.eu/en/notice/{}/html".format(pub_number) if pub_number else "",
    }


def cle_quasi_doublon(avis_normalise):
    """Cle de repli pour detecter les republications (pas de champ de
    procedure TED confirme). Titre COMPLET (pas tronque) : une
    troncature courte avait fusionne a tort deux marches routiers
    moldaves distincts en conditions reelles."""
    return (
        extraire_texte(avis_normalise.get("acheteur")).strip().lower(),
        extraire_texte(avis_normalise.get("pays_execution")).strip().lower(),
        extraire_texte(avis_normalise.get("titre")).strip().lower(),
    )


def dedupliquer_quasi_doublons(avis_normalises):
    par_cle = {}
    for avis in avis_normalises:
        cle = cle_quasi_doublon(avis)
        existant = par_cle.get(cle)
        date_avis = extraire_texte(avis.get("date_publication"))
        date_existant = extraire_texte(existant.get("date_publication")) if existant else ""
        if existant is None or date_avis > date_existant:
            par_cle[cle] = avis
    return list(par_cle.values()), len(avis_normalises) - len(par_cle)


# ===========================================================================
# PARTIE 4 -- EXTRACTION LLM ET SCORE (Sprint 2)
# ===========================================================================

def appeler_modele(prompt, modele=None):
    """Appel brut au modele. Renvoie le texte de la reponse, ou None
    (avec message d'erreur affiche) en cas d'echec reseau/auth."""
    cle_api = os.environ.get("ANTHROPIC_API_KEY")
    if not cle_api:
        print("ERREUR : la variable d'environnement ANTHROPIC_API_KEY n'est pas definie.")
        return None

    headers = {
        "x-api-key": cle_api,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    corps = {
        "model": modele or MODELE,
        "max_tokens": 1000,  # 700 coupait parfois une reparation JSON qui doit reecrire tout l'objet
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    STATS_LLM["appels"] += 1
    try:
        reponse = session_robuste().post(ANTHROPIC_ENDPOINT, headers=headers, json=corps, timeout=30)
        reponse.raise_for_status()
    except requests.exceptions.Timeout:
        # V13 : message explicite. Timeout etait deja attrape par le bloc
        # RequestException ci-dessous (classe parente), mais un signal clair
        # aide a distinguer une lenteur serveur Anthropic d'une vraie erreur
        # d'API. session_robuste a deja retente avant d'en arriver la.
        print("  Timeout API Anthropic (>30s, apres reessais) -- avis ignore "
              "pour ce run, le pipeline continue.")
        _marquer_echec_llm("timeout")
        return None
    except requests.exceptions.RequestException as e:
        # Le corps de la reponse contient l'explication reelle d'Anthropic
        # (ex: "invalid x-api-key"), bien plus utile que le seul code HTTP.
        detail = ""
        try:
            detail = reponse.text[:300]
        except NameError:
            pass
        print("  Erreur d'appel API : {}".format(e))
        if detail:
            print("  Detail renvoye par Anthropic : {}".format(detail))
        _marquer_echec_llm(detail or str(e))
        return None

    texte = reponse.json()["content"][0]["text"].strip()
    if texte.startswith("```"):
        texte = texte.split("```")[1]
        if texte.startswith("json"):
            texte = texte[4:]
        texte = texte.strip()
    return texte


def reparer_json(texte_casse, modele=None):
    """Demande au modele de corriger UNIQUEMENT la syntaxe JSON d'une
    reponse precedente invalide, sans refaire l'analyse de fond. Moins
    cher et plus fiable qu'une nouvelle tentative complete depuis zero
    (le contenu de l'analyse etait probablement deja correct -- seule
    la syntaxe avait un defaut, ex: commentaire entre parentheses glisse
    dans une valeur, cas observe sur l'avis Togo)."""
    prompt_reparation = (
        "Le texte suivant devait etre un objet JSON valide mais contient "
        "une erreur de syntaxe (probablement un commentaire ou une "
        "remarque entre parentheses glissee a l'interieur d'une valeur). "
        "Corrige UNIQUEMENT la syntaxe JSON, sans modifier le contenu ni "
        "refaire l'analyse. Renvoie uniquement le JSON corrige, rien "
        "d'autre, sans balises Markdown.\n\n" + texte_casse
    )
    return appeler_modele(prompt_reparation, modele=modele)


# ===========================================================================
# NORMALISATION SECURITE (P3) : levier concurrentiel "deplacement"
# ---------------------------------------------------------------------------
# Le modele renseigne desormais l'enum 'securite_existante' (aucune /
# interne_client / prestataire_tiers / inconnu) au lieu d'un booleen. On en
# derive le booleen historique 'securite_existante_detectee' AVEC UNE SEMANTIQUE
# PLUS FINE : True UNIQUEMENT quand la securite est geree en interne par le
# client (marche reellement ferme). 'prestataire_tiers' n'est PLUS supprime --
# c'est une opportunite de conquete : il remonte a plein score et est marque
# [DEPLACEMENT CONCURRENT] dans la justification. Comme scores, action et
# collecteurs lisent tous le booleen derive, le correctif s'applique a TOUT le
# pipeline (TED + BM + AFDB + ADB + EBRD + ReliefWeb) sans toucher aux
# collecteurs ni au schema des onglets.
MARQUEUR_DEPLACEMENT = "[DÉPLACEMENT CONCURRENT]"
_SECU_VALEURS = {"aucune", "interne_client", "prestataire_tiers", "inconnu"}


def normaliser_securite(extraction):
    """Derive le booleen historique de l'enum et marque les deplacements.
    Idempotent (ne double pas le marqueur), tolerant au repli (ancien schema
    booleen) et au None. Fonction PURE (testable)."""
    if not isinstance(extraction, dict):
        return extraction
    brut = extraction.get("securite_existante")
    if isinstance(brut, str) and brut.strip():
        enum = brut.strip().lower()
        if enum not in _SECU_VALEURS:
            enum = "inconnu"
        detectee = (enum == "interne_client")
    else:
        # Repli transition : le modele n'a renvoye que l'ancien booleen.
        detectee = bool(extraction.get("securite_existante_detectee"))
        enum = "interne_client" if detectee else "inconnu"
    extraction["securite_existante"] = enum
    extraction["securite_existante_detectee"] = detectee
    if enum == "prestataire_tiers":
        just = str(extraction.get("justification") or "").strip()
        if not just.startswith(MARQUEUR_DEPLACEMENT):
            extraction["justification"] = (MARQUEUR_DEPLACEMENT + " " + just).strip()
    return extraction


def appeler_llm(avis, modele=None):
    """Appelle le modele pour extraire les faits structures d'un avis.
    Renvoie le dict JSON parse, ou None en cas d'echec definitif.
    modele=None utilise le modele rapide par defaut (MODELE) ; passer
    MODELE_RAFFINEMENT pour l'escalade sur les cas qui le justifient.

    Trois niveaux de recuperation en cas de JSON invalide, du moins cher
    au plus cher (revue critique : un parseur strict + nettoyage gratuit
    doit toujours passer avant un appel LLM de reparation, jamais l'un
    sans l'autre) :
    1. json.loads direct (deja nettoye des balises Markdown en amont).
    2. extraction de la sous-chaine entre la premiere '{' et la derniere
       '}' (gratuit, couvre le cas d'un prefixe/suffixe de texte parasite
       autour d'un JSON par ailleurs valide).
    3. uniquement si les deux precedents echouent, reparation cible par
       le modele (reparer_json)."""
    prompt = PROMPT_TEMPLATE.format(
        acheteur=avis.get("acheteur", ""),
        pays_acheteur=avis.get("pays_acheteur", ""),
        pays_execution=avis.get("pays_execution", ""),
        titre=avis.get("titre", ""),
        cpv=avis.get("cpv", ""),
        description=avis.get("description", "") or "(non fournie par l'avis)",
    )
    texte = appeler_modele(prompt, modele=modele)
    if texte is None:
        return None

    try:
        return normaliser_securite(json.loads(texte))
    except json.JSONDecodeError:
        pass

    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin != -1 and fin > debut:
        try:
            return normaliser_securite(json.loads(texte[debut:fin + 1]))
        except json.JSONDecodeError:
            pass

    print("  JSON invalide pour '{}', tentative de reparation cible (Sonnet)...".format(avis.get("titre", "")[:50]))
    # V10 (point B) : on force MODELE_RAFFINEMENT (Sonnet) pour la rustine,
    # quel que soit le modele d'origine. Si Haiku a casse la syntaxe JSON a
    # cause de la complexite du texte source, lui redemander de se corriger
    # rate souvent une seconde fois ; Sonnet restructure proprement du
    # premier coup. Cout negligeable : la reparation ne se declenche que si
    # les deux niveaux de parsing gratuits ont deja echoue (cas rare).
    texte_repare = reparer_json(texte, modele=MODELE_RAFFINEMENT)
    if texte_repare is None:
        return None
    try:
        return normaliser_securite(json.loads(texte_repare))
    except json.JSONDecodeError:
        print("  Reparation echouee pour '{}'. Reponse brute : {}".format(
            avis.get("titre", "")[:50], texte_repare[:200]
        ))
        return None


# Poids "personnes exposees" pour le score SURETE (V7, suite a une revue
# critique). REMPLACE l'ancien poids base sur type_client
# (POIDS_SURETE_PRESENCE_ETRANGERE) : le type de client dit QUI PAIE, pas
# QUI EST PHYSIQUEMENT SUR LE TERRAIN -- une entreprise privee peut
# deployer surtout du personnel local avec un ou deux superviseurs
# etrangers, alors qu'un bailleur peut envoyer une equipe d'experts
# internationaux nombreuse. Garder les deux poids bases sur type_client
# aurait recree la meme incoherence que l'ancien doublon
# bailleur_ou_institution_internationale / type_client (deja corrige a
# une revue precedente) : deux champs qui repondent a la meme question
# finissent par se contredire. Le signal direct (qui est expose) est
# desormais demande explicitement au modele plutot que deduit du payeur.
POIDS_SURETE_PROFIL_PERSONNES = {
    "expert_international": 1.0,
    "executive": 1.0,
    "technicien": 0.6,
    "ouvrier_local": 0.15,
    "aucun": 0.0,
}

# Poids du type de client pour le score COMMERCIAL (distinct du poids
# sûreté). Observation cle (revue critique V6) : un bailleur bilateral
# comme l'AFD est souvent PLUS accessible pour une societe francaise
# qu'un marche multilateral ONU/UE (listes de prestataires deja
# referencees, procedures plus rigides). Le score sûreté, lui, ne fait
# pas cette distinction : le besoin physique est le meme quel que soit
# le bailleur.
POIDS_CLIENT_COMMERCIAL = {
    "bailleur_donateur": 4.0,
    "entreprise_privee": 3.5,
    "autre": 2.0,
    "institution_ue_onu": 2.0,
    "etat_administration_locale": 0.5,
}

TYPE_MOBILITE_POINTS = {
    "frontiere": 5.0,
    "terrain_isole": 4.5,
    "chantier": 3.5,
    "multi_sites": 3.0,
    "capitale": 0.5,
    "aucune": 0.0,
}


def calculer_scores(avis, extraction):
    """Renvoie (score_surete, score_commercial, score_final), tous sur
    10, calcules en code a partir des faits extraits par le modele.

    V6 (suite a une revue critique) : SEPARATION des deux questions que
    le score precedent melangeait --
    - score_surete : un deploiement physique necessite-t-il probablement
      une protection ? (independant de qui paierait)
    - score_commercial : Amarante a-t-elle une chance realiste de
      vendre cette prestation ? (independant du niveau de risque)
    Exemple qui a motive la separation : une mission ONU au Mali peut
    avoir un besoin sûreté tres eleve mais un marche verrouille (securite
    geree en interne, prestataires deja referencees) ; une entreprise
    miniere privee peut avoir un besoin comparable ET un marche
    accessible. Un score unique fusionne ces deux cas tres differents.

    type_mobilite (remplace l'ancien mobilite_terrain_probable +
    zone_geographique, qui se recoupaient) est desormais le signal
    principal d'exposition terrain, avec un barreme dedie
    (TYPE_MOBILITE_POINTS) plutot qu'un simple booleen.

    securite_existante_detectee fait baisser LES DEUX scores : si une
    prestation de securite est deja en place ou prevue par un tiers, le
    besoin ET l'opportunite commerciale s'effondrent tous les deux.

    V7 (suite a une nouvelle revue critique) :
    - Le poids "presence etrangere" du score surete vient desormais de
      profil_personnes_exposees, pas de type_client : QUI PAIE et QUI EST
      PHYSIQUEMENT EXPOSE sont deux questions differentes (une entreprise
      privee peut deployer surtout du personnel local avec un ou deux
      superviseurs etrangers).
    - score_final repondere a 50/50 (etait 60/40 surete) : Amarante vend
      une capacite d'intervention, pas seulement un niveau de risque -- un
      lead a tres haut risque mais totalement inaccessible reste peu utile."""
    if extraction is None:
        return 0.0, 0.0, 0.0

    pays_exec = (avis.get("pays_execution") or "").split(",")[0].strip()
    cpv_divisions = {c.strip()[:2] for c in (avis.get("cpv") or "").split(",") if c.strip()}
    infra_critique = bool(cpv_divisions & DIVISIONS_INFRASTRUCTURE_CRITIQUE)
    securite_existante = bool(extraction.get("securite_existante_detectee"))

    # --- Score surete : besoin physique de protection ---
    # Le poids ici repond a "qui est physiquement expose", pas "qui paie"
    # (cette deuxieme question est le score commercial, plus bas).
    # Applique uniquement sur l'exposition liee a la presence humaine --
    # pas sur la zone ni l'infrastructure, des facteurs d'environnement
    # independants de qui est deploye.
    exposition_terrain = 0.0
    if extraction.get("deploiement_terrain_reel"):
        exposition_terrain += 2.0
    exposition_terrain += TYPE_MOBILITE_POINTS.get(extraction.get("type_mobilite"), 0.0)
    poids_personnes = POIDS_SURETE_PROFIL_PERSONNES.get(extraction.get("profil_personnes_exposees"), 0.5)

    surete = exposition_terrain * poids_personnes
    surete += MULTIPLICATEUR_ZONE.get(pays_exec, 0.2) * 1.5  # 0.3 a 1.5 selon la zone
    if infra_critique:
        surete += 1.0
    if securite_existante:
        surete -= 3.0
    surete = max(0.0, min(surete, 10.0))

    # --- Score commercial : probabilite de vente realiste ---
    commercial = POIDS_CLIENT_COMMERCIAL.get(extraction.get("type_client"), 2.0)
    commercial += {"facile": 3.0, "moyenne": 1.5, "difficile": 0.0}.get(
        extraction.get("accessibilite_commerciale"), 1.5
    )
    if extraction.get("duree_estimee") == "longue_ou_residente":
        commercial += 1.5
    elif extraction.get("duree_estimee") == "indetermine":
        commercial += 0.5
    if infra_critique:
        commercial += 1.0  # plus gros projet, budget plus probable
    if securite_existante:
        commercial -= 2.0  # un tiers est probablement deja en place
    commercial = max(0.0, min(commercial, 10.0))

    score_final = round(min(surete * 0.5 + commercial * 0.5, 10.0), 1)

    # --- V9 : garde-fou anti faux-FORT ---
    # Un score commercial eleve ne doit pas faire passer "FORT" un avis
    # sans besoin surete reel (ex: eau/energie AFD en pays stable, surete
    # moyenne mais commercial 7.0). Deux coupe-circuits, chacun suffisant :
    # surete pure sous le seuil, OU opportunite jugee faible par le modele.
    # On plafonne alors juste sous SEUIL_ALERTE : l'avis peut rester "a
    # surveiller", jamais "fort". Tout en aval (etiquette, action
    # recommandee, tri du Sheet) lit score_final, donc herite du plafond
    # sans modification. Compromis assume : le critere opportunite repose
    # sur un jugement du modele, pas un fait brut -- mais le cout d'une
    # retrogradation a tort reste faible (l'avis reste affiche en "a
    # surveiller", il n'est pas supprime).
    surete_insuffisante = surete < SEUIL_SURETE_POUR_FORT
    opportunite_faible = extraction.get("niveau_opportunite_amarante") == "faible"
    if (surete_insuffisante or opportunite_faible) and score_final >= SEUIL_ALERTE:
        score_final = round(SEUIL_ALERTE - 0.1, 1)  # 5.9 : plafonne sous FORT

    return round(surete, 1), round(commercial, 1), score_final


SEUIL_PLANCHER_SURETE = 3.0  # sous ce seuil, aucune action n'est recommandee
# quel que soit le score commercial -- voir calculer_action_recommandee.


def calculer_action_recommandee(score_final, extraction, surete=None):
    """Traduit le score en action concrete, calculee en code (meme
    philosophie que le reste : le LLM fournit les faits, le code applique
    la doctrine). Un score eleve avec une securite deja en place ou un
    marche tres difficile ne doit pas dire "contacter" malgre le chiffre.

    CORRECTION (revue critique) : le score final fusionne surete et
    commercial a 50/50, ce qui peut produire un score moyen meme avec un
    besoin sûreté quasi nul (ex: surete=0, commercial=9.5 -> final=4.75,
    au-dessus du seuil de surveillance) -- mathematiquement certain
    d'arriver un jour, meme si pas encore observe sur un run reel. Un
    marche sans aucun risque physique reel n'a rien a vendre pour
    Amarante, peu importe son accessibilite commerciale : coupe-circuit
    sur le score sûreté PUR, independant du score final fusionne."""
    if extraction is None:
        return "ignorer"
    if extraction.get("securite_existante_detectee"):
        return "ignorer"
    if surete is not None and surete < SEUIL_PLANCHER_SURETE:
        return "ignorer"
    if score_final < SEUIL_SURVEILLANCE:
        return "ignorer"
    if score_final >= SEUIL_ALERTE and extraction.get("accessibilite_commerciale") != "difficile":
        return "contacter"
    return "surveiller"


def calculer_fenetre_action(avis):
    """Categorise l'urgence commerciale a partir de la date limite deja
    collectee (deadline), sans appel LLM : c'est une donnee structuree,
    pas besoin de la faire rejuger. Un lead a 9/10 dont la date limite
    est demain est presque inutile a faire remonter comme prioritaire."""
    deadline_brut = (avis.get("deadline") or "")[:10]  # garde juste AAAA-MM-JJ
    if not deadline_brut:
        return "indetermine"
    try:
        date_limite = date.fromisoformat(deadline_brut)
    except ValueError:
        return "indetermine"
    jours_restants = (date_limite - date.today()).days
    if jours_restants < 0:
        return "indetermine"  # deadline passee : avis d'attribution ou perime
    if jours_restants <= 15:
        return "immediate"
    if jours_restants <= 60:
        return "court_terme"
    return "long_terme"


# ===========================================================================
# PARTIE 5BIS -- SORTIE GOOGLE SHEET (optionnelle)
# ===========================================================================
# Active si TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE sont definis dans
# l'environnement. Sinon, le script reste en mode console uniquement
# (comportement inchange). dependances : pip install gspread google-auth

NOM_ONGLET_SHEET = "ted_radar"

# Derniere colonne (statut_suivi) volontairement EXCLUE des mises a jour :
# c'est la colonne ou l'humain note "contacte"/"perdu"/"gagne", elle ne
# doit jamais etre ecrasee par un run automatique qui retombe sur le
# meme avis (rectificatif, ou simplement le meme avis encore actif).
COLONNES_SHEET = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "pays_acheteur",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "justification", "confiance",
    "modele", "raffine", "divergence", "source_mode_b",
    "pays_execution_incertitude", "publication_number", "lien_avis",
    "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_VALEUR_ESTIMEE = "valeur_estimee"   # V11
COLONNE_DATE_DETECTION = "date_detection"   # V11
# IMPORTANT : les deux colonnes V11 sont placees APRES statut_suivi, donc
# dans la zone PRESERVEE (hors de la plage de mise a jour A:<COLONNES_SHEET>).
# Choix de migration : ne decaler aucune colonne existante, pour qu'une
# feuille deja peuplee ne soit pas desalignee (statut_suivi garde sa
# position d'origine). Consequence assumee : ces deux colonnes sont
# ecrites a l'INSERTION seulement, jamais reecrites sur un re-run. C'est
# exactement le comportement voulu pour date_detection (date de 1re
# detection, ne doit jamais changer) et acceptable pour la valeur estimee
# (statique pour un avis donne).
TOUTES_COLONNES_SHEET = COLONNES_SHEET + [
    COLONNE_STATUT_SUIVI, COLONNE_VALEUR_ESTIMEE, COLONNE_DATE_DETECTION
]


def lettre_colonne(index_1_base):
    """Convertit un numero de colonne (1, 2, 3...) en lettre A1 (A, B,
    ..., Z, AA, AB...), sans dependre de gspread (import differe)."""
    lettres = ""
    n = index_1_base
    while n > 0:
        n, reste = divmod(n - 1, 26)
        lettres = chr(65 + reste) + lettres
    return lettres


def ouvrir_feuille(sheet_id, fichier_compte_service):
    import gspread
    from google.oauth2.service_account import Credentials

    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_compte_service, scopes=portee)
    client = gspread.authorize(creds)
    classeur = client.open_by_key(sheet_id)
    try:
        feuille = classeur.worksheet(NOM_ONGLET_SHEET)
    except gspread.WorksheetNotFound:
        feuille = classeur.add_worksheet(
            title=NOM_ONGLET_SHEET, rows=2000, cols=len(TOUTES_COLONNES_SHEET)
        )
        feuille.append_row(TOUTES_COLONNES_SHEET)
        return feuille

    # V11 : auto-reparation de l'en-tete. Une feuille creee avant la V11
    # n'a pas les colonnes valeur_estimee / date_detection. Comme elles
    # sont ajoutees EN FIN (apres statut_suivi), reecrire la ligne 1 avec
    # l'en-tete complet n'affecte aucune donnee existante (positions 1..N
    # inchangees, nouvelles colonnes a la suite). Idempotent : on ne
    # reecrit que si une colonne manque.
    entetes = feuille.row_values(1)
    if COLONNE_DATE_DETECTION not in entetes or COLONNE_VALEUR_ESTIMEE not in entetes:
        feuille.update(values=[TOUTES_COLONNES_SHEET], range_name="A1")
    return feuille


def charger_index_publication(feuille):
    """{publication_number -> numero de ligne}, pour mettre a jour un
    avis deja vu plutot que de le dupliquer a chaque run."""
    valeurs = feuille.get_all_records()
    index = {}
    for numero_ligne, ligne in enumerate(valeurs, start=2):  # ligne 1 = entetes
        pub = ligne.get("publication_number", "")
        if pub:
            index[pub] = numero_ligne
    return index


def _publications_depuis_valeurs(valeurs, colonnes):
    """Ensemble des publication_number d'une grille brute (get_all_values), lus
    PAR POSITION selon l'ordre officiel `colonnes`. Robuste a un en-tete
    desaligne. Ignore une eventuelle ligne d'en-tete."""
    if not valeurs:
        return set()
    try:
        idx = colonnes.index("publication_number")
    except ValueError:
        return set()
    premiere = [str(c).strip() for c in valeurs[0]]
    debut = 1 if "publication_number" in premiere else 0
    nums = set()
    for row in valeurs[debut:]:
        if idx < len(row):
            pub = str(row[idx]).strip()
            if pub:
                nums.add(pub)
    return nums


def numeros_publication_existants(sheet_id, fichier_compte_service, nom_onglet, colonnes):
    """Memoire inter-runs : ensemble des publication_number deja presents dans un
    onglet. Permet de NE PAS reanalyser un avis deja traite (economie de tokens)
    ni de le re-ajouter en double. Lecture positionnelle (robuste a l'en-tete).
    Tolerant aux pannes : toute erreur ou absence de Sheet -> ensemble vide, on
    analyse alors tout, comme avant."""
    if not (sheet_id and fichier_compte_service):
        return set()
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(fichier_compte_service, scopes=portee)
        classeur = gspread.authorize(creds).open_by_key(sheet_id)
        try:
            feuille = classeur.worksheet(nom_onglet)
        except gspread.WorksheetNotFound:
            return set()
        return _publications_depuis_valeurs(feuille.get_all_values(), colonnes)
    except Exception as e:
        print("  (info) Lecture des avis deja analyses impossible ({}). On analyse tout.".format(e))
        return set()


def ligne_depuis_resultat(r):
    """Construit la ligne (hors statut_suivi) a partir d'un resultat de
    main(). Tout est converti en chaine pour eviter les soucis de type
    avec l'API Google Sheets."""
    avis, extraction = r["avis"], r["extraction"]
    modele_utilise = MODELE_RAFFINEMENT if r["raffine"] else MODELE
    valeurs = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"],
        "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": calculer_action_recommandee(r["score"], extraction, surete=r["surete"]),
        "fenetre_action": calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": extraction.get("niveau_opportunite_amarante") if extraction else "",
        "titre": avis.get("titre", ""),
        "acheteur": avis.get("acheteur", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "pays_acheteur": avis.get("pays_acheteur", ""),
        "type_client": extraction.get("type_client") if extraction else "",
        "type_mobilite": extraction.get("type_mobilite") if extraction else "",
        "profil_personnes_exposees": extraction.get("profil_personnes_exposees") if extraction else "",
        "duree_estimee": extraction.get("duree_estimee") if extraction else "",
        "accessibilite_commerciale": extraction.get("accessibilite_commerciale") if extraction else "",
        "securite_existante_detectee": extraction.get("securite_existante_detectee") if extraction else "",
        "profils_acteurs_probables": ", ".join(extraction.get("profils_acteurs_probables") or []) if extraction else "",
        "justification": extraction.get("justification") if extraction else "",
        "confiance": extraction.get("confiance") if extraction else "",
        "modele": modele_utilise,
        "raffine": r["raffine"],
        "divergence": r["divergence"],
        "source_mode_b": avis.get("source_mode_b", False),
        "pays_execution_incertitude": avis.get("pays_execution_incertitude", False),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(valeurs.get(c, "")) for c in COLONNES_SHEET]


def ecrire_resultats_dans_sheet(feuille, resultats):
    """Insere les nouveaux avis, met a jour les scores de ceux deja
    presents SANS toucher a statut_suivi (le suivi humain ne doit jamais
    etre efface par un run automatique qui retombe sur le meme avis).

    V10 (point A) : ecriture GROUPEE. Toutes les mises a jour partent en
    UN seul appel reseau (batch_update), tous les nouveaux avis en UN seul
    (append_rows). Avant, le script faisait un appel par ligne avec une
    pause de 0.3s : au-dela de ~40-50 avis dans une fenetre serree, on
    risquait l'erreur 429 (quota Sheets ~60 ecritures/minute/utilisateur)
    et on perdait ~N x 0.3s en pauses. Desormais c'est 2 appels au maximum,
    quel que soit le nombre d'avis. Bonus : supprime aussi le
    DeprecationWarning de feuille.update() (ordre des arguments)."""
    index = charger_index_publication(feuille)
    derniere_lettre = lettre_colonne(len(COLONNES_SHEET))

    maj_groupees = []       # mises a jour : liste de {range, values}
    nouvelles_lignes = []   # nouveaux avis : ajoutes en un bloc
    nb_nouveaux, nb_maj = 0, 0

    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne_valeurs = ligne_depuis_resultat(r)
        if pub and pub in index:
            num_ligne = index[pub]
            # On ecrit A:<derniere_colonne_donnees>, ce qui exclut volontairement
            # la colonne statut_suivi (placee juste apres) : jamais ecrasee.
            maj_groupees.append({
                "range": "A{0}:{1}{0}".format(num_ligne, derniere_lettre),
                "values": [ligne_valeurs],
            })
            nb_maj += 1
        else:
            # Tail preserve, dans l'ordre du schema : statut_suivi,
            # valeur_estimee, date_detection. statut_suivi initialise a
            # "nouveau" ; date_detection = aujourd'hui (1re detection, fige).
            valeur = str(r["avis"].get("valeur_estimee", "inconnu"))
            date_detection = date.today().isoformat()
            nouvelles_lignes.append(ligne_valeurs + ["nouveau", valeur, date_detection])
            nb_nouveaux += 1

    # Au maximum 2 appels reseau, independamment du volume d'avis.
    if maj_groupees:
        feuille.batch_update(maj_groupees)
    if nouvelles_lignes:
        feuille.append_rows(nouvelles_lignes, value_input_option="RAW")

    return nb_nouveaux, nb_maj


# ===========================================================================
# PARTIE 5 -- POINT D'ENTREE
# ===========================================================================

def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERREUR : ANTHROPIC_API_KEY n'est pas definie dans cette session.\n"
            "Execute d'abord, dans une cellule SEPAREE, AVANT cette cellule :\n"
            '    import os\n'
            '    os.environ["ANTHROPIC_API_KEY"] = "ta-cle-ici"\n'
            '    print("Cle definie :", bool(os.environ.get("ANTHROPIC_API_KEY")))\n'
            "Verifie que ce print affiche bien True avant de relancer ce script.\n"
            "Si le runtime Colab a ete redemarre depuis, la cle est perdue : "
            "il faut la redefinir."
        )
        return

    print("Etape 1/2 -- Collecte TED Mode A (filtre CPV + pays + dedup)...")
    avis_bruts = interroger_ted()
    pertinents = [a for a in avis_bruts if avis_correspond(a)]
    seuil = (date.today() - timedelta(days=NB_JOURS_FENETRE)).isoformat()
    recents = [a for a in pertinents if extraire_texte(a.get("publication-date")) >= seuil]
    avis_normalises = [normaliser(a) for a in recents]
    print("Mode A -- Bruts : {} | pertinents : {} | dans la fenetre : {}".format(
        len(avis_bruts), len(pertinents), len(recents)
    ))

    avis_normalises, nb_doublons = dedupliquer_quasi_doublons(avis_normalises)

    print("\nTotal combine apres dedup : {} avis".format(len(avis_normalises)))

    if not avis_normalises:
        print("Aucun avis a analyser. Rien a envoyer au modele.")
        return

    # Memoire inter-runs : on ne reanalyse pas un avis deja traite lors d'un run
    # precedent (economie de tokens + temps), ce qui evite aussi de le
    # re-ajouter en double dans le Sheet. Si pas de Sheet ou erreur de lecture,
    # numeros_publication_existants renvoie un ensemble vide -> on analyse tout.
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_compte_service = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    deja_vus = numeros_publication_existants(
        sheet_id, fichier_compte_service, NOM_ONGLET_SHEET, COLONNES_SHEET)
    if deja_vus:
        avant = len(avis_normalises)
        avis_normalises = [a for a in avis_normalises
                           if str(a.get("publication_number", "")).strip() not in deja_vus]
        print("Memoire : {} avis deja analyses (runs precedents) ignores, "
              "{} nouveau(x) a analyser.".format(avant - len(avis_normalises), len(avis_normalises)))
    if not avis_normalises:
        print("Aucun NOUVEL avis TED a analyser (tout deja vu). "
              "Le Sheet et le dashboard restent a jour.")
        return

    # V12 : controle rapide de l'enrichissement. Si ce compteur est a 0/N,
    # c'est que TED n'a pas renvoye de description (champs rejetes -- voir
    # message (info) plus haut -- ou notices a l'ancien schema). Le pipeline
    # fonctionne quand meme, mais sur le titre seul, comme avant la V12.
    nb_avec_desc = sum(1 for a in avis_normalises if a.get("description"))
    print("\nEnrichissement description : {}/{} avis ont une description exploitable.".format(
        nb_avec_desc, len(avis_normalises)
    ))

    print("\nEtape 2/2 -- Extraction LLM et score ({} avis, modele {})...\n".format(
        len(avis_normalises), MODELE
    ))

    resultats = []
    for i, avis in enumerate(avis_normalises, start=1):
        print("[{}/{}] {}...".format(i, len(avis_normalises), avis["titre"][:60]))
        extraction = appeler_llm(avis)
        surete, commercial, final = calculer_scores(avis, extraction)
        resultats.append({
            "avis": avis, "extraction": extraction,
            "surete_haiku": surete, "commercial_haiku": commercial, "final_haiku": final,
            "surete": surete, "commercial": commercial, "score": final,
            "raffine": False, "divergence": False,
        })
        time.sleep(0.5)

    # Escalade (cadrage section 7.3) : les cas qui le justifient sont
    # rejoues avec un modele plus capable. CORRECTION (revue critique) :
    # le premier jet remplacait automatiquement le resultat Haiku par
    # celui de Sonnet, au risque qu'une lecture plus prudente de Sonnet
    # enterre a tort un bon lead. On affiche desormais LES DEUX scores et
    # on signale l'ecart au lieu de trancher silencieusement dans un sens
    # ou l'autre -- une regle de tolerance arbitraire aurait pu tout
    # autant ignorer a tort une correction legitime de Sonnet (ex:
    # "securite deja prevue" repere uniquement au second passage).
    #
    # CRITERE D'ESCALADE AFFINE (V8, suite a une revue critique) : se
    # contenter de "score >= seuil de surveillance" devient cher a
    # grande echelle (sur 60-90 jours, beaucoup d'avis depasseront 4).
    # Trois conditions, chacune suffisante : score elevé (vraiment digne
    # d'interet), confiance faible (le cas ambigu merite davantage une
    # deuxieme lecture qu'un score moyen mais confiant), ou securite
    # deja detectee (la conclusion la plus consequente -- mieux vaut la
    # confirmer avec le modele le plus capable avant d'ecarter un lead).
    def merite_escalade(r):
        if r["extraction"] is None:
            return False
        if r["final_haiku"] >= 5:
            return True
        if r["extraction"].get("confiance", 1.0) < 0.7:
            return True
        if r["extraction"].get("securite_existante_detectee"):
            return True
        return False

    a_escalader = [r for r in resultats if merite_escalade(r)]
    if a_escalader:
        print("\n{} avis remplissent un critere d'escalade (score, confiance ou securite detectee), vers {}...\n".format(
            len(a_escalader), MODELE_RAFFINEMENT
        ))
        for i, r in enumerate(a_escalader, start=1):
            print("[{}/{}] Raffinement : {}...".format(i, len(a_escalader), r["avis"]["titre"][:60]))
            extraction_raffinee = appeler_llm(r["avis"], modele=MODELE_RAFFINEMENT)
            if extraction_raffinee is not None:
                s, c, f = calculer_scores(r["avis"], extraction_raffinee)
                r["extraction"] = extraction_raffinee  # affichage = la lecture la plus capable
                r["surete"], r["commercial"], r["score"] = s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0
            time.sleep(0.5)

    resultats.sort(key=lambda r: r["score"], reverse=True)

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_compte_service = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sheet_id and fichier_compte_service:
        print("\nEcriture dans le Google Sheet ({} avis)...".format(len(resultats)))
        try:
            feuille = ouvrir_feuille(sheet_id, fichier_compte_service)
            nb_nouveaux, nb_maj = ecrire_resultats_dans_sheet(feuille, resultats)
            print("-> {} nouvel(s) avis ajoute(s), {} mis a jour (statut_suivi jamais touche).".format(
                nb_nouveaux, nb_maj
            ))
        except Exception as e:
            print("ERREUR lors de l'ecriture dans le Sheet : {}".format(e))
            print("La console continue normalement ci-dessous.")
    else:
        print(
            "\n(Pas de Sheet configure : definis TED_SHEET_ID et "
            "GOOGLE_SERVICE_ACCOUNT_FILE pour activer l'ecriture. "
            "Affichage console uniquement ci-dessous.)"
        )

    print("\n" + "=" * 70)
    print("RESULTATS (score final = surete x0.5 + commercial x0.5)")
    print("FORT >= {} | A SURVEILLER >= {} | faible en dessous".format(SEUIL_ALERTE, SEUIL_SURVEILLANCE))
    print("=" * 70)
    for r in resultats:
        score, avis, extraction = r["score"], r["avis"], r["extraction"]
        if score >= SEUIL_ALERTE:
            etiquette = "[FORT]"
        elif score >= SEUIL_SURVEILLANCE:
            etiquette = "[A SURVEILLER]"
        else:
            etiquette = "[faible]"
        suffixe = ""
        if r["raffine"]:
            suffixe = " (relu par {} ; Haiku avait {:.1f})".format(MODELE_RAFFINEMENT, r["final_haiku"])
            if r["divergence"]:
                suffixe += "  /!\\ ECART NOTABLE -- lire les deux justifications"
        print("\n{} Score final {:.1f}/10 (surete {:.1f} | commercial {:.1f}){}".format(
            etiquette, score, r["surete"], r["commercial"], suffixe,
        ))
        print("  Action recommandee : {} | Fenetre : {}".format(
            calculer_action_recommandee(score, extraction, surete=r["surete"]),
            calculer_fenetre_action(avis),
        ))
        print("  {}".format(avis["titre"][:90]))
        print("  Acheteur : {} | Pays exec. : {}{}".format(
            avis["acheteur"], avis["pays_execution"],
            " [incertain, repli sur pays acheteur]" if avis.get("pays_execution_incertitude") else "",
        ))
        print("  Valeur estimee (indicatif, hors score) : {}".format(
            avis.get("valeur_estimee", "inconnu")
        ))
        if extraction:
            print("  Type client : {} | Duree : {} | Mobilite : {} | Personnes exposees : {}".format(
                extraction.get("type_client"), extraction.get("duree_estimee"),
                extraction.get("type_mobilite"), extraction.get("profil_personnes_exposees"),
            ))
            print("  Accessibilite commerciale : {} | Securite deja en place : {} | Opportunite (qualitatif) : {}".format(
                extraction.get("accessibilite_commerciale"),
                extraction.get("securite_existante_detectee"),
                extraction.get("niveau_opportunite_amarante"),
            ))
            print("  Profils d'acteurs probables (hypothese, pas de noms) : {}".format(
                ", ".join(extraction.get("profils_acteurs_probables") or []) or "aucun"
            ))
            print("  Justification : {}".format(extraction.get("justification")))
            print("  Confiance modele : {}".format(extraction.get("confiance")))
        else:
            print("  (extraction echouee, voir message d'erreur ci-dessus)")
        print("  Lien : {}".format(avis["lien_avis"]))


if __name__ == "__main__":
    main()
