// Runs tests/browser/app.test.js inside the real app (frontend/index.html) in
// headless Chrome, with a fresh profile (so an empty library) and a fake
// speech service with generated silent audio, so nothing depends on the
// online service or the Python server. No npm packages: a static file server
// from node:http, and the DevTools protocol over Node's built-in WebSocket.
//
//   node tests/browser/run.mjs        (CHROME_PATH=... to use another Chrome)
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = normalize(join(dirname(fileURLToPath(import.meta.url)), "..", ".."));
const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function serve() {
  const server = createServer((req, res) => {
    const path = normalize(join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname)));
    if (!path.startsWith(ROOT) || !existsSync(path)) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "Content-Type": TYPES[extname(path)] || "application/octet-stream" });
    res.end(readFileSync(path));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function launchChrome(profile) {
  const chrome = spawn(CHROME, [
    "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check",
    // fake audio output: playback never depends on this machine's sound device
    "--mute-audio", "--disable-audio-output", "about:blank",
  ], { stdio: "ignore" });
  const portFile = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 200 && !existsSync(portFile); i++) await sleep(50);
  if (!existsSync(portFile)) throw new Error(`Chrome didn't start (${CHROME})`);
  const port = readFileSync(portFile, "utf8").split("\n")[0];
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  return { chrome, wsUrl: pages.find((p) => p.type === "page").webSocketDebuggerUrl };
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    } else if (message.method) {
      listeners.forEach((fn) => fn(message));
    }
  };
  const send = (method, params = {}) => new Promise((resolve) => {
    pending.set(++id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  return new Promise((resolve) => { ws.onopen = () => resolve({ send, on: (fn) => listeners.push(fn), close: () => ws.close() }); });
}

const server = await serve();
const profile = mkdtempSync(join(tmpdir(), "focus-reader-tests-"));
const { chrome, wsUrl } = await launchChrome(profile);
let exitCode = 1;
try {
  const page = await connect(wsUrl);
  const consoleProblems = [];
  page.on((m) => {
    if (m.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(m.params.type)) {
      consoleProblems.push(m.params.args.map((a) => a.value ?? a.description).join(" "));
    } else if (m.method === "Runtime.exceptionThrown") {
      consoleProblems.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
  });
  await page.send("Runtime.enable");
  await page.send("Page.enable");
  await page.send("Page.navigate", { url: `http://127.0.0.1:${server.address().port}/frontend/index.html` });
  await sleep(1500);

  const tests = readFileSync(join(ROOT, "tests", "browser", "app.test.js"), "utf8");
  const reply = await page.send("Runtime.evaluate", { expression: tests, awaitPromise: true, returnByValue: true });
  if (reply.result.exceptionDetails) {
    throw new Error(reply.result.exceptionDetails.exception?.description || "test script threw");
  }
  const results = reply.result.result.value;
  for (const r of results) {
    console.log(`${r.ok ? "✔" : "✖"} ${r.name}${r.ok ? "" : `\n    ${r.detail}`}`);
  }
  const failed = results.filter((r) => !r.ok).length;
  if (consoleProblems.length) {
    console.log(`✖ console errors/warnings during the run:\n    ${consoleProblems.join("\n    ")}`);
  }
  console.log(`\nbrowser: ${results.length - failed} passed, ${failed} failed${consoleProblems.length ? `, ${consoleProblems.length} console problem(s)` : ""}`);
  exitCode = failed || consoleProblems.length ? 1 : 0;
  page.close();
} catch (error) {
  console.error("browser tests couldn't run:", error.message);
} finally {
  chrome.kill();
  server.close();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
process.exit(exitCode);
