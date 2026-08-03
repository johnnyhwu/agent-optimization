// Who the user is, in both modes.
//
// fake     — the username is picked in the top bar and remembered in
//            localStorage. Every request carries it as `X-User-Subject`. This is
//            what local development, the seeded demo and the owner/viewer
//            permission checks run on.
// keycloak — Authorization Code + PKCE against the company realm. Every request
//            carries `Authorization: Bearer <access token>`.
//
// The rest of the app never branches on this: it calls `getAuthHeaders()` and
// `getUsername()` and gets an answer either way.
//
// **Access tokens here live about 60 seconds.** That number drives two decisions
// below — `getAuthHeaders()` refreshes on demand rather than trusting a timer,
// and the SSE client (api.js) calls it again on every reconnect instead of
// reusing the URL it first connected with.
import { cfg, isKeycloak } from "./app_config.js";

let keycloak = null;
let fakeSubject = localStorage.getItem("subject") || "alice";

/** The signed-in username. Lower-cased to match what the backend stores. */
export function getUsername() {
  if (!isKeycloak) return fakeSubject;
  // The claim is `preferred_username`. Falling back to `sub` would look like it
  // works and quietly put a UUID everywhere a username belongs — in the share
  // list, in `triggered_by`, in every permission row.
  return (keycloak?.tokenParsed?.preferred_username || "").trim().toLowerCase();
}

/** fake mode only: switch identity to exercise owner/viewer permissions. */
export function setUsername(next) {
  fakeSubject = next;
  localStorage.setItem("subject", next);
}

/**
 * Headers that identify the caller, refreshing the token first if it is close
 * to expiring.
 *
 * Async on purpose. A timer alone cannot be trusted: it does not fire while the
 * tab is backgrounded, and with a 60-second token a tab left alone for a minute
 * would come back and fire its next request with a dead one. Refreshing at the
 * point of use means the token is fresh *because* it is about to be used.
 */
export async function getAuthHeaders() {
  if (!isKeycloak) return { "X-User-Subject": fakeSubject };
  try {
    // No-op when more than 30s of life remain.
    await keycloak.updateToken(30);
  } catch {
    // The refresh token is gone (12h lifetime, or the session was ended
    // elsewhere). There is nothing to recover to but a fresh login.
    keycloak.login();
    throw new Error("session expired");
  }
  return { Authorization: `Bearer ${keycloak.token}` };
}

export function logout() {
  if (!isKeycloak) return;
  keycloak.logout({ redirectUri: window.location.origin });
}

/**
 * Resolve once the user is known. Called before the app renders, so no component
 * ever has to handle "identity not decided yet".
 */
export async function initAuth() {
  if (!isKeycloak) return true;

  const { default: Keycloak } = await import("keycloak-js");
  keycloak = new Keycloak({
    url: cfg.keycloakUrl,
    realm: cfg.keycloakRealm,
    // camelCase. `clientid` is silently ignored and the redirect then fails with
    // an "invalid client" that names nothing useful.
    clientId: cfg.keycloakClientId,
  });

  const authenticated = await keycloak.init({
    onLoad: "login-required",
    // The check runs in a hidden iframe and needs third-party cookies, which
    // browsers increasingly refuse. `updateToken` already tells us when the
    // session is gone.
    checkLoginIframe: false,
    // Required for a public client: the authorization code travels through the
    // browser's address bar, so possession of it must not be enough to redeem it.
    // Recent keycloak-js defaults to this; stating it means the guarantee does
    // not depend on which version resolves in the lockfile.
    pkceMethod: "S256",
  });

  // Backstop for the on-demand refresh in `getAuthHeaders`: it keeps a session
  // alive across a long idle stretch on an open tab, so someone who steps away
  // mid-run comes back to a working page rather than a login redirect.
  setInterval(() => keycloak.updateToken(30).catch(() => {}), 20_000);

  return authenticated;
}
