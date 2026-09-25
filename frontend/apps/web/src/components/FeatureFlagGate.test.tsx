import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { setFeatureFlagOverride } from "../lib/featureFlags";
import { FeatureFlagGate } from "./FeatureFlagGate";

afterEach(() => {
  cleanup();
  setFeatureFlagOverride("onboarding_demo_mode", null);
});

describe("FeatureFlagGate", () => {
  it("플래그가 켜져 있으면 자식을 렌더한다", () => {
    setFeatureFlagOverride("onboarding_demo_mode", true);
    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <div>보호된 화면</div>
      </FeatureFlagGate>,
    );
    expect(screen.getByText("보호된 화면")).toBeInTheDocument();
  });

  it("negative: 플래그가 꺼져 있으면 자식 대신 비활성 안내를 렌더하고 자식은 마운트하지 않는다", () => {
    setFeatureFlagOverride("onboarding_demo_mode", false);
    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <div>보호된 화면</div>
      </FeatureFlagGate>,
    );
    expect(screen.queryByText("보호된 화면")).not.toBeInTheDocument();
    expect(screen.getByText("이 기능은 현재 비활성화되어 있습니다.")).toBeInTheDocument();
  });
});
