import { describe, expect, it } from "vitest";
import i18next, { DEFAULT_LANGUAGE, initI18n } from "./index";

describe("i18n bootstrap", () => {
  it("initializes with ko as the active and fallback language", () => {
    initI18n();
    expect(i18next.language).toBe(DEFAULT_LANGUAGE);
    expect(i18next.options.fallbackLng).toEqual([DEFAULT_LANGUAGE]);
  });

  it("is idempotent -- a second call does not throw or reset the instance", () => {
    const first = initI18n();
    const second = initI18n();
    expect(second).toBe(first);
    expect(i18next.language).toBe(DEFAULT_LANGUAGE);
  });

  it("resolves a nested catalog key", () => {
    expect(i18next.t("common.retry")).toBe("다시 시도");
  });

  it("interpolates {{var}} placeholders from the catalog", () => {
    expect(i18next.t("errors.retryAfterSeconds", { seconds: 5 })).toBe("5초 후 재시도 가능");
  });

  it("[negative] a missing key falls back to returning the key itself, not undefined/throw", () => {
    // Deliberately off-catalog key (e.g. a stale key left after a rename, or one
    // built from a dynamic error code) -- cast past the typed-resources guard from
    // i18next.d.ts to exercise the runtime fallback path this test targets.
    expect(i18next.t("does.not.exist" as never)).toBe("does.not.exist");
  });

  it("[negative] does not silently render an empty string for a missing key (returnNull: false)", () => {
    const result = i18next.t("also.missing" as never);
    expect(result).not.toBe("");
    expect(typeof result).toBe("string");
  });
});
