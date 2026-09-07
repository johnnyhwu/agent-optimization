// Where to send the browser for sign-in, and how to get back to the page that
// was asked for.
//
// The whole rule is: **the redirect URI must not carry a fragment**, and this
// app keeps its entire route in one (`useHashRoute.js`). Those two facts fight,
// and until now the fragment won.
//
// What that cost: every "?" beside an agent field opens the documentation in a
// new tab, deep-linked to the section being asked about —
// `#/documentation/agent-server#skills-endpoint`. A new tab is a fresh load with
// nothing signed in, so keycloak-js redirected to the provider with
// `redirect_uri` defaulted to `location.href`, fragment and all. Two things go
// wrong with that and either one is enough:
//
//   * **The provider rejects it.** RFC 6749 §3.1.2: the redirection URI "MUST
//     NOT include a fragment component". Keycloak enforces it, and its error
//     page is what appeared instead of the documentation.
//   * **The callback cannot be parsed.** Keycloak answers in fragment response
//     mode, so `code` and `state` arrive appended after the route's own `#` —
//     and keycloak-js reads everything past the *first* `#` as its parameter
//     list. It finds no `state`, concludes this is not a callback, and
//     `login-required` sends the tab back to the provider. A loop.
//
// So the fragment is stripped on the way out and put back on the way in. Pure
// functions over a location-shaped object rather than reads of `window`,
// because this is the kind of rule that is only ever exercised by a signed-out
// deep link against a real identity provider — the case nobody re-tests.

/**
 * The sign-in redirect target — this page without its fragment — and what to do
 * with the fragment that was there.
 *
 * Returns `{ redirectUri, route }`, where `route` is deliberately three-valued,
 * because a sign-in is two page loads and this function runs on both:
 *
 *   `"#/…"`  an app route. Park it; this is the outbound leg.
 *   `""`     nothing to come back to. Clear anything parked, so a route left
 *            over from an attempt that never completed — the provider was
 *            unreachable, the tab was closed at the login screen — cannot
 *            hijack the next plain visit to the app's front door.
 *   `null`   not ours. Leave whatever is parked alone.
 *
 * **`null` is the inbound leg, and it is the whole reason this is not a
 * boolean.** `initAuth` passes the redirect URI as an argument to
 * `keycloak.init`, so it is computed *before* `init` looks at the URL — and on
 * the way back from the provider the fragment is still the OAuth response,
 * `#state=…&code=…`. Treating that as a route parks it over the real one: the
 * deep link is lost, and the authorization code is written back into the
 * address bar immediately after keycloak-js took it out.
 *
 * Telling them apart on the leading `#/` rather than by sniffing for `state`:
 * every address this app produces is `href.*` in `useHashRoute.js` and every one
 * of them starts that way, so "is this one of ours" is the question with the
 * cheap, total answer.
 */
export function loginRedirect({ origin, pathname, search, hash }) {
  const fragment = hash || "";
  const redirectUri = `${origin}${pathname}${search || ""}`;
  if (fragment.startsWith("#/")) return { redirectUri, route: fragment };
  // A bare "#" is not a route: restoring it would leave a stray character in
  // the address bar, and it is what a plain visit to the app can look like.
  if (fragment === "" || fragment === "#") return { redirectUri, route: "" };
  return { redirectUri, route: null };
}

/**
 * The address to put back once identity is settled, or `null` to leave the bar
 * alone.
 *
 * Only ever restores over an *empty* fragment. A tab that came back already
 * carrying a route did not go anywhere — the parked value is the same one or an
 * older one, and either way what is in the address bar is what the user is
 * looking at.
 */
export function routeAfterLogin({ pathname, search, hash }, saved) {
  if (!saved) return null;
  if ((hash || "").length > 1) return null;
  return `${pathname}${search || ""}${saved}`;
}
