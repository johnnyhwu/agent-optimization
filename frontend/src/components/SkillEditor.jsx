import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { IconRefresh } from "./icons.jsx";

// Pick one of the agent's skills, load its text, edit it, and send the edit with
// the next question as a per-request override (§10.2 / §10.7).
//
// Two things this deliberately does NOT claim:
//   * That the override took effect. The platform cannot verify that — the
//     evidence is the skill text appearing in the trace's first system message,
//     which the span view renders. The hint below says exactly that.
//   * That the catalogue is real. With SKILL_IMPL=fake the names are canned, so
//     the picker says so rather than letting someone edit a fake skill expecting
//     the real agent to have it.
//
// The catalogue is a convenience, not a requirement: if the agent server cannot
// be read, the free-text box still works and the error explains why the dropdown
// is empty. Losing the starting point silently would be worse — the developer
// would retype the skill from memory and test the wrong text.
export default function SkillEditor({ value, onChange, fakeSeam }) {
  const [skills, setSkills] = useState([]);
  const [catalogueError, setCatalogueError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadedContent, setLoadedContent] = useState(null);

  useEffect(() => {
    api
      .listSkills()
      .then((rows) => {
        setSkills(rows);
        setCatalogueError(null);
      })
      .catch((e) => setCatalogueError(e.message));
  }, []);

  async function pick(name) {
    if (!name) {
      onChange(null);
      setLoadedContent(null);
      return;
    }
    setLoading(true);
    try {
      const skill = await api.getSkill(name);
      setLoadedContent(skill.content);
      onChange({ name, content: skill.content });
    } catch (e) {
      // Still let the developer write the override by hand for this skill.
      setLoadedContent(null);
      onChange({ name, content: "" });
      setCatalogueError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const active = value || null;
  const edited = active && loadedContent !== null && active.content !== loadedContent;

  return (
    <div className="skill-editor">
      <div className="field">
        <label>Skill override</label>
        <select
          value={active?.name || ""}
          onChange={(e) => pick(e.target.value)}
          disabled={loading}
        >
          <option value="">None — use the agent's own skill</option>
          {skills.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
              {s.description ? ` — ${s.description}` : ""}
            </option>
          ))}
          {/* A name the catalogue doesn't have (e.g. carried over from a
              question's skill tag) still has to be selectable. */}
          {active && !skills.some((s) => s.name === active.name) && (
            <option value={active.name}>{active.name}</option>
          )}
        </select>
        {fakeSeam && (
          <div className="hint">
            SKILL_IMPL=fake — these skills are canned examples, not the agent's.
          </div>
        )}
        {catalogueError && (
          <div className="hint error-text">
            Could not read the agent's skills: {catalogueError}. You can still
            paste a skill below.
          </div>
        )}
      </div>

      {active && (
        <div className="field">
          <label>
            {active.name} {edited && <span className="badge">edited</span>}
            {loadedContent !== null && (
              <button
                className="linkish"
                onClick={() => onChange({ ...active, content: loadedContent })}
                disabled={!edited}
                title="Restore the text as the agent server has it"
              >
                <IconRefresh size={12} /> revert
              </button>
            )}
          </label>
          <textarea
            className="skill-text"
            value={active.content}
            placeholder={
              loading ? "Loading the current skill…" : "Paste or edit the skill text…"
            }
            onChange={(e) => onChange({ ...active, content: e.target.value })}
          />
          <div className="hint">
            Sent with this one call as <code>metadata.skill_override</code>; nothing
            is written back to the agent server. Whether the agent honoured it is
            visible in the trace — the text appears in the first span's system
            message.
          </div>
        </div>
      )}
    </div>
  );
}
