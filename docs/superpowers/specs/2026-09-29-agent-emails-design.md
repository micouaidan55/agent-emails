# Agent de tri des emails pour PME — Design

**Date :** 2026-09-29
**Statut :** validé en brainstorming, en attente de relecture
**Projet lié :** [agent-factures](https://github.com/micouaidan55/agent-factures) (projet 1 du portfolio)

## 1. Objectif

C'est le deuxième projet du portfolio « agents pour PME ». Il cible :

- **les recruteurs (postes d'AI Product Manager)** : un choix d'architecture justifié (un pipeline plutôt qu'un agent), un arbitrage coût/qualité entre modèles prouvé par la mesure, des indicateurs produit et la réutilisation d'un produit existant ;
- **les clients PME** : une boîte de réception qui se trie seule, des réponses prêtes à relire, des factures transmises automatiquement à l'agent de factures.

L'entreprise fictive utilisatrice est **Atelier Lumière SAS**, la même que dans le projet 1, pour que les deux projets racontent une seule histoire.

## 2. Choix techniques

| Sujet | Choix | Raison |
|---|---|---|
| Langage | Python ≥ 3.12, uv | Cohérence avec le projet 1 |
| Architecture IA | **Pipeline en étapes piloté par le code** (pas d'agent libre) | Tâche bien définie : fiable, moins chère, mesurable étape par étape |
| Modèle de tri et d'extraction | Claude Haiku 4.5 (`claude-haiku-4-5`) | Tâches courtes et structurées : environ 5 fois moins cher que Sonnet |
| Modèle de rédaction | Claude Sonnet 5 (`claude-sonnet-5`) | Meilleure qualité de rédaction |
| Sorties des modèles | Structurées, validées par des modèles Pydantic | Pas d'analyse de texte libre ; une seule nouvelle tentative si la sortie est invalide |
| Source des emails | Interface `MailSource` ; V1 = `FolderSource` (fichiers `.eml`) | Évaluable et démontrable sans identifiants ; Gmail ou IMAP en V2 sans toucher au reste |
| Réutilisation | `agent-factures` installé comme dépendance depuis GitHub | Deux produits autonomes et composables |
| Interface | Streamlit, navigation en haut de page | Cohérence avec le projet 1 |
| Stockage | SQLite | Zéro configuration |
| Tests | pytest, faux client Anthropic | Aucun crédit consommé par les tests |

## 3. Périmètre V1

### Inclus

1. Relevé des nouveaux emails dans `inbox/` (fichiers `.eml`). Un email déjà traité, reconnu à son `Message-ID`, n'est jamais retraité.
2. **Tri** : catégorie, urgence, explication courte et confiance (entre 0 et 1).
   - Catégories : `demande_devis`, `reclamation`, `facture`, `rendez_vous`, `information`, `prospection`.
   - Urgence : `haute` (client mécontent, relance, délai court), `normale` ou `basse` (prospection, information générale).
   - Si la confiance est inférieure à **0,7**, l'email est marqué **« à vérifier »** et affiché en tête de liste.
3. **Extraction** d'informations, avec un schéma propre à chaque catégorie :
   - devis : produit ou service, quantité, date souhaitée, budget ;
   - réclamation : commande ou facture concernée, problème, attente du client ;
   - rendez-vous : créneaux proposés, objet ;
   - facture : fournisseur, numéro de facture s'il figure dans le texte ;
   - toutes catégories : nom et entreprise du contact.
4. **Transfert des factures** : chaque pièce jointe PDF, PNG ou JPG d'un email de catégorie `facture` est analysée par l'`InvoiceAgent` d'agent-factures, en lecture seule. Le verdict (OK, anomalie ou à revoir) est affiché. Le bouton **« Enregistrer la facture »** ajoute la pièce à la base d'agent-factures (chemin donné par `AGENT_FACTURES_DB`), seulement après validation humaine.
5. **Brouillon de réponse** (Sonnet), adapté à la catégorie et aux informations extraites. Il n'y a pas de brouillon pour la prospection : l'interface propose « Ignorer ». Pour une facture avec anomalie, le brouillon reprend `draft_correction_request` d'agent-factures ; pour une facture correcte, c'est un accusé de réception.
6. **Validation humaine** : relire, modifier le brouillon, corriger la catégorie ou l'urgence, **valider** (le brouillon est enregistré comme « déposé », **jamais envoyé**), **ignorer** ou **régénérer** le brouillon.
7. Suivi du **coût et du temps** par étape et par email.
8. Tableau de bord avec des indicateurs produit (§7).

### Exclus (V2 et au-delà)

Connecteurs Gmail et IMAP, envoi réel, fils de discussion, multi-utilisateurs, pièces jointes autres que des factures.

## 4. Architecture

```
agent-emails/
├── src/agent_emails/
│   ├── models.py        # Email, Attachment, Category, Urgency, Triage, Extraction*, Draft, EmailStatus
│   ├── parsing.py       # .eml → Email (texte extrait du HTML, pièces jointes)
│   ├── connectors/
│   │   ├── base.py      # MailSource (Protocol) : fetch_new(), mark_processed(message_id)
│   │   └── folder.py    # FolderSource : lit inbox/*.eml
│   ├── pipeline/
│   │   ├── llm.py       # appel structuré au modèle + une nouvelle tentative + suivi de l'usage
│   │   ├── triage.py    # étape ① (Haiku)
│   │   ├── extraction.py# étape ② (Haiku)
│   │   ├── invoices.py  # étape ③ (agent-factures)
│   │   ├── drafting.py  # étape ④ (Sonnet)
│   │   └── runner.py    # orchestration → EmailResult
│   ├── storage/         # SQLite : emails, résultats, brouillons, corrections, journal
│   └── web.py           # éléments d'interface partagés
├── app/                 # main.py + app_pages/
├── evals/               # generate.py, dataset/, scoring.py, judge.py, run.py, results/
└── tests/
```

**Frontières :**
- Le pipeline ne connaît que `MailSource` : ajouter Gmail revient à ajouter `connectors/gmail.py`.
- Chaque étape est une fonction pure du point de vue du code : elle prend un client, des entrées et le modèle à utiliser, et renvoie un résultat Pydantic et son usage. On peut donc la tester seule avec un faux client.
- Seule l'interface écrit une décision humaine en base. Le pipeline enregistre ses résultats, mais ne valide jamais rien.

## 5. Flux de données

```
inbox/*.eml → FolderSource → parsing → déjà traité ? → ignoré
                                           │ non
① tri (Haiku) ──► prospection → statut « à ignorer », pas de brouillon
② extraction (Haiku, schéma de la catégorie)
③ si facture + pièces jointes → InvoiceAgent (agent-factures) → verdict par pièce jointe
④ brouillon (Sonnet)
⑤ enregistrement de l'EmailResult → statut « brouillon prêt » (ou « à vérifier »)
⑥ humain : valider / modifier / corriger / ignorer / régénérer → journal
```

**Statuts d'un email :** `a_verifier` · `brouillon_pret` · `a_ignorer` · `valide` · `ignore` · `erreur`.

## 6. Gestion des erreurs

| Cas | Comportement |
|---|---|
| `.eml` illisible | Statut `erreur` avec la raison ; le pipeline continue avec les emails suivants |
| Sortie du modèle invalide | Une nouvelle tentative, puis `a_verifier` avec les champs partiels |
| Erreur d'API (réseau, 429, 5xx) | Nouvelles tentatives gérées par le SDK, puis `erreur` ; l'email reste relevable |
| Pièce jointe non supportée | Ignorée pour l'analyse de facture, signalée dans la fiche |
| agent-factures en échec | Verdict « à revoir » pour la pièce ; le reste du pipeline continue |
| Clé d'API absente | Message clair dans l'interface, aucun traitement lancé |

## 7. Interface

Navigation en haut de page : **Boîte de réception** · **Tableau de bord** · **Journal ▾** (Brouillons validés · Ignorés · Historique).

- **Boîte de réception** :
  - un bouton « Relever les emails », avec la progression affichée ;
  - la liste triée par urgence puis par date, les emails « à vérifier » et « erreur » en tête ;
  - des badges de catégorie et d'urgence, l'expéditeur, l'objet et un résumé d'une ligne ;
  - des filtres par catégorie (pastilles).
- **Fiche email** :
  - à gauche, l'email d'origine ;
  - à droite, l'analyse de l'agent :
    - la catégorie et l'urgence, modifiables (chaque correction est enregistrée) ;
    - les informations extraites ;
    - le verdict de facture et le bouton « Enregistrer la facture » ;
    - le brouillon, modifiable ;
    - les actions Valider, Ignorer et Régénérer, puis le coût.
- **Tableau de bord** :
  - le volume par catégorie, les urgences en attente, les brouillons à relire ;
  - les indicateurs produit :
    - le taux de brouillons validés sans modification ;
    - le taux de catégories corrigées ;
    - le coût moyen par email ;
    - le temps gagné estimé (hypothèse de 3 minutes par email, affichée et modifiable).

## 8. Tests

- `parsing` : texte brut, HTML seul, pièces jointes, accents, en-têtes encodés.
- `connectors` : relevé et absence de double traitement.
- Chaque étape du pipeline avec un faux client : sortie valide, nouvelle tentative, seuil de confiance, usage comptabilisé.
- Intégration avec agent-factures : transfert d'une pièce jointe et réception du verdict (faux client).
- `storage` : statuts, corrections, brouillons.
- Interface (AppTest) : chaque page s'affiche, validation d'un brouillon, correction d'une catégorie, clé d'API absente.

## 9. Évaluations

- `evals/generate.py` génère **40 emails `.eml`** avec les réponses attendues (`expected.json`) :
  - répartition : 8 demandes de devis, 7 réclamations, 7 factures (dont 5 avec une pièce jointe reprise du jeu d'agent-factures, 1 avec une erreur de TVA et 1 sans pièce jointe), 6 rendez-vous, 6 demandes d'information et 6 prospections ;
  - pièges : une réclamation polie mais urgente, une demande de devis formulée comme une question, une relance de client impatient, une prospection qui imite un client, un email en anglais, un email en HTML seul, une facture sans pièce jointe.
- Pour chaque email, les réponses attendues sont : la catégorie, l'urgence, les champs à extraire, les éléments que le brouillon **doit mentionner** (nom, référence…) et l'indication qu'il faut ou non produire un brouillon.
- `evals/run.py --model-triage … --model-draft … --max-cost …` mesure :
  - la précision du tri, avec une matrice de confusion ;
  - la précision de l'urgence ;
  - la précision de l'extraction, champ par champ ;
  - pour les brouillons :
    - des **contrôles déterministes** : les éléments obligatoires sont présents, et **aucun montant ni aucune date absents de l'email** ne sont inventés ;
    - une **note de 1 à 5 attribuée par Claude Sonnet** selon une grille publiée (pertinence, ton, complétude, absence d'invention) ;
  - le coût et le temps moyens par email.
- **Expérience coût/qualité :** tri avec Haiku puis avec Sonnet, sur le même jeu.
- Chaque run réel est plafonné (`--max-cost`) et soumis à l'accord de l'utilisateur.

## 10. Présentation portfolio

- **README** : pitch métier et GIF de démo, résultats mesurés, schéma du pipeline, section « Pourquoi un pipeline plutôt qu'un agent », grille d'évaluation des brouillons, lien vers agent-factures.
- **Document de pitchs** (`Pitchs_portfolio.docx`) : ajout d'une section « Projet 2 ».
- **CV et LinkedIn** : ajout du projet.

## 11. Critères de réussite

| Critère | Seuil |
|---|---|
| Tri correct | ≥ 90 % |
| Urgence correcte | ≥ 85 % |
| Champs extraits corrects | ≥ 90 % |
| Note moyenne des brouillons | ≥ 4/5 |
| Informations inventées dans les brouillons | 0 |
| Coût par email hors analyse de facture | ≤ 0,02 $ |
| Tests | Tous verts, sans clé d'API |
| Démo | Lancée par un inconnu en moins de 5 minutes grâce au README |
