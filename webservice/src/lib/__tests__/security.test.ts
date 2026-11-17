/**
 * Unit tests for src/lib/security.ts — systemFile validation (path traversal
 * and option injection), path confinement, job tokens, and API auth policy.
 */

import { promises as fs } from "fs";
import * as os from "os";
import * as path from "path";
import {
  API_TOKEN_ENV,
  authorizeRequest,
  constantTimeEqual,
  hostHeaderHostname,
  isLoopbackHostname,
  isValidJobToken,
  presentedToken,
  resolveInside,
  validateSystemFile,
} from "../security";

describe("validateSystemFile", () => {
  it.each([
    "system_c0.json",
    "c0.json",
    "cases/c0/system_c0.json",
    "IEEE-14b_v2.json",
    "plan+1.json",
  ])("accepts %s", (name) => {
    const r = validateSystemFile(name);
    expect(r.ok).toBe(true);
  });

  it("trims surrounding whitespace", () => {
    const r = validateSystemFile("  c0.json ");
    expect(r).toEqual({ ok: true, value: "c0.json" });
  });

  describe("path traversal", () => {
    it.each([
      "../etc/passwd",
      "a/../../etc/passwd",
      "..",
      "a/..",
      "/etc/passwd",
      "C:/Windows/win.ini",
      "c:evil.json",
      "..\\..\\evil.json",
      "a\\b.json",
      "./c0.json",
      ".hidden.json",
      "a//b.json",
      "a/b/",
      "c0.json\0.txt",
    ])("rejects %j", (name) => {
      expect(validateSystemFile(name).ok).toBe(false);
    });
  });

  describe("option injection", () => {
    it.each([
      "--help",
      "-h",
      "--set=output_directory=/tmp/x",
      "--output-directory",
      "-",
      "a/-rf.json",
      "a/--set.json",
      "c0.json --help",
      "c0.json;rm -rf ~",
      "$(id).json",
      "`id`.json",
      "c0.json\n--help",
    ])("rejects %j", (name) => {
      expect(validateSystemFile(name).ok).toBe(false);
    });
  });

  it("rejects empty, non-string and overlong values", () => {
    expect(validateSystemFile("").ok).toBe(false);
    expect(validateSystemFile("   ").ok).toBe(false);
    expect(validateSystemFile(null).ok).toBe(false);
    expect(validateSystemFile(42).ok).toBe(false);
    expect(validateSystemFile("a".repeat(300) + ".json").ok).toBe(false);
  });
});

describe("resolveInside", () => {
  let tmp: string;
  let base: string;
  let outside: string;

  beforeAll(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "gtopt-sec-"));
    base = path.join(tmp, "input");
    outside = path.join(tmp, "secret.json");
    await fs.mkdir(path.join(base, "sub"), { recursive: true });
    await fs.writeFile(path.join(base, "c0.json"), "{}");
    await fs.writeFile(path.join(base, "sub", "s.json"), "{}");
    await fs.writeFile(outside, "{}");
    await fs.symlink(outside, path.join(base, "link.json"));
    await fs.symlink(tmp, path.join(base, "dirlink"));
  });

  afterAll(async () => {
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("resolves files inside the base directory", async () => {
    const real = await fs.realpath(path.join(base, "c0.json"));
    expect(await resolveInside(base, "c0.json")).toBe(real);
    expect(await resolveInside(base, "sub/s.json")).toBe(
      await fs.realpath(path.join(base, "sub", "s.json"))
    );
  });

  it("rejects '..' escapes and absolute paths", async () => {
    expect(await resolveInside(base, "../secret.json")).toBeNull();
    expect(await resolveInside(base, "sub/../../secret.json")).toBeNull();
    expect(await resolveInside(base, outside)).toBeNull();
    expect(await resolveInside(base, ".")).toBeNull();
  });

  it("rejects symlink escapes via realpath", async () => {
    expect(await resolveInside(base, "link.json")).toBeNull();
    expect(await resolveInside(base, "dirlink/secret.json")).toBeNull();
  });

  it("returns null for missing files", async () => {
    expect(await resolveInside(base, "missing.json")).toBeNull();
  });
});

describe("isValidJobToken", () => {
  it("accepts UUIDs", () => {
    expect(isValidJobToken("3f2504e0-4f89-41d3-9a0c-0305e82c3301")).toBe(true);
  });
  it.each(["", "nonexistent-token", "../../etc", "..", "a/b", "3f2504e0"])(
    "rejects %j",
    (tok) => {
      expect(isValidJobToken(tok)).toBe(false);
    }
  );
});

describe("constantTimeEqual", () => {
  it("compares strings", () => {
    expect(constantTimeEqual("abc", "abc")).toBe(true);
    expect(constantTimeEqual("abc", "abd")).toBe(false);
    expect(constantTimeEqual("abc", "abcd")).toBe(false);
    expect(constantTimeEqual("", "")).toBe(true);
    expect(constantTimeEqual("", "x")).toBe(false);
  });
});

describe("host helpers", () => {
  it("recognises loopback names", () => {
    for (const h of ["localhost", "127.0.0.1", "127.1.2.3", "::1", "[::1]", "LOCALHOST"]) {
      expect(isLoopbackHostname(h)).toBe(true);
    }
    for (const h of ["0.0.0.0", "10.0.0.1", "example.com", "localhost.evil.com", "", "::"]) {
      expect(isLoopbackHostname(h)).toBe(false);
    }
  });

  it("strips ports from Host headers", () => {
    expect(hostHeaderHostname("localhost:3000")).toBe("localhost");
    expect(hostHeaderHostname("127.0.0.1")).toBe("127.0.0.1");
    expect(hostHeaderHostname("[::1]:3000")).toBe("[::1]");
    expect(hostHeaderHostname("evil.com:80")).toBe("evil.com");
  });

  it("extracts presented tokens", () => {
    expect(presentedToken("Bearer abc", null)).toBe("abc");
    expect(presentedToken("bearer  abc ", null)).toBe("abc");
    expect(presentedToken(null, "xyz")).toBe("xyz");
    expect(presentedToken("Basic abc", null)).toBeNull();
    expect(presentedToken(null, null)).toBeNull();
  });
});

describe("authorizeRequest", () => {
  const base = { authorization: null, tokenHeader: null };

  describe("without a configured token (loopback only)", () => {
    const env = {};

    it("allows loopback Host headers", () => {
      for (const host of ["localhost:3000", "127.0.0.1:3000", "[::1]:3000"]) {
        expect(authorizeRequest({ ...base, path: "/api/jobs", host }, env).ok).toBe(true);
      }
    });

    it("rejects non-loopback Host headers (DNS rebinding / remote)", () => {
      for (const host of ["evil.com", "192.168.1.10:3000", "", "0.0.0.0:3000"]) {
        const r = authorizeRequest({ ...base, path: "/api/jobs", host }, env);
        expect(r.ok).toBe(false);
        if (!r.ok) expect(r.status).toBe(403);
      }
      const r = authorizeRequest({ ...base, path: "/api/jobs", host: null }, env);
      expect(r.ok).toBe(false);
    });

    it("refuses everything when bound to a non-loopback address", () => {
      const r = authorizeRequest(
        { ...base, path: "/api/jobs", host: "localhost:3000" },
        { GTOPT_WEBSERVICE_HOST: "0.0.0.0" }
      );
      expect(r.ok).toBe(false);
    });

    it("keeps the GET /api liveness probe public", () => {
      expect(authorizeRequest({ ...base, path: "/api", host: "evil.com" }, env).ok).toBe(true);
    });
  });

  describe("with a configured token", () => {
    const env = { [API_TOKEN_ENV]: "s3cret-token", GTOPT_WEBSERVICE_HOST: "0.0.0.0" };
    const host = "gtopt.example.com";

    it("rejects missing tokens with 401", () => {
      const r = authorizeRequest({ ...base, path: "/api/jobs", host }, env);
      expect(r).toEqual(expect.objectContaining({ ok: false, status: 401 }));
    });

    it("rejects wrong tokens with 403", () => {
      for (const t of ["wrong", "s3cret-toke", "s3cret-token2", ""]) {
        const r = authorizeRequest(
          { ...base, path: "/api/jobs", host, authorization: `Bearer ${t}` },
          env
        );
        expect(r.ok).toBe(false);
      }
    });

    it("accepts the right token via Authorization or X-GTOpt-Api-Token", () => {
      expect(
        authorizeRequest(
          { ...base, path: "/api/jobs", host, authorization: "Bearer s3cret-token" },
          env
        ).ok
      ).toBe(true);
      expect(
        authorizeRequest(
          { ...base, path: "/api/ping", host, tokenHeader: "s3cret-token" },
          env
        ).ok
      ).toBe(true);
    });

    it("still requires the token from loopback clients", () => {
      const r = authorizeRequest({ ...base, path: "/api/jobs", host: "localhost" }, env);
      expect(r.ok).toBe(false);
    });
  });
});
