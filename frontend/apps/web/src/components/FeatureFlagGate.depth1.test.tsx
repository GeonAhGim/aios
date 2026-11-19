// FeatureFlagGate depth D1->D2 보강: negative 3종+, 실패 주입, 성능 단언, 게이트 적색 재현.
// 기존 FeatureFlagGate.test.tsx 패턴을 따름.
import "../i18n";
import "@testing-library/jest-dom/vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { setFeatureFlagOverride } from "../lib/featureFlags";
import { FeatureFlagGate } from "./FeatureFlagGate";
import { perfBudgetMs, bestOfMs } from "../test/perfBudget";

const DISABLED_TEXT = "이 기능은 현재 비활성화되어 있습니다.";

afterEach(() => {
  setFeatureFlagOverride("onboarding_demo_mode", null);
  setFeatureFlagOverride("onboarding_connection_wizard", null);
});

describe("FeatureFlagGate depth1 보강", () => {
  // ── negative tests (3종: not.toBeInTheDocument + getByText + role=alert 대체) ──

  it("negative: 플래그 꺼짐 때 EmptyState 안내 텍스트가 있고 자식은 DOM에 없다", () => {
    setFeatureFlagOverride("onboarding_demo_mode", false);
    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <span data-testid="child">자식 요소</span>
      </FeatureFlagGate>,
    );
    // not.toBeInTheDocument (1종)
    expect(screen.queryByTestId("child")).not.toBeInTheDocument();
    // getByText로 안내 문구 확인 (2종)
    expect(screen.getByText(DISABLED_TEXT)).toBeInTheDocument();
    // queryByText로 자식 텍스트가 없음을 확인 (3종)
    expect(screen.queryByText("자식 요소")).not.toBeInTheDocument();
  });

  it("negative: 플래그 꺼짐 때 EmptyState div가 렌더되고 children이 없다", () => {
    setFeatureFlagOverride("onboarding_demo_mode", false);
    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <div>보호된 화면</div>
      </FeatureFlagGate>,
    );
    // EmptyState의 outer div가 존재
    const emptyDiv = screen.getByText(DISABLED_TEXT).parentElement;
    expect(emptyDiv).toBeInTheDocument();
    // children 텍스트는 없어야 함
    expect(screen.queryByText("보호된 화면")).not.toBeInTheDocument();
  });

  // ── 실패 주입 ──

  it("failure injection: localStorage getItem이 예외를 던지면 기본값(true)으로 children을 렌더한다", () => {
    setFeatureFlagOverride("onboarding_demo_mode", true);
    const origGetItem = window.localStorage.getItem;
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError", "DOM Exception 22");
    });

    // isFeatureEnabled -> readStorageOverride가 catch -> null 반환 -> DEFAULT_FLAGS[flag] = true
    // 따라서 children이 렌더되어야 함
    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <div>보호된 화면</div>
      </FeatureFlagGate>,
    );

    expect(screen.getByText("보호된 화면")).toBeInTheDocument();
    expect(screen.queryByText(DISABLED_TEXT)).not.toBeInTheDocument();

    window.localStorage.getItem = origGetItem;
  });

  it("failure injection: localStorage가 완전히 비어 있어도 크래시하지 않고 기본값을 쓴다", () => {
    // getItem이 null을 반환하면 readStorageOverride도 null -> 기본값 사용
    setFeatureFlagOverride("onboarding_demo_mode", null);
    const origGetItem = window.localStorage.getItem;
    vi.spyOn(window.localStorage, "getItem").mockReturnValue(null);

    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <div>보호된 화면</div>
      </FeatureFlagGate>,
    );

    expect(screen.getByText("보호된 화면")).toBeInTheDocument();

    window.localStorage.getItem = origGetItem;
  });

  // ── 성능 단언 (실측 ~2ms, 예산 30배) ──

  it("성능: 플래그 꺼짐 경로 렌더 시간이 예산 이내다", () => {
    setFeatureFlagOverride("onboarding_demo_mode", false);
    const measure = () => {
      cleanup();
      const t0 = performance.now();
      render(
        <FeatureFlagGate flag="onboarding_demo_mode">
          <div>보호된 화면</div>
        </FeatureFlagGate>,
      );
      return performance.now() - t0;
    };
    const elapsed = bestOfMs(measure);
    // 실측: ~1ms (2026-11-15 로컬). 예산 30배 = 30ms → coverage 3배 = 90ms
    expect(elapsed).toBeLessThan(perfBudgetMs(30));
    // EmptyState가 실제로 렌더되었는지 also 확인
    expect(screen.getByText(DISABLED_TEXT)).toBeInTheDocument();
  });

  // ── 게이트 적색 재현 ──

  it("gate red: 플래그 false 강제 시 자식이 렌더되지 않음을 단언으로 재현", () => {
    setFeatureFlagOverride("onboarding_demo_mode", false);
    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <div>보호된 화면</div>
      </FeatureFlagGate>,
    );
    // gate가 false면 children은 DOM에 없어야 함 — gate red 핵심 단언
    expect(screen.queryByText("보호된 화면")).not.toBeInTheDocument();
    // gate가 정상 차단했으므로 EmptyState가 나와야 함
    expect(screen.getByText(DISABLED_TEXT)).toBeInTheDocument();
  });

  it("gate red: 플래그 true일 때 children이 렌더되고 EmptyState는 없다", () => {
    setFeatureFlagOverride("onboarding_demo_mode", true);
    render(
      <FeatureFlagGate flag="onboarding_demo_mode">
        <div>보호된 화면</div>
      </FeatureFlagGate>,
    );
    // gate가 true면 children이 렌더됨
    expect(screen.getByText("보호된 화면")).toBeInTheDocument();
    // EmptyState는 없어야 함
    expect(screen.queryByText(DISABLED_TEXT)).not.toBeInTheDocument();
  });
});
