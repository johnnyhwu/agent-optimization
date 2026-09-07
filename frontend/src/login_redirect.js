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
 * The sign-in redirect target: this page, without its fragment.
 *
 * Returns `{ redirectUri, route }` — `route` being the fragment to park for
 * `routeAfterLogin` below, or "" when there was nothing worth keeping. A bare
 * "#" is nothing worth keeping: it is not a route, and restoring it would put a
 * stray character in the address bar.
 */
export function loginRedirect({ origin, pathname, search, hash }) {
  const route = (hash || "").length > 1 ? hash : "";
  return { redirectUri: `${origin}${pathname}${search || ""}`, route };
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
