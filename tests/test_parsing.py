import pytest

from agent_emails.parsing import EmailParseError, html_to_text, parse_eml
from tests.factories import build_eml


def test_plain_text_email():
    email = parse_eml(build_eml())
    assert email.message_id == "<abc@exemple.fr>"
    assert (email.sender_name, email.sender_address) == ("Marie Dubois", "marie@exemple.fr")
    assert email.subject == "Demande de devis"
    assert email.body.startswith("Bonjour, pouvez-vous")
    assert email.received_at.utcoffset() is not None
    assert email.attachments == []


def test_html_only_email_is_converted_to_text():
    html = "<html><style>p{color:red}</style><p>Bonjour,</p><p>Je veux <b>2 lampadaires</b>.<br>Merci&nbsp;!</p><script>x()</script></html>"
    email = parse_eml(build_eml(body=None, html=html))
    assert "<" not in email.body
    assert "color" not in email.body and "x()" not in email.body
    assert "Je veux 2 lampadaires." in email.body
    assert "Merci" in email.body


def test_plain_part_is_preferred_over_html():
    email = parse_eml(build_eml(body="Version texte", html="<p>Version HTML</p>"))
    assert email.body.strip() == "Version texte"


def test_attachments_are_kept_with_their_bytes():
    email = parse_eml(build_eml(attachments=(("F-2026-190.pdf", "application/pdf", b"%PDF-1.4 x"),)))
    assert [(a.filename, a.content_type) for a in email.attachments] == [("F-2026-190.pdf", "application/pdf")]
    assert email.attachments[0].data == b"%PDF-1.4 x"


def test_encoded_subject_and_accents():
    email = parse_eml(build_eml(subject="Réclamation — commande n°42", body="Ça ne marche pas, j'ai été déçue."))
    assert email.subject == "Réclamation — commande n°42"
    assert "déçue" in email.body


def test_missing_message_id_gets_a_stable_fallback():
    raw = build_eml(message_id=None)
    assert parse_eml(raw).message_id == parse_eml(raw).message_id
    assert parse_eml(raw).message_id.startswith("sha1:")


def test_missing_sender_is_an_error():
    with pytest.raises(EmailParseError, match="Expéditeur"):
        parse_eml(build_eml(sender=None))


def test_html_to_text_collapses_blank_lines():
    assert html_to_text("<div>A</div><div></div><div></div><p>B</p>") == "A\n\nB"


def test_html_table_cells_stay_on_one_line():
    """Table cells in one row should be separated by spaces, not newlines."""
    result = html_to_text("<table><tr><td>Qté</td><td>2</td></tr></table>")
    assert result == "Qté 2"


def test_html_block_tags_create_separate_lines():
    """Extended block tags (hr, blockquote, etc.) should create line breaks."""
    result = html_to_text("x<hr>y<blockquote>q</blockquote>z")
    lines = result.split("\n")
    # Should have at least x, y, q, z as separate elements
    assert "x" in result and "y" in result and "q" in result and "z" in result
    # Check they're on separate lines (blockquote and hr create newlines)
    assert result.count("\n") >= 3  # at least 3 line breaks for hr, blockquote, etc.


def test_html_head_and_title_are_skipped():
    """Content in head and title tags should be removed."""
    result = html_to_text("<head><title>T</title></head><body>b</body>")
    assert result == "b"
    assert "T" not in result


def test_unknown_charset_falls_back_to_utf8():
    """Email with unknown charset should still parse by falling back to UTF-8."""
    # Build a raw email with unknown-8bit charset
    raw = (
        b"From: sender@example.com\r\n"
        b"To: recipient@example.com\r\n"
        b"Subject: Test\r\n"
        b"Message-ID: <test@example.com>\r\n"
        b"Date: Mon, 28 Sep 2026 09:15:00 +0200\r\n"
        b"Content-Type: text/plain; charset=unknown-8bit\r\n"
        b"\r\n"
        b"This is ASCII body text"
    )
    email = parse_eml(raw)
    assert "This is ASCII body text" in email.body
    assert email.sender_address == "sender@example.com"


def test_attachment_names_are_unique():
    """Two attachments with the same name should get distinct names."""
    email = parse_eml(
        build_eml(
            attachments=(
                ("anonymous.pdf", "application/pdf", b"%PDF-1.4 x"),
                ("anonymous.pdf", "application/pdf", b"%PDF-1.4 y"),
            )
        )
    )
    assert len(email.attachments) == 2
    names = [a.filename for a in email.attachments]
    assert len(set(names)) == 2  # All names must be unique
    assert names[0] == "anonymous.pdf"
    assert names[1] == "anonymous_2.pdf"


def test_generated_attachment_names_have_extension():
    """Anonymous attachments should get names with appropriate extensions."""
    email = parse_eml(
        build_eml(
            attachments=(
                ("", "application/pdf", b"%PDF-1.4 x"),
                ("", "image/png", b"PNG data"),
            )
        )
    )
    assert len(email.attachments) == 2
    # Names should end with .pdf and .png
    names = [a.filename for a in email.attachments]
    assert any(n.endswith(".pdf") for n in names)
    assert any(n.endswith(".png") for n in names)
