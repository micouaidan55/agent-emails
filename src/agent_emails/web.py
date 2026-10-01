"""Éléments d'interface Streamlit partagés par les pages de app/."""

import os
from pathlib import Path

import anthropic
import pandas as pd
import streamlit as st
from agent_factures.agent.loop import DEFAULT_COMPANY, InvoiceAgent
from agent_factures.extraction.models import Invoice
from agent_factures.storage.db import connect as connect_factures
from agent_factures.storage.repository import InvoiceRepository
from dotenv import load_dotenv

from agent_emails.connectors.folder import FolderSource
from agent_emails.models import Category, EmailStatus, InvoiceCheck, Urgency
from agent_emails.pipeline.runner import Pipeline, PipelineConfig, process_inbox, regenerate_draft, reprocess_cleared
from agent_emails.storage.db import connect
from agent_emails.storage.repository import EmailStore, StoredEmail

load_dotenv()

CATEGORY_LABELS = {
    Category.QUOTE: "Demande de devis", Category.COMPLAINT: "Réclamation", Category.INVOICE: "Facture",
    Category.APPOINTMENT: "Rendez-vous", Category.INFORMATION: "Information", Category.PROSPECTING: "Prospection",
}
URGENCY_LABELS = {Urgency.HIGH: "🔴 Haute", Urgency.NORMAL: "🟠 Normale", Urgency.LOW: "⚪ Basse"}
STATUS_LABELS = {
    EmailStatus.TO_CHECK: "❓ À vérifier", EmailStatus.DRAFT_READY: "✉️ Brouillon prêt",
    EmailStatus.TO_IGNORE: "🗑️ À ignorer", EmailStatus.VALIDATED: "✅ Validé",
    EmailStatus.IGNORED: "Ignoré", EmailStatus.ERROR: "⚠️ Erreur",
    EmailStatus.SUSPECT: "⚠️ Fraude possible", EmailStatus.FRAUD: "⛔ Fraude confirmée",
}
INVOICE_ICONS = {"ok": "✅", "anomalie": "⚠️", "a_revoir": "❓"}
PENDING = {EmailStatus.TO_CHECK, EmailStatus.ERROR, EmailStatus.DRAFT_READY, EmailStatus.TO_IGNORE,
           EmailStatus.SUSPECT}


_MD_SPECIAL = set("\\`*_{}[]()#+-.!|<>:~$&")


def md_escape(text: str | None) -> str:
    """Neutralise le Markdown d'un texte non fiable (emails, sorties du modèle)."""
    return "".join(f"\\{c}" if c in _MD_SPECIAL else c for c in (text or ""))


def company() -> str:
    return os.environ.get("COMPANY_NAME", DEFAULT_COMPANY)


def _path(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable, default))


@st.cache_resource
def get_connection(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return connect(path)


def store() -> EmailStore:
    return EmailStore(get_connection(str(_path("AGENT_EMAILS_DB", "data/emails.db"))))


def source() -> FolderSource:
    return FolderSource(_path("AGENT_EMAILS_INBOX", "inbox"), _path("AGENT_EMAILS_DRAFTS", "data/brouillons"))


def invoice_repository() -> InvoiceRepository:
    path = _path("AGENT_FACTURES_DB", "data/factures.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    return InvoiceRepository(connect_factures(str(path)))


def _client() -> anthropic.Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("Clé d'API Claude manquante : renseigne ANTHROPIC_API_KEY dans le fichier .env, puis relance l'application.")
        return None
    return anthropic.Anthropic()


def _pipeline(client) -> Pipeline:
    analyzer = InvoiceAgent(client=client, repo=invoice_repository(), company=company())
    extra = tuple(d.strip() for d in os.environ.get("KNOWN_DOMAINS", "").split(",") if d.strip())
    config = PipelineConfig(company=company(), company_domain=os.environ.get("COMPANY_DOMAIN", ""),
                            extra_domains=extra,
                            attachments_dir=_path("AGENT_EMAILS_ATTACHMENTS", "data/pieces_jointes"))
    return Pipeline(client, analyzer, config)


def _close(message_id: str, emails: EmailStore, status: EmailStatus, action: str, detail: dict | None = None) -> None:
    emails.set_status(message_id, status)
    emails.log(message_id, action, detail)
    st.session_state.pop("selected", None)
    st.rerun()


def _render_invoice_check(stored: StoredEmail, check: InvoiceCheck, emails: EmailStore) -> None:
    with st.expander(f"{INVOICE_ICONS.get(check.status, '❓')} Facture jointe : {md_escape(check.filename)}",
                     expanded=True):
        st.write(md_escape(check.explanation))
        for issue in check.issues:
            st.warning(f"**{md_escape(issue.code)}** : {md_escape(issue.message)}")
        if not check.invoice:
            return
        already = any(
            e["message_id"] == stored.message_id and e["action"] == "facture_enregistree"
            and e["detail"].get("fichier") == check.filename
            for e in emails.list_log(limit=100000)
        )
        if already:
            st.success("Facture enregistrée dans agent-factures.")
        elif st.button("Enregistrer la facture dans agent-factures",
                       key=f"facture-{stored.message_id}-{check.filename}"):
            try:
                invoice_id = invoice_repository().add(Invoice.model_validate(check.invoice), source_file=check.path)
            except Exception as exc:
                st.error(f"Enregistrement de la facture impossible : {md_escape(str(exc))}")
                return
            emails.log(stored.message_id, "facture_enregistree", {"id": invoice_id, "fichier": check.filename})
            st.rerun()


def _render_fraud_alert(stored: StoredEmail, emails: EmailStore) -> None:
    key = stored.message_id
    lines = [f"- {md_escape(s.message)}" for s in stored.fraud_signals]
    if stored.fraud_reason:
        lines.append(f"- Avis de l'agent : {md_escape(stored.fraud_reason)}")
    st.error("**Fraude possible : aucun traitement n'a été lancé.**\n\n" + "\n".join(lines or ["- Risque élevé."]))
    if stored.links:
        st.caption("Liens de l'email (non cliquables) :")
        st.code("\n".join(f"{link.text} → {link.url}" for link in stored.links), language=None, wrap_lines=True)
    actions = st.container(horizontal=True)
    if actions.button("Confirmer la fraude", type="primary", key=f"fraude-{key}"):
        _close(key, emails, EmailStatus.FRAUD, "fraude_confirmee",
               {"signaux": [s.code for s in stored.fraud_signals]})
    if actions.button("Ce n'est pas une fraude", key=f"pas-fraude-{key}"):
        client = _client()
        if client:
            try:
                reprocess_cleared(source(), emails, _pipeline(client), key)
            except Exception:
                st.error("Le traitement de l'email a échoué. Réessaie dans un instant.")
            else:
                st.rerun()


def render_email_card(stored: StoredEmail, emails: EmailStore) -> None:
    key = stored.message_id
    with st.container(border=True):
        st.subheader(md_escape(stored.subject))
        left, right = st.columns(2)
        with left:
            st.caption(f"De : {md_escape(stored.sender_name)} \\<{md_escape(stored.sender_address)}\\> · {stored.received_at:%d/%m/%Y %H:%M}")
            st.text(stored.body)
            if stored.attachments:
                st.caption("Pièces jointes : " + ", ".join(md_escape(a) for a in stored.attachments))
        with right:
            if stored.status is EmailStatus.SUSPECT:
                _render_fraud_alert(stored, emails)
                return
            if stored.error:
                st.warning(md_escape(stored.error))
            if stored.category:
                category = st.selectbox("Catégorie", list(Category), index=list(Category).index(stored.category),
                                        format_func=CATEGORY_LABELS.get, key=f"cat-{key}")
                if category != stored.category:
                    emails.correct(key, "category", category.value)
                    st.rerun()
            if stored.urgency:
                urgency = st.selectbox("Urgence", list(Urgency), index=list(Urgency).index(stored.urgency),
                                       format_func=URGENCY_LABELS.get, key=f"urg-{key}")
                if urgency != stored.urgency:
                    emails.correct(key, "urgency", urgency.value)
                    st.rerun()
            if stored.explanation:
                st.caption(f"{md_escape(stored.explanation)} (confiance {stored.confidence or 0:.0%})")
            if stored.extraction:
                flat = pd.json_normalize(stored.extraction, sep=" · ").T.rename(columns={0: "Valeur"})
                st.dataframe(flat.astype(str), width="stretch")
            for check in stored.invoice_checks:
                _render_invoice_check(stored, check, emails)
            draft = None
            if stored.draft is not None:
                draft = st.text_area("Brouillon de réponse (jamais envoyé automatiquement)", stored.draft,
                                     height=260, key=f"draft-{key}")
            actions = st.container(horizontal=True)
            if draft is not None and actions.button("Valider le brouillon", type="primary", key=f"valider-{key}"):
                try:
                    location = source().save_draft(stored.to_email(), draft)
                except OSError as exc:
                    st.error(f"Le brouillon n'a pas pu être déposé, rien n'a été modifié : {md_escape(exc.strerror or str(exc))}")
                else:
                    emails.update_draft(key, draft)
                    _close(key, emails, EmailStatus.VALIDATED, "brouillon_valide",
                           {"modifie": draft != stored.draft_original, "fichier": location})
            if actions.button("Ignorer", key=f"ignorer-{key}"):
                _close(key, emails, EmailStatus.IGNORED, "ignore")
            if stored.category and stored.category is not Category.PROSPECTING and actions.button(
                "Régénérer le brouillon", key=f"regenerer-{key}"
            ):
                client = _client()
                if client:
                    try:
                        text, usage = regenerate_draft(client, stored, company())
                    except Exception:
                        st.error("La régénération du brouillon a échoué. Réessaie dans un instant.")
                    else:
                        emails.update_draft(key, text or "", original=True)
                        emails.log(key, "brouillon_regenere", {"cout_usd": round(usage.cost_usd, 5)})
                        st.session_state.pop(f"draft-{key}", None)
                        st.rerun()
            st.caption(f"Coût de l'analyse : {stored.cost_usd:.4f} $ · {stored.seconds:.1f} s")


def render_inbox_page() -> None:
    emails = store()
    if st.button("Relever les emails", type="primary", icon=":material/refresh:"):
        client = _client()
        if client:
            with st.status("Relevé et analyse des emails…", expanded=True) as box:
                try:
                    results = process_inbox(
                        source(), emails, _pipeline(client),
                        on_result=lambda r: box.write(f"{md_escape(r.email.subject)} → {STATUS_LABELS[r.status]}"))
                except Exception:
                    box.update(label="Le relevé a échoué", state="error")
                    st.error("Le relevé des emails a échoué (service d'analyse ou dossier inaccessible). Réessaie dans un instant.")
                else:
                    box.update(label=f"{len(results)} email(s) traité(s)", state="complete", expanded=False)
    selected = st.session_state.get("selected")
    stored = emails.get(selected) if selected else None
    if stored:
        render_email_card(stored, emails)
    chosen = st.pills("Filtrer par catégorie", list(Category), selection_mode="multi",
                      format_func=CATEGORY_LABELS.get, key="filtre_categories")
    only_suspects = st.pills("Alertes", ["Suspects"], selection_mode="multi", key="filtre_suspects")
    pending = [e for e in emails.list_emails(PENDING)
               if (not chosen or e.category in chosen) and (not only_suspects or e.status is EmailStatus.SUSPECT)]
    st.caption(f"{len(pending)} email(s) en attente de décision")
    if not pending:
        st.info("Aucun email en attente. Dépose des fichiers .eml dans inbox/ puis clique sur « Relever les emails ».")
    for index, email in enumerate(pending):
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            badges = " · ".join(filter(None, [
                STATUS_LABELS[email.status],
                CATEGORY_LABELS.get(email.category) if email.category else None,
                URGENCY_LABELS.get(email.urgency) if email.urgency else None,
            ]))
            st.markdown(f"**{md_escape(email.subject)}**  \n{md_escape(email.sender_name or email.sender_address)} — "
                        f"{md_escape(email.summary or email.error or '')}  \n:small[{badges}]")
            if st.button("Ouvrir", key=f"open-{index}"):
                st.session_state["selected"] = email.message_id
                st.rerun()


def render_dashboard_page() -> None:
    stats = store().stats()
    minutes = st.number_input("Minutes de traitement manuel par email (hypothèse)", min_value=0.5, value=3.0, step=0.5)
    decided = stats.validated + stats.ignored
    row = st.container(horizontal=True)
    row.metric("Emails traités", stats.total)
    row.metric("Urgences en attente", stats.pending_high)
    row.metric("Brouillons à relire", stats.drafts_to_review)
    row = st.container(horizontal=True)
    rate = f"{stats.validated_unchanged / stats.validated:.0%}" if stats.validated else "—"
    row.metric("Brouillons validés sans modification", rate)
    row.metric("Catégories corrigées", f"{stats.corrected / stats.total:.0%}" if stats.total else "—")
    row.metric("Coût moyen par email", f"{stats.mean_cost_usd:.4f} $")
    row.metric("Temps gagné estimé", f"{decided * minutes / 60:.1f} h")
    row = st.container(horizontal=True)
    row.metric("Emails suspects en attente", stats.suspects)
    row.metric("Fraudes bloquées", stats.fraud_confirmed)
    alerts = stats.fraud_confirmed + stats.fraud_cleared
    row.metric("Fausses alertes de fraude", f"{stats.fraud_cleared / alerts:.0%}" if alerts else "—",
               help="Part des alertes levées par « Ce n'est pas une fraude ».")
    if stats.by_category:
        st.subheader("Emails par catégorie")
        data = pd.DataFrame({"Catégorie": [CATEGORY_LABELS[Category(c)] for c in stats.by_category],
                             "Emails": list(stats.by_category.values())})
        st.bar_chart(data, x="Catégorie", y="Emails")


def _render_decided(status: EmailStatus, empty: str) -> None:
    emails = store().list_emails({status})
    if not emails:
        st.info(empty)
    for email in emails:
        with st.expander(f"{md_escape(email.subject)} — {md_escape(email.sender_name or email.sender_address)}"):
            if email.draft:
                st.code(email.draft, language=None, wrap_lines=True)
            else:
                st.caption(md_escape(email.summary))


def render_validated_page() -> None:
    _render_decided(EmailStatus.VALIDATED, "Aucun brouillon validé pour l'instant.")


def render_ignored_page() -> None:
    _render_decided(EmailStatus.IGNORED, "Aucun email ignoré pour l'instant.")


def render_fraud_page() -> None:
    _render_decided(EmailStatus.FRAUD, "Aucune fraude confirmée pour l'instant.")


def render_history_page() -> None:
    entries = store().list_log()
    if not entries:
        st.info("Le journal est vide.")
        return
    st.dataframe(pd.DataFrame(entries), hide_index=True)
