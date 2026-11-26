// Test double of the UniFi Access notifications WebSocket, built on the
// widely used "ws" library (an implementation independent from ours).
// Usage: node ws_server.js <cert.pem> <key.pem> <tokens-json> [port]
// Control: JSON lines on stdin. Events: JSON lines on stdout.
"use strict";

const fs = require("fs");
const https = require("https");
const readline = require("readline");

const wsPath = process.env.WS_MODULE_PATH || "/opt/npm-tools/node_modules/ws";
const { WebSocketServer } = require(wsPath);

const [certFile, keyFile, tokensJson, portArg] = process.argv.slice(2);
// tokens: ["t1", ...] (all permissions) or {"t1": ["view:device", ...], ...}
const tokenSpec = JSON.parse(tokensJson || "[]");
const ALL_SCOPES = ["view:device", "view:space", "edit:space"];
const scopes = {};
if (Array.isArray(tokenSpec)) tokenSpec.forEach((t) => (scopes[t] = ALL_SCOPES));
else Object.assign(scopes, tokenSpec);
const DOORS = [
  {
    door_lock_relay_status: "lock",
    door_position_status: "close",
    floor_id: "23c5db06-b59b-494d-94f1-23e88fbe4909",
    full_name: "UCG-Max - GF - Portone",
    id: "0ed545f8-2fcd-4839-9021-b39e707f6aa9",
    is_bind_hub: true,
    name: "Portone",
    type: "door",
  },
];
const NOTIFY_PATH = "/api/v1/developer/devices/notifications";

let muted = false;
const clients = new Set();

function report(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

const server = https.createServer({
  cert: fs.readFileSync(certFile),
  key: fs.readFileSync(keyFile),
});
function tokenOf(req) {
  const auth = req.headers["authorization"] || "";
  return auth.startsWith("Bearer ") ? auth.slice(7) : "";
}

// REST part of the Access API on the same port (doors + unlock).
server.on("request", (req, res) => {
  let body = "";
  req.on("data", (chunk) => (body += chunk));
  req.on("end", () => {
    const token = tokenOf(req);
    let parsed = null;
    try {
      parsed = body ? JSON.parse(body) : null;
    } catch (err) {
      parsed = { invalid: body };
    }
    report({ event: "http", method: req.method, path: req.url, token, body: parsed });
    const reply = (status, payload) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(payload));
    };
    const granted = scopes[token];
    if (!granted) return reply(401, { code: "CODE_UNAUTHORIZED", msg: "unauthorized" });
    if (req.method === "GET" && req.url === "/api/v1/developer/doors") {
      if (!granted.includes("view:space")) return reply(403, { code: "CODE_FORBIDDEN", msg: "forbidden" });
      return reply(200, { code: "SUCCESS", data: DOORS, msg: "success" });
    }
    const m = req.url.match(/^\/api\/v1\/developer\/doors\/([^/]+)\/unlock$/);
    if (req.method === "PUT" && m) {
      if (!granted.includes("edit:space")) return reply(403, { code: "CODE_FORBIDDEN", msg: "forbidden" });
      if (!DOORS.some((d) => d.id === decodeURIComponent(m[1])))
        return reply(200, { code: "CODE_DEVICE_NOT_FOUND", msg: "door not found" });
      return reply(200, { code: "SUCCESS", data: "success", msg: "success" });
    }
    reply(404, { code: "CODE_NOT_FOUND", msg: "not found" });
  });
});

const wss = new WebSocketServer({ noServer: true, autoPong: false });

server.on("upgrade", (req, socket, head) => {
  const url = req.url.split("?")[0];
  if (url !== NOTIFY_PATH) {
    socket.write("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
    socket.destroy();
    report({ event: "rejected", reason: "path", path: url });
    return;
  }
  const token = tokenOf(req);
  if (!scopes[token] || !scopes[token].includes("view:device")) {
    socket.write("HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n");
    socket.destroy();
    report({ event: "rejected", reason: "token", token });
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => {
    ws.token = token;
    clients.add(ws);
    ws.on("ping", (data) => {
      report({ event: "client_ping", token, data: data.toString() });
      if (!muted) ws.pong(data);
    });
    ws.on("pong", (data) => report({ event: "pong", token, data: data.toString() }));
    ws.on("message", (data, isBinary) =>
      report({ event: "message", token, data: data.toString(), binary: isBinary })
    );
    ws.on("close", (code, reason) => {
      clients.delete(ws);
      report({ event: "closed", token, code, reason: reason.toString() });
    });
    ws.on("error", (err) => report({ event: "error", token, message: String(err) }));
    report({ event: "connected", token, ua: req.headers["user-agent"] || "" });
    ws.send("Hello");
  });
});

function forEachClient(fn, token) {
  for (const ws of clients) {
    if (!token || ws.token === token) fn(ws);
  }
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let cmd;
  try {
    cmd = JSON.parse(line);
  } catch (err) {
    report({ event: "bad_command", line });
    return;
  }
  switch (cmd.cmd) {
    case "broadcast":
      forEachClient((ws) => ws.send(cmd.data), cmd.token);
      break;
    case "binary":
      forEachClient((ws) => ws.send(Buffer.from(cmd.data, "base64"), { binary: true }), cmd.token);
      break;
    case "fragmented":
      forEachClient((ws) => {
        cmd.parts.forEach((part, i) => ws.send(part, { fin: i === cmd.parts.length - 1 }));
      }, cmd.token);
      break;
    case "ping":
      forEachClient((ws) => ws.ping(cmd.data || ""), cmd.token);
      break;
    case "mute":
      muted = Boolean(cmd.value);
      break;
    case "close":
      forEachClient((ws) => ws.close(cmd.code || 1000, cmd.reason || ""), cmd.token);
      break;
    case "terminate":
      forEachClient((ws) => ws.terminate(), cmd.token);
      break;
    case "stop":
      forEachClient((ws) => ws.terminate());
      server.close(() => process.exit(0));
      setTimeout(() => process.exit(0), 500);
      break;
    default:
      report({ event: "unknown_command", cmd: cmd.cmd });
  }
  report({ event: "ack", cmd: cmd.cmd, seq: cmd.seq });
});
rl.on("close", () => process.exit(0));

server.listen(Number(portArg || 0), "127.0.0.1", () => {
  report({ event: "ready", port: server.address().port });
});
