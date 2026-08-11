import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { IconCheck, IconPlus, IconX } from "./icons.jsx";
import Button, { IconButton } from "./ui/Button.jsx";
import Badge from "./ui/Badge.jsx";

// Reusable "share with" editor used by the upload, config and shortlist dialogs.
// Edits a list of {subject, role}. The current user is always an owner and is
// shown locked.
//
// Sharing is still a person typing a colleague's username — but the name is now
// checked against the employee directory before it can be added. The failure
// this prevents is a quiet one: `INSERT INTO eval_set_roles` succeeds for any
// string, so a typo produces an eval set shared with an account that never signs
// in, and nothing anywhere reports it.
//
// **A denied name and an unreachable directory are treated differently.** Only
// the first blocks. If the directory itself is down, refusing to add anyone
// would turn an outage over there into "nobody in the company can share
// anything" over here — so that case warns and lets the add through.
//
// Presentationally this is one bordered list, not a stack of loose rows. It used
// to be three unrelated flex rows whose columns lined up by coincidence — the
// owner row held its last column open with a hardcoded 30px spacer next to a
// control that is 34px, and the add row paired a full-height input with a
// short button. Columns are a grid now, so they line up because they are the
// same columns, and the directory's answer sits under the field that caused it
// rather than at the bottom of the whole block.
const IDLE = { state: "idle" };

export default function ShareEditor({ shares, setShares, currentUser }) {
  const [freeText, setFreeText] = useState("");
  const [check, setCheck] = useState(IDLE);

  const taken = useMemo(
    () => new Set([currentUser, ...shares.map((s) => s.subject)]),
    [shares, currentUser]
  );

  // Identity strings are compared byte-for-byte against what the backend stored,
  // so the same normalisation has to happen on both sides of the wire.
  const typed = freeText.trim().toLowerCase();

  useEffect(() => {
    if (!typed || taken.has(typed)) {
      setCheck(IDLE);
      return undefined;
    }
    let cancelled = false;
    setCheck({ state: "checking" });
    // Debounced: this fires between keystrokes, and the directory is a network
    // hop away.
    const t = setTimeout(() => {
      api
        .lookupUser(typed)
        .then((r) => {
          if (cancelled) return;
          setCheck(
            r.verified
              ? { state: "found", name: r.employee_name }
              : { state: "unverified", reason: r.reason }
          );
        })
        .catch((e) => {
          if (cancelled) return;
          // 404 is the directory answering "no such person" — the one case worth
          // blocking on. Anything else is this app or the network failing, and
          // holding sharing hostage to that would be the worse outcome.
          setCheck(
            e.status === 404
              ? { state: "missing" }
              : { state: "unverified", reason: e.message }
          );
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [typed, taken]);

  const canAdd = typed && !taken.has(typed) && check.state !== "missing" && check.state !== "checking";

  function add() {
    if (!canAdd) return;
    setShares([...shares, { subject: typed, role: "viewer" }]);
    setFreeText("");
    setCheck(IDLE);
  }
  function setRole(subject, role) {
    setShares(shares.map((s) => (s.subject === subject ? { ...s, role } : s)));
  }
  function remove(subject) {
    setShares(shares.filter((s) => s.subject !== subject));
  }

  return (
    <div className="share-editor">
      <div className="share-list">
        <div className="share-row">
          <span className="share-who">
            <strong className="nm">{currentUser}</strong>
            <span className="hint">you</span>
          </span>
          <Badge tone="success">owner</Badge>
          {/* The grid holds this column open, so there is nothing to measure
              and nothing to get wrong. */}
          <span />
        </div>

        {shares.map((s) => (
          <div className="share-row" key={s.subject}>
            <span className="share-who">
              <span className="nm">{s.subject}</span>
            </span>
            <select
              value={s.role}
              aria-label={`Role for ${s.subject}`}
              onChange={(e) => setRole(s.subject, e.target.value)}
            >
              <option value="viewer">viewer</option>
              <option value="owner">owner</option>
            </select>
            <IconButton
              label={`Stop sharing with ${s.subject}`}
              icon={<IconX size={15} />}
              onClick={() => remove(s.subject)}
            />
          </div>
        ))}
      </div>

      <div className="share-add">
        <div className="share-add-row">
          <input
            placeholder="username"
            aria-label="Username to share with"
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <Button icon={<IconPlus size={14} />} onClick={add} disabled={!canAdd}>
            Add
          </Button>
        </div>

        {/* Directly under the field it is about. As a loose line at the bottom
            of the block it read as a statement about the whole share list. */}
        {typed && !taken.has(typed) && (
          <div className="hint share-check">
            {check.state === "checking" && <>Checking the directory…</>}
            {check.state === "found" && (
              <span className="ok-text"><IconCheck size={13} /> {check.name}</span>
            )}
            {check.state === "missing" && (
              <span className="error-text">No employee named “{typed}”. Check the spelling.</span>
            )}
            {check.state === "unverified" && (
              <span className="amber-text">
                Could not verify this name — the employee directory did not answer.
                You can still add it.
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
