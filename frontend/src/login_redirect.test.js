// The signed-out deep link. Every "?" in the app opens one, in a new tab, and
// the bug it used to hit — a Keycloak error page where the documentation should
// be — is invisible in fake mode and unreachable from a signed-in tab. So the
// rule is tested here rather than found again in production.
import test from "node:test";
import assert from "node:assert/strict";
import { loginRedirect, routeAfterLogin } from "./login_redirect.js";

const at = (hash, { search = "", pathname = "/" } = {}) => ({
  origin: "https://studio.example.com",
  pathname,
  search,
  hash,
});

// What Keycloak puts in the fragment on the way back, in fragment response
// mode. Not a route, and the thing that must never be mistaken for one.
const CALLBACK = "#state=9f2a&session_state=aa-bb&iss=https%3A%2F%2Fsso&code=abc.def";

test("the redirect uri never carries the route", () => {
  const { redirectUri, route } = loginRedirect(
    at("#/documentation/agent-server#skills-endpoint")
  );
  // A `#` here is what the provider rejects, and what makes its answer
  // unparseable if it does not.
  assert.equal(redirectUri.includes("#"), false);
  assert.equal(redirectUri, "https://studio.example.com/");
  assert.equal(route, "#/documentation/agent-server#skills-endpoint");
});

test("a query string survives, because it is not the part that breaks", () => {
  const { redirectUri } = loginRedirect(at("#/optimize", { search: "?debug=1" }));
  assert.equal(redirectUri, "https://studio.example.com/?debug=1");
});

test("no route means clear whatever was parked", () => {
  // `""` and not `null`: a route parked by an attempt that never came back —
  // the provider was down, the tab sat on the login screen — must not send the
  // next plain visit to the app's front door somewhere else.
  assert.equal(loginRedirect(at("")).route, "");
  // "#" alone is not a route. Restoring it leaves a stray character in the
  // address bar and reads as a link that half worked.
  assert.equal(loginRedirect(at("#")).route, "");
});

test("the provider's own callback is not a route and parks nothing", () => {
  // The inbound leg. `initAuth` computes the redirect uri as an argument to
  // `keycloak.init`, so this runs while the OAuth response is still in the
  // address bar. Parking it would lose the deep link *and* put the
  // authorization code back after keycloak-js had just removed it.
  const { redirectUri, route } = loginRedirect(at(CALLBACK));
  assert.equal(route, null);
  assert.equal(redirectUri, "https://studio.example.com/");
});

test("a round trip keeps the page that was asked for", () => {
  // Both legs in order, against the same one-slot store `auth.js` keeps in
  // sessionStorage — this is the sequence the bug was in.
  let parked = null;
  const park = (loc) => {
    const { route } = loginRedirect(loc);
    if (route) parked = route;
    else if (route === "") parked = null;
  };

  park(at("#/documentation/agent-server#skills-endpoint")); // out
  park(at(CALLBACK)); // back, before keycloak-js has cleaned the url
  assert.equal(parked, "#/documentation/agent-server#skills-endpoint");

  // keycloak-js strips its parameters and replaceStates to the bare url first.
  assert.equal(
    routeAfterLogin(at(""), parked),
    "/#/documentation/agent-server#skills-endpoint"
  );
});

test("the parked route comes back on the way in", () => {
  assert.equal(
    routeAfterLogin(at(""), "#/documentation/agent-server#skills-endpoint"),
    "/#/documentation/agent-server#skills-endpoint"
  );
});

test("a tab that already has a route keeps it", () => {
  // Nothing was parked, or the tab never left. Either way the address bar is
  // what the user is looking at, and overwriting it would move them.
  assert.equal(routeAfterLogin(at("#/evaluation"), "#/optimize"), null);
  assert.equal(routeAfterLogin(at(""), ""), null);
  assert.equal(routeAfterLogin(at(""), null), null);
});
