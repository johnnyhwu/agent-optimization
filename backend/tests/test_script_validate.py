"""Static checks on an uploaded script (services/script_validate).

These run before anything is executed and before the user is asked for a database
password, so the failure cases matter as much as the passing one: a script with no
`main()` must be rejected without ever reaching the credential prompt.

Note what is deliberately NOT tested here: nothing about imports, `eval`, or
"dangerous" calls. This validator is a UX device, not a security boundary — see
its module docstring. The security tests live in test_script_sandbox.py.
"""
from __future__ import annotations

from app.services.script_validate import CHECK_IDS, validate_script_source

GOOD = '''
from datetime import date

def rows_for(handler, team):
    return handler.run_sql("SELECT 1 WHERE %s = %s", (team, team))

def main(database_handler) -> list[dict]:
    return rows_for(database_handler, "billing")
'''


def status_of(result, check_id):
    for check in result.checks:
        if check.id == check_id:
            return check.status
    raise AssertionError(f"no check {check_id!r} in {[c.id for c in result.checks]}")


def test_a_well_formed_script_passes_every_check():
    result = validate_script_source(GOOD)
    assert result.ok
    assert [c.status for c in result.checks] == ["pass"] * len(CHECK_IDS)


def test_checks_are_always_returned_in_a_stable_order():
    # The UI renders this list top to bottom; it must not reorder between runs or
    # between a failing and a passing script.
    assert [c.id for c in validate_script_source(GOOD).checks] == list(CHECK_IDS)
    assert [c.id for c in validate_script_source("def x(:").checks] == list(CHECK_IDS)


def test_syntax_error_fails_the_parse_check_and_says_where():
    result = validate_script_source("def main(database_handler)\n    return []\n")
    assert not result.ok
    assert status_of(result, "parses") == "fail"
    detail = next(c.detail for c in result.checks if c.id == "parses")
    assert "line 1" in detail


def test_later_checks_are_skipped_when_the_file_does_not_parse():
    # Reporting "no main()" for a file that simply has a typo sends the user
    # looking for the wrong thing.
    result = validate_script_source("def main(:")
    assert status_of(result, "has_main") == "skipped"
    assert status_of(result, "one_param") == "skipped"


def test_missing_main_fails():
    result = validate_script_source("def helper(x):\n    return x\n")
    assert not result.ok
    assert status_of(result, "has_main") == "fail"


def test_main_nested_in_a_class_does_not_count():
    src = "class Job:\n    def main(self, database_handler):\n        return []\n"
    result = validate_script_source(src)
    assert not result.ok
    assert status_of(result, "has_main") == "fail"


def test_main_nested_in_a_function_does_not_count():
    src = "def outer():\n    def main(database_handler):\n        return []\n"
    result = validate_script_source(src)
    assert not result.ok
    assert status_of(result, "has_main") == "fail"


def test_async_main_is_accepted():
    src = "async def main(database_handler) -> list[dict]:\n    return []\n"
    result = validate_script_source(src)
    assert result.ok
    assert status_of(result, "has_main") == "pass"
    assert result.is_async


def test_sync_main_reports_not_async():
    assert validate_script_source(GOOD).is_async is False


def test_main_with_no_parameters_fails():
    result = validate_script_source("def main():\n    return []\n")
    assert not result.ok
    assert status_of(result, "one_param") == "fail"


def test_main_with_two_parameters_fails():
    result = validate_script_source("def main(database_handler, other):\n    return []\n")
    assert not result.ok
    assert status_of(result, "one_param") == "fail"


def test_main_with_a_wrongly_named_parameter_fails_and_names_both():
    result = validate_script_source("def main(db):\n    return []\n")
    assert not result.ok
    assert status_of(result, "one_param") == "fail"
    detail = next(c.detail for c in result.checks if c.id == "one_param")
    assert "db" in detail and "database_handler" in detail


def test_varargs_do_not_satisfy_the_one_parameter_rule():
    for src in [
        "def main(*args):\n    return []\n",
        "def main(**kwargs):\n    return []\n",
        "def main(database_handler, *rest):\n    return []\n",
    ]:
        assert status_of(validate_script_source(src), "one_param") == "fail", src


def test_a_defaulted_single_parameter_is_still_fine():
    # The system always passes the handler, so a default is harmless — and it is
    # how a script stays runnable by hand.
    src = "def main(database_handler=None):\n    return []\n"
    assert validate_script_source(src).ok


def test_keyword_only_handler_is_accepted():
    src = "def main(*, database_handler):\n    return []\n"
    result = validate_script_source(src)
    assert result.ok
    assert result.handler_is_keyword_only


def test_missing_return_annotation_warns_but_does_not_block():
    src = "def main(database_handler):\n    return []\n"
    result = validate_script_source(src)
    assert result.ok  # a warning must never stop the run
    assert status_of(result, "returns_list") == "warn"


def test_a_wrong_return_annotation_warns_rather_than_failing():
    # The annotation is a hint, not the contract — the contract is checked against
    # the value the script actually returns. Failing here would block a script
    # that works.
    src = "def main(database_handler) -> str:\n    return []\n"
    result = validate_script_source(src)
    assert result.ok
    assert status_of(result, "returns_list") == "warn"


def test_plain_list_annotation_is_accepted():
    src = "def main(database_handler) -> list:\n    return []\n"
    assert status_of(validate_script_source(src), "returns_list") == "pass"


def test_empty_file_fails_cleanly():
    result = validate_script_source("")
    assert not result.ok
    assert status_of(result, "has_main") == "fail"


def test_null_bytes_do_not_crash_the_validator():
    result = validate_script_source("def main(database_handler):\n    return []\n\x00")
    assert not result.ok
    assert status_of(result, "parses") == "fail"


def test_every_check_carries_a_human_label():
    for check in validate_script_source(GOOD).checks:
        assert check.label and not check.label.endswith(".")
