import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import * as http from "node:http";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createGatewaySessionProxy, createGatewayUrlResolver } from "@abstractframework/app-server";

// Dev-server twin of bin/server.js: the SAME app-origin session proxy, so sign-in
// and every /api/* call behave in dev exactly as in production.
//   npm run dev -- --gateway-url http://127.0.0.1:18850   (scripts/dev.mjs sets ABSTRACTNOTE_GATEWAY_URL)
//   npm run dev:mock                                       (mock/server.mjs in-process)
function argGatewayUrl(): string {
  return String(process.env.ABSTRACTNOTE_GATEWAY_URL || "").trim();
}

function sessionProxyPlugin(mode: string): Plugin {
  return {
    name: "abstractnote-gateway-session-proxy",
    async configureServer(server) {
      let gatewayUrl: string | ReturnType<typeof createGatewayUrlResolver> = argGatewayUrl() || createGatewayUrlResolver({});
      if (mode === "mock") {
        const port = Number(process.env.ABSTRACTNOTE_MOCK_PORT || 18890);
        // Loaded at run time (not bundled into the config): the mock is a plain Node module.
        const { createMockGateway } = await import(pathToFileURL(resolve(__dirname, "mock/server.mjs")).href);
        const mock = createMockGateway({ processDelayMs: 6000, liveProcessing: true });
        await new Promise<void>((done) => http.createServer(mock.handler).listen(port, "127.0.0.1", () => done()));
        gatewayUrl = `http://127.0.0.1:${port}`;
        server.config.logger.info(`  Mock gateway on ${gatewayUrl} (sign in with the token demo-token)`);
      }
      const proxy = createGatewaySessionProxy({ appId: "abstractnote", defaultGatewayUrl: gatewayUrl });
      server.middlewares.use((req, res, next) => {
        const pathname = new URL(req.url || "/", "http://local").pathname;
        if (pathname.startsWith("/api/") && proxy.handle(req, res, pathname)) return;
        next();
      });
    },
  };
}

// The app's llms.txt ships in dist/ and is served at /llms.txt (dev too): the gateway's
// `GET /docs/corpus?app=note` reads it from the running app to ground the Docs assistant
// (top-bar set, 2026-10-29; the same plugin as the other apps). One file, the repo's own.
const LLMS_TXT = resolve(__dirname, "../llms.txt");
function llmsTxtPlugin(): Plugin {
  return {
    name: "abstractframework-llms-txt",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (String(req.url || "").split("?")[0] !== "/llms.txt") return next();
        res.setHeader("Content-Type", "text/plain; charset=utf-8");
        res.end(readFileSync(LLMS_TXT, "utf8"));
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "llms.txt", source: readFileSync(LLMS_TXT, "utf8") });
    },
  };
}

function packageVersion(): string {
  const pkg = JSON.parse(readFileSync(resolve(__dirname, "package.json"), "utf8"));
  const version = String(pkg?.version || "").trim();
  if (!version) throw new Error("package.json has no version");
  return version;
}

export default defineConfig(({ mode }) => ({
  // Relative asset URLs: the same build serves at / and under the gateway's /apps/note/.
  base: "./",
  plugins: [sessionProxyPlugin(mode), llmsTxtPlugin(), react()],
  define: { __APP_VERSION__: JSON.stringify(packageVersion()) },
  resolve: { dedupe: ["@abstractframework/ui-kit", "@abstractframework/panel-chat", "react", "react-dom"] },
  server: { host: "127.0.0.1", strictPort: false },
  build: { outDir: "dist", sourcemap: true, chunkSizeWarningLimit: 1500 },
  test: { environment: "node", include: ["test/**/*.test.ts"] },
}));
