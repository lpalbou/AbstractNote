#!/usr/bin/env node
/**
 * Development server (Vite) with the app's session proxy:
 *   npm run dev -- --gateway-url http://127.0.0.1:18850 [--port 3006]
 *   npm run dev:mock [-- --port 3006]        (the mock gateway, mock/server.mjs, in-process)
 * Without --gateway-url the gateway is ABSTRACTNOTE_GATEWAY_URL, else the local gateway pointer.
 */
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const opt = (name) => {
  const i = argv.findIndex((a) => a === `--${name}` || a.startsWith(`--${name}=`));
  if (i < 0) return "";
  return argv[i].includes("=") ? argv[i].split("=").slice(1).join("=") : argv[i + 1] || "";
};
const gatewayUrl = opt("gateway-url");
if (gatewayUrl) process.env.ABSTRACTNOTE_GATEWAY_URL = gatewayUrl;
const mock = argv.includes("--mock");
const port = Number(opt("port") || 3006);

const server = await createServer({ configFile: resolve(HERE, "..", "vite.config.ts"), mode: mock ? "mock" : "development", server: { port, host: "127.0.0.1" } });
await server.listen();
server.printUrls();
