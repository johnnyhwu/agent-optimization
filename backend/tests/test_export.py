"""Downloading an eval set (§6.13 card action).

The tests that matter here are the ones guarding promises the type checker
cannot:

* **Round-trip.** `questions.*` is offered to the developer as re-uploadable, so
  an export is fed straight back through `parse_jsonl`. The names differ from
  the API's on purpose (`ground_truth_reasoning_process_description`, not
  `ground_truth_reasoning`; `skill`, not `skills`), which is exactly the kind of
  detail a later refactor "tidies up" into a file that 422s on upload.
* **Secrets.** `Run.config` and `Run.secrets` are separate columns so that
  credentials leaking is structurally impossible rather than merely unintended.
  That only holds while the export path goes through `RunConfig`, so a run is
  built with a secret in *both* columns and the whole archive is searched for
  its value.
* **Provenance.** The extra `eval_set_id` / `eval_set_name` columns exist because
  `question_id` is unique per set, not globally. They are only free if the
  upload parser ignores them, so the round-trip carries them along.

No database: the service functions are pure, and the two endpoints run against a
stub session that dispatches on the queried entity.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone

import pytest

from app.models import EvalSet, Question, QuestionResult, QuestionSkill, Run
from app.routers import export as export_router
from app.services import export as export_service
from app.services.upload import REQUIRED_FIELDS, parse_jsonl

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


# --- Fixtures ---------------------------------------------------------------

def make_eval_set(name="Customer Support Eval") -> EvalSet:
    es = EvalSet(name=name, description="the demo set", source_format="jsonl")
    es.id = uuid.uuid4()
    es.meta = {"team": "billing"}
    es.version = 1
    es.created_at = NOW
    es.updated_at = NOW
    return es


def make_question(eval_set, qid="q_00000001", skills=("billing",), text="how much?") -> Question:
    q = Question(
        question_id=qid,
        question=text,
        ground_truth_response="NT$1,200",
        ground_truth_reasoning="look up the invoice, then sum the line items",
    )
    q.id = uuid.uuid4()
    q.eval_set_id = eval_set.id
    q.version = 1
    q.skills = [
        QuestionSkill(skill_name=s, ordinal=i) for i, s in enumerate(skills)
    ]
    return q


def make_run(eval_set, name="nightly", secrets=None, config=None) -> Run:
    run = Run(
        triggered_by="alice",
        name=name,
        status="completed",
        config=config if config is not None else {"judge_model": "gpt-4o-mini"},
        secrets=secrets or {},
    )
    run.id = uuid.uuid4()
    run.eval_set_id = eval_set.id
    run.started_at = NOW
    run.completed_at = NOW
    run.pass_rate = 0.5
    run.total_count = 2
    run.correct_count = 1
    run.error_message = None
    run.cancel_requested = False
    return run


def make_result(run, question, verdict="correct", status="done") -> QuestionResult:
    r = QuestionResult(correlation_id="corr-" + question.question_id, status=status)
    r.id = uuid.uuid4()
    r.run_id = run.id
    r.question_pk = question.id
    r.agent_response = "NT$1,200"
    r.verdict = verdict
    r.judge_score = 0.9
    r.judge_comment = "matches"
    r.error_message = None
    r.agent_latency_ms = 1234
    r.trace_ready = True
    r.trace_error = None
    r.diagnosis_error = None
    return r


# --- Round-trip: the export must survive re-upload ---------------------------

def test_questions_jsonl_round_trips_through_the_upload_parser():
    """The whole promise of the `re-uploadable` badge, in one assertion."""
    es = make_eval_set()
    questions = [
        make_question(es, "q_aaa", skills=("billing", "reports"), text="first?"),
        make_question(es, "q_bbb", skills=("refunds",), text="second?"),
    ]

    jsonl = export_service.to_jsonl(
        export_service.question_rows(es, questions), export_service.QUESTION_FIELDS
    )
    parsed = parse_jsonl(jsonl)

    assert parsed.errors == []
    assert [q.question_id for q in parsed.questions] == ["q_aaa", "q_bbb"]
    assert parsed.questions[0].question == "first?"
    assert parsed.questions[0].ground_truth_reasoning.startswith("look up the invoice")
    # Skills survive as a list, in order — the tags are half of why a set is
    # worth re-uploading rather than retyping.
    assert parsed.questions[0].skills == ["billing", "reports"]
    assert parsed.questions[1].skills == ["refunds"]


def test_question_fields_use_the_upload_names_not_the_api_names():
    """Pins the rename. `QuestionOut` calls these `ground_truth_reasoning` and
    `skills`; a file using those names fails upload with 'missing required
    field(s)', which reads as the developer's file being at fault."""
    for field in REQUIRED_FIELDS:
        assert field in export_service.QUESTION_FIELDS
    assert "skill" in export_service.QUESTION_FIELDS
    assert "ground_truth_reasoning" not in export_service.QUESTION_FIELDS
    assert "skills" not in export_service.QUESTION_FIELDS


def test_provenance_columns_ride_along_without_breaking_re_upload():
    """`eval_set_id` is only free as a column because the parser ignores what it
    does not recognise. If that ever stops being true, this fails."""
    es = make_eval_set()
    rows = export_service.question_rows(es, [make_question(es)])

    assert rows[0]["eval_set_id"] == str(es.id)
    assert rows[0]["eval_set_name"] == "Customer Support Eval"

    parsed = parse_jsonl(
        export_service.to_jsonl(rows, export_service.QUESTION_FIELDS)
    )
    assert parsed.errors == []
    assert parsed.questions[0].question_id == "q_00000001"


def test_questions_csv_round_trips_once_the_skill_cell_is_decoded():
    """The CSV path, as far as Python can follow it.

    `upload_parse.js` reads the skill cell with `parseSkillCell`, which accepts a
    JSON array literal — so the check here is that the cell *is* one, and that
    the decoded row still satisfies the backend parser. Writing the array as a
    literal rather than `a, b` is what keeps a skill whose own name contains a
    comma from splitting into two.
    """
    es = make_eval_set()
    questions = [make_question(es, "q_aaa", skills=("billing, urgent", "reports"))]

    text = export_service.to_csv(
        export_service.question_rows(es, questions), export_service.QUESTION_FIELDS
    )
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    row = next(iter(reader))

    assert json.loads(row["skill"]) == ["billing, urgent", "reports"]
    # And the header carries the upload names, which is what the JS parser
    # looks up by name.
    assert "ground_truth_reasoning_process_description" in reader.fieldnames

    rebuilt = dict(row)
    rebuilt["skill"] = json.loads(row["skill"])
    assert parse_jsonl(json.dumps(rebuilt)).errors == []


def test_csv_is_excel_safe():
    """BOM and CRLF, both required for Excel to open UTF-8 Chinese text intact.
    Neither disturbs re-upload (see services/export.to_csv)."""
    es = make_eval_set(name="客服評測")
    text = export_service.to_csv(
        export_service.question_rows(es, [make_question(es, text="欠多少錢？")]),
        export_service.QUESTION_FIELDS,
    )
    assert text.startswith("﻿")
    assert "\r\n" in text
    assert "欠多少錢？" in text


# --- Credentials never reach a file -----------------------------------------

def test_run_rows_carry_slot_names_never_credential_values():
    es = make_eval_set()
    run = make_run(
        es,
        secrets={"llm_api_key": "sk-super-secret", "langfuse_secret_key": "lf-secret"},
    )

    rows = export_service.run_rows(es, [run], {run.id: ["llm", "langfuse"]})

    assert rows[0]["credentials_set"] == ["llm", "langfuse"]
    blob = json.dumps(rows)
    assert "sk-super-secret" not in blob
    assert "lf-secret" not in blob


def test_a_secret_mis_stored_in_config_still_cannot_be_exported():
    """The structural half of the guarantee: run rows go through `RunConfig`,
    which has no credential fields and drops unknown keys. So even a secret
    written into the wrong column never reaches a file."""
    es = make_eval_set()
    run = make_run(
        es, config={"judge_model": "gpt-4o-mini", "llm_api_key": "sk-leaked"}
    )

    rows = export_service.run_rows(es, [run], {})

    assert rows[0]["config"]["judge_model"] == "gpt-4o-mini"
    assert "llm_api_key" not in rows[0]["config"]
    assert "sk-leaked" not in json.dumps(rows)


# --- Results are per (run x question) ---------------------------------------

def test_result_rows_keep_every_run_not_one_representative_per_question():
    """`GET .../results` collapses to one row per question for the UI's left
    column. Exporting that shape would silently drop every earlier run — which
    is most of why someone exports results at all."""
    es = make_eval_set()
    q1 = make_question(es, "q_aaa")
    q2 = make_question(es, "q_bbb")
    newer = make_run(es, name="run-2")
    older = make_run(es, name="run-1")
    results = [
        make_result(newer, q1, verdict="correct"),
        make_result(newer, q2, verdict="incorrect"),
        make_result(older, q1, verdict="incorrect"),
        make_result(older, q2, verdict="incorrect"),
    ]

    rows = export_service.result_rows(
        es, [newer, older], results, {q1.id: q1, q2.id: q2}
    )

    assert len(rows) == 4
    # Grouped by the caller's run order, question_id ascending inside each run.
    assert [(r["run_name"], r["question_id"]) for r in rows] == [
        ("run-2", "q_aaa"),
        ("run-2", "q_bbb"),
        ("run-1", "q_aaa"),
        ("run-1", "q_bbb"),
    ]
    assert rows[0]["phase"] == "judged"


def test_result_rows_skip_results_whose_question_is_gone():
    es = make_eval_set()
    q = make_question(es, "q_aaa")
    run = make_run(es)
    orphan_question = make_question(es, "q_zzz")

    rows = export_service.result_rows(
        es,
        [run],
        [make_result(run, q), make_result(run, orphan_question)],
        {q.id: q},
    )

    assert [r["question_id"] for r in rows] == ["q_aaa"]


def test_running_questions_export_as_pending_rather_than_being_dropped():
    """A run still in flight is exportable; the preview says how many rows are
    unfinished, so the file must actually contain them."""
    es = make_eval_set()
    q = make_question(es, "q_aaa")
    run = make_run(es)
    pending = make_result(run, q, status="pending")
    pending.agent_response = None
    pending.verdict = None

    rows = export_service.result_rows(es, [run], [pending], {q.id: q})

    assert rows[0]["phase"] == "pending"
    assert rows[0]["verdict"] is None


# --- Serialisation details ---------------------------------------------------

def test_jsonl_keeps_types_so_the_parser_sees_a_real_list():
    es = make_eval_set()
    line = export_service.to_jsonl(
        export_service.question_rows(es, [make_question(es, skills=("billing",))]),
        export_service.QUESTION_FIELDS,
    ).strip()

    assert json.loads(line)["skill"] == ["billing"]


def test_empty_table_still_writes_its_header():
    """An empty file with a header says "no rows"; a zero-byte file says
    "something went wrong"."""
    text = export_service.to_csv([], export_service.RESULT_FIELDS)
    assert text.lstrip("﻿").startswith("eval_set_id,eval_set_name,run_id")


def test_zip_is_byte_identical_for_identical_content():
    """Fixed timestamps, so two exports of unchanged data can be diffed or
    checksummed."""
    files = {"a.csv": "x,y\r\n1,2\r\n", "manifest.json": '{"v":1}'}
    assert export_service.build_zip(files) == export_service.build_zip(files)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Customer Support Eval", "customer-support-eval"),
        ("  spaced  out  ", "spaced-out"),
        ("客服評測", "eval-set"),  # no ASCII at all -> the fallback, not "-"
        ("", "eval-set"),
    ],
)
def test_slugify_always_produces_a_usable_filename(name, expected):
    assert export_service.slugify(name) == expected


def test_manifest_records_the_question_id_policy():
    """Two paths in this system disagree about question ids on purpose: an
    export preserves them, `from-shortlist` mints new ones. Whoever finds the
    same id in two sets needs to know which rule produced the file."""
    es = make_eval_set()
    manifest = export_service.build_manifest(
        es, exported_by="alice", files=["questions.csv"], counts={"questions": 2},
        run_ids=[], fmt="csv",
    )

    assert manifest["question_id_policy"] == "preserved"
    assert manifest["join_key"] == ["eval_set_id", "question_id"]
    assert manifest["source"]["eval_set_id"] == str(es.id)
    assert manifest["export_format_version"] == export_service.EXPORT_FORMAT_VERSION


def test_manifest_never_carries_the_share_list():
    """Share entries are user subjects — PII, and meaningless in another
    deployment. The set's own metadata is fine and useful."""
    es = make_eval_set()
    manifest = export_service.build_manifest(
        es, exported_by="alice", files=[], counts={}, run_ids=[], fmt="csv"
    )

    assert manifest["source"]["metadata"] == {"team": "billing"}
    assert "roles" not in manifest["source"]
    assert "shares" not in manifest["source"]


# --- Endpoints ---------------------------------------------------------------

class StubScalars:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return self._items


class StubSession:
    """Dispatches each query on the entity it selects from.

    Routing by entity rather than by call order keeps the tests from breaking
    every time the endpoint reorders its loads. SQL itself is not executed, so
    the run-scope clauses (`latest`, `latest_n`) are not exercised here — the
    endpoint tests below pass `run_scope='all'` and assert on file assembly,
    which is the part that has decisions in it.
    """

    def __init__(self, eval_set=None, questions=(), runs=(), results=(), analyses=()):
        self.eval_set = eval_set
        self.commits = 0
        self._by_entity = {
            "Question": list(questions),
            "Run": list(runs),
            "QuestionResult": list(results),
            "SpanAnalysis": list(analyses),
        }

    async def get(self, model, pk):
        if model.__name__ == "EvalSet":
            return self.eval_set if self.eval_set and self.eval_set.id == pk else None
        return next(
            (o for o in self._by_entity.get(model.__name__, []) if o.id == pk), None
        )

    async def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return StubScalars(self._by_entity.get(entity.__name__, []))

    async def commit(self):
        self.commits += 1


def build_session():
    es = make_eval_set()
    q1 = make_question(es, "q_aaa")
    q2 = make_question(es, "q_bbb")
    run = make_run(es, secrets={"llm_api_key": "sk-super-secret"})
    results = [make_result(run, q1), make_result(run, q2)]
    session = StubSession(
        eval_set=es, questions=[q1, q2], runs=[run], results=results
    )
    return es, session


async def test_export_of_questions_alone_is_a_file_not_a_zip():
    """"Will I get a file or an archive?" is its own small uncertainty. One
    selected file is handed over as that file."""
    es, session = build_session()

    response = await export_router.export_eval_set(
        eval_set_id=es.id, questions=True, runs=False, traces=False,
        fmt="csv", run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    assert response.media_type.startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert "customer-support-eval-questions-" in disposition
    assert disposition.endswith('.csv"')
    body = response.body.decode("utf-8")
    assert "q_aaa" in body and "q_bbb" in body


async def test_full_export_bundles_the_files_and_a_manifest():
    es, session = build_session()

    response = await export_router.export_eval_set(
        eval_set_id=es.id, questions=True, runs=True, traces=False,
        fmt="csv", run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    assert response.media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        names = set(archive.namelist())
        assert names == {"questions.csv", "runs.csv", "results.csv", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        # One row per (run x question): 1 run, 2 questions.
        assert manifest["counts"] == {"questions": 2, "runs": 1, "results": 2}
        assert manifest["exported_by"] == "alice"
        # The manifest lists the data files, not itself.
        assert "manifest.json" not in manifest["files"]


async def test_no_credential_value_appears_anywhere_in_a_full_export():
    """The end-to-end version of the guarantee: the run in this fixture holds a
    secret, and the whole archive is searched for it."""
    es, session = build_session()

    response = await export_router.export_eval_set(
        eval_set_id=es.id, questions=True, runs=True, traces=False,
        fmt="jsonl", run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        blob = "".join(
            archive.read(name).decode("utf-8") for name in archive.namelist()
        )
    assert "sk-super-secret" not in blob
    # ...while the slot name is still reported, which is what makes "the judge
    # failed because no key was set" diagnosable from the export alone.
    assert "llm" in blob


async def test_selecting_nothing_is_rejected_rather_than_sending_an_empty_zip():
    es, session = build_session()

    with pytest.raises(Exception) as excinfo:
        await export_router.export_eval_set(
            eval_set_id=es.id, questions=False, runs=False, traces=False,
            fmt="csv", run_scope="all", last_n=5, run_ids=[],
            subject="alice", session=session,
        )
    assert getattr(excinfo.value, "status_code", None) == 422


async def test_preview_counts_are_real_including_the_awkward_ones():
    """The preview panel is the answer to "what will I actually get", so it
    reports unfinished rows and unready traces rather than rounding them away."""
    es = make_eval_set()
    q1 = make_question(es, "q_aaa")
    q2 = make_question(es, "q_bbb")
    run = make_run(es)
    done = make_result(run, q1)
    still_running = make_result(run, q2, status="pending")
    still_running.agent_response = None
    still_running.verdict = None
    still_running.trace_ready = False
    session = StubSession(
        eval_set=es, questions=[q1, q2], runs=[run], results=[done, still_running]
    )

    preview = await export_router.export_preview(
        eval_set_id=es.id, run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    assert preview["questions"] == 2
    assert preview["runs"] == 1
    assert preview["results"] == 2
    assert preview["results_running"] == 1
    # The pending question has not been asked yet, so it has no trace to fetch.
    assert preview["traces"] == 1
    assert preview["traces_ready"] == 1
    assert preview["filename_stem"] == "customer-support-eval"


async def test_preview_serves_the_columns_the_writer_actually_uses():
    """The dialog prints these as each file's header. Serving them from the
    writer's own tuples is what stops the panel describing a file that no
    longer exists."""
    es, session = build_session()

    preview = await export_router.export_preview(
        eval_set_id=es.id, run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    assert preview["columns"]["questions"] == list(export_service.QUESTION_FIELDS)
    assert preview["columns"]["results"] == list(export_service.RESULT_FIELDS)
    # And they are the header the CSV writer emits, not a parallel list.
    header = export_service.to_csv([], export_service.QUESTION_FIELDS)
    header = header.lstrip("﻿").splitlines()[0]
    assert header.split(",") == preview["columns"]["questions"]


# --- Traces: the branch that reaches out of the database ---------------------
#
# Every test above passes `traces=False`, so `_collect_traces` — the one part of
# an export that makes live calls to the trace store — had no coverage at all.
# It is also the part that used to keep a pooled database connection checked out
# for the whole fetch: `export_max_traces` is 1000 at a concurrency of 8, so one
# download could sit on a connection for minutes while doing nothing with it,
# and the pool is shared with every other request (see app/db.py).


class StubTraceClient:
    """Records the export's reads and reports the session state at each one."""

    def __init__(self, outcome, session_holder=None):
        self.calls: list[str] = []
        self.commits_when_called: list[int] = []
        self.outcome = outcome
        self._session_holder = session_holder

    async def fetch_trace(self, correlation_id):
        self.calls.append(correlation_id)
        session = self._session_holder() if self._session_holder else None
        self.commits_when_called.append(session.commits if session else 0)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def stub_seams(monkeypatch, trace_client):
    from app.integrations import Seams

    monkeypatch.setattr(
        export_router, "build_seams",
        lambda config=None, secrets=None: Seams(
            agent=None, judge=None, trace=trace_client, diagnosis=None
        ),
    )


async def test_trace_export_carries_the_spans_it_fetched(monkeypatch):
    from app.integrations.base import Span, Trace

    es, session = build_session()
    client = StubTraceClient(
        Trace(correlation_id="corr-q_aaa",
              spans=[Span(index=0, tool_name="sql", status="success",
                          input="i", output="o")]),
        session_holder=lambda: session,
    )
    stub_seams(monkeypatch, client)

    response = await export_router.export_eval_set(
        eval_set_id=es.id, questions=False, runs=False, traces=True,
        fmt="jsonl", run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    document = json.loads(response.body.decode("utf-8"))
    assert document["truncated"] is False
    assert [e["trace_state"] for e in document["traces"]] == ["ready", "ready"]
    assert document["traces"][0]["spans"][0]["tool_name"] == "sql"


async def test_trace_export_releases_its_connection_before_fetching(monkeypatch):
    """The ordering guarantee, same rule as GET .../trace.

    Asserted per call rather than at the end, because "committed eventually" is
    not the property that keeps the pool free — "committed before the first
    outbound read" is.
    """
    from app.integrations.base import NOT_READY

    es, session = build_session()
    client = StubTraceClient(NOT_READY, session_holder=lambda: session)
    stub_seams(monkeypatch, client)

    await export_router.export_eval_set(
        eval_set_id=es.id, questions=False, runs=False, traces=True,
        fmt="jsonl", run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    assert client.calls, "precondition: the export should have fetched traces"
    assert all(commits >= 1 for commits in client.commits_when_called), (
        "the export reached the trace store while still holding its database "
        "connection; commit after the last read and before the gather"
    )


async def test_trace_export_reports_failures_instead_of_dropping_them(monkeypatch):
    """An export that silently omitted unreachable traces would read as "this run
    had no traces", which is a different and much more alarming claim."""
    from app.integrations.base import TraceFetchError

    es, session = build_session()
    client = StubTraceClient(TraceFetchError("HTTP 401: invalid credentials"),
                             session_holder=lambda: session)
    stub_seams(monkeypatch, client)

    response = await export_router.export_eval_set(
        eval_set_id=es.id, questions=False, runs=False, traces=True,
        fmt="jsonl", run_scope="all", last_n=5, run_ids=[],
        subject="alice", session=session,
    )

    document = json.loads(response.body.decode("utf-8"))
    assert [e["trace_state"] for e in document["traces"]] == ["error", "error"]
    assert "invalid credentials" in document["traces"][0]["trace_error"]
