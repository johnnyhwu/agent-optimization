// `crypto.randomUUID` for origins the browser calls insecure.
//
// keycloak-js generates the OAuth `state` and `nonce` with `crypto.randomUUID()`
// and throws "Web Crypto API is not available" when it is missing. That happens
// on every sign-in, whether or not PKCE is in play — which is why turning PKCE
// off is not on its own enough to reach a deployment over plain http.
//
// The browser withholds `crypto.subtle` and `crypto.randomUUID` from insecure
// origins, but **not** `crypto.getRandomValues` — that one is available
// everywhere, and it is the only primitive a v4 UUID needs. So this is a
// re-implementation in terms of an API that is present, not a weakening of one
// that isn't: the bits come from the same CSPRNG either way.
//
// Only `randomUUID` is shimmed. `crypto.subtle` would need a SHA-256 in
// JavaScript, and the one thing it buys — the PKCE S256 challenge — protects an
// authorization code that, on a plain-http origin, travels in the clear next to
// the access token it would be exchanged for. See `initAuth` in auth.js.

// "00".."ff", so the hot path is 16 lookups and no per-byte padding.
const HEX = Array.from({ length: 256 }, (_, i) => (i + 0x100).toString(16).slice(1));

function randomUUID() {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // variant 1 (RFC 4122)
  return (
    HEX[b[0]] + HEX[b[1]] + HEX[b[2]] + HEX[b[3]] + "-" +
    HEX[b[4]] + HEX[b[5]] + "-" +
    HEX[b[6]] + HEX[b[7]] + "-" +
    HEX[b[8]] + HEX[b[9]] + "-" +
    HEX[b[10]] + HEX[b[11]] + HEX[b[12]] + HEX[b[13]] + HEX[b[14]] + HEX[b[15]]
  );
}

/**
 * Define `crypto.randomUUID` if the browser has not. No-op when it exists, so
 * a secure context keeps the native implementation.
 *
 * `defineProperty` rather than assignment: `randomUUID` is an accessor on
 * `Crypto.prototype` with no setter, and a plain assignment to it is dropped
 * silently outside strict mode. Defining an own property on the instance
 * shadows the prototype and works in both.
 *
 * @returns {boolean} whether a shim was installed.
 */
export function installRandomUUID() {
  if (typeof crypto === "undefined" || typeof crypto.getRandomValues !== "function") return false;
  if (typeof crypto.randomUUID === "function") return false;
  Object.defineProperty(crypto, "randomUUID", {
    value: randomUUID,
    writable: true,
    configurable: true,
  });
  return true;
}
