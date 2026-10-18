/**
 * The AbstractNote web app server: the built app (dist/) plus the app-origin
 * gateway session proxy, standalone at `/` or through the gateway at
 * `/apps/note/` (the @abstractframework/app-server mount contract):
 *
 * - every response announces `X-AbstractFramework-App: note; mount=1`
 *   (`createMountedHandler`), so the gateway may serve it;
 * - the page gets `<base href="<basePath>/">` and `base_path` in
 *   `window.__ABSTRACT_UI_CONFIG__` (`injectShell`); the app's URLs are relative;
 * - the session proxy relays `/api/*` to the gateway with the browser session
 *   and sets its cookies at `Path=<basePath>/`. The browser never holds a token.
 */

import * as http from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, normalize, resolve, sep } from "node:path";

import { createGatewaySessionProxy, createMountedHandler, injectShell } from "@abstractframework/app-server";

/** The gateway catalog id this app announces (`/apps/note/`). */
export const APP_ID = "note";
/** Cookie / CSRF prefix of the session proxy (`abstractnote_gateway_*`, `x-abstractnote-csrf`). */
export const COOKIE_APP_ID = "abstractnote";

const MIME_TYPES = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".map": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

function mimeType(filePath) {
  return MIME_TYPES[extname(filePath).toLowerCase()] || "application/octet-stream";
}

/**
 * @param {object} options
 * @param {string} options.distDir  the built app
 * @param {string | {current(): string, refresh(): boolean}} options.gatewayUrl  the server-pinned gateway
 * @returns {(req: http.IncomingMessage, res: http.ServerResponse) => void}
 */
export function createNoteHandler(options) {
  const distDir = resolve(options.distDir);
  const proxy = createGatewaySessionProxy({ appId: COOKIE_APP_ID, defaultGatewayUrl: options.gatewayUrl });

  /** A file under dist/, or null (never outside it). */
  function distFile(pathname) {
    let rel;
    try {
      rel = normalize(decodeURIComponent(pathname)).replace(/^([/\\])+/, "");
    } catch {
      return null;
    }
    const abs = resolve(distDir, rel);
    if (abs !== distDir && !abs.startsWith(distDir + sep)) return null;
    try {
      return existsSync(abs) && statSync(abs).isFile() ? abs : null;
    } catch {
      return null;
    }
  }

  function serve(res, filePath, ctx) {
    const type = mimeType(filePath);
    if (type === "text/html") {
      const page = injectShell(readFileSync(filePath, "utf8"), {
        basePath: ctx.basePath,
        config: { gateway_url: proxy.defaultGatewayUrl },
      });
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache" });
      res.end(page);
      return;
    }
    res.writeHead(200, { "Content-Type": type, "Cache-Control": "no-cache" });
    res.end(readFileSync(filePath));
  }

  function servePage(res, ctx) {
    const index = distFile("index.html");
    if (!index) {
      res.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("dist/index.html is missing: build the app with npm run build.");
      return;
    }
    serve(res, index, ctx);
  }

  return createMountedHandler({ appId: APP_ID }, (req, res, ctx) => {
    const pathname = new URL(req.url || "/", "http://note.invalid").pathname;
    // The connection endpoint and everything under /api/ (browser session swapped for gateway credentials).
    if (proxy.handle(req, res, pathname)) return;
    if (pathname === "/" || pathname === "/index.html") return servePage(res, ctx);
    const file = distFile(pathname);
    if (file) return serve(res, file, ctx);
    // An asset that is not in the build is a 404, never the page.
    if (extname(pathname)) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Not Found");
      return;
    }
    // SPA fallback: the page, under the same base.
    return servePage(res, ctx);
  });
}

/** An http.Server for the handler (bin/cli.js listens on it). */
export function createNoteServer(options) {
  return http.createServer(createNoteHandler(options));
}
