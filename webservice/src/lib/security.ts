/**
 * Security helpers for the gtopt webservice.
 *
 *  - `validateSystemFile()`  — allowlist validation of the user-supplied
 *    system file name (no traversal, no absolute paths, never an option).
 *  - `resolveInside()`       — resolve a relative path inside a base
 *    directory and confine it there, following symlinks via realpath.
 *  - `isValidJobToken()`     — job tokens are server-generated UUIDs.
 *  - `authorizeRequest()`    — API token / loopback-only access policy.
 *  - `constantTimeEqual()`   — timing-safe string comparison.
 *
 * Everything here is pure (no Next.js imports) so it can be unit-tested
 * with jest and used from both the proxy and the route handlers.
 */

import { promises as fs } from "fs";
import path from "path";

// ---------------------------------------------------------------------------
// System file validation
// ---------------------------------------------------------------------------

/** Maximum accepted length of the `systemFile` form field. */
export const MAX_SYSTEM_FILE_LENGTH = 255;

/**
 * One path segment: must start with an alphanumeric or underscore (so it can
 * never start with `-` or `.`), followed by a conservative character set.
 */
const SEGMENT_RE = /^[A-Za-z0-9_][A-Za-z0-9_.+-]*$/;

export type SystemFileCheck =
  | { ok: true; value: string }
  | { ok: false; error: string };

/**
 * Validate the user-supplied system file name.
 *
 * The value is passed as a positional argument to the gtopt binary (no
 * shell is involved), so the rules aim at (a) never being interpreted as a
 * command-line option and (b) never escaping the job input directory:
 *
 *  - non-empty, at most {@link MAX_SYSTEM_FILE_LENGTH} characters;
 *  - relative, forward-slash separated (no `\`, no drive letters, no NUL);
 *  - every segment matches `[A-Za-z0-9_][A-Za-z0-9_.+-]*` — hence no `..`,
 *    no `.`, no hidden files, and no segment (in particular the first one)
 *    starting with `-`.
 */
export function validateSystemFile(raw: unknown): SystemFileCheck {
  if (typeof raw !== "string") {
    return { ok: false, error: "systemFile must be a string" };
  }
  const value = raw.trim();
  if (value.length === 0) {
    return { ok: false, error: "systemFile must not be empty" };
  }
  if (value.length > MAX_SYSTEM_FILE_LENGTH) {
    return { ok: false, error: "systemFile is too long" };
  }
  if (value.startsWith("-")) {
    return { ok: false, error: "systemFile must not start with '-'" };
  }
  if (value.startsWith("/") || path.isAbsolute(value) || /^[A-Za-z]:/.test(value)) {
    return { ok: false, error: "systemFile must be a relative path" };
  }
  if (value.includes("\\") || value.includes("\0")) {
    return { ok: false, error: "systemFile contains forbidden characters" };
  }
  const segments = value.split("/");
  for (const seg of segments) {
    if (seg === "..") {
      return { ok: false, error: "systemFile must not contain '..'" };
    }
    if (!SEGMENT_RE.test(seg)) {
      return {
        ok: false,
        error:
          "systemFile may only contain letters, digits, '_', '.', '+', '-' " +
          "and '/' separators, and no path segment may start with '.' or '-'",
      };
    }
  }
  return { ok: true, value };
}

// ---------------------------------------------------------------------------
// Path confinement
// ---------------------------------------------------------------------------

function isWithin(base: string, target: string): boolean {
  const rel = path.relative(base, target);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
}

/**
 * Resolve `rel` inside `baseDir` and make sure the result (after following
 * symlinks) is still inside `baseDir`.  Returns the real absolute path, or
 * `null` when the path escapes the base directory or does not exist.
 */
export async function resolveInside(
  baseDir: string,
  rel: string
): Promise<string | null> {
  if (path.isAbsolute(rel)) return null;
  const base = path.resolve(baseDir);
  const candidate = path.resolve(base, rel);
  if (!isWithin(base, candidate)) return null;
  try {
    const [realBase, realCandidate] = await Promise.all([
      fs.realpath(base),
      fs.realpath(candidate),
    ]);
    if (!isWithin(realBase, realCandidate)) return null;
    return realCandidate;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Job tokens
// ---------------------------------------------------------------------------

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Job tokens are server-generated UUIDs; anything else is rejected. */
export function isValidJobToken(token: unknown): token is string {
  return typeof token === "string" && UUID_RE.test(token);
}

// ---------------------------------------------------------------------------
// API authentication
// ---------------------------------------------------------------------------

/** Environment variable holding the shared API token. */
export const API_TOKEN_ENV = "GTOPT_WEBSERVICE_API_TOKEN";

/** Header carrying the API token (alternative to `Authorization: Bearer`). */
export const API_TOKEN_HEADER = "x-gtopt-api-token";

/**
 * Timing-safe string comparison.  Runs over the longer of the two inputs so
 * the time taken does not depend on where the first mismatch occurs.
 */
export function constantTimeEqual(a: string, b: string): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) {
    const ca = i < a.length ? a.charCodeAt(i) : 0;
    const cb = i < b.length ? b.charCodeAt(i) : 0;
    diff |= ca ^ cb;
  }
  return diff === 0;
}

const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

/** True when `hostname` (no port) names the loopback interface. */
export function isLoopbackHostname(hostname: string): boolean {
  const h = hostname.trim().toLowerCase();
  if (LOOPBACK_HOSTNAMES.has(h)) return true;
  // Any 127.0.0.0/8 address
  return /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h);
}

/** Strip the optional `:port` from a Host header value. */
export function hostHeaderHostname(host: string): string {
  const h = host.trim();
  if (h.startsWith("[")) {
    const end = h.indexOf("]");
    return end > 0 ? h.slice(0, end + 1) : h;
  }
  const colon = h.lastIndexOf(":");
  // A bare IPv6 literal has several colons; only strip a single ":port".
  if (colon > 0 && h.indexOf(":") === colon) return h.slice(0, colon);
  return h;
}

/** Extract the API token presented by the client, if any. */
export function presentedToken(
  authorization: string | null | undefined,
  tokenHeader: string | null | undefined
): string | null {
  if (tokenHeader && tokenHeader.length > 0) return tokenHeader;
  if (authorization) {
    const m = /^Bearer\s+(.+)$/i.exec(authorization.trim());
    if (m) return m[1].trim();
  }
  return null;
}

export interface AuthInput {
  /** Request path (e.g. `/api/jobs`). */
  path: string;
  /** Value of the Host header. */
  host: string | null;
  /** Value of the Authorization header. */
  authorization: string | null;
  /** Value of the X-GTOpt-Api-Token header. */
  tokenHeader: string | null;
}

export type AuthResult =
  | { ok: true }
  | { ok: false; status: number; error: string };

/** Paths reachable without a token (liveness only, no information leak). */
const PUBLIC_PATHS = new Set(["/api"]);

/**
 * Access policy for `/api/*` requests.
 *
 *  - When `GTOPT_WEBSERVICE_API_TOKEN` is set, every API path except the
 *    bare liveness probe `GET /api` requires that token, presented as
 *    `Authorization: Bearer <token>` or `X-GTOpt-Api-Token: <token>`, and
 *    compared in constant time.
 *  - When no token is configured the service is loopback-only: the launcher
 *    refuses to bind a non-loopback address, and here the Host header must
 *    name the loopback interface (this also defeats DNS-rebinding attacks
 *    from a browser).  If `GTOPT_WEBSERVICE_HOST` reports a non-loopback
 *    bind address, all API requests are refused.
 */
export function authorizeRequest(
  input: AuthInput,
  env: Record<string, string | undefined> = process.env
): AuthResult {
  if (PUBLIC_PATHS.has(input.path)) return { ok: true };

  const expected = env[API_TOKEN_ENV] ?? "";
  if (expected.length > 0) {
    const got = presentedToken(input.authorization, input.tokenHeader);
    if (got === null) {
      return { ok: false, status: 401, error: "API token required" };
    }
    if (!constantTimeEqual(got, expected)) {
      return { ok: false, status: 403, error: "Invalid API token" };
    }
    return { ok: true };
  }

  const bindHost = env.GTOPT_WEBSERVICE_HOST;
  if (bindHost && !isLoopbackHostname(bindHost)) {
    return {
      ok: false,
      status: 403,
      error:
        `No ${API_TOKEN_ENV} configured: the webservice only serves ` +
        "loopback clients when bound to a non-loopback address without a token.",
    };
  }

  const hostname = hostHeaderHostname(input.host ?? "");
  if (!isLoopbackHostname(hostname)) {
    return {
      ok: false,
      status: 403,
      error:
        `No ${API_TOKEN_ENV} configured: only loopback clients ` +
        "(localhost / 127.0.0.1 / ::1) are accepted.",
    };
  }
  return { ok: true };
}
