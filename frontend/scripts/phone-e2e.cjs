// node scripts/phone-e2e.cjs — walk the customer phone demo in Bangla and in English, at a
// phone-sized window, then file an appeal on a held payment, approve it from the console's
// Customer appeals queue and see the phone report the payment released. Needs the API and the
// console running with demo data (`make demo`, or `make api` and `make console`).
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const CONSOLE = process.env.CONSOLE_URL || "http://127.0.0.1:3100";

function seedPassword() {
  if (process.env.FRAUDLENS_SEED_PASSWORD) return process.env.FRAUDLENS_SEED_PASSWORD;
  const file = path.join(__dirname, "../../backend/.env");
  const line = fs.existsSync(file) && fs.readFileSync(file, "utf8").match(/^FRAUDLENS_SEED_PASSWORD=(.+)$/m);
  if (!line) throw new Error("set FRAUDLENS_SEED_PASSWORD, or put it in backend/.env");
  return line[1].trim();
}

// The button labels the phone shows, as written in app/(console)/phone/copy.ts.
const SAY = {
  bn: { send: "সেন্ড মানি করুন", cancel: "বাতিল করুন", next: "এরপর কী হবে", cancelled: "বাতিল হয়েছে", submit: "আপিল জমা দিন" },
  en: { send: "Send", cancel: "Cancel the payment", next: "What happens next", cancelled: "Cancelled", submit: "Send the appeal" },
};

let failures = 0;
// Digits in an appeal are redacted, so a run marks its own appeal with letters.
const tag = () => Array.from({ length: 8 }, () => "abcdefghjkmnpqrstuvwxyz"[Math.floor(Math.random() * 23)]).join("");
function check(ok, what) {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures += 1;
}

async function signIn(page, password, user = "analyst1") {
  await page.goto(`${CONSOLE}/login`);
  await page.fill("#username", user);
  await page.fill("#password", password);
  await page.click("button[type=submit]");
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15000 });
}

/** Put a ready payment on the phone and press send; false when the demo has none of that kind. */
async function pay(page, scenario, lang) {
  const pick = page.locator(`[data-scenario=${scenario}]`);
  if (!(await pick.count())) return false;
  await pick.click();
  const phone = page.getByTestId("phone");
  await phone.getByRole("button", { name: SAY[lang].send, exact: true }).click();
  await phone.locator(".border-b[role=status]").waitFor({ timeout: 15000 });
  return true;
}

async function interrupted(page, scenario, lang) {
  const phone = page.getByTestId("phone");
  if (!(await pay(page, scenario, lang))) return check(true, `${lang} ${scenario}: skipped, the demo has no such payment`);
  check((await phone.getAttribute("lang")) === lang, `${lang} ${scenario}: the phone is marked lang=${lang}`);
  const warning = phone.getByTestId("phone-warning");
  check((await warning.count()) === 1 && !!(await warning.getAttribute("data-cue")), `${lang} ${scenario}: a scam-specific warning is shown`);
  check(await phone.getByText(SAY[lang].next).isVisible(), `${lang} ${scenario}: the "what happens next" timeline is shown`);
  check((await page.evaluate(() => document.activeElement?.tagName)) === "H2", `${lang} ${scenario}: focus moved to the screen title`);
  if (scenario === "step_up") {
    const clock = phone.getByTestId("phone-countdown");
    const first = await clock.innerText();
    await page.waitForTimeout(2100);
    check((await clock.innerText()) !== first, `${lang} step_up: the cooling-off countdown is running (${first})`);
    const verify = phone.locator("button").filter({ hasText: lang === "bn" ? "যাচাই" : "Verify" });
    check(await verify.isDisabled(), `${lang} step_up: verify-and-send waits out the cooling-off`);
  }
  await phone.getByRole("button", { name: SAY[lang].cancel, exact: true }).click();
  await phone.getByTestId("phone-outcome").waitFor({ timeout: 15000 });
  check((await phone.getByTestId("phone-outcome").getAttribute("data-status")) === "cancelled", `${lang} ${scenario}: the customer can always cancel`);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(wide <= 0, `${lang} ${scenario}: nothing wider than a 360px phone`);
}

async function main() {
  const password = seedPassword();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 360, height: 800 } });
  const page = await context.newPage();
  page.on("pageerror", (error) => check(false, "page error: " + String(error).slice(0, 200)));
  await signIn(page, password);
  await page.goto(`${CONSOLE}/phone`);
  await page.evaluate(() => window.localStorage.removeItem("fraudlens.phone.lang"));
  await page.reload();
  await page.getByTestId("phone").waitFor();

  for (const lang of ["bn", "en"]) {
    if (lang === "en") await page.getByTestId("phone").getByTestId("phone-lang").click();
    await interrupted(page, "warn", lang);
    await interrupted(page, "step_up", lang);
  }
  await page.reload();
  check((await page.getByTestId("phone").getAttribute("lang")) === "en", "the English choice is remembered");

  // The appeal round trip: the customer appeals a hold, a reviewer approves it, the money moves.
  const phone = page.getByTestId("phone");
  if (await pay(page, "hold", "en")) {
    check(await phone.getByTestId("phone-countdown").isVisible(), "hold: the review deadline counts down");
    await phone.getByTestId("phone-appeal").click();
    await phone.getByLabel("Family").check();
    const reason = `It is my brother's rent, I pay him every month, ref ${tag()}.`;
    await phone.locator("textarea").fill(reason);
    await phone.getByRole("button", { name: SAY.en.submit }).click();
    const note = phone.getByTestId("phone-appeal-status");
    await note.waitFor({ timeout: 15000 });
    check((await note.getAttribute("data-status")) === "pending", "hold: the appeal is filed and pending");

    // A second person at a desk: the session lives in one tab, so this tab signs in itself.
    const desk = await context.newPage();
    await desk.setViewportSize({ width: 1440, height: 900 });
    await signIn(desk, password, "supervisor1");
    await desk.goto(`${CONSOLE}/appeals`);
    const row = desk.locator("tr").filter({ hasText: reason }).first();
    await row.waitFor({ timeout: 15000 });
    await row.getByRole("button", { name: "Approve" }).click();
    await desk.locator("textarea").fill("Spoke to the customer; a regular rent payment.");
    await desk.getByRole("button", { name: "Approve appeal" }).click();
    await desk.getByRole("status").filter({ hasText: "approved" }).waitFor({ timeout: 15000 });
    check(await desk.getByText("released to the recipient").isVisible(), "console: approving the appeal released the payment");
    await desk.close();

    await phone.getByRole("button", { name: "Check the status" }).click();
    await phone.getByTestId("phone-outcome").waitFor({ timeout: 15000 });
    check((await phone.getByTestId("phone-outcome").getAttribute("data-status")) === "completed", "phone: the customer sees the payment sent");
  } else {
    check(true, "hold: skipped, the demo has no held payment ready");
  }

  await browser.close();
  console.log(failures ? `\n${failures} check(s) failed` : "\nthe phone flow works in both languages");
  process.exit(failures ? 1 : 0);
}

main().catch((error) => { console.error(String(error).slice(0, 600)); process.exit(1); });
