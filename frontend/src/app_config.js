// Where every environment-dependent value in the frontend comes from.
//
// Two sources, in priority order:
//
//   1. `window.__APP_CONFIG__`, written by /config.js — a tiny file nginx
//      generates from environment variables when the container starts.
//   2. `import.meta.env?.VITE_*`, baked in by `vite build`.
//
// The order is the whole point. Vite freezes its variables into the bundle at
// build time, so a build that hard-codes the Keycloak URL can only ever be
// deployed at one place; changing environments would mean rebuilding the image.
// Reading them at runtime instead means one image runs anywhere, configured by
// the compose file alone.
//
// The VITE_ fallbacks are not dead code: under `vite dev` there is no nginx and
// therefore no /config.js, so that half is what makes local development work.
//
// (Not to be confused with the served /config.js — that file *sets*
// window.__APP_CONFIG__; this module reads it.)
// `import.meta.env` is Vite's; the optional chaining is what lets this module
// (and everything importing it) also load under plain Node, which is how the
// SSE client below it gets tested.
const runtime = (typeof window !== "undefined" && window.__APP_CONFIG__) || {};

function pick(runtimeValue, buildValue, fallback) {
  // envsubst writes an empty string for an unset variable, so blank has to mean
  // "not configured" rather than "configured as empty".
  const value = runtimeValue || buildValue || fallback;
  return typeof value === "string" ? value.trim() : value;
}

export const cfg = {
  // "fake" trusts a locally chosen username (the top-bar switcher); "keycloak"
  // runs the real OIDC flow. Mirrors AUTH_MODE on the backend — the two have to
  // agree, since a fake frontend sends no token and a keycloak backend rejects
  // anything without one.
  authMode: pick(runtime.AUTH_MODE, import.meta.env?.VITE_AUTH_MODE, "fake"),

  // Behind nginx this is the relative "/api", which is what lets the same bundle
  // work under any hostname. Under `vite dev` the backend is a separate origin.
  apiBase: pick(runtime.API_BASE, import.meta.env?.VITE_API_BASE, "http://localhost:8000"),

  // Copied verbatim, including any /auth suffix: Keycloak dropped that prefix in
  // 17 but a deployment can put it back, and ours does.
  keycloakUrl: pick(runtime.KEYCLOAK_URL, import.meta.env?.VITE_KEYCLOAK_URL, ""),
  keycloakRealm: pick(runtime.KEYCLOAK_REALM, import.meta.env?.VITE_KEYCLOAK_REALM, "tsmc"),
  keycloakClientId: pick(
    runtime.KEYCLOAK_CLIENT_ID,
    import.meta.env?.VITE_KEYCLOAK_CLIENT_ID,
    "ai4bi-public"
  ),

  // "S256" or "off". Only ever set to "off" to reach a deployment over plain
  // http from another machine — see the check in auth.js, which explains what
  // that costs and why the browser leaves no other choice.
  pkceMethod: pick(runtime.PKCE_METHOD, import.meta.env?.VITE_PKCE_METHOD, "S256"),
};

export const isKeycloak = cfg.authMode === "keycloak";
