"""The forked SkillOpt edit applier: multi-file, path-validated, mode-protected.

Upstream SkillOpt (`skillopt/optimizer/skill.py`) applies edits to **one string**
— the whole skill is a single markdown document. Here a skill is a *directory*
(`billing/SKILL.md` plus everything under `billing/references/`), which is the
one place the algorithm had to be forked rather than vendored verbatim.

Every test below names a specific way that fork can go wrong. They are worth
having because all three failure families are silent:

  * **Wrong file.** Upstream's `append` concatenates at the end of *the*
    document. Ported naively to a dict of files, every appended rule lands in
    whichever file happens to be last — the skill still looks edited, the diff
    still renders, and the rule is simply in the wrong place.
  * **Escaping the skill.** `path` arrives from an LLM and is written into the
    workspace override sent to the agent server. `../../etc/passwd` must never
    become a key in that payload. docs/spec.md §17 makes the same demand of the
    agent server; there is no reason for this end to author one either.
  * **Protection inverted.** `isolated` mode optimises the body and must not
    touch the frontmatter; `routing` mode optimises the frontmatter and must not
    touch the body. Getting that backwards produces a run whose gate is
    measuring one thing while the optimizer edits another.

Parity with upstream is tested too (`test_upstream_parity_*`): with one file, no
frontmatter and nothing protected, each op must behave exactly as
`skillopt/optimizer/skill.py` documents it.
"""
from __future__ import annotations

import pytest

from app.optimizer import skillio
from app.optimizer.vendor.skill import (
    APPENDIX_END,
    APPENDIX_START,
    SLOW_UPDATE_END,
    SLOW_UPDATE_START,
    apply_patch_with_report,
)

SKILL = "billing"
ENTRY = "billing/SKILL.md"
REF = "billing/references/refunds.md"

BODY = (
    "# Billing skill\n"
    "Invoices, balances, refunds and payment status.\n"
    "\n"
    "1. Identify the customer or order the question is about.\n"
    "2. Query the `invoices` table with the SQL tool.\n"
    "3. State the amount and the period explicitly in the answer.\n"
)

FRONTMATTER = (
    "---\n"
    "name: billing\n"
    "description: Invoices, balances, refunds and payment status.\n"
    "---\n"
)


def files(**overrides: str) -> dict[str, str]:
    base = {ENTRY: BODY, REF: "# Refund rules\n- Prorated by service days.\n"}
    base.update(overrides)
    return base


def isolated(f: dict[str, str]):
    return skillio.build_protection(f, skill_dir=SKILL, mode="isolated")


def routing(f: dict[str, str]):
    return skillio.build_protection(f, skill_dir=SKILL, mode="routing")


def apply(f, edits, protection=None):
    protection = protection if protection is not None else isolated(f)
    return apply_patch_with_report(
        f, {"edits": edits}, skill_dir=SKILL, protection=protection
    )


def statuses(reports: list[dict]) -> list[str]:
    return [r["status"] for r in reports]


# --- Multi-file routing -----------------------------------------------------


def test_append_goes_to_the_named_file_not_the_last_one():
    """An appended rule must land in the file the edit names.

    This is the single most likely way the port breaks, and it is invisible
    afterwards: the skill *is* edited, the diff *does* render, and the rule is
    simply sitting in a reference file nobody reads at answer time.
    """
    f = files()
    out, reports = apply(f, [{"op": "append", "path": REF, "content": "- New rule."}])

    assert out[ENTRY] == BODY, "the entry point must be untouched"
    assert out[REF].rstrip().endswith("- New rule.")
    assert statuses(reports) == ["applied_append"]


def test_append_to_an_unknown_path_creates_the_file():
    """Creating a reference file is a legitimate edit and has no dedicated op.

    Upstream has no create-file operation at all. Rather than invent one, an
    `append` naming a path that does not exist yet creates it — but that must be
    reported distinctly, or a typo'd path silently becomes a new file instead of
    editing the intended one.
    """
    f = files()
    new = "billing/references/periods.md"
    out, reports = apply(f, [{"op": "append", "path": new, "content": "# Periods\n"}])

    assert out[new] == "# Periods\n"
    assert statuses(reports) == ["applied_append_created_file"]


def test_replace_only_touches_the_named_file_when_the_target_appears_twice():
    """Two files can legitimately contain the same sentence.

    Upstream resolves `replace` with `str.replace(target, content, 1)` over one
    document. Over a dict of files, resolving by content alone would edit
    whichever file iteration reached first — non-deterministic across Python
    versions and impossible to see in a per-file diff.
    """
    shared = "Prorated by service days."
    f = files(**{ENTRY: BODY + shared + "\n", REF: "# Refund rules\n" + shared + "\n"})

    out, _ = apply(
        f,
        [{"op": "replace", "path": REF, "target": shared, "content": "Prorated by amount."}],
    )

    assert shared in out[ENTRY], "the unnamed file must keep its copy"
    assert shared not in out[REF]
    assert "Prorated by amount." in out[REF]


def test_a_missing_path_defaults_to_the_entry_point_and_says_so():
    """Upstream edits carry no `path`; a model will sometimes still omit it.

    Dropping such an edit would throw away a usable gradient. Silently applying
    it to an arbitrary file would be worse. It goes to SKILL.md and the report
    records that the platform chose the file, not the model.
    """
    f = files()
    out, reports = apply(f, [{"op": "append", "content": "4. Cite the period."}])

    assert out[ENTRY].rstrip().endswith("4. Cite the period.")
    assert reports[0]["path"] == ENTRY
    assert reports[0]["path_defaulted"] is True


def test_later_edits_see_the_result_of_earlier_ones():
    """Edits are applied in sequence, as upstream's `apply_patch` does.

    A ranked patch routinely contains an `append` followed by a `replace`
    targeting the text it just added. Applying every edit against the original
    files would drop the second one as target-not-found.
    """
    f = files()
    out, reports = apply(
        f,
        [
            {"op": "append", "path": ENTRY, "content": "4. Draft rule."},
            {"op": "replace", "path": ENTRY, "target": "4. Draft rule.", "content": "4. Final rule."},
        ],
    )

    assert "4. Final rule." in out[ENTRY]
    assert "4. Draft rule." not in out[ENTRY]
    assert statuses(reports) == ["applied_append", "applied_replace"]


# --- Path validation --------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "../reporting/SKILL.md",
        "billing/../../etc/passwd",
        "/etc/passwd",
        "reporting/SKILL.md",  # a real skill, but not the one being optimised
        "billing/../reporting/SKILL.md",
        "",
    ],
)
def test_a_path_outside_the_skill_directory_is_rejected(bad_path):
    """`path` is LLM output that ends up as a key in the agent's workspace.

    The override replaces the agent's skill directory for that call, so a path
    that escapes `billing/` is not a cosmetic problem — it is this platform
    authoring a write outside the directory it was given. Rejected, recorded,
    and the run continues: one bad edit must not fail a step.
    """
    f = files()
    out, reports = apply(f, [{"op": "append", "path": bad_path, "content": "x"}])

    assert out == f, "no file may change"
    assert statuses(reports) == ["skipped_invalid_path"]


def test_a_rejected_path_does_not_stop_the_edits_after_it():
    """One malformed edit must cost only itself.

    A step's patch is several edits; failing the whole patch on the first bad
    path would throw away good gradient for a model formatting slip.
    """
    f = files()
    out, reports = apply(
        f,
        [
            {"op": "append", "path": "../escape.md", "content": "x"},
            {"op": "append", "path": ENTRY, "content": "4. Kept."},
        ],
    )

    assert "4. Kept." in out[ENTRY]
    assert statuses(reports) == ["skipped_invalid_path", "applied_append"]


def test_emptying_the_entry_point_is_rejected():
    """Deleting SKILL.md is out of scope, and `delete` can express it anyway.

    Upstream's `delete` removes *text*. A delete whose target is the whole
    document leaves an empty SKILL.md, which is deletion by another name: the
    agent would be sent a skill with no instructions and every later step would
    optimise a blank file.
    """
    f = files()
    out, reports = apply(f, [{"op": "delete", "path": ENTRY, "target": BODY}])

    assert out[ENTRY] == BODY
    assert statuses(reports) == ["skipped_would_empty_entry_point"]


# --- Mode-dependent protection ---------------------------------------------


def test_isolated_mode_protects_the_frontmatter():
    """`isolated` optimises the body; the description is not measurable there.

    Only one skill is sent to the agent, so there is no routing decision for a
    description to influence — an edit to it cannot be validated by the gate.
    Letting one through would put unmeasurable text into an accepted skill.
    """
    f = files(**{ENTRY: FRONTMATTER + BODY})
    out, reports = apply(
        f,
        [{"op": "replace", "path": ENTRY, "target": "description: Invoices, balances, refunds and payment status.", "content": "description: Anything at all."}],
    )

    assert out[ENTRY] == FRONTMATTER + BODY
    assert statuses(reports) == ["skipped_protected_region"]


def test_isolated_mode_still_edits_the_body_of_a_file_with_frontmatter():
    """Protecting the frontmatter must not freeze the whole file."""
    f = files(**{ENTRY: FRONTMATTER + BODY})
    out, reports = apply(
        f,
        [{"op": "replace", "path": ENTRY, "target": "1. Identify the customer or order the question is about.", "content": "1. Identify the customer."}],
    )

    assert out[ENTRY].startswith(FRONTMATTER), "frontmatter untouched"
    assert "1. Identify the customer.\n" in out[ENTRY]
    assert statuses(reports) == ["applied_replace"]


def test_routing_mode_protects_the_body():
    """`routing` optimises the description; body edits belong to `isolated`.

    The two modes gate on different guards (routing additionally requires
    activation not to drop). An edit that slipped across would be validated by
    the wrong criterion.
    """
    f = files(**{ENTRY: FRONTMATTER + BODY})
    out, reports = apply(
        f,
        [{"op": "replace", "path": ENTRY, "target": "3. State the amount and the period explicitly in the answer.", "content": "3. Say the number."}],
        protection=routing(f),
    )

    assert out[ENTRY] == FRONTMATTER + BODY
    assert statuses(reports) == ["skipped_protected_region"]


def test_routing_mode_edits_the_description():
    f = files(**{ENTRY: FRONTMATTER + BODY})
    out, reports = apply(
        f,
        [{"op": "replace", "path": ENTRY, "target": "description: Invoices, balances, refunds and payment status.", "content": "description: Customer invoices, outstanding balances, refunds, payment status."}],
        protection=routing(f),
    )

    assert "description: Customer invoices" in out[ENTRY]
    assert out[ENTRY].endswith(BODY), "the body must survive verbatim"
    assert statuses(reports) == ["applied_replace"]


def test_routing_mode_treats_every_other_file_as_read_only():
    """Only SKILL.md has a frontmatter; the reference files have nothing to route on."""
    f = files(**{ENTRY: FRONTMATTER + BODY})
    out, reports = apply(
        f,
        [{"op": "append", "path": REF, "content": "- Anything."}],
        protection=routing(f),
    )

    assert out == f
    assert statuses(reports) == ["skipped_readonly_file"]


def test_routing_mode_rejects_append():
    """Appending to YAML frontmatter blindly produces invalid YAML.

    Routing edits are `replace` of a key's value or `insert_after` a key. An
    `append` has no well-defined insertion point inside a `---` block, and
    guessing one would hand the agent server a skill it cannot parse.
    """
    f = files(**{ENTRY: FRONTMATTER + BODY})
    out, reports = apply(
        f, [{"op": "append", "path": ENTRY, "content": "tags: [x]"}], protection=routing(f)
    )

    assert out == f
    assert statuses(reports) == ["skipped_append_not_allowed"]


def test_routing_mode_on_a_skill_without_frontmatter_protects_everything():
    """A skill with no frontmatter has nothing for routing mode to optimise.

    The run is blocked earlier than this (see the wizard's mode check), but the
    applier must not fall back to editing the body: that would silently turn a
    routing run into a body-optimising run gated on the routing guard.
    """
    f = files()  # no frontmatter
    out, reports = apply(
        f,
        [{"op": "replace", "path": ENTRY, "target": "# Billing skill", "content": "# Billing"}],
        protection=routing(f),
    )

    assert out == f
    assert statuses(reports) == ["skipped_protected_region"]


@pytest.mark.parametrize("mode", ["isolated", "routing"])
def test_upstream_marker_regions_stay_protected_in_both_modes(mode):
    """Slow update and appendix regions are upstream's own protected blocks.

    They are written by the epoch-boundary mechanisms, not by step-level edits,
    and upstream guarantees step edits cannot reach them
    (`skillopt/optimizer/skill.py` `_PROTECTED_REGIONS`). Parameterising the
    protection must not have dropped that guarantee.
    """
    guarded = f"{SLOW_UPDATE_START}\nkeep me\n{SLOW_UPDATE_END}"
    appendix = f"{APPENDIX_START}\nalso keep me\n{APPENDIX_END}"
    f = files(**{ENTRY: FRONTMATTER + BODY + guarded + "\n" + appendix + "\n"})
    protection = skillio.build_protection(f, skill_dir=SKILL, mode=mode)

    out, reports = apply_patch_with_report(
        f,
        {"edits": [
            {"op": "replace", "path": ENTRY, "target": "keep me", "content": "changed"},
            {"op": "delete", "path": ENTRY, "target": "also keep me"},
        ]},
        skill_dir=SKILL,
        protection=protection,
    )

    assert "keep me" in out[ENTRY]
    assert "also keep me" in out[ENTRY]
    assert statuses(reports) == ["skipped_protected_region", "skipped_protected_region"]


def test_append_lands_before_the_protected_regions():
    """Upstream keeps protected blocks at the document tail; so must the fork.

    An append that landed after the slow-update block would put a step-level
    rule inside a region the next epoch rewrites wholesale — the rule would
    vanish without ever appearing as a deletion in any diff.
    """
    guarded = f"{SLOW_UPDATE_START}\nguidance\n{SLOW_UPDATE_END}\n"
    f = files(**{ENTRY: BODY + guarded})

    out, reports = apply(f, [{"op": "append", "path": ENTRY, "content": "4. Appended."}])

    assert out[ENTRY].index("4. Appended.") < out[ENTRY].index(SLOW_UPDATE_START)
    assert statuses(reports) == ["applied_append_before_protected_region"]


# --- Upstream parity --------------------------------------------------------


def test_upstream_parity_insert_after_falls_back_to_append():
    """Upstream appends when `insert_after` cannot find its target.

    Dropping the edit instead would quietly lower the effective learning rate:
    the step reports N edits applied and the skill received fewer.
    """
    f = {ENTRY: BODY}
    out, reports = apply(
        f, [{"op": "insert_after", "path": ENTRY, "target": "nowhere", "content": "4. New."}]
    )

    assert out[ENTRY].rstrip().endswith("4. New.")
    assert statuses(reports) == ["applied_insert_after_fallback_append"]


@pytest.mark.parametrize(
    "edit, expected",
    [
        ({"op": "replace", "target": "", "content": "x"}, "skipped_replace_missing_target"),
        ({"op": "replace", "target": "absent", "content": "x"}, "skipped_replace_target_not_found"),
        ({"op": "delete", "target": ""}, "skipped_delete_missing_target"),
        ({"op": "delete", "target": "absent"}, "skipped_delete_target_not_found"),
        ({"op": "frobnicate", "content": "x"}, "skipped_unknown_op"),
    ],
)
def test_upstream_parity_skip_statuses(edit, expected):
    """The skip vocabulary is upstream's and the Part 2 page renders it verbatim.

    These strings are the only evidence a developer has that an edit the
    optimizer proposed never reached the skill — which is the difference between
    "the model's idea was bad" and "the model's target string had a typo".
    """
    f = {ENTRY: BODY}
    out, reports = apply(f, [{**edit, "path": ENTRY}])

    assert out[ENTRY] == BODY
    assert statuses(reports) == [expected]


def test_upstream_parity_insert_after_places_content_on_the_next_line():
    f = {ENTRY: BODY}
    out, _ = apply(
        f,
        [{
            "op": "insert_after",
            "path": ENTRY,
            "target": "1. Identify the customer or order the question is about.",
            "content": "1b. Confirm the period.",
        }],
    )

    lines = out[ENTRY].splitlines()
    assert lines[lines.index("1. Identify the customer or order the question is about.") + 2] == "1b. Confirm the period."


def test_edit_content_cannot_smuggle_in_protected_markers():
    """Upstream strips region markers out of edit content.

    Without it a model that echoed the markers back would create a second
    slow-update region, and every later `find()` would resolve to the wrong
    block — silently freezing part of the document.
    """
    f = {ENTRY: BODY}
    out, _ = apply(
        f,
        [{"op": "append", "path": ENTRY, "content": f"{SLOW_UPDATE_START} sneaky {SLOW_UPDATE_END}"}],
    )

    assert SLOW_UPDATE_START not in out[ENTRY]
    assert "sneaky" in out[ENTRY]


def test_an_exception_in_one_edit_is_reported_not_raised():
    """A step must survive a malformed edit object.

    Upstream wraps each edit in try/except so one bad payload cannot end the
    run. Here the same rule matters more: a run is an hour of agent calls.
    """
    f = {ENTRY: BODY}
    out, reports = apply(f, [{"op": "replace", "path": ENTRY, "target": None, "content": None}])

    assert out[ENTRY] == BODY
    assert reports[0]["status"] in {"error", "skipped_replace_missing_target"}


# --- skillio: stats, frontmatter, zip, answer leak --------------------------


def test_per_file_stats_counts_added_and_removed_lines():
    """The `+5 / −10` beside each file in the diff tree comes from here.

    It is computed once on the backend and displayed verbatim, so that the
    number in the chart tooltip and the number in the file tree cannot disagree.
    """
    before = {ENTRY: "a\nb\nc\n"}
    after = {ENTRY: "a\nB1\nB2\nc\nd\n"}

    stats = skillio.per_file_stats(before, after)

    assert stats[ENTRY] == {"added": 3, "removed": 1}


def test_per_file_stats_reports_created_and_deleted_files():
    before = {ENTRY: "a\n"}
    after = {ENTRY: "a\n", REF: "x\ny\n"}

    stats = skillio.per_file_stats(before, after)

    assert stats[REF] == {"added": 2, "removed": 0}
    assert ENTRY not in stats, "an unchanged file must not appear as touched"


def test_total_line_changes_matches_the_sum_of_per_file_stats():
    """The step row and the file tree must never disagree about the same edit."""
    before = {ENTRY: "a\nb\n", REF: "x\n"}
    after = {ENTRY: "a\nb2\n", REF: "x\ny\n"}

    added, removed = skillio.total_line_changes(before, after)
    stats = skillio.per_file_stats(before, after)

    assert added == sum(s["added"] for s in stats.values())
    assert removed == sum(s["removed"] for s in stats.values())


@pytest.mark.parametrize(
    "text, expected",
    [
        (FRONTMATTER + BODY, (0, len(FRONTMATTER))),
        (BODY, None),
        ("---\nname: x\n", None),  # unterminated: not a frontmatter block
        ("\n---\nname: x\n---\n", None),  # must be the very first thing
    ],
)
def test_frontmatter_span(text, expected):
    """Routing mode's whole protection rests on locating this block correctly.

    An unterminated `---` must not be read as "everything is frontmatter": in
    routing mode that would make the entire file editable, and in isolated mode
    it would freeze the entire file.
    """
    assert skillio.frontmatter_span(text) == expected


def test_skill_zip_contains_the_files_and_a_manifest():
    """The download is the deliverable — it is how an optimised skill reaches the agent."""
    import io
    import json
    import zipfile

    data = skillio.skill_zip(files(), manifest={"run_id": "r1", "step": 3})

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert ENTRY in names and REF in names
        assert json.loads(z.read("manifest.json"))["step"] == 3


def test_find_answer_leaks_flags_a_gold_answer_copied_into_the_skill():
    """The optimizer sees gold answers, so it can memorise them.

    SkillOpt's analyst prompt forbids hardcoding question-specific values, but a
    prompt is a request. The held-out validation split is the structural defence
    and this is the visible one: a memorised answer is flagged in the diff where
    a person will see it.
    """
    before = {ENTRY: BODY}
    after = {ENTRY: BODY + "When asked about ACME Q2, answer $42,180.00.\n"}

    leaks = skillio.find_answer_leaks(before, after, ["$42,180.00", "Northwind"])

    assert len(leaks) == 1
    assert leaks[0]["path"] == ENTRY
    assert leaks[0]["answer"] == "$42,180.00"


def test_find_answer_leaks_ignores_text_that_was_already_there():
    """Only *added* text can be a new leak.

    Flagging pre-existing text would mark every later step of a run that leaked
    once, and the warning would be ignored within a day.
    """
    seeded = {ENTRY: BODY + "$42,180.00\n"}
    after = {ENTRY: BODY + "$42,180.00\nAlways cite the period.\n"}

    assert skillio.find_answer_leaks(seeded, after, ["$42,180.00"]) == []


# --- The counts and the browser's rows have to describe the same edit --------


def test_line_counts_are_minimal_so_the_browser_can_reproduce_them():
    """The file tree and the diff pane are drawn by two different programs.

    `skillio` counts the lines here; `frontend/src/diff.js` aligns the same two
    files in the browser to draw the rows. Nothing forces those to agree except
    both computing a genuine longest common subsequence — the LCS *length* is
    unique even where the alignment is not, so both counts follow from it.

    This pair is one that `difflib.SequenceMatcher` gets wrong. It is not an LCS:
    it takes the longest matching block and recurses, and here that costs it a
    match, so it reports 3 added and 3 removed where 2 and 2 suffice. On screen
    that is `+3 / −3` in the tree beside two green stripes in the pane, with no
    way for a reader to tell which half is lying.
    """
    before = (
        "- always cite the policy\n\n- always cite the policy\n"
        "Escalate over $500.\n\n- never guess\nRefunds take 5 days.\n"
    )
    after = (
        "\n## Rules\n- always cite the policy\n## Rules\n\n"
        "- never guess\nRefunds take 5 days.\n"
    )
    assert skillio._counts(before, after) == (2, 2)


def test_the_counts_never_exceed_what_the_edit_could_possibly_be():
    """A property, checked on the shapes a skill edit actually takes.

    Both counts are bounded below by the difference in length and above by the
    longer file, and their difference is exactly the change in line count. A
    matcher that misses an alignment breaks the upper bound quietly — the number
    is still plausible, just too big.
    """
    cases = [
        ("a\nb\nc\n", "a\nb\nc\n"),
        ("a\nb\nc\n", "a\nX\nb\nc\n"),
        ("\n\n\n- rule\n\n\n", "\n- rule\n\n- rule\n\n"),
        ("x\n" * 10 + "y\n", "y\n" + "x\n" * 10),
        ("", "a\nb\n"),
        ("a\nb\n", ""),
    ]
    for before, after in cases:
        added, removed = skillio._counts(before, after)
        n_before = len(before.splitlines())
        n_after = len(after.splitlines())
        assert added - removed == n_after - n_before, (before, after)
        assert added <= n_after and removed <= n_before, (before, after)
