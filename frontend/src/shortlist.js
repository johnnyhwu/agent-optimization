// The shortlist: playground attempts a developer wants to turn into eval
// questions (§10.8).
//
// **It stores copies, not references, and that is the whole design.** An
// attempt lives in the backend's memory, capped per user and dropped on
// restart — so a shortlist holding attempt ids would lose an entry the moment
// the cap evicted it, which is exactly when someone is iterating hardest. What
// is copied in is everything needed to create a question: the text, the answer,
// the process, and the provenance flags the dialog has to warn about.
//
// It lives in localStorage rather than on the server for the same reason the
// attempts themselves are not in the database: nothing here is a record yet.
// The difference is that a shortlist is the bridge out of scratch work, so
// losing it to a backend restart would be its own small betrayal — localStorage
// survives that, costs no migration, and is scoped per developer by key.

const KEY = "shortlist";

function storageKey(subject) {
  return `${KEY}:${subject || "anon"}`;
}

export function readShortlist(subject) {
  try {
    const raw = localStorage.getItem(storageKey(subject));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // A corrupt entry is not worth breaking the page over; start clean.
    return [];
  }
}

// Every mutator returns the new list, so the one component that owns this state
// updates from the return value rather than re-reading storage.
function write(subject, items) {
  localStorage.setItem(storageKey(subject), JSON.stringify(items));
  return items;
}

// What an attempt contributes to the shortlist, decided once at add time.
//
// The expected answer is prefilled with the agent's own — a playground question
// usually has no ground truth, which is why it was asked here rather than added
// to an eval set. `answer_from_agent` records that, so the dialog can say the
// answer is unverified instead of letting it pass as a checked fact.
export function itemFromAttempt(attempt, detail) {
  return {
    id: attempt.id,
    added_at: new Date().toISOString(),
    question: detail?.question ?? attempt.question,
    ground_truth_response:
      detail?.ground_truth_response || detail?.trace?.agent_response || "",
    answer_from_agent: !detail?.ground_truth_response,
    ground_truth_reasoning: detail?.ground_truth_reasoning || "",
    reasoning_from_synthesis: false,
    skills: "",
    verdict: attempt.verdict || null,
    // The attempt ran against an edited workspace, so its answer is one the
    // deployed agent may not be able to produce (§10.7). The dialog warns; it
    // does not block, because writing the skill back is a Stage 3 capability
    // and the developer may be promoting the question deliberately.
    workspace_overridden: Boolean(attempt.workspace_overridden),
    edited_skill_files: attempt.edited_skill_files || [],
  };
}

export function add(subject, item) {
  const items = readShortlist(subject);
  if (items.some((i) => i.id === item.id)) return items;
  return write(subject, [...items, item]);
}

export function remove(subject, id) {
  return write(subject, readShortlist(subject).filter((i) => i.id !== id));
}

export function update(subject, id, patch) {
  return write(
    subject, readShortlist(subject).map((i) => (i.id === id ? { ...i, ...patch } : i))
  );
}

export function clear(subject) {
  return write(subject, []);
}

// A question can only be created with all three fields (the column is NOT NULL
// for every one of them), so this is what the Create button waits for.
export function missingFields(item) {
  const missing = [];
  if (!item.question.trim()) missing.push("question");
  if (!item.ground_truth_response.trim()) missing.push("expected answer");
  if (!item.ground_truth_reasoning.trim()) missing.push("expected process");
  return missing;
}

export function toPayloadQuestion(item) {
  return {
    question: item.question.trim(),
    ground_truth_response: item.ground_truth_response.trim(),
    ground_truth_reasoning: item.ground_truth_reasoning.trim(),
    skills: item.skills
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
}
