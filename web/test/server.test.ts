import * as http from "node:http";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
// @ts-expect-error plain ESM module
import { APP_ID, COOKIE_APP_ID, createNoteServer } from "../bin/server.js";

let server: http.Server;
let port = 0;

function get(path: string, headers: Record<string, string> = {}): Promise<{ status: number; headers: http.IncomingHttpHeaders; body: string }> {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path, headers }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => resolve({ status: res.statusCode || 0, headers: res.headers, body }));
    }).on("error", reject);
  });
}

beforeAll(async () => {
  const dist = mkdtempSync(join(tmpdir(), "note-dist-"));
  mkdirSync(join(dist, "assets"));
  writeFileSync(join(dist, "index.html"), '<!doctype html><html><head><title>AbstractNote</title></head><body><script type="module" src="./assets/app.js"></script></body></html>');
  writeFileSync(join(dist, "assets", "app.js"), "console.log(1)");
  // Nothing listens on port 9: the proxy must answer, never hang.
  server = createNoteServer({ distDir: dist, gatewayUrl: "http://127.0.0.1:9" });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  port = (server.address() as { port: number }).port;
});
afterAll(() => server.close());

describe("app server (app-server mount contract)", () => {
  it("announces itself as the note app on every response", async () => {
    expect(APP_ID).toBe("note");
    expect(COOKIE_APP_ID).toBe("abstractnote");
    for (const p of ["/", "/assets/app.js", "/missing.png", "/api/gateway/note/status"]) {
      const r = await get(p);
      expect(r.headers["x-abstractframework-app"], p).toBe("note; mount=1");
    }
  });

  it("serves the page with the shell under the gateway's /apps/note/ prefix", async () => {
    const r = await get("/", { "X-Forwarded-Prefix": "/apps/note" });
    expect(r.status).toBe(200);
    expect(r.body).toContain('<base href="/apps/note/">');
    expect(r.body).toContain("__ABSTRACT_UI_CONFIG__");
    const plain = await get("/");
    expect(plain.body).toContain('<base href="/">');
    // The sign-in dialog defaults to the server's own --gateway-url (operator, 2026-10-19).
    expect(plain.body).toContain('"gateway_url":"http://127.0.0.1:9"');
  });

  it("falls back to the page for app routes, 404 for missing assets", async () => {
    expect((await get("/graph")).status).toBe(200);
    expect((await get("/assets/nope.js")).status).toBe(404);
    expect((await get("/assets/app.js")).body).toContain("console.log");
  });

  it("refuses /api/* without a browser session (the page never holds a token)", async () => {
    const r = await get("/api/gateway/note/status", { Authorization: "Bearer stolen" });
    expect(r.status).toBe(401);
    const c = await get("/api/connection/gateway");
    expect(JSON.parse(c.body)).toMatchObject({ has_session: false });
  });
});
