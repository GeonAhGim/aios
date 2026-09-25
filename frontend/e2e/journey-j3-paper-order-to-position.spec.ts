import { expect, test, type Page, type Route } from "@playwright/test";
import { fieldControl } from "./support/fieldControl";
import { mockBackend } from "./support/mockBackend";

// task-6666 (J3): docs/specs/UX_JOURNEYS.md J3 단계표(주문 생성→리스크/컴플라이언스
// 판정 표시→체결·취소·거부 상태→포지션 반영→컴플라이언스/위임장 상태→알림 수신)를
// 그대로 따라간다. mockBackend가 덮지 않는 portfolio/positions/mandates/alerts/
// notifications 경로는 이 파일에서 page.route로 얹는다 — Playwright는 나중에 등록한
// route를 먼저 검사하므로 mockBackend(page) 호출 뒤에 추가해야 mockBackend의 폴백
// (catch-all)과 충돌하지 않는다.
//
// 갭 노트(문서 UX_JOURNEYS.md §2 J3 단계표 기준, 코드로 재확인): 2단계 "리스크/
// 컴플라이언스 판정 표시"는 G-4(부분 갭) — ExecutionCard.tsx/ExecutionControlPage.tsx
// 어디에도 riskGate/verdict/reasonCode 전용 패널이 없다(grep 0건, PortfolioPage.tsx의
// RebalanceError와 동일하게 BadRequestNotice/ForbiddenNotice/ErrorMessage 일반 오류
// 배너로만 판정 결과가 표면화된다). 전용 판정 패널이 생기기 전까지 "승인/거부 사유가
// 별도 패널로 표시"라는 원 성공 조건은 test.fixme로 남기고, 실제로 동작하는 일반
// 오류 배너 경로를 별도의 실패 주입 테스트로 대신 검증한다 — 우회하지 않는다.
//
// sw.js(서비스 워커)의 fetch 핸들러는 "/v1/"로 시작하지 않는 GET(예: /executions,
// /portfolio, /alerts, /notifications/history)을 캐시 우선(cache-first)으로 처리하며
// 캐시 미스 시 SW 자체 컨텍스트에서 fetch()를 실행한다 — 이 fetch는 page.route로
// 가로채지지 않아 실제 백엔드가 없는 e2e 환경에서 "Failed to fetch"로 끊긴다(order-
// submission.spec.ts의 기존 베이스라인 테스트에서도 동일 증상으로 재현됨). 이 여정
// 테스트의 목이 결정적으로 동작하도록 서비스 워커 등록 자체를 막는다.
test.use({ serviceWorkers: "block" });

const API_BASE = "http://localhost:8000";

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function envelope(data: unknown) {
  return { data, meta: { trace_id: "e2e-trace", as_of: new Date().toISOString(), page: null } };
}

async function mockPortfolio(page: Page, allocations: Record<string, unknown>[]) {
  await page.route(`${API_BASE}/portfolio`, (route) =>
    json(route, 200, {
      allocations,
      unallocated_cash: "1000",
      unallocated_cash_weight_pct: "10",
      total_portfolio_value: "10000",
    }),
  );
  // PortfolioPositionsLive(usePositionList)가 마운트 시 무조건 조회한다 — 목 없이
  // 두면 route.fallback()이 실제 네트워크로 빠져 화면이 오류/로딩에서 멈춘다.
  await page.route(`${API_BASE}/v1/positions`, (route) => json(route, 200, envelope({ items: [] })));
}

async function mockMandateStatus(page: Page, activeRevision: Record<string, unknown> | null) {
  await page.route(`${API_BASE}/v1/foundation/mandates/status`, (route) =>
    json(
      route,
      200,
      envelope({
        tenant_id: "e2e-tenant",
        active_revision: activeRevision,
        pending_revision: null,
      }),
    ),
  );
}

async function mockAlerts(page: Page, alerts: Record<string, unknown>[]) {
  await page.route(`${API_BASE}/alerts`, (route) => json(route, 200, alerts));
}

async function mockNotificationHistory(page: Page, entries: Record<string, unknown>[]) {
  await page.route(`${API_BASE}/notifications/history**`, (route) => json(route, 200, entries));
}

test.describe("J3 여정: 페이퍼 주문 → 리스크/컴플라이언스 판정 → 체결·취소·거부 → 포지션 반영 → 알림", () => {
  test("1단계 주문 제출 시 실행 목록에 새 카드가 나타난다", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/executions");

    await expect(page.getByRole("heading", { name: "실행 제어판" })).toBeVisible();

    await fieldControl(page, "전략 ID").fill("e2e-paper-order-strategy");
    await fieldControl(page, "버전").fill("1.0.0");
    await fieldControl(page, "배분 자본(USDT)").fill("500");
    await page.getByRole("button", { name: "실행 생성" }).click();

    await expect(page.getByText("e2e-paper-order-strategy")).toBeVisible();
  });

  // 갭 G-4(부분 갭): "판정 결과(승인/거부 사유)가 별도 패널로 표시"는 아직 재현 대상이
  // 없다 — ExecutionCard.tsx/ExecutionControlPage.tsx를 읽어 확인했다. 전용 판정 UI가
  // 생기기 전까지 채우지 않는다.
  test.fixme(
    "2단계 [부분 갭 G-4] 주문 제출 시 리스크/컴플라이언스 판정이 전용 패널로 승인/거부 사유와 함께 표시된다",
    async ({ page }) => {
      // 갭 G-4: ExecutionControlPage.tsx/ExecutionCard.tsx 어디에도 riskGate/verdict/
      // reasonCode 패턴이 없다(grep 0건) — 거부는 일반 오류 배너로만 표면화된다.
      // 전용 판정 패널이 추가되면 이 테스트를 채운다.
      await mockBackend(page);
      await page.goto("/executions");
    },
  );

  test("[실패 주입] 2단계 리스크 게이트 거부(403)는 전용 판정 패널 대신 일반 권한 오류 배너로 표면화되고 목록에 반영되지 않는다", async ({
    page,
  }) => {
    await mockBackend(page, {
      createExecutionResponse: {
        status: 403,
        body: {
          error_code: "AUTHZ_FORBIDDEN",
          message: "이 작업을 수행할 권한이 없습니다.",
          details: {},
          trace_id: "e2e-fault-injection-risk-gate",
          retry_after_seconds: null,
        },
      },
    });
    await page.goto("/executions");

    await fieldControl(page, "전략 ID").fill("e2e-should-be-denied");
    await fieldControl(page, "버전").fill("1.0.0");
    await fieldControl(page, "배분 자본(USDT)").fill("500");
    await page.getByRole("button", { name: "실행 생성" }).click();

    await expect(page.getByText("이 작업을 수행할 권한이 없습니다.")).toBeVisible();
    await expect(page.getByText("e2e-should-be-denied")).toHaveCount(0);
  });

  test("3단계 체결·취소·거부: 실행 상태 배지가 실행 목록에 각 상태 그대로 반영된다", async ({ page }) => {
    await mockBackend(page);
    // StatusBadge는 서버가 준 status 문자열을 그대로 렌더한다(가공·번역 없음,
    // ui-web/StatusBadge.tsx 확인) — GET /executions 응답을 직접 고정해 체결
    // (RUNNING)·거부/취소(RETIRED)·대기(PENDING) 세 상태를 한 번에 재현한다.
    // mockBackend(page)가 먼저 등록한 동일 경로 핸들러보다 이 route가 나중에
    // 등록되어 우선한다(Playwright는 마지막에 등록된 route부터 검사).
    await page.route(`${API_BASE}/executions`, (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      return json(route, 200, [
        {
          execution_id: 1,
          strategy_id: "e2e-filled-strategy",
          strategy_version: "1.0.0",
          status: "RUNNING",
          mode: "PAPER",
          exchange: "bitget",
          allocated_capital: "500",
          days_since_start: 1,
          realized_pnl: "10",
          unrealized_pnl: "0",
          max_drawdown_pct: null,
        },
        {
          execution_id: 2,
          strategy_id: "e2e-rejected-strategy",
          strategy_version: "1.0.0",
          status: "RETIRED",
          mode: "PAPER",
          exchange: "bitget",
          allocated_capital: "500",
          days_since_start: 0,
          realized_pnl: "0",
          unrealized_pnl: "0",
          max_drawdown_pct: null,
        },
        {
          execution_id: 3,
          strategy_id: "e2e-pending-strategy",
          strategy_version: "1.0.0",
          status: "PENDING",
          mode: "PAPER",
          exchange: "bitget",
          allocated_capital: "500",
          days_since_start: 0,
          realized_pnl: "0",
          unrealized_pnl: "0",
          max_drawdown_pct: null,
        },
      ]);
    });
    await page.goto("/executions");

    await expect(page.getByText("e2e-filled-strategy")).toBeVisible();
    await expect(page.getByText("e2e-rejected-strategy")).toBeVisible();
    await expect(page.getByText("e2e-pending-strategy")).toBeVisible();
    // getByText는 기본적으로 부분 일치라 "PENDING"이 "e2e-pending-strategy"와도
    // 겹쳐 strict-mode 위반이 난다 — exact: true로 배지 텍스트 자체만 특정한다.
    await expect(page.getByText("RUNNING", { exact: true })).toBeVisible();
    await expect(page.getByText("RETIRED", { exact: true })).toBeVisible();
    await expect(page.getByText("PENDING", { exact: true })).toBeVisible();
  });

  test("4단계 포지션 반영: /portfolio 화면에 배분 내역이 표시된다", async ({ page }) => {
    await mockBackend(page);
    await mockPortfolio(page, [
      {
        execution_id: 1,
        strategy_id: "e2e-paper-order-strategy",
        strategy_version: "1.0.0",
        exchange: "bitget",
        mode: "PAPER",
        status: "RUNNING",
        allocated_capital: "500",
        total_pnl: "12.5",
        current_value: "512.5",
        weight_pct: "5",
      },
    ]);
    await page.goto("/portfolio");

    await expect(page.getByRole("heading", { name: "포트폴리오" })).toBeVisible();
    await expect(page.getByText("총 포트폴리오 가치")).toBeVisible();
    // 동일 strategy_id가 배분 테이블 행과 범례(legend) 등 두 곳에 렌더돼 strict-mode
    // 위반이 난다 — first()로 한 요소만 특정한다.
    await expect(page.getByText("e2e-paper-order-strategy").first()).toBeVisible();
  });

  test("[실패 주입] 4단계 포지션 조회 5xx 시 포트폴리오 화면이 오류 배너를 보여주고 배분 내역을 표시하지 않는다", async ({
    page,
  }) => {
    await mockBackend(page);
    // EXCHANGE_FATAL은 ErrorMessage가 원문 message를 그대로 보여주지 않고
    // apiError.ts의 고정 매핑 문구로 치환한다(PortfolioPage.errors.test.tsx와
    // 동일 계약, 502로만 fatal 분류된다 — 500은 별도 재시도 경로를 탄다).
    await page.route(`${API_BASE}/portfolio`, (route) =>
      json(route, 502, {
        error_code: "EXCHANGE_FATAL",
        message: "raw server detail",
        details: {},
        trace_id: "e2e-fault-injection-portfolio",
        retry_after_seconds: null,
      }),
    );
    await page.goto("/portfolio");

    await expect(page.getByText("거래소 자격증명을 확인해주세요.")).toBeVisible();
    await expect(page.getByText("총 포트폴리오 가치")).toHaveCount(0);
  });

  test("5단계 컴플라이언스/위임장 상태: /mandates 화면에 활성 리비전 상태가 표시된다", async ({ page }) => {
    await mockBackend(page);
    await mockMandateStatus(page, {
      id: "rev-1",
      mandate_id: "mandate-1",
      revision_no: 1,
      state: "ACTIVE",
      max_total_exposure_pct: "80",
      max_single_instrument_pct: "20",
      min_cash_buffer_pct: "10",
      max_daily_loss_pct: "5",
      allowed_autonomy: "SEMI_AUTO",
      forbidden_assets: [],
      revision_hash: "e2e-hash",
      cooling_off_started_at: null,
      created_at: "2026-01-01T00:00:00Z",
      activated_at: "2026-01-01T00:00:00Z",
      schema_version: 1,
    });
    await page.goto("/mandates");

    await expect(page.getByRole("heading", { name: "위임장(Mandate)" })).toBeVisible();
    await expect(page.getByText("현재 활성 리비전")).toBeVisible();
    await expect(page.getByText("ACTIVE")).toBeVisible();
  });

  test("5단계 [실패 주입] 위임장 미설정 시 주문 차단 문구를 fail-closed로 보여준다", async ({ page }) => {
    await mockBackend(page);
    await mockMandateStatus(page, null);
    await page.goto("/mandates");

    await expect(page.getByText("위임장 미설정(주문 차단)")).toBeVisible();
  });

  test("6단계 알림 수신: /alerts, /notifications 화면에 체결/거부 알림이 반영된다", async ({ page }) => {
    await mockBackend(page);
    await mockAlerts(page, [
      {
        id: 1,
        user_id: "e2e-user",
        exchange: "bitget",
        symbol: "BTC/USDT",
        timeframe: "1h",
        indicator: "RSI",
        params: { timeperiod: 14 },
        operator: "<",
        threshold: "30",
        status: "TRIGGERED",
        created_at: "2026-01-01T00:00:00Z",
        triggered_at: "2026-01-01T01:00:00Z",
        triggered_value: "28",
      },
    ]);
    await mockNotificationHistory(page, [
      {
        event_type: "페이퍼 주문 체결 알림",
        channel: "IN_APP",
        status: "SENT",
        created_at: "2026-01-01T01:00:00Z",
      },
    ]);
    await page.goto("/alerts");

    await expect(page.getByRole("heading", { name: "가격/지표 알림" })).toBeVisible();
    await expect(page.getByText("발동됨")).toBeVisible();

    await page.goto("/notifications");

    await expect(page.getByRole("heading", { name: "알림 센터" })).toBeVisible();
    await expect(page.getByText("페이퍼 주문 체결 알림")).toBeVisible();
  });
});
