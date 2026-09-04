import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  deriveSkillsUrl,
  gateFor,
  probeMatches,
  tierOf,
} from "./agent_endpoints.js";

const ok = { ok: true };
const bad = { ok: false };
const untested = { ok: null };

const all = (state) => ({
  chat: state, skills: state, override: state, trace: state,
});

describe("deriveSkillsUrl", () => {
  it("rewrites the conventional chat suffix", () => {
    assert.equal(
      deriveSkillsUrl("http://agent:8080/v1/chat/completions"),
      "http://agent:8080/skills"
    );
  });

  it("keeps whatever prefix the server is mounted under", () => {
    assert.equal(
      deriveSkillsUrl("https://host/api/agent/v1/chat/completions"),
      "https://host/api/agent/skills"
    );
  });

  it("guesses nothing from an unfamiliar path", () => {
    // We know where the skills live only when the chat endpoint sits at the
    // conventional path. Anywhere else, a guess is a wrong URL that reads as a
    // value somebody chose.
    assert.equal(deriveSkillsUrl("http://agent:8080/ask"), "");
    assert.equal(deriveSkillsUrl(""), "");
  });

  it("ignores trailing slashes and surrounding space", () => {
    assert.equal(
      deriveSkillsUrl("  http://agent:8080/v1/chat/completions/  "),
      "http://agent:8080/skills"
    );
  });
});

describe("tierOf", () => {
  it("is 0 while the chat endpoint has not answered", () => {
    assert.equal(tierOf(all(untested)), 0);
    assert.equal(tierOf({ ...all(ok), chat: bad }), 0);
  });

  it("is 0 without a readable skills endpoint", () => {
    assert.equal(tierOf({ ...all(ok), skills: untested }), 0);
    assert.equal(tierOf({ ...all(ok), skills: bad }), 0);
  });

  it("is 1 when the files can be read but nothing more is proven", () => {
    assert.equal(tierOf({ chat: ok, skills: ok, override: untested, trace: untested }), 1);
  });

  it("is 2 only when the override and the trace both landed", () => {
    assert.equal(tierOf(all(ok)), 2);
    assert.equal(tierOf({ ...all(ok), override: bad }), 1);
    assert.equal(tierOf({ ...all(ok), trace: bad }), 1);
  });

  it("never counts an unattempted check as a pass", () => {
    // A tier is a claim about what was proven. Counting "not asked" as "fine"
    // would show Everything against an agent nobody has called.
    assert.equal(tierOf({ ...all(ok), override: untested }), 1);
  });
});

describe("gateFor", () => {
  it("lets an evaluation start with only a chat endpoint", () => {
    // The entry tier, and the point of the whole change: an eval run sends no
    // override and reads no trace, so neither can be allowed to stop it.
    const gate = gateFor("evaluation", {
      chat: ok, skills: untested, override: untested, trace: untested,
    });
    assert.equal(gate.blocked, false);
    assert.deepEqual(gate.warnings, []);
  });

  it("warns rather than blocks an evaluation when the skills endpoint is broken", () => {
    const gate = gateFor("evaluation", { chat: ok, skills: bad });
    assert.equal(gate.blocked, false);
    assert.equal(gate.warnings.length, 1);
  });

  it("blocks an evaluation only on a chat endpoint that failed", () => {
    const gate = gateFor("evaluation", { chat: bad, skills: ok });
    assert.equal(gate.blocked, true);
    assert.match(gate.reason, /chat endpoint/);
  });

  it("does not block on a check nobody has run yet", () => {
    // This is what lets the Run-eval dialog stay pressable before anyone has
    // spent a model call: it asks on the way past instead. An absent check is
    // "not asked", which is not the same as a check that came back with
    // nothing to report.
    assert.equal(gateFor("evaluation", {}).blocked, false);
    assert.equal(gateFor("optimization", {}).blocked, false);
    assert.equal(gateFor("optimization", { chat: ok }).blocked, false);
  });

  it("separates an unconfigured skills endpoint from an unasked one", () => {
    // Both are `skills.ok !== true`, and only one is a reason to stop. The
    // wizard must block on "there is no URL" and must not block on "the probe
    // has not come back".
    const unasked = gateFor("optimization", { chat: ok, override: ok, trace: ok });
    const unconfigured = gateFor("optimization", {
      chat: ok, skills: untested, override: ok, trace: ok,
    });
    assert.equal(unasked.blocked, false);
    assert.equal(unconfigured.blocked, true);
  });

  it("warns the playground about an override that did not land", () => {
    // Asking questions of the deployed skills is still useful, and this check
    // has real false positives — a refusal, a tool that did not load.
    const gate = gateFor("playground", {
      chat: ok, skills: ok, override: bad, trace: untested,
    });
    assert.equal(gate.blocked, false);
    assert.equal(gate.warnings.length, 1);
    assert.match(gate.warnings[0], /skill files we sent/);
  });

  it("blocks optimization on the same override failure", () => {
    // Where the playground loses a little fidelity, a run loses its meaning:
    // every rollout would measure the deployed skill and report a flat line.
    const gate = gateFor("optimization", {
      chat: ok, skills: ok, override: bad, trace: ok,
    });
    assert.equal(gate.blocked, true);
    assert.match(gate.reason, /optimization run would measure/);
  });

  it("blocks optimization on an unreadable trace", () => {
    const gate = gateFor("optimization", {
      chat: ok, skills: ok, override: ok, trace: bad,
    });
    assert.equal(gate.blocked, true);
    assert.match(gate.reason, /trace/);
  });

  it("blocks optimization when there is no skills endpoint at all", () => {
    const gate = gateFor("optimization", {
      chat: ok, skills: untested, override: ok, trace: ok,
    });
    assert.equal(gate.blocked, true);
    // Not "could not be read" — that sends someone to debug a server that is
    // working perfectly. The fix is to add a URL.
    assert.match(gate.reason, /no skills endpoint/);
  });

  it("names the failed chat endpoint first when several checks failed", () => {
    // Everything downstream of a dead endpoint failed because of it. Leading
    // with the override would send someone to fix an agent that is not running.
    const gate = gateFor("optimization", all(bad));
    assert.match(gate.reason, /chat endpoint/);
  });

  it("refuses an unknown feature rather than defaulting to permissive", () => {
    assert.throws(() => gateFor("whatever", all(ok)), /unknown feature/);
  });
});

describe("probeMatches", () => {
  it("is false once the URL has been edited", () => {
    // An answer about the previous address, shown beside the new one, is worse
    // than none: it is indistinguishable from a check that passed.
    const probe = { forChatUrl: "http://a/chat", forSkillsUrl: "http://a/skills" };
    assert.equal(
      probeMatches(probe, { chatUrl: "http://a/chat", skillsUrl: "http://a/skills" }),
      true
    );
    assert.equal(
      probeMatches(probe, { chatUrl: "http://b/chat", skillsUrl: "http://a/skills" }),
      false
    );
  });

  it("notices a change to the skills URL too", () => {
    const probe = { forChatUrl: "http://a/chat", forSkillsUrl: "" };
    assert.equal(
      probeMatches(probe, { chatUrl: "http://a/chat", skillsUrl: "http://a/skills" }),
      false
    );
  });

  it("treats no probe as no match", () => {
    assert.equal(probeMatches(null, { chatUrl: "", skillsUrl: "" }), false);
  });
});
