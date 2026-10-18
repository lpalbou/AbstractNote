#!/usr/bin/env node

/**
 * AbstractNote web app: serves the built app on 127.0.0.1; the gateway serves
 * it at /apps/note/ (see bin/server.js).
 *
 *   abstractnote-web [--gateway-url <url>] [--port <n>] [--host <addr>]
 *
 * PORT, HOST, ABSTRACTNOTE_GATEWAY_URL and ABSTRACTGATEWAY_URL are legacy
 * aliases below the flags. With no flag and no environment the gateway is the
 * one installed on this computer (~/.abstractframework/gateway.json), else
 * http://127.0.0.1:8080.
 */

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createGatewayUrlResolver, parseAppFlagsOrExit } from "@abstractframework/app-server";

import { createNoteServer } from "./server.js";

const HERE = dirname(fileURLToPath(import.meta.url));

const flags = parseAppFlagsOrExit(process.argv.slice(2), {
  appName: "AbstractNote — capture notes, explore what they connect, remember how it changed",
  command: "abstractnote-web",
  envPrefix: "ABSTRACTNOTE",
  defaultPort: 3006,
});

// A gateway URL nobody chose (the pointer, or the built-in default) follows the
// pointer while the server runs; a flag or environment choice never moves.
const chosen = flags.gatewayUrlSource === "flag" || flags.gatewayUrlSource.startsWith("env:");
const gatewayUrl = chosen ? flags.gatewayUrl : createGatewayUrlResolver({});
const server = createNoteServer({ distDir: join(HERE, "..", "dist"), gatewayUrl });

server.listen(flags.port, flags.host, () => {
  console.log(`AbstractNote on http://${flags.host}:${flags.port}/ (gateway ${flags.gatewayUrl}, ${flags.gatewayUrlSource})`);
  console.log("Through the gateway: <gateway>/apps/note/");
});

for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => process.exit(0));
