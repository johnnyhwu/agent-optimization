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

test("no route means nothing to park", () => {
  assert.equal(loginRedirect(at("")).route, "");
  // "#" alone is not a route. Restoring it leaves a stray character in the
  // address bar and reads as a link that half worked.
  assert.equal(loginRedirect(at("#")).route, "");
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
});
