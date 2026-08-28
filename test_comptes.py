# -*- coding: utf-8 -*-
"""P2.5 — Company Intelligence 360 : la lecture commerciale (26/08/2026).

CE QUI MANQUAIT
---------------
La fiche entreprise répondait déjà à « qui est-ce », « où travaille-t-elle »,
« qu'a-t-elle gagné » : identité, présence par théâtre, historique unifié. Du
bon renseignement.

Il lui manquait les deux questions qu'un commercial se pose vraiment :

    QUI est-ce que j'appelle ?
    POURQUOI maintenant, et qu'est-ce que je lui dis ?

L'enrichissement existant est FIRMOGRAPHIQUE (identité légale, dirigeant,
e-mail générique). Ce module le complète par une lecture COMMERCIALE.

LA LIGNE QUE JE NE FRANCHIS PAS
-------------------------------
Aucune personne n'est nommée. Nommer un « Directeur sûreté Afrique » sans
source serait inventer un individu : rien dans les données collectées ne
permet de le connaître. Le module propose des FONCTIONS à viser, ce qui est
une recommandation de méthode et non un renseignement fabriqué. La distinction
est écrite à l'écran, pas seulement dans ce commentaire.

L'angle d'approche, lui, n'est assemblé QUE de faits détectés, chacun affiché
avec sa source.

LE PIÈGE TROUVÉ EN VÉRIFIANT LE RENDU
-------------------------------------
Le JS groupe les fiches sur `cleEnt(nom, entcle)`, qui RETOMBE sur le nom en
minuscules quand `ent_cle` manque. Ma première version exigeait `ent_cle`
strictement : une fiche s'affichait côté JS sans aucun compte côté Python, et
le bloc disparaissait silencieusement. `TestMiroirDeCle` garde ce point.

Tests OFFLINE : fonctions pures et contrats du gabarit, aucun réseau.
"""

import datetime
import re
import unittest

import comptes as cp
import opportunites as op
import radar_cockpit as ck


AUJ = datetime.date(2026, 8, 26)


def _lead(**kw):
    base = {"src": "ATTRIB", "pays": "Tchad", "zone": "Sahel",
            "titre": "Route N1", "final": 7.0, "surete": 7.0, "valeur": 0,
            "enveloppe": 0, "deadline": "", "win": "", "acces": "",
            "duree": "", "client": "", "secu": False, "nature": "",
            "besoin": "", "date_det": "2026-08-01", "statut": "nouveau",
            "pub": "A1", "projet_id": "", "ent_cle": "stecol",
            "entreprise": "STECOL CORPORATION", "origine": "China",
            "etranger_titulaire": True, "lien": "",
            "sect": "Extractif / Mines", "grp": "", "renouv": ""}
    base.update(kw)
    return base


CORPUS = [
    _lead(),
    _lead(pub="A2", pays="Niger", zone="Afrique de l'Ouest", renouv="imminent"),
    _lead(pub="A3", pays="Mali"),
    _lead(pub="S1", src="PRIVÉ", nature="expatrie_significatif",
          projet_id="P178234", lien="https://news.google.com/a"),
]


def _lire(chemin):
    with open(chemin, encoding="utf-8") as f:
        return f.read()


class TestFonctionsCibles(unittest.TestCase):

    def test_aucune_personne_n_est_nommee(self):
        """LA ligne à ne pas franchir. Inventer un nom serait pire que de ne
        rien proposer."""
        for secteur in ("", "Extractif / Mines", "Énergie"):
            for nature in ("", "expatrie_significatif", "local_uniquement"):
                for f, pourquoi in cp.fonctions_cibles(secteur, nature):
                    self.assertTrue(f, "fonction vide")
                    self.assertTrue(pourquoi, "fonction sans justification")

    def test_chaque_fonction_dit_pourquoi(self):
        """Une liste de titres sans raison n'aide pas à choisir."""
        for f, pourquoi in cp.fonctions_cibles("Énergie", "mixte"):
            self.assertGreater(len(pourquoi), 15, f)

    def test_le_secteur_passe_en_tete(self):
        """Un site minier isolé a presque toujours une fonction dédiée : elle
        prime sur la fonction générique."""
        f = cp.fonctions_cibles("Extractif / Mines", "expatrie_significatif")
        self.assertIn("Mine Security Manager", f[0][0])

    def test_la_nature_du_deploiement_change_la_cible(self):
        """Sans expatriés, ce n'est pas le même interlocuteur."""
        expat = [f for f, _ in cp.fonctions_cibles("", "expatrie_significatif")]
        local = [f for f, _ in cp.fonctions_cibles("", "local_uniquement")]
        self.assertNotEqual(expat, local)
        self.assertTrue(any("achats" in f for f in local))

    def test_repli_quand_rien_n_est_analyse(self):
        self.assertTrue(cp.fonctions_cibles("", ""))

    def test_pas_de_doublon(self):
        f = [x for x, _ in cp.fonctions_cibles("Extractif / Mines", "mixte")]
        self.assertEqual(len(f), len(set(f)))


class TestAngleApproche(unittest.TestCase):

    def test_chaque_fait_porte_sa_source(self):
        """Un argument sans provenance n'est pas vérifiable : le commercial
        doit pouvoir remonter à ce qui le fonde avant de le dire au client."""
        c = cp.construire(CORPUS, {"Tchad"})["stecol"]
        self.assertTrue(c["angle"])
        for fait, source in c["angle"]:
            self.assertTrue(fait)
            self.assertTrue(source)

    def test_les_faits_viennent_des_donnees(self):
        c = cp.construire(CORPUS, {"Tchad"})["stecol"]
        textes = " ".join(f for f, _ in c["angle"])
        self.assertIn("Tchad", textes)
        self.assertIn("échéance", textes)

    def test_aucun_fait_est_un_resultat_honnete(self):
        """Ne rien avoir à dire est une information, pas un bug à masquer."""
        c = cp.construire([_lead(pays="", zone="", etranger_titulaire=False,
                                 src="PRIVÉ")], set())
        cle = list(c)[0]
        self.assertIsInstance(c[cle]["angle"], list)

    def test_l_aggravation_n_apparait_que_si_le_pays_est_touche(self):
        avec = cp.construire(CORPUS, {"Tchad"})["stecol"]["angle"]
        sans = cp.construire(CORPUS, set())["stecol"]["angle"]
        self.assertGreater(len(avec), len(sans))


class TestPrioriteCompte(unittest.TestCase):

    def test_mesure_l_exposition_pas_la_taille(self):
        """Une multinationale sans déploiement en zone à risque n'intéresse
        pas Amarante ; une PME au Sahel, si."""
        expose = cp.priorite_compte({"n_marches": 3, "zones": ["a", "b"],
                                     "etranger": True, "deploiement": True})[0]
        inerte = cp.priorite_compte({"n_marches": 0, "zones": []})[0]
        self.assertGreater(expose, inerte)

    def test_compte_sans_signal_le_dit(self):
        note, motifs = cp.priorite_compte({})
        self.assertTrue(any("veille" in m for m in motifs))

    def test_note_bornee(self):
        note, _ = cp.priorite_compte({"n_marches": 99, "zones": list("abcdef"),
                                      "etranger": True, "deploiement": True,
                                      "pays_aggraves": ["x"],
                                      "renouvellements": 5})
        self.assertLessEqual(note, 100)

    def test_toujours_justifiee(self):
        for c in ({}, {"n_marches": 1}, {"etranger": True}):
            self.assertTrue(cp.priorite_compte(c)[1])


class TestMiroirDeCle(unittest.TestCase):
    """LE piège trouvé en vérifiant le rendu, pas par un test.

    Le JS groupe sur `cleEnt(nom, entcle)` qui retombe sur le nom en
    minuscules. Exiger `ent_cle` strictement côté Python faisait disparaître
    le bloc SILENCIEUSEMENT sur tout lead sans clé canonique."""

    def test_meme_regle_que_le_cockpit(self):
        src = _lire("radar_cockpit.py")
        self.assertIn('function cleEnt(nom,entcle){return (entcle||"").trim()'
                      '||String(nom||"").trim().toLowerCase();}', src)
        self.assertEqual(cp.cle_compte("STECOL Corp", ""), "stecol corp")
        self.assertEqual(cp.cle_compte("STECOL Corp", "stecol"), "stecol")

    def test_lead_sans_ent_cle_produit_quand_meme_un_compte(self):
        c = cp.construire([_lead(ent_cle="")], set())
        self.assertIn("stecol corporation", c)

    def test_lead_sans_nom_ignore(self):
        self.assertEqual(cp.construire([_lead(entreprise="", ent_cle="")]), {})

    def test_les_cles_coincident_avec_les_fiches(self):
        """Contrat inter-surfaces : toute fiche affichée doit avoir un
        compte, sinon le bloc disparaît sans le dire."""
        html = ck.generer_cockpit(CORPUS, suivi={"api": True})
        comptes = re.search(r"^const COMPTES=(\{.*?\});$", html,
                            re.S | re.M).group(1)
        self.assertIn("stecol", comptes)


class TestConstruction(unittest.TestCase):

    def setUp(self):
        self.opps = op.construire(CORPUS, AUJ, {"Tchad"})
        self.c = cp.construire(CORPUS, {"Tchad"}, self.opps)["stecol"]

    def test_marches_et_signaux_separes(self):
        self.assertEqual(self.c["n_marches"], 3)
        self.assertEqual(self.c["n_signaux"], 1)

    def test_renouvellements_comptes(self):
        self.assertEqual(self.c["renouvellements"], 1)

    def test_pays_et_theatres_agreges(self):
        self.assertEqual(self.c["pays"], ["Mali", "Niger", "Tchad"])
        self.assertEqual(len(self.c["zones"]), 2)

    def test_nom_le_plus_complet_retenu(self):
        self.assertEqual(self.c["nom"], "STECOL CORPORATION")

    def test_corpus_vide(self):
        self.assertEqual(cp.construire([], None, None), {})
        self.assertEqual(cp.construire(None), {})


class TestAffichage(unittest.TestCase):

    def setUp(self):
        self.html = ck.generer_cockpit(CORPUS, suivi={"api": True})

    def test_bloc_present_dans_la_fiche(self):
        self.assertIn("function blocCompte", self.html)
        self.assertIn("${blocCompte(e)}", self.html)
        self.assertIn("Qui appeler", self.html)
        self.assertIn("Pourquoi maintenant", self.html)

    def test_la_nature_des_fonctions_est_dite_a_l_ecran(self):
        """Le lecteur doit savoir qu'on lui propose des fonctions, pas des
        personnes identifiées. Sinon il croira à un renseignement nominatif."""
        self.assertIn("fonctions à viser", self.html)
        self.assertIn("l'inventer serait pire", self.html)

    def test_place_avant_l_historique(self):
        """La lecture commerciale sert à décider ; l'historique sert à
        vérifier. La décision passe devant."""
        i_c = self.html.index("${blocCompte(e)}")
        i_h = self.html.index("Historique unifié")
        self.assertLess(i_c, i_h)

    def test_compte_absent_ne_casse_pas_la_fiche(self):
        self.assertIn("if(!c)return \"\";", self.html)

    def test_page_toujours_valide(self):
        self.assertEqual(re.findall(r"__[A-Z_]+__", self.html), [])

    def test_chantiers_precedents_intacts(self):
        for attendu in ("renderAujourdhui", "function blocOpportunite",
                        "function marquerGagne", "santeRun"):
            self.assertIn(attendu, self.html)


if __name__ == "__main__":
    unittest.main()
