"""Lance le pipeline sur le jeu d'évaluation. ATTENTION : appelle l'API Claude (payant).

Usage : uv run python -m evals.run --max-cost 2
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import anthropic
from agent_factures.agent.loop import DEFAULT_COMPANY, InvoiceAgent
from agent_factures.storage.db import connect as connect_factures
from agent_factures.storage.repository import InvoiceRepository
from dotenv import load_dotenv

from agent_emails.models import EmailStatus, render_email
from agent_emails.parsing import parse_eml
from agent_emails.pipeline.fraud import KnownSenders
from agent_emails.pipeline.llm import DRAFT_MODEL, EXTRACTION_MODEL, JUDGE_MODEL, TRIAGE_MODEL
from agent_emails.pipeline.runner import Pipeline, PipelineConfig
from evals.judge import judge_draft
from evals.scoring import EmailOutcome, source_text, summarize, to_markdown

ROOT = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--triage-model", default=TRIAGE_MODEL)
    parser.add_argument("--extraction-model", default=EXTRACTION_MODEL)
    parser.add_argument("--draft-model", default=DRAFT_MODEL)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--max-cost", type=float, default=None, help="Budget en $ au-delà duquel l'évaluation s'arrête.")
    parser.add_argument("--dataset", type=Path, default=ROOT / "dataset")
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Clé d'API Claude manquante : renseigne ANTHROPIC_API_KEY dans le fichier .env avant de lancer l'évaluation.")
        sys.exit(1)

    expected = json.loads((args.dataset / "expected.json").read_text())
    today = date.fromisoformat(expected["today"])
    client = anthropic.Anthropic()
    company = os.environ.get("COMPANY_NAME", DEFAULT_COMPANY)
    analyzer = InvoiceAgent(client=client, repo=InvoiceRepository(connect_factures()), today=today, company=company)
    workdir = args.out / "pieces_jointes"
    pipeline = Pipeline(client, analyzer, PipelineConfig(
        company=company, attachments_dir=workdir, triage_model=args.triage_model,
        extraction_model=args.extraction_model, draft_model=args.draft_model,
    ))

    known = KnownSenders(domains=frozenset(expected.get("known_domains", [])))
    outcomes, details, total_cost = [], [], 0.0
    for item in expected["emails"]:
        email = parse_eml((args.dataset / item["file"]).read_bytes())
        started = time.perf_counter()
        result = pipeline.process(email, known=known)
        elapsed = time.perf_counter() - started
        total_cost += result.usage.cost_usd
        judge = None
        if result.draft and not args.no_judge:
            analysis = json.dumps({"categorie": result.triage.category.value if result.triage else None,
                                   "informations": result.extraction}, ensure_ascii=False)
            score, usage = judge_draft(client, render_email(email), analysis, result.draft, JUDGE_MODEL)
            judge = score.model_dump()
            total_cost += usage.cost_usd
        outcome = EmailOutcome(
            file=item["file"], expected=item,
            category=result.triage.category.value if result.triage else None,
            urgency=result.triage.urgency.value if result.triage else None,
            extraction=result.extraction, draft=result.draft,
            invoice_statuses=[c.status for c in result.invoice_checks], judge=judge,
            cost_usd=result.usage.cost_usd, seconds=elapsed, error=result.error, source=source_text(result),
            suspected=result.status is EmailStatus.SUSPECT,
        )
        outcomes.append(outcome)
        details.append({"file": item["file"], "attendu": item["category"], "obtenu": outcome.category,
                        "fraude_attendue": item.get("fraud", False), "suspect": outcome.suspected,
                        "signaux": [s.code for s in result.fraud_signals],
                        "urgence": outcome.urgency, "statut": result.status.value, "brouillon": result.draft,
                        "extraction": result.extraction, "juge": judge, "erreur": result.error,
                        "cout_usd": result.usage.cost_usd, "secondes": elapsed})
        print(f"{item['file']:<32} {outcome.category or '—':<14} {outcome.urgency or '—':<8} {elapsed:.1f}s"
              f" — cumul {total_cost:.4f} $", flush=True)
        if args.max_cost is not None and total_cost >= args.max_cost:
            print(f"Budget de {args.max_cost:.2f} $ atteint : arrêt après {len(outcomes)} emails.")
            break

    label = f"{args.triage_model} + {args.draft_model}"
    report = to_markdown(label, summarize(outcomes))
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"{args.triage_model}__{args.draft_model}"
    (args.out / f"{stem}.md").write_text(report)
    (args.out / f"{stem}.json").write_text(json.dumps(details, ensure_ascii=False, indent=2))
    print("\n" + report)
    print(f"Coût total de l'évaluation (juge compris) : {total_cost:.4f} $")


if __name__ == "__main__":
    main()
