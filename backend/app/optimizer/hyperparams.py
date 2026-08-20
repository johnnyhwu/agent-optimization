"""The algorithm's knobs for one run: one set of defaults, read one way.

Twelve settings decide how the loop behaves — how big a minibatch is, which
metric the gate compares on, how many edits a step may apply. Until this module
they had two homes and neither was authoritative:

    the wizard's prefill    literals in `optimization_defaults`
    the value actually used `config.get("minibatch_size") or 8` in the engine

That is two copies of every number, and the second one is the one that runs.
`_resolve_optimization_config` was supposed to close the gap — its docstring
says a blank field is stored with the environment's value, because "a blank in
a stored config is unreadable afterwards" — but it skips `None`, and seven of
these have no control in the wizard, so they were never stored at all. What a
run was configured with was therefore only half written down; the other half
was whatever the engine's literals happened to say on the day someone asked.
Change a literal and every finished run is retroactively described wrong.

**And `or` is the wrong reader.** Every one of these is read as
`config.get(key) or <default>`, which cannot distinguish "not set" from a
falsy value that was set on purpose. `mixed_weight` is `ge=0` in the schema, so
0 is a legal request meaning "compare on hard accuracy alone" — and it came
back out as 0.5, which is a different gate. Same trap the stop conditions have
(`stopping.py`), same fix: an explicit `is None`.

So: `algorithm_defaults()` is what the wizard is offered, `resolve_algorithm()`
is what the run gets, and both come from `Settings`. The engine reads the
resolved dict and never reaches for a literal of its own.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.config import settings
from app.optimizer.reflection import DEFAULT_REFLECT_BUDGET_CHARS

# Every key `run.config` carries about the algorithm, and where its default
# comes from. Not `num_epochs` and `batch_size`: those are columns on the run,
# not config, because the step count is derived from them at creation.
def algorithm_defaults() -> dict[str, Any]:
    """What an untouched run would do, from this deployment's environment."""
    return {
        "scheduler": settings.optimizer_scheduler,
        "learning_rate": settings.optimizer_learning_rate,
        "min_learning_rate": settings.optimizer_min_learning_rate,
        "minibatch_size": settings.optimizer_minibatch_size,
        "analyst_workers": settings.optimizer_analyst_workers,
        "merge_batch_size": settings.optimizer_merge_batch_size,
        "reflect_budget_chars": DEFAULT_REFLECT_BUDGET_CHARS,
        "gate_metric": settings.optimizer_gate_metric,
        "mixed_weight": settings.optimizer_mixed_weight,
        "failure_only": settings.optimizer_failure_only,
        "slow_update": settings.optimizer_slow_update,
        "meta_skill": settings.optimizer_meta_skill,
    }


def resolve_algorithm(config: Mapping | None) -> dict[str, Any]:
    """This run's values: what it was started with, else the environment's.

    Runs created before a key existed simply do not carry it, and get the
    environment's value — which is what the engine's literals used to do, one
    fallback at a time. Resuming such a run therefore behaves as it did before,
    with one difference worth knowing: if the deployment has since changed an
    `OPTIMIZER_*` setting, the resumed steps use the new value. That was already
    true of the literals; it is only visible now because the numbers are
    settable.
    """
    config = config or {}
    resolved = algorithm_defaults()
    for key, default in resolved.items():
        value = config.get(key)
        # `is None`, never `or`: 0 and False are legal values here. A
        # `mixed_weight` of 0 means "hard accuracy alone" and a `failure_only`
        # of False means "show the analyst its successes too", and both would
        # come back as the opposite under the idiom this replaces.
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        resolved[key] = _coerce(value, default)
    return resolved


def _coerce(value: Any, default: Any) -> Any:
    """Make a stored value the shape the engine expects.

    JSONB round-trips are honest but loose — an int written as `8` can come
    back as `8` and a float as `Decimal`-ish depending on the driver — and the
    vendored stages type-check nothing. The default is the type witness.
    """
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value
