"""The output contract a user's script has to satisfy (services/script_schema).

The script itself imports nothing of ours — it returns plain dicts — so this
module is the only place the shape is enforced. These tests pin the two halves
that matter to the person on the other end: which rows survive, and whether the
warning they get back names the item they have to go and fix.
"""
from __future__ import annotations

import pytest

from app.services.script_schema import (
    MAX_OUTPUT_ROWS,
    ScriptOutputError,
    validate_script_output,
)


def row(**over):
    base = {
        "question": "How much did ACME owe at end of Q2?",
        "ground_truth_response": "ACME owed $42,180.",
        "ground_truth_reasoning_process_description": "Query invoices, sum balances.",
        "skill": ["billing"],
    }
    base.update(over)
    return base


def test_accepts_a_well_formed_list():
    result = validate_script_output([row(), row(question="Second?")])
    assert len(result.rows) == 2
    assert result.warnings == []
    assert result.limits_hit == []
    assert result.rows[0].question == "How much did ACME owe at end of Q2?"
    assert result.rows[0].skill == ["billing"]


def test_question_id_is_optional_and_passed_through():
    result = validate_script_output([row(question_id=" q_keepme "), row()])
    assert result.rows[0].question_id == "q_keepme"
    assert result.rows[1].question_id is None


def test_rejects_a_non_list_return():
    # A whole-output problem is not a per-row warning: there are no rows to keep.
    with pytest.raises(ScriptOutputError) as exc:
        validate_script_output({"question": "x"})
    assert "list" in str(exc.value).lower()


def test_rejects_none_return():
    with pytest.raises(ScriptOutputError) as exc:
        validate_script_output(None)
    assert "list" in str(exc.value).lower()


def test_empty_list_is_an_error_not_an_empty_preview():
    with pytest.raises(ScriptOutputError) as exc:
        validate_script_output([])
    assert "no rows" in str(exc.value).lower()


def test_non_dict_item_becomes_an_indexed_warning():
    result = validate_script_output([row(), "not a dict", row()])
    assert len(result.rows) == 2
    assert len(result.warnings) == 1
    # 1-based, because the warning is read next to a 1-based preview table.
    assert result.warnings[0].startswith("item 2:")


@pytest.mark.parametrize(
    "field",
    [
        "question",
        "ground_truth_response",
        "ground_truth_reasoning_process_description",
    ],
)
def test_each_required_field_is_required(field):
    bad = row()
    del bad[field]
    result = validate_script_output([row(), bad])
    assert len(result.rows) == 1
    assert len(result.warnings) == 1
    assert field in result.warnings[0]
    assert result.warnings[0].startswith("item 2:")


@pytest.mark.parametrize("field", ["question", "ground_truth_response"])
def test_blank_required_field_is_rejected(field):
    result = validate_script_output([row(**{field: "   "})])
    assert result.rows == []
    assert field in result.warnings[0]


@pytest.mark.parametrize("skill", [None, "billing", [], ["  "], [1, 2], {}])
def test_skill_must_be_a_non_empty_list_of_strings(skill):
    result = validate_script_output([row(skill=skill)])
    assert result.rows == []
    assert "skill" in result.warnings[0]


def test_skill_entries_are_stripped():
    result = validate_script_output([row(skill=["  billing ", "reports"])])
    assert result.rows[0].skill == ["billing", "reports"]


def test_unknown_keys_are_ignored_not_fatal():
    # Scripts are written by hand against a database; an extra column selected by
    # accident should not cost the user the whole run.
    result = validate_script_output([row(internal_ticket_id=99)])
    assert len(result.rows) == 1
    assert result.warnings == []


def test_non_string_scalars_are_coerced():
    result = validate_script_output([row(question=12345)])
    assert result.rows[0].question == "12345"


def test_partial_failure_keeps_good_rows_and_warns_about_the_rest():
    items = [row(), row(skill=[]), row(), row(question="")]
    result = validate_script_output(items)
    assert len(result.rows) == 2
    assert [w.split(":")[0] for w in result.warnings] == ["item 2", "item 4"]
    assert result.limits_hit == []


def test_warnings_are_capped_so_a_broken_script_cannot_flood_the_ui():
    result = validate_script_output([row(skill=[]) for _ in range(500)])
    assert result.rows == []
    assert len(result.warnings) <= 51  # 50 detail lines + one "and N more"
    assert "more" in result.warnings[-1]


def test_output_over_the_cap_is_truncated_and_reported():
    items = [row() for _ in range(MAX_OUTPUT_ROWS + 12)]
    result = validate_script_output(items)
    assert len(result.rows) == MAX_OUTPUT_ROWS
    assert len(result.limits_hit) == 1
    hit = result.limits_hit[0]
    # The message has to carry both numbers: "truncated" alone leaves the user
    # unable to tell whether they lost 12 rows or 12,000.
    assert f"{MAX_OUTPUT_ROWS:,}" in hit
    assert f"{MAX_OUTPUT_ROWS + 12:,}" in hit


def test_output_exactly_at_the_cap_is_not_reported_as_truncated():
    result = validate_script_output([row() for _ in range(MAX_OUTPUT_ROWS)])
    assert len(result.rows) == MAX_OUTPUT_ROWS
    assert result.limits_hit == []
