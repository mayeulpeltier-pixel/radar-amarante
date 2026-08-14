# -*- coding: utf-8 -*-
"""
Referentiel CPV officiel au niveau DIVISION (2 premiers chiffres).

SOURCE : eForms-SDK (OP-TED), codelist `cpv.gc` (9 454 codes). On n'embarque
QUE les 45 divisions officielles avec leur libelle FR : c'est le niveau
utilise par secteur_lisible (code[:2]), et cela garde le module leger
(embarquer les 9 454 codes complets, 32 Mo, serait absurde). Donnees UE
librement reutilisables (decision 2011/833/UE).

Usage : COMPLETE la table metier SECTEUR_PAR_DIVISION (libelles courts,
adaptes a Amarante) en FALLBACK pour les divisions qu'elle ne couvre pas,
afin d'eviter un secteur "Autre" quand une division officielle existe.
"""

DIVISIONS = {
    "03": "Produits agricoles, de l'élevage, de la pêche, de la sylviculture et produits connexes",
    "09": "Produits pétroliers, combustibles, électricité et autres sources d'énergie",
    "14": "Produits d'exploitation des mines, métaux de base et produits connexes",
    "15": "Produits alimentaires, boissons, tabac et produits connexes",
    "16": "Machines agricoles",
    "18": "Vêtements, articles chaussants, bagages et accessoires",
    "19": "Produits en cuir et textiles, matériaux en plastique et en caoutchouc",
    "22": "Imprimés et produits connexes",
    "24": "Produits chimiques",
    "30": "Machines, matériel et fourniture informatique et de bureau, excepté les meubles et logiciels",
    "31": "Machines, appareils, équipements et consommables électriques; éclairage",
    "32": "Équipements et appareils de radio, de télévision, de communication, de télécommunication et équipements connexes",
    "33": "Matériels médicaux, pharmaceutiques et produits de soins personnnels",
    "34": "Équipement de transport et produits auxiliaires pour le transport",
    "35": "Équipement de sécurité, de lutte contre l'incendie, de police et de défense",
    "37": "Instruments de musique, articles de sport, jeux, jouets, articles pour artisanat, articles pour travaux artistiques et accessoires",
    "38": "Équipements de laboratoire, d'optique et de précision (excepté les lunettes)",
    "39": "Meubles (y compris les meubles de bureau), aménagements, appareils électroménagers (à l'exclusion de l'éclairage) et produits de nettoyage",
    "41": "Eau collectée et purifiée",
    "42": "Machines industrielles",
    "43": "Équipement minier, équipement pour l'exploitation de carrières, matériel de construction",
    "44": "Matériaux et structures de construction; produits auxiliaires pour la construction (à l'exception des appareils électriques)",
    "45": "Travaux de construction",
    "48": "Logiciels et systèmes d'information",
    "50": "Services de réparation et d'entretien",
    "51": "Services d'installation (à l'exception des logiciels)",
    "55": "Services d'hôtellerie, de restauration et de commerce au détail",
    "60": "Services de transport (à l'exclusion du transport des déchets)",
    "63": "Services d'appui et services auxiliaires dans le domaine des transports, services des agences de voyages",
    "64": "Services des postes et télécommunications",
    "65": "Services publics",
    "66": "Services financiers et d'assurance",
    "70": "Services immobiliers",
    "71": "Services d'architecture, services de construction, services d'ingénierie et services d'inspection",
    "72": "Services de technologies de l'information, conseil, développement de logiciels, internet et appui",
    "73": "Services de recherche et développement et services de conseil connexes",
    "75": "Services de l'administration publique, de la défense et de la sécurité sociale",
    "76": "Services relatifs à l'industrie du pétrole et du gaz",
    "77": "Services agricoles, sylvicoles, horticoles, d'aquaculture et d'apiculture",
    "79": "Services aux entreprises: droit, marketing, conseil, recrutement, impression et sécurité",
    "80": "Services d'enseignement et de formation",
    "85": "Services de santé et services sociaux",
    "90": "Services d'évacuation des eaux usées et d'élimination des déchets, services d'hygiénisation et services relatifs à l'environnement",
    "92": "Services récréatifs, culturels et sportifs",
    "98": "Autres services communautaires, sociaux et personnels",
}


def division_lisible(code_cpv):
    """Libelle FR officiel de la DIVISION d'un code CPV (2 premiers
    chiffres), ou None si la division est inconnue."""
    c = str(code_cpv or "").strip()
    return DIVISIONS.get(c[:2]) if len(c) >= 2 else None
