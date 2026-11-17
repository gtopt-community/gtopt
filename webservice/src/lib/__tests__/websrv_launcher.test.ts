/**
 * Unit tests for the gtopt_websrv.js launcher: default bind address and the
 * "non-loopback bind requires an API token" policy.
 */

const launcher = require("../../../gtopt_websrv.js") as {
  isLoopbackHost: (h: string) => boolean;
  checkBindPolicy: (h: string, env: Record<string, string | undefined>) => string | null;
  parseArgs: () => { hostname: string; port: string };
  DEFAULT_HOSTNAME: string;
};

describe("gtopt_websrv launcher", () => {
  const savedArgv = process.argv;
  const savedEnv = { ...process.env };

  afterEach(() => {
    process.argv = savedArgv;
    process.env = { ...savedEnv };
  });

  it("binds to 127.0.0.1 by default", () => {
    delete process.env.GTOPT_WEBSERVICE_HOST;
    delete process.env.GTOPT_HOSTNAME;
    process.argv = ["node", "gtopt_websrv.js"];
    expect(launcher.DEFAULT_HOSTNAME).toBe("127.0.0.1");
    expect(launcher.parseArgs().hostname).toBe("127.0.0.1");
  });

  it("ignores the system HOSTNAME variable", () => {
    delete process.env.GTOPT_WEBSERVICE_HOST;
    delete process.env.GTOPT_HOSTNAME;
    process.env.HOSTNAME = "my-machine";
    process.argv = ["node", "gtopt_websrv.js"];
    expect(launcher.parseArgs().hostname).toBe("127.0.0.1");
  });

  it("honours GTOPT_WEBSERVICE_HOST and --hostname", () => {
    process.env.GTOPT_WEBSERVICE_HOST = "::1";
    process.argv = ["node", "gtopt_websrv.js"];
    expect(launcher.parseArgs().hostname).toBe("::1");
    process.argv = ["node", "gtopt_websrv.js", "--hostname", "localhost"];
    expect(launcher.parseArgs().hostname).toBe("localhost");
  });

  it("allows loopback binds without a token", () => {
    for (const h of ["127.0.0.1", "localhost", "::1"]) {
      expect(launcher.checkBindPolicy(h, {})).toBeNull();
    }
  });

  it("refuses non-loopback binds without a token", () => {
    for (const h of ["0.0.0.0", "::", "192.168.1.5", "gtopt.example.com"]) {
      expect(launcher.checkBindPolicy(h, {})).toMatch(/GTOPT_WEBSERVICE_API_TOKEN/);
      expect(launcher.checkBindPolicy(h, { GTOPT_WEBSERVICE_API_TOKEN: "" })).not.toBeNull();
    }
  });

  it("allows non-loopback binds with a token", () => {
    expect(
      launcher.checkBindPolicy("0.0.0.0", { GTOPT_WEBSERVICE_API_TOKEN: "x".repeat(32) })
    ).toBeNull();
  });
});
