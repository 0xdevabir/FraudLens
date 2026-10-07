// node scripts/smoke.cjs — sign in as each role and open every page that role may see.
// Fails on a console error, a failed request, an error notice, a blank page, a stray
// "NaN"/"undefined", or a page wider than the window. Needs the API and the console
// running (`make demo`, or `make api` and `make console`).
// PHONE=1 runs it as an iPhone (390 × 844, touch), where nothing may be wider than the screen
// and the tab bar must be there; SHOTS=<dir> saves a screenshot of every page.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const CONSOLE = process.env.CONSOLE_URL || "http://127.0.0.1:3100";
const API = process.env.API_URL || "http://127.0.0.1:8010";
const PHONE = !!process.env.PHONE;
const SHOTS = process.env.SHOTS;
const DEVICE = PHONE
  ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
  : { viewport: { width: 1440, height: 900 } };

function seedPassword() {
  if (process.env.FRAUDLENS_SEED_PASSWORD) return process.env.FRAUDLENS_SEED_PASSWORD;
  const file = path.join(__dirname, "../../backend/.env");
  const line = fs.existsSync(file) && fs.readFileSync(file, "utf8").match(/^FRAUDLENS_SEED_PASSWORD=(.+)$/m);
  if (!line) throw new Error("set FRAUDLENS_SEED_PASSWORD, or put it in backend/.env");
  return line[1].trim();
}

/** Real ids for the detail pages, whatever the replay produced. */
async function examples(password) {
  const login = await fetch(`${API}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "analyst1", password }),
  });
  if (!login.ok) throw new Error(`sign-in at ${API} failed with ${login.status}`);
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const get = async (route) => {
    const response = await fetch(API + route, { headers });
    if (!response.ok) throw new Error(`${route} answered ${response.status}`);
    return response.json();
  };
  const [cases, alerts, rings, agents] = await Promise.all([
    get("/v1/cases?limit=1"), get("/v1/alerts?limit=1"), get("/v1/rings"), get("/v1/agents/risk?limit=1"),
  ]);
  const found = [];
  if (cases.cases[0]) found.push(`/cases/${cases.cases[0].id}`, `/wallets/${cases.cases[0].subject_id}`);
  if (alerts.alerts[0]) found.push(`/decisions/${alerts.alerts[0].txn_id}`);
  if (rings[0]) found.push(`/rings/${rings[0].ring_id}`);
  if (agents[0]) found.push(`/agents/${agents[0].agent_id}`);
  return found;
}

async function main() {
  const password = seedPassword();
  const staff = ["/", "/impact", "/model", "/fairness", "/policy", "/fraud-types", "/phone"];
  const review = ["/alerts", "/cases", "/approvals", "/appeals", "/refunds", "/network", "/rings", "/agents", ...(await examples(password))];
  const plan = {
    analyst1: [...staff, ...review],
    supervisor1: [...staff, ...review, "/audit"],
    admin: [...staff, "/audit"],
  };

  const browser = await chromium.launch();
  let failures = 0;
  for (const [user, routes] of Object.entries(plan)) {
    const context = await browser.newContext(DEVICE);
    // The guided tour offers itself on a first visit; the smoke run has seen it.
    await context.addInitScript(() => window.localStorage.setItem("fraudlens.tour.seen.v1", "smoke"));
    const page = await context.newPage();
    let problems = [];
    page.on("console", (m) => m.type() === "error" && problems.push("console: " + m.text().slice(0, 200)));
    page.on("pageerror", (e) => problems.push("error: " + String(e).slice(0, 200)));
    page.on("response", (r) => r.status() >= 400 && problems.push(`http ${r.status()} ${new URL(r.url()).pathname}`));

    await page.goto(`${CONSOLE}/login`);
    await page.fill("#username", user);
    await page.fill("#password", password);
    await page.click("button[type=submit]");
    await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15000 });

    for (const route of routes) {
      problems = [];
      await page.goto(CONSOLE + route);
      // The alert queue keeps a live stream open, so it never goes idle.
      await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => route === "/alerts" || problems.push("still loading after 20s"));
      await page.waitForTimeout(300);
      const seen = await page.evaluate(() => ({
        chars: document.querySelector("main")?.innerText.length ?? 0,
        wide: document.documentElement.scrollWidth - window.innerWidth,
        bad: document.body.innerText.match(/[^\n]*(NaN|undefined|Invalid Date|\[object Object\])[^\n]*/g) || [],
        notices: [...document.querySelectorAll("[role=alert]")].map((n) => n.innerText.slice(0, 160)),
        tabs: !!document.querySelector("nav[aria-label=Tabs]")?.getClientRects().length,
      }));
      if (PHONE && !seen.tabs) problems.push("no tab bar on the phone");
      if (SHOTS) {
        fs.mkdirSync(SHOTS, { recursive: true });
        const name = `${PHONE ? "phone" : "desk"}-${user}-${route.replace(/\W+/g, "_") || "home"}.png`;
        await page.screenshot({ path: path.join(SHOTS, name), fullPage: true });
      }
      if (seen.chars < 50) problems.push("the page is blank");
      if (seen.wide > 0) problems.push(`${seen.wide}px wider than the window`);
      problems.push(...seen.bad.map((line) => "text: " + line.trim().slice(0, 160)), ...seen.notices.map((line) => "notice: " + line));
      const unique = [...new Set(problems)];
      console.log(`${unique.length ? "FAIL" : "ok  "} ${user} ${route}`);
      for (const line of unique) console.log("       " + line);
      failures += unique.length ? 1 : 0;
    }
    await context.close();
  }
  await browser.close();
  console.log(failures ? `\n${failures} page(s) failed` : "\nevery page opened cleanly");
  process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error(String(e).slice(0, 600)); process.exit(1); });
