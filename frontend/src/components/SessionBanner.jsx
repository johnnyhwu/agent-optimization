import React from "react";
import Banner, { BannerDetail } from "./ui/Banner.jsx";
import Button from "./ui/Button.jsx";
import { logout } from "../auth.js";

// The one line that says the sign-in is running out.
//
// Above the page rather than inside it, because it is true wherever the reader
// is standing and because the thing it warns about — a run stopping — is not
// scoped to a section. It renders nothing at all in the `ok` case, which is
// almost always, so the shell is unchanged for anyone whose session is healthy.
//
// **It offers the fix, not just the diagnosis.** "Your sign-in ends in 12
// minutes" with nothing to press is a sentence that makes someone hunt for a
// sign-out button so they can sign back in. `logout()` returns to the identity
// provider, which is what starts a new session.
//
// Tones are the existing closed vocabulary (`ui_vocabulary.test.js`): warning
// while there is time, error once starting anything is futile.
export default function SessionBanner({ state }) {
  if (!state || state.level === "ok") return null;
  const expired = state.level === "expired";
  return (
    <Banner
      tone={expired ? "error" : "warning"}
      className="is-block session-banner"
      title={expired ? "Your sign-in has ended" : "Your sign-in is about to end"}
      actions={
        <Button variant="secondary" size="sm" onClick={logout}>
          Sign in again
        </Button>
      }
    >
      <BannerDetail>{state.message}</BannerDetail>
    </Banner>
  );
}
