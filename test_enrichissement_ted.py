"""Tests de l'enrichissement texte integral cible (pepite 4, RADAR_TED_ENRICHIR).

A l'escalade, on peut donner au modele le plus capable le texte integral de la
notice (PDF). Regles verifiees ici : jamais bloquant, ne modifie jamais l'avis
d'origine, plafond respecte, URL PDF correcte. Tout hors-ligne (fetch/session
simules).
"""

import unittest

import ted_complet_v14 as ted


class _RepPDF:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class _SessionPDF:
    """Capture l'URL demandee et renvoie un contenu PDF simule."""
    def __init__(self, content):
        self.content = content
        self.urls = []

    def get(self, url, timeout=None, **kw):
        self.urls.append(url)
        return _RepPDF(self.content)


# --- texte_integral_notice -------------------------------------------------

class TestTexteIntegralNotice(unittest.TestCase):

    def test_fetch_injecte_est_utilise(self):
        texte = ted.texte_integral_notice("123-2026", fetch=lambda p: "TEXTE:" + p)
        self.assertEqual(texte, "TEXTE:123-2026")

    def test_pub_vide_renvoie_chaine_vide(self):
        self.assertEqual(ted.texte_integral_notice("", fetch=lambda p: "x"), "")

    def test_url_pdf_correcte_via_session(self):
        """Sans fetch : on doit taper l'URL PDF (rendu serveur), pas /html."""
        sess = _SessionPDF(b"pas un vrai pdf")
        # _pdf_en_texte renverra '' (PDF illisible), ce qui est OK : on teste l'URL.
        ted.texte_integral_notice("456-2026", session=sess)
        self.assertEqual(sess.urls, ["https://ted.europa.eu/en/notice/456-2026/pdf"])

    def test_echec_reseau_jamais_bloquant(self):
        class _SessionKO:
            def get(self, *a, **k):
                raise RuntimeError("reseau coupe")
        self.assertEqual(ted.texte_integral_notice("789-2026", session=_SessionKO()), "")

    def test_pdf_illisible_renvoie_vide(self):
        self.assertEqual(ted._pdf_en_texte(b"ceci n'est pas un pdf"), "")


# --- avis_enrichi_pour_escalade --------------------------------------------

class TestAvisEnrichi(unittest.TestCase):

    def _avis(self, description="desc courte"):
        return {"publication_number": "111-2026", "titre": "T", "description": description}

    def test_description_augmentee_et_marquee(self):
        avis = self._avis()
        enrichi = ted.avis_enrichi_pour_escalade(avis, fetch=lambda p: "CONTENU INTEGRAL")
        self.assertTrue(enrichi.get("enrichi_pdf"))
        self.assertIn("desc courte", enrichi["description"])          # base conservee
        self.assertIn("CONTENU INTEGRAL", enrichi["description"])     # texte ajoute
        self.assertIn("[TEXTE INTEGRAL DE LA NOTICE]", enrichi["description"])

    def test_avis_origine_jamais_modifie(self):
        avis = self._avis()
        original = dict(avis)
        ted.avis_enrichi_pour_escalade(avis, fetch=lambda p: "CONTENU")
        self.assertEqual(avis, original)  # l'avis passe n'a pas bouge
        self.assertNotIn("enrichi_pdf", avis)

    def test_texte_indisponible_renvoie_avis_inchange(self):
        avis = self._avis()
        resultat = ted.avis_enrichi_pour_escalade(avis, fetch=lambda p: "")
        self.assertIs(resultat, avis)                 # meme objet, pas de copie
        self.assertNotIn("enrichi_pdf", resultat)

    def test_plafond_respecte(self):
        avis = self._avis(description="")
        # Caractere de remplissage absent du marqueur "[TEXTE INTEGRAL...]"
        # (sinon on compterait aussi le 'X' de TEXTE).
        gros = "Z" * (ted.PLAFOND_ENRICHISSEMENT + 5000)
        enrichi = ted.avis_enrichi_pour_escalade(avis, fetch=lambda p: gros)
        nb_z = enrichi["description"].count("Z")
        self.assertEqual(nb_z, ted.PLAFOND_ENRICHISSEMENT)

    def test_description_vide_a_la_base(self):
        avis = self._avis(description="")
        enrichi = ted.avis_enrichi_pour_escalade(avis, fetch=lambda p: "SEUL CONTENU")
        self.assertTrue(enrichi["description"].startswith("[TEXTE INTEGRAL DE LA NOTICE]"))
        self.assertIn("SEUL CONTENU", enrichi["description"])


if __name__ == "__main__":
    unittest.main()
