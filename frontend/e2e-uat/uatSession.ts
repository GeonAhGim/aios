import { createHmac, randomInt } from "node:crypto";

import { expect, request, type Page } from "@playwright/test";

export const UAT_API_URL = process.env.UAT_API_URL ?? "http://127.0.0.1:18000";

export interface FreshUser {
  email: string;
  password: string;
}

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Decode(src: string): Buffer {
  let bits = 0;
  let acc = 0;
  const out: number[] = [];
  for (const ch of src.toUpperCase().replace(/[=\s]/g, "")) {
    const v = B32.indexOf(ch);
    if (v < 0) throw new Error("invalid base32 character in MFA secret");
    acc = (acc << 5) | v;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      out.push((acc >> bits) & 0xff);
      acc &= (1 << bits) - 1;
    }
  }
  return Buffer.from(out);
}

// RFC 6238: HMAC-SHA1, 30 s step, 6 digits.
export function totp(secretBase32: string, nowMs = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(nowMs / 30_000)));
  const mac = createHmac("sha1", base32Decode(secretBase32)).update(counter).digest();
  const off = mac[mac.length - 1] & 0x0f;
  const bin =
    ((mac[off] & 0x7f) << 24) | (mac[off + 1] << 16) | (mac[off + 2] << 8) | mac[off + 3];
  return String(bin % 1_000_000).padStart(6, "0");
}

function randomPassword(): string {
  const pick = (set: string) => set[randomInt(set.length)];
  const lower = "abcdefghijkmnopqrstuvwxyz";
  const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const digit = "23456789";
  const special = "!@#$%^&*-";
  const all = lower + upper + digit + special;
  const chars = [pick(lower), pick(upper), pick(digit), pick(special)];
  while (chars.length < 20) chars.push(pick(all));
  for (let i = chars.length - 1; i > 0; i--) {
    const j = randomInt(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

// The UAT API allows ~10 auth calls per 60 s window and one flow uses most of them, so
// consecutive fresh users are spaced one window apart instead of failing on 429.
const RATE_WINDOW_MS = 61_000;
let lastUserAt = 0;

// Every run registers a new throwaway account; the UAT_DEMO_* account stays untouched.
export async function createFreshUser(): Promise<FreshUser> {
  const email = `e2e-${randomInt(2 ** 31).toString(36)}${Date.now().toString(36)}@demo.uat`;
  const password = randomPassword();
  const wait = lastUserAt + RATE_WINDOW_MS - Date.now();
  if (lastUserAt > 0 && wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastUserAt = Date.now();
  const api = await request.newContext();
  try {
    // The UAT API rate-limits bursts of registrations (429 + retry_after_seconds): back off.
    for (let attempt = 0; ; attempt++) {
      const res = await api.post(`${UAT_API_URL}/auth/register`, { data: { email, password } });
      if (res.status() === 201) break;
      if (res.status() === 429 && attempt < 5) {
        const body = (await res.json().catch(() => ({}))) as { retry_after_seconds?: number };
        await new Promise((r) => setTimeout(r, ((body.retry_after_seconds ?? 1) + 1) * 1000));
        continue;
      }
      throw new Error(`/auth/register failed: HTTP ${res.status()} ${(await res.text()).slice(0, 200)}`);
    }
  } finally {
    await api.dispose();
  }
  return { email, password };
}

export async function submitLogin(page: Page, user: FreshUser, totpCode?: string): Promise<void> {
  await page.goto("/login");
  await page.locator("#email").fill(user.email);
  await page.locator("#password").fill(user.password);
  if (totpCode) await page.locator("#totp").fill(totpCode);
  await page.locator('form button[type="submit"]').click();
}

// Reads the manual-entry secret shown on the MFA setup screen. Never logged.
export async function readMfaSecret(page: Page): Promise<string> {
  await expect(page).toHaveURL(/\/onboarding\/mfa-setup/);
  const line = page.locator("p.font-mono");
  await expect(line).toBeVisible();
  const match = /[A-Z2-7]{16,}/.exec((await line.textContent()) ?? "");
  if (!match) throw new Error("MFA secret not found on the setup screen");
  return match[0];
}

export async function submitMfaCode(page: Page, code: string): Promise<void> {
  await page.locator('input[inputmode="numeric"]').fill(code);
  await page.getByRole("button", { name: "인증 완료" }).click();
}

// Most conservative answers: no experience, 0% investable ratio, 0% tolerated loss,
// long-term goal, funds needed within a year.
export async function submitConservativeRisk(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/onboarding\/risk-assessment/);
  await page.locator('input[type="number"]').fill("0");
  const sliders = page.locator('input[type="range"]');
  await sliders.nth(0).fill("0");
  await sliders.nth(1).fill("0");
  const selects = page.locator("form select");
  await selects.nth(0).selectOption("LONG_TERM_GROWTH");
  await selects.nth(1).selectOption("WITHIN_1_YEAR");
  await page.getByRole("button", { name: "제출하기" }).click();
}

// Logs in, completes MFA setup + risk assessment, stops on first-run or dashboard.
// Returns the MFA secret so callers can generate later codes; never print it.
export async function loginAndCompleteOnboarding(page: Page, user: FreshUser): Promise<string> {
  await submitLogin(page, user);
  const secret = await readMfaSecret(page);
  await submitMfaCode(page, totp(secret));
  await submitConservativeRisk(page);
  await expect(page).toHaveURL(/\/(onboarding\/first-run|dashboard)(\?|$)/);
  return secret;
}
