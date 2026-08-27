# -*- coding: utf-8 -*-
"""P1.2 / P1.3 — Défusionner et expliquer le score (26/08/2026).

CE QUI ETAIT FAUX DANS L'AUDIT EXTERNE
--------------------------------------
L'audit affirmait que `accessibilite_commerciale` « existe mais n'est pas
suffisamment pondérée ». C'est faux :

    # ted_complet_v14.py:1631-1634
    commercial = POIDS_CLIENT_COMMERCIAL.get(extraction.get("type_client"), 2.0)
    commercial += {"facile": 3.0, "moyenne": 1.5, "difficile": 0.0}.get(
        extraction.get("accessibilite_commerciale"), 1.5)

Elle vaut jusqu'a +3 sur 10 et sert meme de coupe-circuit sur les alertes. Le
defaut REEL est qu'elle etait fondue dans un chiffre unique et jamais
transportee jusqu'a l'ecran : `avis_vers_lead` ne gardait que `secu`. Un score
de 7,2 ne disait pas s'il venait d'un marche tres accessible ou d'un gros
besoin de surete.

Ce chantier ne recalcule RIEN. Il nomme des contributions deja appliquees.

LE RISQUE PRINCIPAL, ET LE TEST QUI LE COUVRE
---------------------------------------------
Le cockpit porte une COPIE des poids (en JS). Si la formule du collecteur
change et pas la copie, l'interface affiche une decomposition FAUSSE avec
l'autorite d'un detail chiffre -- pire qu'un score opaque.
`TestMiroirDesPoids` compare les deux sources et casse a la moindre
divergence.

Tests OFFLINE : lecture de source et generation HTML, aucun reseau.
"""

import re
import unittest

import radar_cockpit as ck
import radar_dashboard as dash


def _lead(**kw):
    base = {
        "src": "TED", "pays": "Mali", "zone": "Sahel", "titre": "Escorte",
        "agence": "PNUD", "final": 7.2, "surete": 6.0, "comm": 8.4,
        "action": "contacter", "win": "", "nom": "n.c.", "email": "n.c.",
        "tel": "n.c.", "cible": "", "justif": "j", "grp": "AT", "lien": "",
        "ecart": False, "secu": False, "mois": "2026-08", "mois_label": "a",
        "date_det": "2026-08-24", "statut": "nouveau", "motif_ecart": "",
        "deadline": "", "conf": "haute", "modele": "", "pub": "P1",
        "projet_id": "", "valeur": "", "enveloppe": "", "entreprise": "",
        "sect": "Autre", "acces": "facile", "duree": "longue_ou_residente",
        "client": "bailleur_donateur"}
    base.update(kw)
    return base


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


def _table_js(html, nom):
    """Extrait une table de poids du JS genere, sous forme de dict Python."""
    brut = re.search(r"const " + nom + r"=\{(.*?)\};", html, re.S).group(1)
    return {c: float(v) for c, v in re.findall(r"(\w+):\s*(-?[\d.]+)", brut)}


class TestMiroirDesPoids(unittest.TestCase):
    """LE test qui compte. Une decomposition fausse est pire qu'un score
    opaque : elle a l'autorite d'un detail chiffre."""

    def setUp(self):
        self.html = ck.generer_cockpit([_lead()], suivi={"api": True})
        self.src = _lire("ted_complet_v14.py")

    def test_poids_client_identiques_au_collecteur(self):
        bloc = self.src.split("POIDS_CLIENT_COMMERCIAL = {")[1].split("}")[0]
        attendu = {c: float(v) for c, v in re.findall(r'"(\w+)":\s*([\d.]+)', bloc)}
        self.assertEqual(_table_js(self.html, "POIDS_CLIENT"), attendu)

    def test_poids_accessibilite_identiques_au_collecteur(self):
        bloc = self.src.split("commercial += {")[1][:140]
        attendu = {c: float(v) for c, v in
                   re.findall(r'"(facile|moyenne|difficile)":\s*([\d.]+)', bloc)}
        self.assertEqual(_table_js(self.html, "POIDS_ACCES"), attendu)

    def test_bonus_duree_identique_au_collecteur(self):
        """La duree n'est pas une table cote Python mais deux `if` : on verifie
        les valeurs litterales plutot que de recopier une structure absente."""
        self.assertIn('if extraction.get("duree_estimee") == "longue_ou_residente":\n'
                      '        commercial += 1.5', self.src)
        self.assertIn('elif extraction.get("duree_estimee") == "indetermine":\n'
                      '        commercial += 0.5', self.src)
        js = _table_js(self.html, "POIDS_DUREE")
        self.assertEqual(js["longue_ou_residente"], 1.5)
        self.assertEqual(js["indetermine"], 0.5)

    def test_malus_surete_en_place_identique(self):
        self.assertIn("commercial -= 2.0", self.src)
        self.assertIn('["Sûreté déjà en place chez le client",-2.0]', self.html)


class TestTransportDesComposantes(unittest.TestCase):
    """Elles etaient collectees, ecrites en base, et perdues a la construction
    du lead : `avis_vers_lead` ne gardait que `secu`."""

    def test_les_trois_champs_remontent_au_lead(self):
        src = _lire("radar_dashboard.py")
        for cle, colonne in (('"acces"', "accessibilite_commerciale"),
                             ('"duree"', "duree_estimee"),
                             ('"client"', "type_client")):
            self.assertIn('{}: _txt(row.get("{}"))'.format(cle, colonne), src)

    def test_les_colonnes_sont_bien_lues_du_stockage(self):
        """Inutile de transporter un champ que la lecture ne charge pas."""
        src = _lire("radar_dashboard.py")
        self.assertIn('"duree_estimee", "accessibilite_commerciale"', src)

    def test_exposees_au_front(self):
        html = ck.generer_cockpit([_lead()], suivi={"api": True})
        self.assertIn('acces:l.acces||""', html)
        self.assertIn('surete:+l.surete||0,comm:+l.comm||0', html)


class TestAffichageDecomposition(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit([_lead()], suivi={"api": True})

    def test_bloc_present(self):
        self.assertIn("function composantes", self.html)
        self.assertIn("function blocDecomposition", self.html)
        self.assertIn("D'où vient ce score", self.html)

    def test_reserve_aux_avis(self):
        """La formule decomposee est celle des AVIS. L'appliquer a une
        attribution (calcul deterministe different) ou a un signal prive
        afficherait une explication qui n'est pas la leur."""
        self.assertIn('if(l.type!=="avis")return "";', self.html)

    def test_le_volet_surete_est_situe_sans_etre_invente(self):
        """On ne decompose pas la surete : sa formule depend de l'exposition
        terrain et du profil des personnes, non transportes. On dit d'ou elle
        vient plutot que d'inventer des lignes."""
        self.assertIn("moyenne du volet commercial et du volet sûreté", self.html)

    def test_contribution_negative_distinguee(self):
        self.assertIn('class="dc-p ${p<0?"neg":"pos"}', self.html)

    def test_confiance_de_l_analyse_enfin_affichee(self):
        """Champ collecte depuis toujours, jamais mappe dans LEADS."""
        self.assertIn('conf:l.confiance||l.conf||""', self.html)
        self.assertIn("Confiance de l'analyse", self.html)


class TestMotifsProjet(unittest.TestCase):
    """P1.3 : `score_opportunite` retournait ses motifs depuis le debut et
    seule la phrase de synthese etait serialisee."""

    def test_motifs_serialises_par_le_collecteur(self):
        src = _lire("collecteur_projets.py")
        self.assertIn('"opportunite_motifs"', src)
        self.assertIn('.get("motifs", [])', src)

    def test_separateur_compatible_avec_le_contenu(self):
        """Les motifs contiennent des virgules (« contractors : A, B, C ») :
        une virgule comme separateur les couperait en morceaux."""
        src = _lire("collecteur_projets.py")
        self.assertIn('" | ".join(', src)
        html = ck.generer_cockpit([_lead()], suivi={"api": True})
        self.assertIn('.split("|")', html)

    def test_colonne_declaree_dans_le_schema(self):
        src = _lire("collecteur_projets.py")
        bloc = src.split('"maturite", "palier_maturite"')[1][:200]
        self.assertIn("opportunite_motifs", bloc)

    def test_affiches_dans_le_tiroir_projet(self):
        html = ck.generer_cockpit([_lead()], suivi={"api": True})
        self.assertIn("Le détail du score", html)
        self.assertIn("pas une reformulation", html)


class TestNonRegression(unittest.TestCase):

    def test_aucun_placeholder_sur_les_cas_limites(self):
        for leads in ([], [_lead()], [_lead(src="ATTRIB", action="surveiller")],
                      [_lead(src="PRIVÉ")], [_lead(acces="", duree="", client="")]):
            html = ck.generer_cockpit(leads, suivi={"api": True})
            self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])

    def test_lead_sans_composante_n_affiche_pas_de_bloc_vide(self):
        """Un cadre « D'où vient ce score » sans ligne serait pire que rien."""
        html = ck.generer_cockpit([_lead(acces="", duree="", client="",
                                         secu=False)], suivi={"api": True})
        self.assertIn("if(!c.length)return \"\";", html)

    def test_chantiers_precedents_intacts(self):
        html = ck.generer_cockpit([_lead()], suivi={"api": True})
        for attendu in ("santeRun", "function badgeDeadline", "const opps=",
                        "function marquerGagne", "function postureNote",
                        "async function envoyerStatut"):
            self.assertIn(attendu, html)
        self.assertNotIn('mode:"no-cors"', html)


if __name__ == "__main__":
    unittest.main()
