"""The contract a user-uploaded script's `main()` has to return.

The script imports nothing of ours — it runs in a sandbox with no access to this
package, and it also has to keep working when the user runs it by hand on their
own machine, which is how these scripts existed before this feature. So the
contract is "a list of plain dicts", expressed here as the Pydantic model that
validates it on arrival rather than as a class the author has to inherit from.

Field names are deliberately the same wire names `services/upload.py` accepts
from JSONL. A row that came out of a script and a row that came out of a file are
indistinguishable by the time they reach the preview, and that is the whole point
of the feature: only the source differs.

Two failure modes, and they are not the same:

* The *output as a whole* is wrong (not a list, empty) -> `ScriptOutputError`.
  There is nothing to show, so the run failed.
* An *individual item* is wrong -> a warning, and the row is dropped. This mirrors
  what a malformed JSONL line already does (§6.11): the good rows reach the
  editable preview and the developer fixes or removes the rest by hand. A script
  that produced 300 usable rows and 3 broken ones should not cost the user the
  300.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, field_validator

# Ceiling on rows handed back to the browser. The preview is paginated, but the
# whole set still lives in the tab's memory and is re-serialized to JSONL on
# Create, so this is a real limit rather than a display one.
MAX_OUTPUT_ROWS = 3000

# A script that gets the shape wrong gets it wrong on every row. Showing 4,000
# copies of the same sentence buries the one thing the user needs to read.
MAX_WARNINGS = 50


class ScriptOutputError(ValueError):
    """The return value of `main()` is unusable as a whole."""


class ScriptEvalRow(BaseModel):
    """One question, as returned by a script.

    Extra keys are ignored rather than rejected: these scripts are written against
    a real database, and selecting one column too many is a normal thing to do.
    """

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    question: str
    ground_truth_response: str
    ground_truth_reasoning_process_description: str
    skill: list[str]
    question_id: str | None = None

    @field_validator(
        "question",
        "ground_truth_response",
        "ground_truth_reasoning_process_description",
    )
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("skill", mode="before")
    @classmethod
    def _skill_is_a_list(cls, v):
        # A bare string is the mistake people actually make ("billing" instead of
        # ["billing"]), and silently accepting it would split into characters.
        if isinstance(v, str):
            raise ValueError("must be a list of strings, not a string")
        # Checked here rather than left to list[str] coercion: the model coerces
        # scalars to str for the free-text fields (a number pulled straight out of
        # a column is fine there), and that same coercion would quietly turn
        # skill=[1, 2] into ["1", "2"] — a skill name nobody meant.
        if isinstance(v, list) and not all(isinstance(s, str) for s in v):
            raise ValueError("must be a list of strings")
        return v

    @field_validator("skill")
    @classmethod
    def _skill_non_empty(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        if not cleaned:
            raise ValueError("must be a non-empty list of strings")
        return cleaned

    @field_validator("question_id")
    @classmethod
    def _blank_id_means_generate_one(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


@dataclass
class ScriptOutput:
    rows: list[ScriptEvalRow] = field(default_factory=list)
    # Per-item problems, 1-based and already worded for display.
    warnings: list[str] = field(default_factory=list)
    # Limits the run bumped into. Kept apart from `warnings` because these are
    # about the *system's* ceilings rather than the user's data, and the UI shows
    # them differently (a banner, not a list item).
    limits_hit: list[str] = field(default_factory=list)


def _describe(exc) -> str:
    """Pydantic's error list, flattened into one readable clause per field."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "value"
        msg = err["msg"]
        # Pydantic prefixes messages raised from validators; the prefix reads as
        # noise next to a field name we are already printing.
        msg = msg.removeprefix("Value error, ").removeprefix("Assertion failed, ")
        parts.append(f"{loc} {msg}")
    return "; ".join(parts)


def validate_script_output(value: object) -> ScriptOutput:
    """Turn whatever `main()` returned into rows, warnings and limit notices.

    Raises `ScriptOutputError` when the value cannot be a result set at all.
    """
    if not isinstance(value, list):
        raise ScriptOutputError(
            f"main() must return a list of dicts, got {type(value).__name__}"
        )
    if not value:
        raise ScriptOutputError("main() returned no rows")

    out = ScriptOutput()
    suppressed = 0
    for i, item in enumerate(value, start=1):
        if len(out.rows) >= MAX_OUTPUT_ROWS:
            # Stop validating once full: the remainder is truncated either way,
            # and a 200k-item return should not cost 200k validations.
            break
        if not isinstance(item, dict):
            _warn(out, f"item {i}: expected a dict, got {type(item).__name__}", )
            suppressed += _overflow(out)
            continue
        try:
            out.rows.append(ScriptEvalRow.model_validate(item))
        except Exception as exc:  # pydantic.ValidationError
            _warn(out, f"item {i}: {_describe(exc)}")
            suppressed += _overflow(out)

    if suppressed:
        out.warnings.append(f"…and {suppressed} more item(s) with problems")

    total = len(value)
    if total > MAX_OUTPUT_ROWS and len(out.rows) >= MAX_OUTPUT_ROWS:
        # Phrased in items-returned vs rows-kept because the loop stops looking
        # once it is full: past that point we know how many items there were, but
        # not whether they were any good. Claiming otherwise would be a guess.
        out.limits_hit.append(
            f"Script returned {total:,} items; only the first {MAX_OUTPUT_ROWS:,} "
            "rows were kept. Narrow the query and run again if you need the rest."
        )
    return out


def _warn(out: ScriptOutput, message: str) -> None:
    if len(out.warnings) < MAX_WARNINGS:
        out.warnings.append(message)


def _overflow(out: ScriptOutput) -> int:
    """1 once the warning list is full, so the tail can be counted instead."""
    return 1 if len(out.warnings) >= MAX_WARNINGS else 0
