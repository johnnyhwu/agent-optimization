"""Static shape checks on an uploaded script, before anything is executed.

**This is not a security boundary.** It parses the file and looks for `main()`;
it does not and cannot decide whether the code is safe. There is no import
blacklist here on purpose: `__import__`, `getattr(builtins, ...)` and a dozen
other spellings walk straight through any such list, and a check that can be
bypassed is worse than no check because of what people then assume about it. All
containment lives in `script_runner.py`.

What this *is* for is the developer's time. It runs on the file alone — no
database, no subprocess — so a missing `main()` is reported the moment the file is
chosen, and the UI can hold back the credential prompt until the shape is right.
Nobody should type a production database password to find out they have a typo.

The result is shaped for the checklist in the upload dialog: an ordered list of
checks, each `pass` / `warn` / `fail` / `skipped`, each with a sentence saying what
to do about it.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

HANDLER_PARAM = "database_handler"

# Rendered top to bottom in the UI, in this order, always — including when an
# earlier failure means the later ones could not be evaluated.
CHECK_IDS = ("parses", "has_main", "one_param", "returns_list")

_LABELS = {
    "parses": "File parses as Python",
    "has_main": "Defines a top-level main()",
    "one_param": f"main() takes exactly one argument: {HANDLER_PARAM}",
    "returns_list": "main() is annotated as returning a list",
}


@dataclass
class Check:
    id: str
    label: str
    status: str  # 'pass' | 'warn' | 'fail' | 'skipped'
    detail: str = ""


@dataclass
class ScriptValidation:
    checks: list[Check] = field(default_factory=list)
    # True when nothing is `fail`. A `warn` never blocks: the return annotation is
    # advisory, and the real contract is enforced against the returned value.
    ok: bool = False
    is_async: bool = False
    handler_is_keyword_only: bool = False


def _check(checks: dict, cid: str, status: str, detail: str = "") -> None:
    checks[cid] = Check(id=cid, label=_LABELS[cid], status=status, detail=detail)


def _finish(found: dict, **extra) -> ScriptValidation:
    ordered = [
        found.get(cid, Check(id=cid, label=_LABELS[cid], status="skipped"))
        for cid in CHECK_IDS
    ]
    return ScriptValidation(
        checks=ordered,
        ok=all(c.status != "fail" for c in ordered)
        and all(c.status != "skipped" for c in ordered),
        **extra,
    )


def _top_level_main(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """`main` at module level only.

    A `main` nested in a class or another function is invisible to the runner,
    which reaches into the module namespace — so finding one and calling it a
    pass would be a lie the user only discovers at execution time.
    """
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            return node
    return None


def _annotation_is_list(node) -> bool:
    """`list`, `list[...]`, `List[...]` — anything else is treated as unannotated."""
    if isinstance(node, ast.Name):
        return node.id in ("list", "List")
    if isinstance(node, ast.Subscript):
        return _annotation_is_list(node.value)
    if isinstance(node, ast.Attribute):  # typing.List[...]
        return node.attr in ("list", "List")
    return False


def validate_script_source(source: str) -> ScriptValidation:
    found: dict[str, Check] = {}

    try:
        tree = ast.parse(source or "")
    except (SyntaxError, ValueError) as exc:
        # ValueError covers source containing null bytes, which ast.parse rejects
        # without a line number.
        where = f"line {exc.lineno}: " if getattr(exc, "lineno", None) else ""
        reason = getattr(exc, "msg", None) or str(exc)
        _check(found, "parses", "fail", f"{where}{reason}")
        return _finish(found)
    _check(found, "parses", "pass")

    fn = _top_level_main(tree)
    if fn is None:
        _check(
            found,
            "has_main",
            "fail",
            f"No top-level `def main({HANDLER_PARAM})` was found. A `main` inside a "
            "class or another function does not count — the system calls the "
            "module-level one.",
        )
        return _finish(found)

    is_async = isinstance(fn, ast.AsyncFunctionDef)
    _check(found, "has_main", "pass", "async def" if is_async else "")

    args = fn.args
    positional = list(args.posonlyargs) + list(args.args)
    kwonly = list(args.kwonlyargs)
    names = [a.arg for a in positional + kwonly]
    handler_is_keyword_only = not positional and [a.arg for a in kwonly] == [HANDLER_PARAM]

    if args.vararg or args.kwarg:
        _check(
            found,
            "one_param",
            "fail",
            f"main() must take exactly one argument named `{HANDLER_PARAM}`; "
            "*args/**kwargs are not accepted.",
        )
    elif len(names) != 1:
        _check(
            found,
            "one_param",
            "fail",
            f"main() takes {len(names)} arguments ({', '.join(names) or 'none'}); "
            f"it must take exactly one, named `{HANDLER_PARAM}`.",
        )
    elif names[0] != HANDLER_PARAM:
        _check(
            found,
            "one_param",
            "fail",
            f"main() takes `{names[0]}`; the argument must be named "
            f"`{HANDLER_PARAM}` — the system passes it by name.",
        )
    else:
        _check(found, "one_param", "pass")

    if _annotation_is_list(fn.returns):
        _check(found, "returns_list", "pass")
    else:
        _check(
            found,
            "returns_list",
            "warn",
            "Optional. The return value is checked against the required row "
            "format after the script runs, whatever the annotation says — this is "
            "only a readability hint.",
        )

    return _finish(found, is_async=is_async, handler_is_keyword_only=handler_is_keyword_only)
