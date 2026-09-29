import { expect, test } from "@playwright/test";
import { fieldControl } from "./support/fieldControl";
import { mockBackend } from "./support/mockBackend";

// H-7b 스모크 1/3 — 주문(실행) 제출. ExecutionControlPage.tsx(POST /executions,
// envelope=false)를 대상으로, (1) 정상 제출 시 목록에 반영, (2) 400 실패 주입
// 시 에러 배너로 표면화되고 목록에 반영되지 않는지를 함께 본다(DoD의 "실패
// 주입 변조 케이스 1건"이 이 스펙의 두 번째 테스트다).
test.describe("주문 제출", () => {
  test("실행 생성 폼을 제출하면 실행 목록에 새 카드가 나타난다", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/executions");

    await expect(page.getByRole("heading", { name: "실행 제어판" })).toBeVisible();

    await fieldControl(page, "전략 ID").fill("e2e-momentum-strategy");
    await fieldControl(page, "버전").fill("1.0.0");
    await fieldControl(page, "배분 자본(USDT)").fill("500");
    // CI에서 간헐 적색(esc-ci-e2e.json)이 났던 지점 — 제출 클릭 직후 바로
    // toBeVisible(10s)로 넘어가면 POST /executions 응답 + invalidateQueries가
    // 트리거한 GET /executions 재조회 왕복(부하가 큰 CI 러너에서 page.route
    // IPC가 느려질 때 수 초 소요)까지 같은 10초 예산을 나눠 써야 했다. 목록이
    // 실제로 새로고침되는 시점(재조회 GET 응답)까지 먼저 명시적으로 기다려
    // 네트워크 왕복 시간을 시각화 어서션의 타임아웃 예산에서 분리한다.
    const executionsRefetched = page.waitForResponse(
      (res) =>
        res.url().endsWith("/executions") &&
        res.request().method() === "GET" &&
        res.status() === 200,
    );
    await page.getByRole("button", { name: "실행 생성" }).click();
    await executionsRefetched;

    await expect(page.getByText("e2e-momentum-strategy")).toBeVisible();
    // 성공 후 폼의 전략 ID 입력은 비워진다(ExecutionControlPage.submitExecution).
    await expect(fieldControl(page, "전략 ID")).toHaveValue("");
  });

  test("[실패 주입] 400 VALIDATION_INVALID_FIELD 응답이면 오류 배너를 보여주고 목록에 반영하지 않는다", async ({
    page,
  }) => {
    await mockBackend(page, {
      createExecutionResponse: {
        status: 400,
        body: {
          error_code: "VALIDATION_INVALID_FIELD",
          message: "배분 자본이 올바르지 않습니다.",
          details: {},
          trace_id: "e2e-fault-injection",
          retry_after_seconds: null,
        },
      },
    });
    await page.goto("/executions");

    await fieldControl(page, "전략 ID").fill("e2e-should-not-appear");
    await fieldControl(page, "버전").fill("1.0.0");
    await fieldControl(page, "배분 자본(USDT)").fill("-1");
    await page.getByRole("button", { name: "실행 생성" }).click();

    await expect(page.getByText("배분 자본이 올바르지 않습니다.")).toBeVisible();
    await expect(page.getByText("e2e-should-not-appear")).toHaveCount(0);
    // 실패 시 폼은 그대로 남는다(submitExecution catch 분기 — setStrategyId 호출 없음).
    await expect(fieldControl(page, "전략 ID")).toHaveValue("e2e-should-not-appear");
  });
});
