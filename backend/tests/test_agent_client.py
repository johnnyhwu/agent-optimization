"""HTTP agent client: the OpenAI chat-completions request it builds, and the
response shapes an agent's chat endpoint may answer with."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.integrations.base import WorkspaceOverride
from app.integrations.real.agent import (
    SERVER_TIMEOUT_MARGIN_S,
    VENDOR_KEY,
    AgentHttpError,
    HttpAgentClient,
    server_budget_s,
)

CHAT_URL = "https://agent.test/v1/chat/completions"


def completion(text: str, **extra) -> dict:
    """A minimal chat completion carrying `text`."""
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    body.update(extra)
    return body


@pytest.fixture
def client(configure):
    # A round timeout, so the budget the server is sent (60 - the 5s margin) is
    # visibly neither the timeout nor the margin.
    with configure(agent_chat_url=CHAT_URL, agent_timeout_s=60.0):
        yield HttpAgentClient()


@respx.mock
async def test_request_is_a_chat_completion(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("What is 2+2?", "corr-abc", "alice", ["eval_billing"])

    body = json.loads(respx.calls[0].request.content)
    assert body["messages"] == [{"role": "user", "content": "What is 2+2?"}]
    # `model` is required by the OpenAI schema; a gateway in front of the agent
    # rejects the call without it, naming a field nobody chose to omit.
    assert body["model"]
    # Never streamed. Accumulating a stream would work but makes every failure
    # mode harder to report, and buys a batch runner nothing.
    assert body["stream"] is False


@respx.mock
async def test_one_user_message_and_no_system_prompt(client):
    """A question is the whole input; the agent's own prompt is the agent's.

    Sending a system message here would make the platform part of the system
    under test — every answer would be graded against a prompt the developer
    never wrote and cannot see.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("q", "corr-1", "bob")

    messages = json.loads(respx.calls[0].request.content)["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


@respx.mock
async def test_no_sampling_parameters_are_sent(client):
    """Temperature and friends belong to the agent, not to its evaluator.

    Sending them would silently change the system being measured, and the
    numbers would still be reported as that system's.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("q", "corr-1", "bob")

    body = json.loads(respx.calls[0].request.content)
    assert set(body) == {"model", "messages", "stream", VENDOR_KEY}


@respx.mock
async def test_request_carries_trace_data(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("What is 2+2?", "corr-abc", "alice", ["eval_billing"])

    vendor = json.loads(respx.calls[0].request.content)[VENDOR_KEY]
    assert vendor["trace_data"] == {
        "trace_id": "corr-abc",
        "session_id": "corr-abc",
        "user_id": "alice",
        "tags": ["eval_billing"],
    }


@respx.mock
async def test_trace_id_and_session_id_are_the_same_value(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("q", "corr-1", "bob")

    trace = json.loads(respx.calls[0].request.content)[VENDOR_KEY]["trace_data"]
    assert trace["trace_id"] == "corr-1"
    assert trace["session_id"] == "corr-1"


@respx.mock
async def test_tags_default_to_empty_list(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("q", "corr-1", "bob")

    body = json.loads(respx.calls[0].request.content)
    assert body[VENDOR_KEY]["trace_data"]["tags"] == []


@respx.mock
async def test_no_skills_key_without_an_override(client):
    """The playground's override must not leak into every other call.

    `timeout_s` is the one key that rides along unconditionally — it states
    something true of every call. `skills` is the opposite: only the playground
    and the optimizer send one, so its existence must not add a key, or change
    one, for an eval run.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("q", "corr-1", "bob", ["eval_billing"])

    vendor = json.loads(respx.calls[0].request.content)[VENDOR_KEY]
    assert "skills" not in vendor
    assert set(vendor) == {"trace_data", "timeout_s"}


@respx.mock
async def test_request_carries_the_server_budget(client):
    """The agent server is told how long it has (§17.0 #6) — a shorter time than
    we are prepared to wait, so that it is the end that times out first."""
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call("q", "corr-1", "bob")

    body = json.loads(respx.calls[0].request.content)
    assert body[VENDOR_KEY]["timeout_s"] == 55.0
    # The margin is only ever subtracted from the number we send. What we
    # ourselves wait — the httpx timeout, and `pipeline.wait_for` above it — is
    # the full configured timeout, or the server would have no head start.
    assert client.timeout_s == 60.0


@respx.mock
async def test_per_run_timeout_reaches_the_server_budget(configure):
    """The timeout a developer typed into the run dialog is the one that travels."""
    with configure(agent_chat_url=CHAT_URL, agent_timeout_s=60.0):
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
        await HttpAgentClient(timeout_s=600.0).call("q", "corr-1", "bob")

    body = json.loads(respx.calls[0].request.content)
    assert body[VENDOR_KEY]["timeout_s"] == 595.0


def test_server_budget_never_falls_below_half_the_timeout():
    """A margin wider than the timeout itself must not produce a zero or
    negative budget — a 3s question would otherwise be sent as "you have -2s"."""
    assert server_budget_s(60.0, 5.0) == 55.0
    # The margin the payload actually uses is the default, not something the
    # caller passes — this is what ties the two together.
    assert server_budget_s(60.0) == server_budget_s(60.0, SERVER_TIMEOUT_MARGIN_S)
    assert server_budget_s(3.0, 5.0) == 1.5
    assert server_budget_s(10.0, 10.0) == 5.0
    # A negative margin is a misconfiguration, not licence to hand the server
    # *more* time than we will wait.
    assert server_budget_s(60.0, -5.0) == 60.0


@respx.mock
async def test_skill_override_travels_in_the_vendor_namespace(client):
    """Everything platform-specific sits under one key beside `messages`.

    Not OpenAI's own `metadata`, which is specified as 16 short string pairs and
    cannot hold a skill file — a strict gateway rejects that outright. One
    namespace also means a gateway that filters unknown fields has a single
    thing to allow.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call(
        "q", "corr-1", "bob", ["playground"],
        workspace=WorkspaceOverride(skills={"billing/SKILL.md": "# Billing (edited)"}),
    )

    body = json.loads(respx.calls[0].request.content)
    assert body[VENDOR_KEY]["skills"] == {"billing/SKILL.md": "# Billing (edited)"}
    assert "metadata" not in body
    # The correlation mechanism is untouched by the override riding along.
    assert body[VENDOR_KEY]["trace_data"]["trace_id"] == "corr-1"
    assert body[VENDOR_KEY]["trace_data"]["tags"] == ["playground"]


@respx.mock
async def test_an_override_carrying_no_skills_sends_no_key(client):
    """`skills` absent and `skills: null` are not the same request.

    The agent server reads an absent key as "keep yours". An override object
    that was constructed but never given files must not turn into a claim the
    developer never made.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call(
        "q", "corr-1", "bob", ["playground"], workspace=WorkspaceOverride(skills=None),
    )

    assert "skills" not in json.loads(respx.calls[0].request.content)[VENDOR_KEY]


@respx.mock
async def test_empty_skills_map_is_sent_because_it_means_something(client):
    """`skills: {}` is "run with no skills", which is a legitimate experiment.

    The trap this guards is a truthiness test: `{}` is falsy, so `if skills:`
    would drop the key and the agent would silently fall back to its own files —
    the exact opposite of what was asked for.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    await client.call(
        "q", "corr-1", "bob", ["playground"], workspace=WorkspaceOverride(skills={}),
    )

    vendor = json.loads(respx.calls[0].request.content)[VENDOR_KEY]
    assert "skills" in vendor
    assert vendor["skills"] == {}


@respx.mock
async def test_a_chat_completion_answer(client):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json=completion("the answer"))
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "the answer"
    assert resp.latency_ms is not None


@respx.mock
async def test_content_parts_are_concatenated(client):
    """Reasoning and multimodal models answer with an array of parts.

    Refusing that shape would fail those agents for a reason with nothing to do
    with this platform, so the text parts are joined and the rest ignored.
    """
    body = completion("")
    body["choices"][0]["message"]["content"] = [
        {"type": "text", "text": "the "},
        {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
        {"type": "text", "text": "answer"},
    ]
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "the answer"


@respx.mock
async def test_a_truncated_answer_is_kept_and_marked(client):
    """`finish_reason: length` is an answer, and it is graded.

    Failing it would wipe out every long answer; saying nothing about it leaves
    a low score whose cause is invisible. So it is recorded beside the answer.
    """
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json=completion("the answer, cut sh") | {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "the answer, cut sh"},
                        "finish_reason": "length",
                    }
                ]
            },
        )
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.truncated is True


@respx.mock
async def test_usage_is_recorded_when_the_agent_reports_it(client):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json=completion("hi", usage={"prompt_tokens": 12, "completion_tokens": 3}),
        )
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.usage == {"prompt_tokens": 12, "completion_tokens": 3}


@respx.mock
async def test_usage_is_optional(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("hi")))
    assert (await client.call("q", "corr", "alice")).usage is None


@respx.mock
async def test_a_bare_json_string_is_no_longer_an_answer(client):
    """The old protocol had no standard shape to point at, so it took several.

    This one does, and every extra accepted shape is a way for a gateway's stray
    response to be graded as though the agent had answered.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json="the answer"))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True


@respx.mock
async def test_a_plain_text_body_is_no_longer_an_answer(client):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, text="the answer", headers={"content-type": "text/plain"}
        )
    )
    assert (await client.call("q", "corr", "alice")).failed is True


@respx.mock
async def test_an_html_body_is_a_failure_with_its_own_sentence(client):
    """A 200 carrying an error page is the failure worth naming separately.

    A proxy, gateway or framework error page is not an agent answering, and if
    it were passed through the judge would grade the HTML and record a confident
    wrong verdict against an agent that never saw the question.
    """
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, text="<!DOCTYPE html><html><body>502 Bad Gateway</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "markup" in resp.error


@respx.mock
async def test_an_html_body_without_a_doctype_is_also_refused(client):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, text="  <html><body>nope</body></html>")
    )
    assert (await client.call("q", "corr", "alice")).failed is True


@respx.mock
async def test_an_answer_that_merely_mentions_a_tag_is_kept(client):
    """The markup guard keys on how the *body* opens, not on the answer's text.

    An answer discussing `<b>` arrives inside a JSON envelope, so it never
    reaches the guard at all.
    """
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, json=completion("Wrap the value in <b> to bold it.")
        )
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "Wrap the value in <b> to bold it."


@respx.mock
async def test_content_not_a_string_is_a_failure(client):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json=completion({"nested": "shape"}))
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "chat completion" in resp.error


@respx.mock
async def test_no_choices_is_a_failure(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True


@respx.mock
async def test_a_tool_call_with_no_text_is_a_failure(client):
    """An assistant turn that only calls a tool has answered nothing.

    `content` is null there, which is a legitimate chat completion and not a
    legitimate answer — grading it would grade the empty string.
    """
    body = completion("")
    body["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f"}}],
    }
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))
    assert (await client.call("q", "corr", "alice")).failed is True


@respx.mock
async def test_empty_content_is_a_failure_not_a_wrong_answer(client):
    # Judging "" would produce a meaningless incorrect verdict and hide the
    # actual problem, so the question is failed instead.
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("")))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "empty" in resp.error


@respx.mock
async def test_redirect_is_followed_not_treated_as_the_response(client):
    # Some servers register the route with a trailing slash, so the POST comes
    # back as a 307 (httpx does not follow redirects by default). Confirm we
    # follow it instead of parsing the redirect's empty body as the answer.
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(307, headers={"location": f"{CHAT_URL}/"})
    )
    respx.post(f"{CHAT_URL}/").mock(
        return_value=httpx.Response(200, json=completion("hi"))
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "hi"


@respx.mock
async def test_5xx_raises_so_the_orchestrator_can_retry(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    with pytest.raises(AgentHttpError):
        await client.call("q", "corr", "alice")


@respx.mock
async def test_4xx_fails_the_question_without_retrying(client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(400, text="bad request"))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "400" in resp.error


@respx.mock
async def test_an_openai_error_envelope_is_quoted_not_dumped(client):
    """`error.message` is a sentence; the envelope around it is four lines of JSON.

    Showing the sentence is the difference between "context length exceeded" and
    a developer reading braces to find it.
    """
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "This model's maximum context length is 8192 tokens.",
                    "type": "invalid_request_error",
                }
            },
        )
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "maximum context length" in resp.error
    assert "invalid_request_error" not in resp.error


@respx.mock
async def test_per_run_url_and_timeout_override_the_environment(configure):
    # The run, not the process, decides which agent server a question goes to.
    other = "https://agent-b.test/chat"
    with configure(agent_chat_url=CHAT_URL, agent_timeout_s=5.0):
        c = HttpAgentClient(chat_url=other, timeout_s=1.5)
        respx.post(other).mock(return_value=httpx.Response(200, json=completion("x")))
        resp = await c.call("q", "corr", "alice")

    assert resp.failed is False
    assert str(respx.calls[0].request.url) == other
    assert c.timeout_s == 1.5


def test_no_chat_url_anywhere_is_a_sentence_not_a_stack_trace(configure):
    with configure(agent_chat_url="", agent_timeout_s=5.0):
        with pytest.raises(RuntimeError, match="AGENT_CHAT_URL"):
            HttpAgentClient()
