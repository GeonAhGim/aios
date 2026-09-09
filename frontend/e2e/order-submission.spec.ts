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
    await page.getByRole("button", { name: "실행 생성" }).click();

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
