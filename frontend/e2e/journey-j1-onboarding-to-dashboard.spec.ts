import { expect, test, type Page, type Route } from "@playwright/test";
import { fieldControl } from "./support/fieldControl";
import { mockBackend } from "./support/mockBackend";

// task-6664 (J1): docs/specs/UX_JOURNEYS.md J1 단계표(가입→로그인→MFA 설정→위험성향
// 평가→최초 실행 체크리스트→거래소 자격증명(데모 키에 준하는 고정 키)→첫 대시보드)를
// 그대로 따라간다. mockBackend가 덮는 최소 인증 계약(users/me, risk-profile 등)은
// "이미 온보딩을 마친 사용자"를 전제로 하므로, 1~4단계(가입→MFA→위험성향평가→대시보드
// 도달)는 mockBackend를 쓰지 않고 이 파일에서 직접 상태를 갖는 라우트를 구성한다(ProtectedRoute가
// useMe/useRiskProfile 쿼리 무효화(invalidateQueries)에 반응해 단계를 넘기므로 정적
// 픽스처로는 재현 불가 — useAuth.ts/useSuitability.ts 확인). 5~6단계(거래소 연결)·7단계
// (대시보드)는 mockBackend(page)로 인증을 이미 통과한 상태에서 검증한다.
//
// sw.js(서비스 워커)가 "/v1/"로 시작하지 않는 GET을 캐시 우선으로 처리해 page.route를
// 우회하는 문제(J3 여정 테스트에서 확인된 것과 동일)를 막기 위해 서비스 워커 등록 자체를
// 막는다.
test.use({ serviceWorkers: "block" });

const API_BASE = "http://localhost:8000";

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function envelope(data: unknown) {
  return { data, meta: { trace_id: "e2e-trace", as_of: new Date().toISOString(), page: null } };
}

interface OnboardingState {
  mfaEnabled: boolean;
  riskProfile: Record<string, unknown> | null;
}

async function mockOnboardingChain(page: Page): Promise<OnboardingState> {
  const state: OnboardingState = { mfaEnabled: false, riskProfile: null };

  await page.route(`${API_BASE}/auth/register`, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return json(
      route,
      201,
      envelope({
        access_token: "e2e-fixture-token",
        refresh_token: "e2e-fixture-refresh",
        token_type: "bearer",
        expires_in: 3600,
        session_id: "e2e-session",
      }),
    );
  });

  await page.route(`${API_BASE}/users/me`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return json(
      route,
      200,
      envelope({
        user_id: "e2e-user",
        email: "e2e-j1@example.com",
        display_name: null,
        mfa_enabled: state.mfaEnabled,
        status: "active",
        is_verifier: false,
        is_platform_admin: false,
      }),
    );
  });

  await page.route(`${API_BASE}/users/me/risk-profile`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    if (!state.riskProfile) {
      return json(route, 404, {
        error_code: "NOT_FOUND",
        message: "위험성향평가가 아직 제출되지 않았습니다.",
        details: {},
        trace_id: "e2e-trace",
        retry_after_seconds: null,
      });
    }
    return json(route, 200, state.riskProfile);
  });

  await page.route(`${API_BASE}/auth/mfa/setup`, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    return json(
      route,
      200,
      envelope({
        secret: "JBSWY3DPEHPK3PXP",
        provisioning_uri: "otpauth://totp/AIOS:e2e-j1@example.com?secret=JBSWY3DPEHPK3PXP&issuer=AIOS",
      }),
    );
  });

  await page.route(`${API_BASE}/auth/mfa/verify`, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    state.mfaEnabled = true;
    return json(route, 200, envelope({ mfa_enabled: true }));
  });

  await page.route(`${API_BASE}/users/me/risk-assessment`, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    state.riskProfile = {
      risk_profile: "중립형",
      assessed_at: "2026-01-01T00:00:00Z",
      next_reassessment_due: "2099-01-01T00:00:00Z",
      is_higher_risk_than_previous: false,
    };
    return json(route, 200, state.riskProfile);
  });

  await page.route(`${API_BASE}/executions`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return json(route, 200, []);
  });

  await page.route(`${API_BASE}/portfolio`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return json(route, 200, {
      allocations: [],
      unallocated_cash: "0",
      unallocated_cash_weight_pct: "0",
      total_portfolio_value: "0",
    });
  });

  return state;
}

test.describe("J1 여정: 온보딩 → 계좌/거래소 연결(데모 키) → 첫 대시보드", () => {
  test("1~4단계 가입 → MFA 설정 → 위험성향평가까지 순서대로 이어져 대시보드에 도달한다", async ({
    page,
  }) => {
    await mockOnboardingChain(page);

    await page.goto("/signup");
    await expect(page.getByRole("heading", { name: "AIOS 회원가입" })).toBeVisible();

    await fieldControl(page, "이메일").fill("e2e-j1@example.com");
    await fieldControl(page, "비밀번호").fill("e2e-strong-password-12");
    await page.getByRole("button", { name: "가입하기" }).click();

    await expect(page).toHaveURL(/\/onboarding\/mfa-setup/);
    await expect(page.getByRole("heading", { name: "2단계 인증 설정 (필수)" })).toBeVisible();

    const totpInput = page.getByPlaceholder("6자리 코드");
    await expect(page.getByRole("button", { name: "인증 완료" })).toBeEnabled();
    await totpInput.fill("123456");
    await page.getByRole("button", { name: "인증 완료" }).click();

    await expect(page).toHaveURL(/\/onboarding\/risk-assessment/);
    await expect(page.getByRole("heading", { name: "투자자 적합성평가 (필수)" })).toBeVisible();

    await page.getByRole("button", { name: "제출하기" }).click();

    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "대시보드" })).toBeVisible();
    // 온보딩 직후 실행/포지션이 없는 첫 방문 상태 — 빈 상태 문구가 보인다.
    await expect(page.getByText("실행 중인 전략이 없습니다.")).toBeVisible();
  });

  test("[실패 주입] 1단계 가입 시 이메일 중복(409)이면 오류 배너를 보이고 MFA 설정으로 이동하지 않는다", async ({
    page,
  }) => {
    await page.route(`${API_BASE}/auth/register`, (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      return json(route, 409, {
        error_code: "STATE_CONCURRENCY_CONFLICT",
        message: "이미 사용 중인 이메일입니다.",
        details: {},
        trace_id: "e2e-fault-injection-signup",
        retry_after_seconds: null,
      });
    });

    await page.goto("/signup");
    await fieldControl(page, "이메일").fill("dup@example.com");
    await fieldControl(page, "비밀번호").fill("e2e-strong-password-12");
    await page.getByRole("button", { name: "가입하기" }).click();

    await expect(page).toHaveURL(/\/signup/);
    // ErrorMessage는 서버 message를 그대로 노출하지 않고 apiError.ts의
    // EXACT_MESSAGES(errorCode→고정 한국어 문구) 매핑을 쓴다(SignupError가
    // classifyBadRequest/classifyForbidden 어디에도 안 걸리면 이 경로로 빠진다).
    await expect(page.getByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요.")).toBeVisible();
  });

  // 6a(실계좌 연결 마법사)/6b·6c(데모 모드)는 이미 각각 온보딩 문서 코드 확인상
  // `/onboarding/connect`(FeatureFlagGate)와 demo-onboarding-flow.spec.ts(task-2632)가
  // 별도로 존재/검증한다 — 이 여정 파일은 "가입→MFA→위험성향평가→대시보드"(1~4단계)와
  // "거래소 자격증명→대시보드"(7~8단계, 데모 키에 준하는 고정 키 입력)만 잇는다.
  test("7단계 거래소 자격증명 등록 시 연동된 거래소 목록에 반영된다", async ({ page }) => {
    await mockBackend(page);
    let registered: Record<string, unknown> | null = null;

    await page.route(`${API_BASE}/exchange-credentials`, (route) => {
      const method = route.request().method();
      if (method === "GET") {
        return json(route, 200, registered ? [registered] : []);
      }
      if (method === "POST") {
        const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
        registered = {
          id: 1,
          exchange: body.exchange,
          isActive: true,
          linkedAt: "2026-01-01T00:00:00Z",
          withdrawalPermissionWarning: null,
        };
        return json(route, 201, registered);
      }
      return route.fallback();
    });

    await page.goto("/exchanges");
    await expect(page.getByRole("heading", { name: "거래소 연동", exact: true })).toBeVisible();
    await expect(page.getByText("연동된 거래소가 없습니다.")).toBeVisible();

    await fieldControl(page, "API Key").fill("e2e-demo-api-key");
    await fieldControl(page, "API Secret").fill("e2e-demo-api-secret");
    await fieldControl(page, "API Passphrase").fill("e2e-demo-api-passphrase");
    await page.getByRole("button", { name: "등록" }).click();

    await expect(page.getByText("연동된 거래소가 없습니다.")).toHaveCount(0);
    await expect(page.getByText("활성")).toBeVisible();
  });

  test("[실패 주입] 7단계 거래소 자격증명 등록 403 시 오류 배너를 보이고 목록에 반영하지 않는다", async ({
    page,
  }) => {
    await mockBackend(page);
    await page.route(`${API_BASE}/exchange-credentials`, (route) => {
      const method = route.request().method();
      if (method === "GET") return json(route, 200, []);
      if (method === "POST") {
        return json(route, 403, {
          error_code: "AUTHZ_FORBIDDEN",
          message: "이 작업을 수행할 권한이 없습니다.",
          details: {},
          trace_id: "e2e-fault-injection-exchange-register",
          retry_after_seconds: null,
        });
      }
      return route.fallback();
    });

    await page.goto("/exchanges");
    await fieldControl(page, "API Key").fill("e2e-demo-api-key");
    await fieldControl(page, "API Secret").fill("e2e-demo-api-secret");
    await fieldControl(page, "API Passphrase").fill("e2e-demo-api-passphrase");
    await page.getByRole("button", { name: "등록" }).click();

    await expect(page.getByText("이 작업을 수행할 권한이 없습니다.")).toBeVisible();
    await expect(page.getByText("연동된 거래소가 없습니다.")).toBeVisible();
  });

  test("8단계 첫 대시보드: 포지션·현금이 있으면 포트폴리오 요약과 실행 중인 전략이 함께 표시된다", async ({
    page,
  }) => {
    await mockBackend(page);
    await page.route(`${API_BASE}/portfolio`, (route) =>
      json(route, 200, {
        allocations: [
          {
            execution_id: 1,
            strategy_id: "e2e-j1-dashboard-strategy",
            strategy_version: "1.0.0",
            exchange: "bitget",
            mode: "PAPER",
            status: "RUNNING",
            allocated_capital: "500",
            total_pnl: "12.5",
            current_value: "512.5",
            weight_pct: "5",
          },
        ],
        unallocated_cash: "9487.5",
        unallocated_cash_weight_pct: "95",
        total_portfolio_value: "10000",
      }),
    );
    await page.route(`${API_BASE}/executions`, (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      return json(route, 200, [
        {
          execution_id: 1,
          strategy_id: "e2e-j1-dashboard-strategy",
          strategy_version: "1.0.0",
          status: "RUNNING",
          mode: "PAPER",
          exchange: "bitget",
          allocated_capital: "500",
          days_since_start: 1,
          realized_pnl: "12.5",
          unrealized_pnl: "0",
          max_drawdown_pct: null,
        },
      ]);
    });

    await page.goto("/dashboard");

    await expect(page.getByRole("heading", { name: "대시보드" })).toBeVisible();
    await expect(page.getByText("포트폴리오 요약")).toBeVisible();
    await expect(page.getByText("총 포트폴리오 가치")).toBeVisible();
    await expect(page.getByText("실행 중인 전략")).toBeVisible();
    await expect(page.getByText("e2e-j1-dashboard-strategy").first()).toBeVisible();
    await expect(page.getByText("RUNNING", { exact: true })).toBeVisible();
  });

  // 갭 노트(UX_JOURNEYS.md §2 G-2, 코드로 재확인): DashboardPage.tsx는 useMyAlerts/
  // useNotificationHistory 등 알림 관련 훅을 전혀 호출하지 않는다(grep 0건) — J1 8단계
  // 성공 조건 "포지션·현금·알림 표시" 중 알림은 대시보드에 직접 노출되지 않고 별도
  // /alerts, /notifications 화면으로 분리돼 있다. 전용 위젯이 생기기 전까지 이 단계는
  // 채우지 않는다 — 우회하지 않는다.
  test.fixme(
    "8단계 [갭 G-2] 대시보드에 최근 알림이 위젯으로 함께 표시된다",
    async ({ page }) => {
      await mockBackend(page);
      await page.goto("/dashboard");
      // 갭 G-2: DashboardPage.tsx에 알림 위젯이 추가되면 이 테스트를 채운다.
    },
  );

  test("[실패 주입] 8단계 포트폴리오 조회 5xx 시 대시보드가 빈 요약을 보여주고 크래시하지 않는다", async ({
    page,
  }) => {
    await mockBackend(page);
    await page.route(`${API_BASE}/portfolio`, (route) =>
      json(route, 502, {
        error_code: "EXCHANGE_FATAL",
        message: "raw server detail",
        details: {},
        trace_id: "e2e-fault-injection-dashboard-portfolio",
        retry_after_seconds: null,
      }),
    );

    await page.goto("/dashboard");

    await expect(page.getByRole("heading", { name: "대시보드" })).toBeVisible();
    // DashboardPage.tsx는 portfolio 쿼리 오류를 별도 배너로 표면화하지 않는다(코드
    // 확인 — portfolioLoading이 끝나면 portfolio가 falsy이므로 그냥 아무것도 렌더하지
    // 않는다) — 크래시 없이 "총 포트폴리오 가치"가 나타나지 않는 것으로 이 계약을 검증한다.
    await expect(page.getByText("총 포트폴리오 가치")).toHaveCount(0);
  });
});
