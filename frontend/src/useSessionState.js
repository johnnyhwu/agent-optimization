// How much sign-in is left, re-read on a timer.
//
// The *rule* lives in `session_expiry.js` and is tested there; this is only the
// React plumbing that keeps it current, which is why it is deliberately thin —
// see `frontend/CLAUDE.md` on logic belonging in a pure module.
//
// **A timer, not a one-off read.** The session expires while the page sits open,
// which is exactly the case the warning exists for. Recomputed rather than
// re-fetched: the expiry is already in the token keycloak-js holds, so nothing
// here talks to the network.
//
// Thirty seconds is fine for a window measured in minutes, and it means a page
// left open overnight ticks 2,880 times rather than 86,400.
import { useEffect, useState } from "react";
import { getSessionExpiry } from "./auth.js";
import { sessionState } from "./session_expiry.js";

export const POLL_MS = 30_000;

export default function useSessionState(enabled) {
  const [state, setState] = useState(() =>
    sessionState({ refreshExpUnix: getSessionExpiry(), enabled })
  );

  useEffect(() => {
    const read = () =>
      setState((previous) => {
        const next = sessionState({ refreshExpUnix: getSessionExpiry(), enabled });
        // Only when something the UI acts on changed. `sessionState` returns a
        // fresh object every time, so returning it unconditionally would give
        // the whole app tree a new prop twice a minute for the entire time
        // anyone leaves the page open — and `secondsLeft` alone changes on
        // every tick, which nothing renders.
        if (previous && previous.level === next.level && previous.message === next.message) {
          return previous;
        }
        return next;
      });
    read();
    // Cleared on unmount and re-created when `enabled` arrives, which it does
    // one render after mount: the flag is fetched, so the first read runs
    // against `undefined` and has to be redone.
    const id = setInterval(read, POLL_MS);
    return () => clearInterval(id);
  }, [enabled]);

  return state;
}
