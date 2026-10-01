"""Génère 48 emails .eml réalistes (dont 6 fraudes) et leurs réponses attendues.

Usage : uv run python -m evals.generate
"""

import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

TODAY = date(2026, 9, 29)
COMPANY = "Atelier Lumière SAS"
# Domaines « connus » de l'entreprise pendant l'évaluation : le sien, un client et des fournisseurs habituels.
KNOWN_DOMAINS = ["atelier-lumiere.fr", "hotel-canuts.fr", "bureauplus.fr", "imprimerie-dupont.fr", "transports-martin.fr"]
DATASET_DIR = Path(__file__).parent / "dataset"


def invoice_pdf(supplier: str, number: str, issued: date, lines: list[tuple[str, int, str]], vat_error: str | None = None) -> bytes:
    ht = sum(Decimal(price) * qty for _, qty, price in lines)
    vat = (ht * Decimal("0.20")).quantize(Decimal("0.01"))
    printed_vat = vat + Decimal(vat_error) if vat_error else vat
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    rows = [supplier, "", f"FACTURE N° {number}", f"Date : {issued:%d/%m/%Y}",
            f"Échéance : {issued + timedelta(days=30):%d/%m/%Y}", "", f"Client : {COMPANY}", ""]
    rows += [f"{label} | {qty} | {Decimal(price):.2f} € | {Decimal(price) * qty:.2f} €" for label, qty, price in lines]
    rows += ["", f"Total HT : {ht:.2f} €", f"TVA 20 % : {printed_vat:.2f} €", f"Total TTC : {ht + vat:.2f} €"]
    y = A4[1] - 60
    for row in rows:
        pdf.drawString(50, y, row.replace(".", ",") if "€" in row else row)
        y -= 18
    pdf.save()
    return buffer.getvalue()


def mail(n, slug, category, urgency, sender, subject, body, *, fields=None, must=(), lang="fr",
         html_only=False, attachments=(), day=28, fraud=False, reply_to=None):
    name, address = sender
    return {
        "file": f"{n:02d}_{slug}.eml", "message_id": f"<{n:02d}.{slug}@eval.atelier-lumiere.fr>",
        "category": category, "urgency": urgency, "fraud": fraud, "reply_to": reply_to,
        "sender_name": name, "sender_address": address,
        "subject": subject, "body": body, "fields": fields or {}, "must_mention": list(must),
        "draft_expected": category != "prospection" and not fraud, "lang": lang, "html_only": html_only,
        "attachments": [{"file": f, "expected_status": s, "pdf": pdf} for f, s, pdf in attachments],
        "received_at": datetime(2026, 9, day, 9, n % 60, tzinfo=timezone.utc).isoformat(),
    }


def build_specs() -> list[dict]:
    d = date
    return [
        # --- Demandes de devis (8) ---
        mail(1, "devis_suspensions", "demande_devis", "normale", ("Claire Martin", "c.martin@hotel-canuts.fr"),
             "Demande de devis — hall de l'hôtel",
             "Bonjour,\n\nNous rénovons le hall de notre hôtel et souhaiterions un devis pour 12 suspensions en laiton, "
             "pose comprise, idéalement avant le 15 novembre.\n\nCordialement,\nClaire Martin\nHôtel des Canuts",
             fields={"contact.name": "Claire Martin", "contact.company": "Hôtel des Canuts", "quantity": "12",
                     "product_or_service": "suspension"}, must=("Martin", "suspension")),
        mail(2, "devis_restaurant", "demande_devis", "normale", ("Julien Roche", "julien@bistrot-saint-paul.fr"),
             "Éclairage de notre nouveau restaurant",
             "Bonjour,\n\nNous ouvrons un second restaurant en janvier. Pourriez-vous chiffrer l'éclairage complet de la salle "
             "(environ 80 m²) ? Notre budget est d'environ 6 000 €.\n\nJulien Roche, Bistrot Saint-Paul",
             fields={"contact.name": "Julien Roche", "budget": "6 000", "product_or_service": "éclairage"},
             must=("Roche", "éclairage")),
        mail(3, "devis_question", "demande_devis", "normale", ("Sophie Leroy", "sophie.leroy@gmail.com"),
             "Petite question",
             "Bonjour,\n\nEst-ce que vous faites aussi des lampes sur mesure pour un escalier ? Il me faudrait 3 appliques assez fines, "
             "et j'aimerais connaître le prix.\n\nMerci, Sophie Leroy",
             fields={"contact.name": "Sophie Leroy", "quantity": "3", "product_or_service": "applique"},
             must=("Leroy", "applique")),
        mail(4, "devis_urgent", "demande_devis", "haute", ("Marc Petit", "m.petit@galerie-nova.fr"),
             "URGENT — éclairage sur rail en panne",
             "Bonjour,\n\nNotre exposition ouvre le 8 octobre et notre éclairage sur rail est tombé en panne. Pouvez-vous nous "
             "envoyer un devis de remplacement cette semaine ? C'est urgent.\n\nMarc Petit, Galerie Nova",
             fields={"contact.company": "Galerie Nova", "desired_date": "8 octobre"}, must=("Petit", "rail")),
        mail(5, "devis_bureaux", "demande_devis", "normale", ("Nadia Benali", "n.benali@benali-avocats.fr"),
             "Remplacement des éclairages du cabinet",
             "Madame, Monsieur,\n\nNous souhaitons remplacer les éclairages de nos 6 bureaux par des lampes LED. Merci de nous faire "
             "parvenir un devis.\n\nNadia Benali\nCabinet Benali Avocats",
             fields={"contact.name": "Nadia Benali", "contact.company": "Benali", "quantity": "6"}, must=("Benali",)),
        mail(6, "devis_anglais", "demande_devis", "normale", ("Tom Walsh", "tom@dublindesign.ie"),
             "Quote request — custom wall lights",
             "Hello,\n\nWe are looking for 20 custom wall lights for a hotel project in Lyon, with delivery in December. "
             "Could you send us a quote?\n\nBest regards,\nTom Walsh\nDublin Design Studio",
             fields={"contact.name": "Tom Walsh", "quantity": "20"}, must=("Walsh",), lang="en"),
        mail(7, "devis_html", "demande_devis", "normale", ("Élodie Garnier", "elodie@maison-garnier.fr"),
             "Lampadaires pour mon salon",
             "<p>Bonjour,</p><p>Je souhaiterais un devis pour <b>2 lampadaires</b> design pour mon salon.</p><p>Merci !<br>Élodie Garnier</p>",
             fields={"contact.name": "Garnier", "quantity": "2", "product_or_service": "lampadaire"},
             must=("Garnier",), html_only=True),
        mail(8, "devis_relance", "demande_devis", "haute", ("Pierre Faure", "p.faure@boulangerie-faure.fr"),
             "Relance : devis vitrine",
             "Bonjour,\n\nJe vous ai écrit il y a deux semaines pour un devis d'éclairage de vitrine et je n'ai toujours pas de réponse. "
             "Pouvez-vous me répondre rapidement ?\n\nPierre Faure, Boulangerie Faure",
             fields={"contact.name": "Pierre Faure", "product_or_service": "vitrine"}, must=("Faure", "vitrine")),
        # --- Réclamations (7) ---
        mail(9, "recl_casse", "reclamation", "haute", ("Laura Simon", "laura.simon@orange.fr"),
             "Commande CMD-2291 arrivée cassée",
             "Bonjour,\n\nJ'ai reçu hier ma commande CMD-2291 et l'abat-jour de la lampe est cassé. Je suis très déçue. "
             "Je souhaite un remplacement au plus vite.\n\nLaura Simon",
             fields={"order_reference": "CMD-2291", "expectation": "remplacement"}, must=("CMD-2291", "Simon")),
        mail(10, "recl_polie_urgente", "reclamation", "haute", ("Henri Blanc", "h.blanc@le-gourmet.fr"),
             "Appliques qui clignotent",
             "Bonjour,\n\nSans vouloir vous presser, les appliques installées la semaine dernière clignotent depuis hier soir. "
             "Nous avons un service de 80 couverts demain midi. Pourriez-vous passer ?\n\nHenri Blanc, Restaurant Le Gourmet",
             fields={"contact.name": "Henri Blanc", "problem": "clignot"}, must=("Blanc", "applique")),
        mail(11, "recl_retard", "reclamation", "haute", ("Isabelle Morel", "isabelle.morel@free.fr"),
             "Toujours pas de livraison — CMD-2305",
             "Bonjour,\n\nMa commande CMD-2305 devait être livrée le 20 septembre et je n'ai rien reçu. Je suis vraiment mécontente, "
             "c'est la deuxième fois. Pouvez-vous me dire où elle en est ?\n\nIsabelle Morel",
             fields={"order_reference": "CMD-2305"}, must=("CMD-2305", "Morel")),
        mail(12, "recl_facture", "reclamation", "normale", ("Paul Girard", "compta@hotel-bellevue.fr"),
             "Erreur sur votre facture AL-2026-083",
             "Bonjour,\n\nSur votre facture AL-2026-083, vous nous facturez 22 lampes de chevet alors que nous en avons reçu 20. "
             "Merci de la corriger.\n\nPaul Girard, Hôtel Bellevue",
             fields={"order_reference": "AL-2026-083"}, must=("AL-2026-083",)),
        mail(13, "recl_colere", "reclamation", "haute", ("Karim Haddad", "karim.haddad@gmail.com"),
             "Troisième relance !!!",
             "C'est inadmissible !!! Troisième relance pour la lampe défectueuse (commande CMD-2210). Si je n'ai pas de réponse "
             "aujourd'hui, je laisse un avis négatif.\n\nK. Haddad",
             fields={"order_reference": "CMD-2210"}, must=("CMD-2210",)),
        mail(14, "recl_garantie", "reclamation", "normale", ("Anne Roux", "anne@cafedesarts.fr"),
             "Suspension qui ne s'allume plus",
             "Bonjour,\n\nUne des suspensions installées en septembre ne s'allume plus. Est-ce couvert par la garantie ? "
             "Merci de nous indiquer la marche à suivre.\n\nAnne Roux, Café des Arts",
             fields={"contact.name": "Anne Roux", "problem": "allume"}, must=("Roux", "garantie")),
        mail(15, "recl_finition", "reclamation", "normale", ("Lucie Bernard", "lucie.bernard@outlook.fr"),
             "Mauvaise finition — CMD-2318",
             "Bonjour,\n\nLes lampes reçues (commande CMD-2318) sont en finition noire alors que j'avais commandé du laiton. "
             "Je souhaite un échange.\n\nLucie Bernard",
             fields={"order_reference": "CMD-2318", "expectation": "échange"}, must=("CMD-2318", "Bernard")),
        # --- Factures (7) ---
        mail(16, "fact_bureau_plus", "facture", "normale", ("Service comptabilité", "compta@bureauplus.fr"),
             "Facture F-2026-190", "Bonjour,\n\nVeuillez trouver ci-joint notre facture F-2026-190.\n\nCordialement,\nBureau Plus SARL",
             fields={"supplier": "Bureau Plus", "invoice_number": "F-2026-190"}, must=("F-2026-190",),
             attachments=[("F-2026-190.pdf", "ok", invoice_pdf("Bureau Plus SARL", "F-2026-190", d(2026, 9, 25),
                                                                [("Ramettes papier A4", 20, "4.50"), ("Classeurs", 10, "3.20")]))]),
        mail(17, "fact_dupont", "facture", "normale", ("Imprimerie Dupont", "factures@imprimerie-dupont.fr"),
             "Votre facture 4820", "Bonjour,\n\nCi-joint la facture 4820 pour les cartes de visite.\n\nImprimerie Dupont",
             fields={"supplier": "Dupont", "invoice_number": "4820"}, must=("4820",),
             attachments=[("facture_4820.pdf", "ok", invoice_pdf("Imprimerie Dupont", "4820", d(2026, 9, 24),
                                                                  [("Cartes de visite x500", 2, "45.00")]))]),
        mail(18, "fact_technet_tva", "facture", "normale", ("TechNet Services", "facturation@technet-services.fr"),
             "Facture TN-960", "Bonjour,\n\nVous trouverez en pièce jointe la facture TN-960.\n\nTechNet Services",
             fields={"supplier": "TechNet", "invoice_number": "TN-960"}, must=("TN-960", "rectificative"),
             attachments=[("TN-960.pdf", "anomalie", invoice_pdf("TechNet Services", "TN-960", d(2026, 9, 22),
                                                                 [("Licence antivirus (12 mois)", 10, "24.00")], vat_error="12.00"))]),
        mail(19, "fact_martin", "facture", "normale", ("Transports Martin", "compta@transports-martin.fr"),
             "Facture TM/26/061", "Bonjour,\n\nCi-joint notre facture TM/26/061 pour la livraison du 21 septembre.\n\nTransports Martin",
             fields={"supplier": "Transports Martin", "invoice_number": "TM/26/061"}, must=("TM/26/061",),
             attachments=[("TM-26-061.pdf", "ok", invoice_pdf("Transports Martin", "TM/26/061", d(2026, 9, 23),
                                                              [("Livraison Lyon - Villeurbanne", 1, "180.00")]))]),
        mail(20, "fact_electricite", "facture", "normale", ("Électricité Lyon Pro", "contact@elec-lyon-pro.fr"),
             "Facture EL-5561", "Bonjour,\n\nVeuillez trouver notre facture EL-5561. Merci de la régler sous 30 jours.\n\nÉlectricité Lyon Pro",
             fields={"supplier": "Électricité Lyon Pro", "invoice_number": "EL-5561"}, must=("EL-5561",),
             attachments=[("EL-5561.pdf", "ok", invoice_pdf("Électricité Lyon Pro", "EL-5561", d(2026, 9, 26),
                                                            [("Installation de spots LED", 8, "45.00")]))]),
        mail(21, "fact_sans_pj", "facture", "normale", ("Nordic Lamps Ltd", "accounts@nordiclamps.ie"),
             "Invoice INV-21044", "Hello,\n\nPlease find attached our invoice INV-21044 for your last order.\n\nKind regards,\nNordic Lamps Ltd",
             fields={"supplier": "Nordic Lamps", "invoice_number": "INV-21044"}, must=("INV-21044",), lang="en"),
        mail(22, "fact_relance", "facture", "haute", ("Imprimerie Dupont", "factures@imprimerie-dupont.fr"),
             "Relance : facture 4755 impayée",
             "Bonjour,\n\nSauf erreur de notre part, notre facture 4755 du 16/09 reste impayée. Merci de procéder au règlement.\n\nImprimerie Dupont",
             fields={"invoice_number": "4755"}, must=("4755",)),
        # --- Rendez-vous (6) ---
        mail(23, "rdv_showroom", "rendez_vous", "normale", ("Nicolas Garcia", "nicolas.garcia@gmail.com"),
             "Visite du showroom",
             "Bonjour,\n\nJe souhaiterais visiter votre showroom pour choisir des luminaires. Seriez-vous disponibles jeudi 1er octobre "
             "à 14 h ou vendredi 2 octobre à 10 h ?\n\nNicolas Garcia",
             fields={"contact.name": "Nicolas Garcia", "proposed_slots": "1er octobre"}, must=("Garcia",)),
        mail(24, "rdv_chantier", "rendez_vous", "normale", ("Emma Lefèvre", "emma@lefevre-architecte.fr"),
             "Visite de chantier rue Mercière",
             "Bonjour,\n\nPourrions-nous organiser une visite du chantier rue Mercière pour valider l'implantation des luminaires ? "
             "Je suis disponible le mardi 6 octobre au matin.\n\nEmma Lefèvre, architecte",
             fields={"contact.name": "Emma Lefèvre", "proposed_slots": "6 octobre"}, must=("Lefèvre", "chantier")),
        mail(25, "rdv_panne", "rendez_vous", "haute", ("Service maintenance", "maintenance@hotel-bellevue.fr"),
             "Intervention demain matin",
             "Bonjour,\n\nNous avons besoin d'un technicien demain matin pour une panne d'éclairage dans le hall. "
             "Pouvez-vous confirmer un passage à 9 h ?\n\nLe service maintenance, Hôtel Bellevue",
             fields={"contact.company": "Bellevue", "proposed_slots": "9 h"}, must=("hall",)),
        mail(26, "rdv_visio", "rendez_vous", "normale", ("Sarah Cohen", "sarah@studio-cohen.fr"),
             "Point sur le projet de boutique",
             "Bonjour,\n\nPouvons-nous faire un point en visio la semaine prochaine sur le projet de boutique ? "
             "Lundi ou mardi après-midi me conviendraient.\n\nSarah Cohen, Studio Cohen",
             fields={"contact.name": "Sarah Cohen", "proposed_slots": "lundi"}, must=("Cohen",)),
        mail(27, "rdv_report", "rendez_vous", "normale", ("Julien Roche", "julien@bistrot-saint-paul.fr"),
             "Report de notre rendez-vous",
             "Bonjour,\n\nJe dois décaler notre rendez-vous de mercredi. Serait-il possible de le reporter au vendredi 9 octobre ?\n\nJulien Roche",
             fields={"contact.name": "Julien Roche", "proposed_slots": "9 octobre"}, must=("Roche",)),
        mail(28, "rdv_anglais", "rendez_vous", "normale", ("Anna Svensson", "anna.svensson@nordiclamps.ie"),
             "Meeting in Lyon",
             "Hi,\n\nI will be in Lyon on October 14th. Could we meet at your showroom so I can present our new collection?\n\nBest,\nAnna Svensson",
             fields={"contact.name": "Anna Svensson", "proposed_slots": "14"}, must=("Svensson",), lang="en"),
        # --- Informations (6) ---
        mail(29, "info_horaires", "information", "basse", ("Marion Petit", "marion.p@gmail.com"),
             "Horaires du samedi", "Bonjour,\n\nQuels sont les horaires d'ouverture de votre showroom le samedi ?\n\nMerci, Marion",
             fields={"question": "horaires"}),
        mail(30, "info_livraison", "information", "basse", ("Thomas Durand", "t.durand@laposte.net"),
             "Livraison à Grenoble ?", "Bonjour,\n\nLivrez-vous à Grenoble, et quels sont les délais habituels ?\n\nThomas Durand",
             fields={"question": "Grenoble"}, must=("Grenoble",)),
        mail(31, "info_ampoules", "information", "basse", ("Chloé Lambert", "chloe.lambert@gmail.com"),
             "Quelles ampoules pour la NORD-40 ?",
             "Bonjour,\n\nQuelles ampoules faut-il pour la suspension NORD-40 que j'ai achetée chez vous ?\n\nChloé Lambert",
             fields={"question": "NORD-40"}, must=("NORD-40",)),
        mail(32, "info_catalogue", "information", "basse", ("Hugo Mercier", "hugo.mercier@archi-mercier.fr"),
             "Catalogue 2026", "Bonjour,\n\nPouvez-vous m'envoyer votre catalogue 2026 ?\n\nHugo Mercier, architecte d'intérieur",
             fields={"question": "catalogue"}, must=("catalogue",)),
        mail(33, "info_candidature", "information", "basse", ("Inès Robin", "ines.robin@gmail.com"),
             "Candidature spontanée", "Bonjour,\n\nJe suis électricienne et je voulais savoir si vous recrutez en ce moment.\n\nInès Robin",
             fields={"contact.name": "Inès Robin"}, must=("Robin",)),
        mail(34, "info_garantie", "information", "basse", ("David Moreau", "david.moreau@sfr.fr"),
             "Durée de garantie", "Bonjour,\n\nQuelle est la durée de garantie de vos luminaires ?\n\nDavid Moreau",
             fields={"question": "garantie"}, must=("garantie",)),
        # --- Prospection (6) ---
        mail(35, "prosp_seo", "prospection", "basse", ("WebBoost Agency", "offres@webboost-agency.com"),
             "Boostez votre référencement Google !",
             "Bonjour,\n\nOffre spéciale -50 % sur notre pack référencement : apparaissez en première page de Google en 30 jours !\n\nL'équipe WebBoost"),
        mail(36, "prosp_imite_client", "prospection", "basse", ("Julie de EnergiePro", "julie@energiepro-offres.fr"),
             "Suite à notre échange",
             "Bonjour,\n\nSuite à notre échange, je vous envoie notre proposition commerciale pour vos besoins en énergie. "
             "Répondez simplement OUI pour valider votre nouveau contrat.\n\nJulie, EnergiePro"),
        mail(37, "prosp_leads", "prospection", "basse", ("LeadMax", "contact@leadmax.io"),
             "Des clients qualifiés chaque semaine",
             "Bonjour,\n\nNous générons des leads qualifiés pour les entreprises de décoration. Réservez une démo gratuite de 15 minutes.\n\nLeadMax"),
        mail(38, "prosp_formation", "prospection", "basse", ("FormaPlus", "inscriptions@formaplus.fr"),
             "Formation Excel éligible CPF",
             "Bonjour,\n\nDerniers jours pour vous inscrire à notre formation Excel, 100 % financée par votre CPF.\n\nFormaPlus"),
        mail(39, "prosp_fabricant", "prospection", "basse", ("Shenzhen Bright LED", "sales@szbrightled.com"),
             "LED supplier — factory price",
             "Dear purchasing manager,\n\nWe are a LED manufacturer offering factory prices, MOQ 500 pieces. Contact us for our catalog.\n\nSales team",
             lang="en"),
        mail(40, "prosp_salon", "prospection", "basse", ("Salon Lumière & Design", "newsletter@salon-lumiere-design.fr"),
             "Réservez votre stand pour 2027",
             "<h2>Salon Lumière &amp; Design 2027</h2><p>Les inscriptions exposants sont ouvertes !</p><p><a href='#'>Réserver mon stand</a></p>",
             html_only=True),
        # --- Fraudes (6) : catégorie et urgence indicatives, non mesurées ---
        mail(41, "fraude_rib_sosie", "facture", "haute", ("Bureau Plus SARL", "compta@bureau-plus.fr"),
             "Changement de coordonnées bancaires — facture F-2026-190",
             "Bonjour,\n\nSuite à un changement de banque, merci de régler dès aujourd'hui notre facture F-2026-190 sur notre "
             "nouveau compte : IBAN FR76 1027 8060 3100 0205 7380 127. Nos anciennes coordonnées ne sont plus valables.\n\n"
             "Service comptabilité, Bureau Plus SARL", fraud=True),
        mail(42, "fraude_rib_parfait", "facture", "haute", ("Transports Martin", "compta@transports-martin.fr"),
             "Mise à jour de nos coordonnées bancaires",
             "Bonjour,\n\nNous vous informons que notre établissement bancaire a changé. Nous vous remercions d'effectuer "
             "désormais vos règlements, y compris celui de la facture TM/26/061, sur le compte suivant : "
             "IBAN FR76 1695 8000 0141 2575 6018 342.\n\nMerci de ne plus utiliser les coordonnées figurant sur nos "
             "précédentes factures.\n\nBien cordialement,\nLe service comptable, Transports Martin", fraud=True),
        mail(43, "fraude_president", "information", "haute", ("Antoine Mercier (Président)", "president.atelierlumiere@gmail.com"),
             "Opération confidentielle",
             "Bonjour,\n\nJ'ai besoin que tu effectues aujourd'hui un virement de 18 500 € pour une acquisition que nous "
             "finalisons. L'opération est strictement confidentielle : n'en parle à personne, pas même à la comptabilité. "
             "Je suis en réunion toute la journée, réponds-moi uniquement par email.\n\nAntoine Mercier",
             fraud=True, reply_to="a.mercier.direction@protonmail.com"),
        mail(44, "fraude_hameconnage", "information", "haute", ("Banque Populaire - Service sécurité", "securite@bp-alertes-clients.com"),
             "Action requise : votre accès sera suspendu",
             "<p>Madame, Monsieur,</p><p>Une connexion inhabituelle a été détectée sur votre espace professionnel. "
             "Sans vérification de votre part sous 24 h, votre accès sera suspendu.</p>"
             "<p><a href='http://bp-verification-compte.xyz/login'>https://www.banquepopulaire.fr/securite</a></p>"
             "<p>Le service sécurité</p>", html_only=True, fraud=True),
        mail(45, "fraude_annuaire", "facture", "normale", ("Registre National des Professionnels", "facturation@registre-pro-national.com"),
             "Avis d'échéance — inscription 2026",
             "Madame, Monsieur,\n\nVotre inscription annuelle au Registre National des Professionnels arrive à échéance. "
             "Montant : 489,00 € HT. Règlement sous 8 jours par virement : IBAN FR76 3000 4012 3400 0101 2345 678, "
             "faute de quoi votre fiche sera retirée.\n\nLe service facturation", fraud=True),
        mail(46, "fraude_sosie_client", "information", "haute", ("Claire Martin", "c.martin@hoteI-canuts.fr"),
             "Réorganisation de notre comptabilité",
             "Bonjour,\n\nSuite à la réorganisation de notre service comptable, pourriez-vous nous renvoyer dès aujourd'hui "
             "vos dernières factures ainsi que le détail des sommes que nous vous devons ? Merci de répondre directement "
             "à cette adresse.\n\nClaire Martin, Hôtel des Canuts", fraud=True),
        # --- Emails légitimes piégeux (2) : ne doivent pas déclencher d'alerte ---
        mail(47, "devis_artisan_gmail", "demande_devis", "normale", ("Patrick Lemoine", "patrick.lemoine.elec@gmail.com"),
             "Devis pour un chantier",
             "Bonjour,\n\nJe suis électricien et je refais une boutique à Villeurbanne. Pouvez-vous me faire un devis pour "
             "10 spots encastrables ? Merci.\n\nPatrick Lemoine",
             fields={"contact.name": "Patrick Lemoine", "quantity": "10", "product_or_service": "spot"},
             must=("Lemoine", "spot")),
        mail(48, "fact_dupont_iban", "facture", "normale", ("Imprimerie Dupont", "factures@imprimerie-dupont.fr"),
             "Facture 4831",
             "Bonjour,\n\nCi-joint notre facture 4831 pour les flyers. Règlement par virement à 30 jours "
             "(IBAN FR76 1820 6000 4512 3456 7890 123, inchangé).\n\nImprimerie Dupont",
             fields={"supplier": "Dupont", "invoice_number": "4831"}, must=("4831",),
             attachments=[("facture_4831.pdf", "ok", invoice_pdf("Imprimerie Dupont", "4831", d(2026, 9, 27),
                                                                  [("Flyers A5 x1000", 1, "120.00")]))]),
    ]


def build_eml(spec: dict) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{spec['sender_name']} <{spec['sender_address']}>"
    msg["To"] = "contact@atelier-lumiere.fr"
    msg["Subject"] = spec["subject"]
    msg["Date"] = format_datetime(datetime.fromisoformat(spec["received_at"]))
    msg["Message-ID"] = spec["message_id"]
    if spec.get("reply_to"):
        msg["Reply-To"] = spec["reply_to"]
    if spec["html_only"]:
        msg.set_content(spec["body"], subtype="html")
    else:
        msg.set_content(spec["body"])
    for attachment in spec["attachments"]:
        msg.add_attachment(attachment["pdf"], maintype="application", subtype="pdf", filename=attachment["file"])
    if msg.is_multipart():
        msg.set_boundary("=====" + hashlib.sha1(spec["message_id"].encode()).hexdigest()[:24] + "=====")
    return msg.as_bytes()


def generate(out_dir: Path = DATASET_DIR) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = build_specs()
    for spec in specs:
        (out_dir / spec["file"]).write_bytes(build_eml(spec))
    keys = ("file", "message_id", "category", "urgency", "fraud", "fields", "must_mention", "draft_expected", "lang")
    emails = [{**{k: s[k] for k in keys},
               "attachments": [{"file": a["file"], "expected_status": a["expected_status"]} for a in s["attachments"]]}
              for s in specs]
    (out_dir / "expected.json").write_text(
        json.dumps({"today": TODAY.isoformat(), "known_domains": KNOWN_DOMAINS, "emails": emails},
                   ensure_ascii=False, indent=2)
    )
    return specs


if __name__ == "__main__":
    print(f"{len(generate())} emails générés dans {DATASET_DIR}")
