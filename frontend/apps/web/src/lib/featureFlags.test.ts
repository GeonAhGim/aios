import { afterEach, describe, expect, it } from "vitest";
import { isFeatureEnabled, setFeatureFlagOverride } from "./featureFlags";

afterEach(() => {
  setFeatureFlagOverride("onboarding_connection_wizard", null);
  setFeatureFlagOverride("onboarding_demo_mode", null);
});

describe("featureFlags", () => {
  it("오버라이드가 없으면 기본값(true)을 반환한다", () => {
    expect(isFeatureEnabled("onboarding_connection_wizard")).toBe(true);
    expect(isFeatureEnabled("onboarding_demo_mode")).toBe(true);
  });

  it("negative: 로컬 오버라이드로 false를 저장하면 false를 반환한다", () => {
    setFeatureFlagOverride("onboarding_connection_wizard", false);
    expect(isFeatureEnabled("onboarding_connection_wizard")).toBe(false);
  });

  it("negative: 손상된(허용값 밖) 저장값은 기본값으로 폴백한다", () => {
    window.localStorage.setItem("aios_feature_flag:onboarding_demo_mode", "garbage");
    expect(isFeatureEnabled("onboarding_demo_mode")).toBe(true);
  });

  it("negative: 오버라이드를 null로 지우면 다시 기본값을 반환한다", () => {
    setFeatureFlagOverride("onboarding_connection_wizard", false);
    setFeatureFlagOverride("onboarding_connection_wizard", null);
    expect(isFeatureEnabled("onboarding_connection_wizard")).toBe(true);
  });

  it("다른 플래그 키를 오버라이드해도 서로 영향을 주지 않는다(테넌트/플래그 격리)", () => {
    setFeatureFlagOverride("onboarding_connection_wizard", false);
    expect(isFeatureEnabled("onboarding_demo_mode")).toBe(true);
  });
});
