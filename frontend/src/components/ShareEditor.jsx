import React, { useMemo, useState } from "react";
import { IconPlus, IconUsers, IconX } from "./icons.jsx";

// Reusable "share with" editor used by upload + config dialogs. Edits a list of
// {subject, role}. The current user is always an owner and is shown locked.
// Sharing is by direct name entry only: type a subject (e.g. "bob") and add it.
export default function ShareEditor({ shares, setShares, currentUser }) {
  const [freeText, setFreeText] = useState("");

  const taken = useMemo(
    () => new Set([currentUser, ...shares.map((s) => s.subject)]),
    [shares, currentUser]
  );

  function add(subject) {
    const subj = (subject || "").trim();
    if (!subj || taken.has(subj)) return;
    setShares([...shares, { subject: subj, role: "viewer" }]);
  }
  function setRole(subject, role) {
    setShares(shares.map((s) => (s.subject === subject ? { ...s, role } : s)));
  }
  function remove(subject) {
    setShares(shares.filter((s) => s.subject !== subject));
  }

  return (
    <div>
      <div className="share-row">
        <div className="who">
          <IconUsers size={14} />
          <strong>{currentUser}</strong> <span className="hint">(you)</span>
        </div>
        <span className="rolechip owner">owner</span>
        <span style={{ width: 30 }} />
      </div>

      {shares.map((s) => (
        <div className="share-row" key={s.subject}>
          <div className="who">
            <IconUsers size={14} />
            {s.subject}
          </div>
          <select value={s.role} onChange={(e) => setRole(s.subject, e.target.value)}>
            <option value="viewer">viewer</option>
            <option value="owner">owner</option>
          </select>
          <button className="icon-btn" onClick={() => remove(s.subject)} aria-label="Remove">
            <IconX size={15} />
          </button>
        </div>
      ))}

      <div className="share-add">
        <input
          placeholder="type a name to share with (e.g. bob)"
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); add(freeText); setFreeText(""); }
          }}
        />
        <button onClick={() => { add(freeText); setFreeText(""); }}>
          <IconPlus size={14} /> add
        </button>
      </div>
    </div>
  );
}
