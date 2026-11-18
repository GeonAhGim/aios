import { expect, test } from "@playwright/test";

import { UAT_WEB_URL, demoAccount } from "./uatEnv";

test("UAT_WEB_URL resolves to an http URL", () => {
  expect(UAT_WEB_URL).toMatch(/^https?:\/\//);
});

test("demoAccount reads credentials from the environment", () => {
  const prev = { e: process.env.UAT_DEMO_EMAIL, p: process.env.UAT_DEMO_PASSWORD };
  process.env.UAT_DEMO_EMAIL = "demo@example.com";
  process.env.UAT_DEMO_PASSWORD = "pw-from-env";
  try {
    expect(demoAccount()).toEqual({ email: "demo@example.com", password: "pw-from-env" });
  } finally {
    if (prev.e === undefined) delete process.env.UAT_DEMO_EMAIL;
    else process.env.UAT_DEMO_EMAIL = prev.e;
    if (prev.p === undefined) delete process.env.UAT_DEMO_PASSWORD;
    else process.env.UAT_DEMO_PASSWORD = prev.p;
  }
});
