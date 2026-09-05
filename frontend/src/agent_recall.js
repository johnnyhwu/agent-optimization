// The agents this developer has connected the playground to before.
//
// Kept in localStorage, keyed by subject, exactly like the shortlist: it costs
// no migration, survives a backend restart, and nothing here is a record — it is
// a convenience so an agent's URL is typed once rather than once per session.
//
// **It prefills; it does not connect.** Reconnecting on load would silently
// point the playground at whichever agent was used last, and the whole reason
// this screen now has a connect step is that "which agent am I talking to?" must
// never be answered by a leftover. The one exception lives in the component: an
// environment that ships an AGENT_CHAT_URL default connects to that on its own,
// because refusing to would make every existing single-agent deployment press a
// button to get back to where it already was.
//
// A developer's saved default from the settings page arrives through that same
// exception, and belongs in it: the defaults endpoint hands it over as
// `agent_chat_url` exactly as the environment's would, so the playground
// connects to it on open. That is not the leftover this file refuses to act on
// — a leftover is the last address that happened to be typed, and this one was
// chosen on a page whose whole subject is which address to use.

const KEY = "playground-agents";
const LIMIT = 5;

function storageKey(subject) {
  return `${KEY}:${subject || "anon"}`;
}

// [{ chat_url, skills_url, timeout_s }], most recently connected first.
//
// Entries written before an agent was named by two URLs carry `base_url` and no
// chat endpoint. They are dropped rather than migrated: the protocol changed
// underneath them, so a URL derived from one would point at an endpoint that
// does not exist — and it would arrive in the field looking like a value
// somebody chose.
export function recentAgents(subject) {
  try {
    const raw = localStorage.getItem(storageKey(subject));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((a) => a && a.chat_url) : [];
  } catch {
    // A corrupt entry is not worth breaking the page over; start clean.
    return [];
  }
}

// Recorded on a *successful* connect only. A URL that could not be reached is
// precisely the one not worth offering back next time.
export function rememberAgent(subject, { chat_url, skills_url, timeout_s }) {
  if (!chat_url) return recentAgents(subject);
  const rest = recentAgents(subject).filter((a) => a.chat_url !== chat_url);
  const items = [
    { chat_url, skills_url: skills_url || "", timeout_s: timeout_s ?? null },
    ...rest,
  ].slice(0, LIMIT);
  try {
    localStorage.setItem(storageKey(subject), JSON.stringify(items));
  } catch {
    // Storage full or blocked: the connection still works, it just won't be
    // offered back. Not worth a message.
  }
  return items;
}
