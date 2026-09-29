import pytest
from pydantic import BaseModel

from agent_emails.pipeline.llm import PRICES_PER_MTOK, StepError, call_structured
from tests.fakes import FakeClient, reply


class Out(BaseModel):
    value: str


def call(client, model="claude-haiku-4-5"):
    return call_structured(client, model=model, system="sys", content="<email>x</email>", output_model=Out)


def test_success_returns_output_and_cost():
    client = FakeClient([reply(Out(value="ok"), input_tokens=1000, output_tokens=200)])
    output, usage = call(client)
    assert output.value == "ok"
    assert (usage.input_tokens, usage.output_tokens) == (1000, 200)
    assert usage.cost_usd == pytest.approx((1000 * 1.0 + 200 * 5.0) / 1_000_000)
    sent = client.messages.calls[0]
    assert sent["model"] == "claude-haiku-4-5" and sent["output_format"] is Out and sent["system"] == "sys"
    assert sent["max_tokens"] == 2048
    assert sent["messages"] == [{"role": "user", "content": "<email>x</email>"}]
    assert "thinking" not in sent


def test_empty_output_is_retried_once():
    client = FakeClient([reply(None), reply(Out(value="ok"))])
    output, usage = call(client)
    assert output.value == "ok"
    assert usage.input_tokens == 200
    assert len(client.messages.calls) == 2


def test_two_failures_raise_with_usage():
    client = FakeClient([reply(None), reply(None, stop_reason="max_tokens")])
    with pytest.raises(StepError, match="2 tentatives") as info:
        call(client)
    assert info.value.usage.input_tokens == 200


def test_validation_error_is_retried():
    from pydantic import ValidationError

    try:
        Out.model_validate({})
    except ValidationError as exc:
        error = exc
    client = FakeClient([error, reply(Out(value="ok"))])
    assert call(client)[0].value == "ok"


def test_refusal_is_not_retried():
    client = FakeClient([reply(None, stop_reason="refusal")])
    with pytest.raises(StepError, match="refusé"):
        call(client)
    assert len(client.messages.calls) == 1


def test_prices_cover_the_configured_models():
    assert PRICES_PER_MTOK["claude-haiku-4-5"] == (1.00, 5.00)
    assert PRICES_PER_MTOK["claude-sonnet-5"] == (2.00, 10.00)
