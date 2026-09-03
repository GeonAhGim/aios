import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CheckResult } from "@aios/api-client";
import { ReadinessChecksTable } from "./ReadinessChecksTable";

afterEach(cleanup);

const OK: CheckResult = { ok: true, detail: null, observed: 3, threshold: 10 };
const FAILED: CheckResult = { ok: false, detail: "no consumers", observed: 0, threshold: 1 };

describe("ReadinessChecksTable", () => {
  it("체크별 ok·detail·observed·threshold 4필드를 모두 표시한다", () => {
    render(
      <ReadinessChecksTable
        checks={{ db_pool: OK, event_bus: FAILED }}
      />,
    );

    expect(screen.getByText("db_pool")).toBeInTheDocument();
    expect(screen.getByText("정상")).toBeInTheDocument();
    expect(screen.getByText("event_bus")).toBeInTheDocument();
    expect(screen.getByText("실패")).toBeInTheDocument();
    expect(screen.getByText("no consumers")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
  });

  it("loop:<name> 키는 별도 '루프' 그룹으로 묶는다", () => {
    render(
      <ReadinessChecksTable
        checks={{ db_pool: OK, "loop:trading": FAILED, "loop:reporting": OK }}
      />,
    );

    expect(screen.getByText("체크")).toBeInTheDocument();
    expect(screen.getByText("루프")).toBeInTheDocument();
    expect(screen.getByText("loop:trading")).toBeInTheDocument();
    expect(screen.getByText("loop:reporting")).toBeInTheDocument();
  });

  it("negative: checks가 빈 객체여도 깨지지 않고 안내 문구만 보여준다", () => {
    expect(() => render(<ReadinessChecksTable checks={{}} />)).not.toThrow();
    expect(screen.getByText("등록된 체크가 없습니다.")).toBeInTheDocument();
  });

  it("negative: 미지 키·observed=null·threshold=null인 항목도 대시(-)로 표시하며 깨지지 않는다", () => {
    expect(() =>
      render(
        <ReadinessChecksTable
          checks={{ custom_未知_check: { ok: true, detail: null, observed: null, threshold: null } }}
        />,
      ),
    ).not.toThrow();

    expect(screen.getByText("custom_未知_check")).toBeInTheDocument();
    expect(screen.getAllByText("-").length).toBeGreaterThanOrEqual(2);
  });
});
