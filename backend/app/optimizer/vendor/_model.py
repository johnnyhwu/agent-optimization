"""The single seam SkillOpt's vendored stages reach a model through.

Upstream, `reflect`, `aggregate`, `clip`, `slow_update` and `meta_skill` each
carry one line — `from skillopt.model import chat_optimizer` — and that is the
only way any of them talks to an LLM. Vendoring therefore comes down to pointing
those five lines here (see `VENDORED.md`), and this module's whole job is to
hand the call to whichever `OptimizerClient` the running optimization was built
with, with upstream's signature preserved exactly.

**Why a module-level global rather than an argument.** Threading the client
through five files would mean editing every function upstream owns, which is the
opposite of what a small, reviewable diff looks like — and it would not even
work: `reflect` and `aggregate` fan their calls out over their own
`ThreadPoolExecutor`, and a `ThreadPoolExecutor` does not propagate
`contextvars` to its workers. A worker thread has no way to ask which run it is
serving. Upstream solves the same problem the same way (`configure_azure_openai`
sets process-wide state), so this is also the shape the vendored code expects.

**The lock is the price of that, and it is deliberate.** `use_optimizer` holds it
for the whole stage, so two optimization runs reflecting at the same time take
turns instead of one silently answering with the other's model. Serialised is
slower; wrong is unfixable after the fact, because the edits are already in the
skill and nothing records which endpoint produced them. Rollouts — the part of a
run that actually takes the time — are unaffected: they never come through here.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from app.integrations.base import OptimizerClient

_lock = threading.Lock()
_active: OptimizerClient | None = None


@contextmanager
def use_optimizer(client: OptimizerClient) -> Iterator[None]:
    """Make `client` the one the vendored stages call, for the duration.

    Blocks while another run holds the seam. Always released, including on the
    way out of an exception, so a failed step cannot strand every later run.
    """
    global _active
    with _lock:
        _active = client
        try:
            yield
        finally:
            _active = None


def chat_optimizer(
    system: str,
    user: str,
    max_completion_tokens: int = 16384,
    retries: int = 3,
    stage: str = "optimizer",
    timeout: int | None = None,
) -> tuple[str, dict[str, int]]:
    """Upstream's `chat_optimizer`, delegating to the installed client.

    The signature is upstream's to the character — that is what lets the five
    vendored files differ from their originals by a single import line each.
    """
    client = _active
    if client is None:
        raise RuntimeError(
            "no optimizer client is installed; the vendored SkillOpt stages must "
            "run inside `use_optimizer(...)`"
        )
    return client.chat_optimizer(
        system=system,
        user=user,
        max_completion_tokens=max_completion_tokens,
        retries=retries,
        stage=stage,
        timeout=timeout,
    )
