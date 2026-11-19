import json
import logging

from radar.logs import JsonFormatter, redact


def test_redact_removes_secrets_tokens_and_emails():
    raw = (
        "key=sk-abcdefghijklmnopqrstuvwxyz123456 telegram 123456789:AAFabcdefghijklmnopqrstuvwxyz012345 "
        "password=hunter2 contato pessoa@exemplo.com Authorization: Bearer abc.def.ghi"
    )
    out = redact(raw)
    for leaked in ("sk-abcdefghijklmnopqrstuvwxyz123456", "AAFabcdefghijklmnop", "hunter2", "pessoa@exemplo.com", "abc.def.ghi"):
        assert leaked not in out
    assert "[REDACTED]" in out


def test_json_formatter_emits_one_json_object_with_redacted_message():
    rec = logging.LogRecord("radar.test", logging.INFO, __file__, 1, "token=segredo123 ok", None, None)
    rec.source_id = "src-1"
    data = json.loads(JsonFormatter().format(rec))
    assert data["level"] == "INFO" and data["logger"] == "radar.test"
    assert "segredo123" not in data["message"]
    assert data["source_id"] == "src-1"


def test_telegram_bot_token_inside_url_is_redacted():
    from radar.logs import redact

    line = "POST https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage"
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in redact(line)
