// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
import { describe, it, expect } from 'vitest';
import {
  AiEndpointError,
  FIXED_PROVIDER_BASES,
  chatCompletionsUrl,
  normalizeBaseUrl,
  parseAllowedBaseUrls,
  resolveOpenAIBaseUrl,
} from '@/lib/ai-endpoint';

describe('normalizeBaseUrl', () => {
  it('canonicalises scheme/host/path', () => {
    expect(normalizeBaseUrl(' https://LLM.example.com:8443/v1/ ')).toBe(
      'https://llm.example.com:8443/v1',
    );
  });
  it('rejects non-http schemes, credentials, query and fragment', () => {
    expect(normalizeBaseUrl('file:///etc/passwd')).toBeNull();
    expect(normalizeBaseUrl('ftp://example.com')).toBeNull();
    expect(normalizeBaseUrl('https://user:pw@example.com/v1')).toBeNull();
    expect(normalizeBaseUrl('https://example.com/v1?x=1')).toBeNull();
    expect(normalizeBaseUrl('https://example.com/v1#frag')).toBeNull();
    expect(normalizeBaseUrl('not a url')).toBeNull();
  });
});

describe('parseAllowedBaseUrls', () => {
  it('returns [] when unset', () => {
    expect(parseAllowedBaseUrls(undefined)).toEqual([]);
    expect(parseAllowedBaseUrls('')).toEqual([]);
  });
  it('parses, dedupes and drops invalid entries', () => {
    expect(
      parseAllowedBaseUrls(
        'http://localhost:11434/v1, http://localhost:11434/v1/,gopher://x,https://a.example/v1',
      ),
    ).toEqual(['http://localhost:11434/v1', 'https://a.example/v1']);
  });
});

describe('resolveOpenAIBaseUrl', () => {
  const allowed = ['http://localhost:11434/v1', 'https://llm.example.com/v1'];

  it('ignores the client baseUrl for openai', () => {
    const url = resolveOpenAIBaseUrl(
      { provider: 'openai', baseUrl: 'http://169.254.169.254/latest' },
      allowed,
    );
    expect(url.href).toBe(new URL(FIXED_PROVIDER_BASES.openai).href);
  });

  it('accepts an allow-listed custom endpoint (normalised match)', () => {
    const url = resolveOpenAIBaseUrl(
      { provider: 'custom', baseUrl: 'https://LLM.example.com/v1/' },
      allowed,
    );
    expect(url.href).toBe('https://llm.example.com/v1');
  });

  it('uses the first allow-listed entry when baseUrl is empty', () => {
    const url = resolveOpenAIBaseUrl({ provider: 'ollama', baseUrl: '' }, allowed);
    expect(url.href).toBe('http://localhost:11434/v1');
  });

  it('rejects endpoints that are not allow-listed', () => {
    for (const baseUrl of [
      'http://169.254.169.254/latest/meta-data',
      'http://localhost:11434/v1/../../admin',
      'https://llm.example.com.evil.test/v1',
      'https://llm.example.com/v1?redirect=http://internal',
      'file:///etc/passwd',
    ]) {
      expect(() =>
        resolveOpenAIBaseUrl({ provider: 'custom', baseUrl }, allowed),
      ).toThrow(AiEndpointError);
    }
  });

  it('rejects custom endpoints when no allow-list is configured', () => {
    expect(() =>
      resolveOpenAIBaseUrl({ provider: 'custom', baseUrl: 'https://llm.example.com/v1' }, []),
    ).toThrow(AiEndpointError);
  });
});

describe('chatCompletionsUrl', () => {
  it('appends /chat/completions to the base path', () => {
    expect(chatCompletionsUrl(new URL('https://llm.example.com/v1')).href).toBe(
      'https://llm.example.com/v1/chat/completions',
    );
  });
});
