"""Juge LLM : note un brouillon selon une grille publiée."""

from pydantic import BaseModel

from agent_emails.models import Usage
from agent_emails.pipeline.llm import JUDGE_MODEL, call_structured

RUBRIC = """Note chaque critère de 1 (très mauvais) à 5 (excellent) :
- pertinence : le brouillon répond à la demande réelle de l'email.
- ton : professionnel, chaleureux, adapté à la situation (client mécontent, fournisseur, prospect…).
- completude : reprend les éléments utiles (nom, références, créneaux, pièces manquantes) et propose une suite claire.
- fidelite : n'invente aucun prix, date, délai ni engagement absent de l'email ; les inconnues sont marquées [à compléter].
commentaire : une phrase qui justifie la note la plus basse."""


class JudgeScore(BaseModel):
    pertinence: int
    ton: int
    completude: int
    fidelite: int
    commentaire: str


def judge_draft(client, email_text: str, analysis: str, draft: str, model: str = JUDGE_MODEL) -> tuple[JudgeScore, Usage]:
    return call_structured(
        client,
        model=model,
        system="Tu évalues des brouillons de réponse rédigés par un assistant pour une PME.\n\n" + RUBRIC,
        content=f"{email_text}\n\n<analyse>\n{analysis}\n</analyse>\n\n<brouillon>\n{draft}\n</brouillon>",
        output_model=JudgeScore,
        max_tokens=16000,
    )
