import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// UAT stack web entrypoint; override with UAT_WEB_URL.
export const UAT_WEB_URL = process.env.UAT_WEB_URL ?? "http://127.0.0.1:18080";

export interface DemoAccount {
  email: string;
  password: string;
}

// Minimal dotenv parser: KEY=VALUE lines, optional quotes, '#' comments.
function parseEnvFile(path: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      value.length >= 2 &&
      (value[0] === '"' || value[0] === "'") &&
      value[value.length - 1] === value[0]
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

// Credentials come from UAT_DEMO_EMAIL/UAT_DEMO_PASSWORD, else the repo-root .env.uat.
// The password is never included in error messages.
export function demoAccount(): DemoAccount {
  let email = process.env.UAT_DEMO_EMAIL;
  let password = process.env.UAT_DEMO_PASSWORD;
  if (!email || !password) {
    const envPath = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", ".env.uat");
    if (existsSync(envPath)) {
      const file = parseEnvFile(envPath);
      email = email || file.UAT_DEMO_EMAIL;
      password = password || file.UAT_DEMO_PASSWORD;
    }
  }
  if (!email || !password) {
    throw new Error(
      "UAT demo account not configured: set UAT_DEMO_EMAIL and UAT_DEMO_PASSWORD " +
        "or define them in the repository-root .env.uat",
    );
  }
  return { email, password };
}
