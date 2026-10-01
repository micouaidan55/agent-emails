# Agent emails

**Un pipeline IA qui trie la boîte de réception d'une PME, extrait l'essentiel de chaque email, transmet les factures à l'[agent de factures](https://github.com/micouaidan55/agent-factures) et prépare des brouillons de réponse, sous votre contrôle.**

Une PME reçoit chaque jour des demandes de devis, des réclamations, des factures, des demandes de rendez-vous et de la prospection. Trier, relire et répondre prend des heures. Cet agent fait le premier passage : chaque email arrive classé, résumé, avec une réponse prête à relire. Rien n'est envoyé sans validation humaine.

## Ce que fait l'agent

1. **Trie** chaque email : demande de devis, réclamation, facture, rendez-vous, information ou prospection, avec un niveau d'urgence et une explication.
2. **Extrait** les informations utiles selon la catégorie : contact, références de commande, créneaux proposés, budget…
3. **Transmet les factures** jointes à l'agent de factures, qui vérifie les montants et les doublons.
4. **Rédige un brouillon de réponse** adapté, sans jamais inventer de prix ni de date.
5. **Repère les fraudes** : changement de RIB, faux président, hameçonnage, fausse facture, expéditeur qui imite un client. Un email suspect est bloqué (ni réponse, ni transmission de facture) jusqu'à votre décision.
6. **Attend votre validation** : vous relisez, corrigez et validez. Le brouillon est déposé, jamais envoyé automatiquement.

## Résultats mesurés

Évaluation sur 48 emails générés : 42 emails légitimes (devis, réclamations, factures dont 6 avec PDF, rendez-vous, informations, prospections) et 6 fraudes (2 changements de RIB dont un sans aucun signal technique, un faux président, un hameçonnage, une fausse facture d'annuaire, un domaine sosie d'un client). Pièges : réclamation polie mais urgente, devis déguisé en question, prospection qui imite un client, emails en anglais et en HTML, facture annoncée sans pièce jointe, artisan qui écrit depuis Gmail, fournisseur habituel qui mentionne son IBAN.

Voir `evals/results/`. Pour reproduire (payant, plafonné) : `uv run python -m evals.run --max-cost 2`.

## Démarrage rapide

```bash
git clone https://github.com/micouaidan55/agent-emails.git && cd agent-emails
cp .env.example .env        # puis renseigner ANTHROPIC_API_KEY (et COMPANY_NAME / COMPANY_DOMAIN, le nom et le domaine de votre entreprise)
uv sync
uv run python -m evals.generate && cp evals/dataset/*.eml inbox/
uv run streamlit run app/main.py
```

## Architecture

```
inbox/*.eml ─► MailSource ─► signaux de fraude (code) + ① tri et avis de fraude (Haiku) ─┬─► suspect : alerte, décision humaine
                                                                                         └─► ② extraction (Haiku) ─► ③ factures (agent-factures) ─► ④ brouillon (Sonnet) ─► validation humaine
```

| Module | Rôle |
|---|---|
| `agent_emails.connectors` | Interface `MailSource` ; V1 : dossier de fichiers `.eml` (Gmail ou IMAP en V2 sans toucher au reste) |
| `agent_emails.pipeline` | Étapes du pipeline, appel structuré au modèle, orchestration |
| `agent_emails.storage` | SQLite : emails, corrections humaines, journal |
| `app/` | Interface Streamlit : boîte de réception, tableau de bord, journal |
| `evals/` | Jeu de 48 emails (dont 6 fraudes), métriques, juge LLM |

### Pourquoi un pipeline plutôt qu'un agent

Le tri d'emails est une tâche **bien définie** : l'enchaînement des étapes est connu à l'avance. Un pipeline piloté par le code est donc plus fiable, moins cher et plus facile à mesurer qu'un agent qui décide seul de ses actions. Chaque étape a sa propre évaluation, et **le bon modèle est placé au bon endroit** : Haiku pour trier et extraire (rapide, environ 5 fois moins cher), Sonnet pour rédiger. L'[agent de factures](https://github.com/micouaidan55/agent-factures), lui, reste un agent, car vérifier une facture demande de choisir ses contrôles.

### Détection de fraude : le code vérifie, le modèle juge

Le même principe que dans l'agent de factures. Du code testé repère les signaux objectifs : domaine qui imite un client ou un fournisseur connu (`hoteI-canuts.fr`), lien dont le texte affiche une banque mais qui mène ailleurs, pièce jointe exécutable déguisée, adresse de réponse différente, messagerie gratuite pour une « direction », IBAN ou changement de coordonnées bancaires. Le modèle, dans l'appel de tri existant (aucun coût supplémentaire), juge le contexte : urgence, confidentialité, demande de virement. Le code tranche : un signal fort, deux signaux faibles ou un risque élevé selon le modèle suffisent à bloquer l'email. Si l'API tombe, les signaux du code protègent toujours. Le tableau de bord suit le taux de fausses alertes : trop d'alertes, et plus personne ne les lit.

### Garde-fous

- Le contenu des emails est traité comme une **donnée non fiable** : les instructions qu'il contient ne sont jamais suivies.
- **Sorties structurées** validées par un schéma à chaque étape, avec une seule nouvelle tentative.
- **Seuil de confiance** : un tri incertain est signalé « à vérifier » au lieu d'être classé en silence.
- **Aucun envoi automatique**, et un contrôle anti-invention des montants et des dates dans les évaluations.

## Grille d'évaluation des brouillons

Chaque brouillon est noté de 1 à 5 par Claude Sonnet sur quatre critères : pertinence, ton, complétude et fidélité (aucun prix, date ou engagement inventé). La grille complète se trouve dans `evals/judge.py`.

## Tests

```bash
uv run pytest
```

Les tests utilisent un faux client et ne consomment aucun crédit d'API.

## Limites connues et feuille de route

- V1 limitée à un dossier de fichiers `.eml` : les connecteurs Gmail et IMAP sont la prochaine étape.
- Pas de gestion des fils de discussion ni du multi-utilisateur.
- Les emails d'évaluation sont synthétiques : la mesure sur de vrais emails anonymisés reste à faire.
