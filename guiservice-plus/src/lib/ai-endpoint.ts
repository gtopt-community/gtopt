// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * Server-side resolution of the outbound AI provider endpoint.
 *
 * SSRF hardening: the browser sends an `AiConfig` (including a free-form
 * `baseUrl`) to `/api/ai/chat`, which then performs a server-side `fetch`.
 * A user-controlled host in that fetch would let any client make the server
 * talk to arbitrary hosts (internal services, cloud metadata endpoints, ...).
 *
 * Therefore the server never uses the client-supplied URL directly:
 *
 *  - `openai` / `anthropic` always use fixed, hard-coded base URLs.
 *  - `ollama` / `custom` may only use a base URL that the *server operator*
 *    has allow-listed via the `GTOPT_AI_ALLOWED_BASE_URLS` environment
 *    variable (comma-separated list of `http(s)://host[:port]/path`).  The
 *    client value is only used to *select* one of those entries; the URL that
 *    is actually fetched is always built from the operator-configured entry.
 *
 * With `GTOPT_AI_ALLOWED_BASE_URLS` unset, only the fixed OpenAI and
 * Anthropic endpoints are reachable.
 */

import type { AiConfig } from '@/lib/ai-config';

/** Environment variable holding the operator allow-list. */
export const AI_ALLOWED_BASE_URLS_ENV = 'GTOPT_AI_ALLOWED_BASE_URLS';

/** Fixed endpoints for the hosted providers; never user-configurable. */
export const FIXED_PROVIDER_BASES = {
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
} as const;

/** Raised when the requested endpoint is not permitted (maps to HTTP 400). */
export class AiEndpointError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AiEndpointError';
  }
}

/**
 * Canonicalise a base URL to `scheme://host[:port]/path` (no trailing
 * slash, no credentials, no query, no fragment).  Returns `null` if the
 * value is not an acceptable http(s) URL.
 */
export function normalizeBaseUrl(raw: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(raw.trim());
  } catch {
    return null;
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
  if (parsed.username || parsed.password) return null;
  if (parsed.search || parsed.hash) return null;
  const path = parsed.pathname.replace(/\/+$/, '');
  return `${parsed.protocol}//${parsed.host.toLowerCase()}${path}`;
}

/** Parse the operator allow-list; invalid entries are ignored. */
export function parseAllowedBaseUrls(raw: string | undefined): string[] {
  if (!raw) return [];
  const out: string[] = [];
  for (const item of raw.split(',')) {
    const norm = normalizeBaseUrl(item);
    if (norm && !out.includes(norm)) out.push(norm);
  }
  return out;
}

/**
 * Resolve the base URL for an OpenAI-compatible provider.
 *
 * The returned URL is always constructed from a constant or from an
 * operator allow-list entry — never from the client-supplied string.
 */
export function resolveOpenAIBaseUrl(
  config: Pick<AiConfig, 'provider' | 'baseUrl'>,
  allowed: readonly string[] = parseAllowedBaseUrls(
    process.env[AI_ALLOWED_BASE_URLS_ENV],
  ),
): URL {
  if (config.provider === 'openai') {
    return new URL(FIXED_PROVIDER_BASES.openai);
  }
  if (config.provider === 'anthropic') {
    return new URL(FIXED_PROVIDER_BASES.anthropic);
  }

  if (allowed.length === 0) {
    throw new AiEndpointError(
      `Custom AI endpoints are disabled on this server. Ask the administrator ` +
        `to allow-list the endpoint in ${AI_ALLOWED_BASE_URLS_ENV}.`,
    );
  }

  const requested = config.baseUrl ? normalizeBaseUrl(config.baseUrl) : null;
  // With no explicit base URL, fall back to the first allow-listed entry.
  const entry =
    requested === null && !config.baseUrl
      ? allowed[0]
      : allowed.find((candidate) => candidate === requested);

  if (entry === undefined) {
    throw new AiEndpointError(
      `AI base URL is not allow-listed on this server (see ` +
        `${AI_ALLOWED_BASE_URLS_ENV}).`,
    );
  }
  return new URL(entry);
}

/** Build the `/chat/completions` endpoint below a resolved base URL. */
export function chatCompletionsUrl(base: URL): URL {
  const basePath = base.pathname.replace(/\/+$/, '');
  return new URL(`${basePath}/chat/completions`, base.origin);
}
