"""JSONL eval-set upload parsing + question_id generation (§6.11).

JSONL only for Stage 1 (avoids CSV quoting edge cases). One JSON object per line
with the exact §6.11 field names.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

REQUIRED_FIELDS = (
    "question",
    "ground_truth_response",
    "ground_truth_reasoning_process_description",
)


@dataclass
class ParsedQuestion:
    question: str
    ground_truth_response: str
    ground_truth_reasoning: str
    skills: list[str]
    question_id: str  # provided or system-generated (immutable, NOT a content hash)


@dataclass
class ParseResult:
    questions: list[ParsedQuestion] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def generate_question_id() -> str:
    """Immutable, content-independent id (§6.11 — not a content hash)."""
    return "q_" + uuid.uuid4().hex[:8]


def parse_jsonl(raw: str) -> ParseResult:
    result = ParseResult()
    seen_ids: set[str] = set()
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            result.errors.append(f"line {lineno}: invalid JSON ({exc.msg})")
            continue
        if not isinstance(obj, dict):
            result.errors.append(f"line {lineno}: expected a JSON object")
            continue

        missing = [f for f in REQUIRED_FIELDS if not str(obj.get(f, "")).strip()]
        if missing:
            result.errors.append(f"line {lineno}: missing required field(s): {', '.join(missing)}")
            continue

        skill = obj.get("skill")
        if not isinstance(skill, list) or not skill or not all(
            isinstance(s, str) and s.strip() for s in skill
        ):
            result.errors.append(f"line {lineno}: 'skill' must be a non-empty list of strings")
            continue

        qid = obj.get("question_id")
        if qid is not None:
            qid = str(qid).strip()
            if not qid:
                qid = None
        if qid is None:
            qid = generate_question_id()
        if qid in seen_ids:
            result.errors.append(f"line {lineno}: duplicate question_id '{qid}'")
            continue
        seen_ids.add(qid)

        result.questions.append(
            ParsedQuestion(
                question=obj["question"],
                ground_truth_response=obj["ground_truth_response"],
                ground_truth_reasoning=obj["ground_truth_reasoning_process_description"],
                skills=[s.strip() for s in skill],
                question_id=qid,
            )
        )
    if not result.questions and not result.errors:
        result.errors.append("file contained no questions")
    return result
