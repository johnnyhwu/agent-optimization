"""Exporting an eval set as files a developer can read, analyse, and re-upload.

Three rules hold this module together, and each one is load-bearing:

**1. The question columns are the *upload* names, not the API names.**
`QuestionOut` calls them `ground_truth_reasoning` and `skills`; `parse_jsonl`
requires `ground_truth_reasoning_process_description` and `skill`. Exporting the
API names produces a file that fails re-upload with "missing required field(s)",
and the developer reads that as *their* file being broken. `QUESTION_FIELDS`
below is written against `services.upload`, and `test_export.py` round-trips an
export back through `parse_jsonl` so a later refactor cannot quietly re-align
them with the API.

**2. Rows carry `(eval_set_id, question_id)`, because that is the DB's key.**
`question_id` is unique per eval set (`UniqueConstraint("eval_set_id",
"question_id")`), not globally: two sets can hold the same id, and after a
download-edit-re-upload cycle they routinely do. Inside the system nothing cares
— every join runs on `question_pk` — but an exported file is read by pandas and
Excel, where joining two sets on `question_id` alone silently merges unrelated
questions. Exporting the set id alongside makes the file's join key the same one
the database enforces.

The extra columns are safe for re-upload: `parse_csv` resolves columns by name
and `parse_jsonl` reads keys by name, so both ignore what they do not recognise.

**3. Credentials cannot reach a file.** `Run.config` and `Run.secrets` are
separate columns precisely so this is structural (models.py). Run rows are built
from `RunConfig`, which has no credential fields and drops unknown keys, so a
secret cannot arrive here even if one were mis-stored in `config`.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Iterable

from app.models import EvalSet, Question, QuestionResult, Run
from app.schemas import RunConfig
from app.services import judge_prompt
from app.services.aggregation import result_phase

# Bumped when the shape of an exported file changes. Recorded in the manifest so
# a future importer can tell what it is holding rather than guessing from the
# column names.
EXPORT_FORMAT_VERSION = 1

# Where each set's rows came from. Prefixed onto every table (rule 2 above).
PROVENANCE_FIELDS = ("eval_set_id", "eval_set_name")

# The re-uploadable half. These names are `services.upload`'s, not the API's —
# see rule 1. `skill` is singular and holds a list.
QUESTION_FIELDS = (
    *PROVENANCE_FIELDS,
    "question_id",
    "question",
    "ground_truth_response",
    "ground_truth_reasoning_process_description",
    "skill",
)

# One row per run. `config` is the resolved non-secret config as JSON; run-level
# facts live here rather than repeated down every result row.
RUN_FIELDS = (
    *PROVENANCE_FIELDS,
    "run_id",
    "run_name",
    "status",
    "triggered_by",
    "started_at",
    "completed_at",
    "pass_rate",
    "total_count",
    "correct_count",
    "error_message",
    "config",
    "credentials_set",
)

# One row per (run x question) — the shape a pivot table wants. Deliberately not
# the shape of `GET .../results`, which collapses to one representative row per
# question for the UI's left column and would silently drop every earlier run.
RESULT_FIELDS = (
    *PROVENANCE_FIELDS,
    "run_id",
    "run_name",
    "question_id",
    "question",
    "agent_response",
    "verdict",
    "judge_score",
    "judge_comment",
    "status",
    "phase",
    "error_message",
    # Which step failed and how, in the vocabulary the UI already reads rather
    # than the prose beside it: 'agent_timeout' | 'agent' | 'judge' |
    # 'judge_timeout' | 'judge_invalid'. Without it, "how many of these timed
    # out" is a substring search over `error_message`, which is a sentence that
    # is allowed to change wording.
    "failure_kind",
    # Both ends of the agent call. The latency was exported without the moment
    # it started, which is the column anyone lining a run up against an incident
    # timeline actually needs.
    "started_at",
    "agent_latency_ms",
    # Model calls the agent made answering this question, as counted off the
    # trace at run time. Blank — never 0 — for a question whose trace never
    # arrived: nobody knows what it cost, and a 0 in a spreadsheet is a number
    # people average.
    "llm_call_count",
    "correlation_id",
    "trace_ready",
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def run_label(run: Run) -> str:
    """What the UI calls this run, so a spreadsheet and the screen agree.

    Mirrors `routers/results.py`'s `run_labels`: a developer-supplied name when
    there is one, else the start time.
    """
    return run.name or run.started_at.isoformat(timespec="seconds")


def question_rows(eval_set: EvalSet, questions: Iterable[Question]) -> list[dict]:
    """Question rows, ordered by `question_id` so two exports of the same set
    diff cleanly."""
    rows = [
        {
            "eval_set_id": str(eval_set.id),
            "eval_set_name": eval_set.name,
            "question_id": q.question_id,
            "question": q.question,
            "ground_truth_response": q.ground_truth_response,
            # API name -> upload name. See rule 1.
            "ground_truth_reasoning_process_description": q.ground_truth_reasoning,
            "skill": [s.skill_name for s in q.skills],
        }
        for q in questions
    ]
    rows.sort(key=lambda r: r["question_id"])
    return rows


def run_rows(eval_set: EvalSet, runs: Iterable[Run], credentials: dict) -> list[dict]:
    """Run rows, newest first.

    `credentials` maps run id -> slot names (never values); the caller supplies
    it so this module never touches `Run.secrets` at all.
    """
    return [
        {
            "eval_set_id": str(eval_set.id),
            "eval_set_name": eval_set.name,
            "run_id": str(run.id),
            "run_name": run_label(run),
            "status": run.status,
            "triggered_by": run.triggered_by,
            "started_at": _iso(run.started_at),
            "completed_at": _iso(run.completed_at),
            "pass_rate": float(run.pass_rate) if run.pass_rate is not None else None,
            "total_count": run.total_count,
            "correct_count": run.correct_count,
            "error_message": run.error_message,
            # Through RunConfig, which has no credential fields — rule 3.
            "config": RunConfig(**(run.config or {})).model_dump(),
            "credentials_set": credentials.get(run.id, []),
        }
        for run in runs
    ]


def result_rows(
    eval_set: EvalSet,
    runs: Iterable[Run],
    results: Iterable[QuestionResult],
    questions_by_pk: dict[uuid.UUID, Question],
) -> list[dict]:
    """One row per (run x question), grouped by run in the caller's run order
    and sorted by `question_id` within each run."""
    by_run: dict[uuid.UUID, list[QuestionResult]] = {}
    for r in results:
        by_run.setdefault(r.run_id, []).append(r)

    rows = []
    for run in runs:
        # A result whose question has since gone with the set is skipped rather
        # than exported with blank text.
        pairs = [
            (questions_by_pk[r.question_pk], r)
            for r in by_run.get(run.id, [])
            if r.question_pk in questions_by_pk
        ]
        pairs.sort(key=lambda pair: pair[0].question_id)
        for question, r in pairs:
            rows.append(
                {
                    "eval_set_id": str(eval_set.id),
                    "eval_set_name": eval_set.name,
                    "run_id": str(run.id),
                    "run_name": run_label(run),
                    "question_id": question.question_id,
                    "question": question.question,
                    "agent_response": r.agent_response,
                    "verdict": r.verdict,
                    "judge_score": float(r.judge_score) if r.judge_score is not None else None,
                    "judge_comment": r.judge_comment,
                    "status": r.status,
                    # Same derivation the UI colours its list with, so "answered
                    # but not judged" reads identically in both places.
                    "phase": result_phase(
                        r.status, r.agent_response, r.verdict, r.failure_kind
                    ),
                    "error_message": r.error_message,
                    "failure_kind": r.failure_kind,
                    "started_at": _iso(r.started_at),
                    "agent_latency_ms": r.agent_latency_ms,
                    "llm_call_count": r.llm_call_count,
                    "correlation_id": r.correlation_id,
                    "trace_ready": r.trace_ready,
                }
            )
    return rows


def _csv_cell(value: Any) -> str:
    """One value as CSV text.

    Lists and dicts become JSON literals rather than Python `repr`s — that is
    what makes the `skill` column re-uploadable, since `parseSkillCell` reads
    `["billing","reports"]` as a list. A plain comma-joined string would also
    parse, but not for a skill whose own name contains a comma.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def to_csv(rows: list[dict], fields: tuple[str, ...]) -> str:
    """Rows as CSV text, header first.

    `\\r\\n` line endings and the leading BOM are what make Excel open a UTF-8
    file with Chinese question text intact; without the BOM it renders as
    mojibake, and opening this in Excel is the whole point of the CSV option.

    Neither disturbs re-upload: `tokenizeCsv` skips `\\r`, and the BOM lands on
    `eval_set_id` — the first column, and not one the parser looks up — where
    `parse_csv`'s `.trim()` would strip it anyway (JS counts U+FEFF as
    whitespace). The cost is that `pandas.read_csv` shows the BOM on that one
    column name unless it is read with `encoding="utf-8-sig"`; Excel opening
    cleanly for everyone is worth more than that.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(fields), extrasaction="ignore", lineterminator="\r\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_cell(row.get(k)) for k in fields})
    return "﻿" + buffer.getvalue()


def to_jsonl(rows: list[dict], fields: tuple[str, ...]) -> str:
    """Rows as JSONL, one object per line, keys in `fields` order.

    Values keep their JSON types here — `skill` stays a real list, which is what
    `parse_jsonl` requires (`isinstance(skill, list)`), and numbers stay numbers.
    """
    lines = [
        json.dumps({k: row.get(k) for k in fields}, ensure_ascii=False)
        for row in rows
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def json_document(payload: Any) -> str:
    """A pretty-printed JSON file.

    Indented because these are files a person opens and reads, not payloads on
    a wire — the manifest especially is meant to be the first thing someone
    looks at to work out what they have been handed.
    """
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"


def today_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


MEDIA_TYPES = {
    # `text/csv` without a charset makes Excel on Windows guess the encoding;
    # naming UTF-8 alongside the BOM `to_csv` writes leaves nothing to guess.
    "csv": "text/csv; charset=utf-8",
    "jsonl": "application/x-ndjson; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def build_manifest(
    eval_set: EvalSet,
    *,
    exported_by: str,
    files: list[str],
    counts: dict[str, int],
    run_ids: list[uuid.UUID],
    fmt: str,
) -> dict:
    """What this archive is, for whoever opens it later.

    `question_id_policy` is recorded because the system has two answers and they
    disagree on purpose: an export preserves ids so a re-uploaded set can still
    be compared question-by-question against the original, while
    `POST /eval-sets/from-shortlist` mints new ones because a derived set is a
    different set. Without this line, a reader finding the same id in two sets
    cannot tell which rule produced it.
    """
    judge_system, judge_user = judge_prompt.effective(
        eval_set.judge_system_prompt, eval_set.judge_user_prompt
    )
    judge_is_default = judge_prompt.is_default(
        eval_set.judge_system_prompt, eval_set.judge_user_prompt
    )
    judge_fingerprint = judge_prompt.fingerprint(
        eval_set.judge_system_prompt, eval_set.judge_user_prompt
    )
    return {
        "export_format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": exported_by,
        "source": {
            "eval_set_id": str(eval_set.id),
            "eval_set_name": eval_set.name,
            "description": eval_set.description,
            "metadata": eval_set.meta or {},
            "version": eval_set.version,
            "created_at": _iso(eval_set.created_at),
            # How this set's answers are graded, as it stands today. In the
            # manifest and not in questions.* on purpose: a judge prompt is
            # thousands of characters and identical on every row, so a column of
            # it would make the CSV unopenable in the tools people export for.
            # Each run's own frozen copy already travels in runs.config, which is
            # what makes the per-run pass rates in this archive interpretable.
            "judge_prompt": {
                "system_prompt": judge_system,
                "user_prompt": judge_user,
                "is_default": judge_is_default,
                "fingerprint": judge_fingerprint,
            },
        },
        "files": files,
        "counts": counts,
        "run_ids": [str(r) for r in run_ids],
        "format": fmt,
        "join_key": ["eval_set_id", "question_id"],
        "question_id_policy": "preserved",
        "notes": [
            "questions.* can be re-uploaded; doing so creates a NEW eval set "
            "rather than updating the source set.",
            "question_id is unique per eval set, not globally — join on "
            "(eval_set_id, question_id).",
            "Share lists and credentials are never exported.",
        ],
    }


def build_zip(files: dict[str, str | bytes]) -> bytes:
    """Files as a ZIP, deflated, in insertion order.

    A fixed timestamp keeps two exports of unchanged data byte-identical, which
    is what lets someone diff or checksum them.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(
                info, content.encode("utf-8") if isinstance(content, str) else content
            )
    return buffer.getvalue()


def slugify(name: str) -> str:
    """A filename-safe stem for the download.

    Non-ASCII names (the common case here) collapse to nothing, so the caller's
    fallback matters — an eval set named entirely in Chinese must still produce
    a usable filename rather than a leading dash.
    """
    out = []
    for ch in (name or "").lower():
        if ch.isascii() and (ch.isalnum()):
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")
    return slug[:60] or "eval-set"
