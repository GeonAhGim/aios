import "../i18n";
import "@testing-library/jest-dom/vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ResearchSourceStatusView } from "@aios/shared-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CoverageFreshnessPanel } from "./CoverageFreshnessPanel";
import { perfBudgetMs } from "../test/perfBudget";

// CoverageBadge.test.tsx/DataFreshness.test.tsx와 동일 사유(vitest.config에
// globals:true가 없어 자동 cleanup 미등록).
afterEach(cleanup);

function source(overrides: Partial<ResearchSourceStatusView> = {}): ResearchSourceStatusView {
  return {
    sourceId: "opendart",
    publisher: "금감원 OpenDART",
    redistribution: "store_full",
    licenseRef: "opendart-tos",
    rateLimit: 1000,
    coverage: "2015-01-01~present",
    lastIngestedAt: "2026-09-24T00:00:00Z",
    health: "ok",
    ...overrides,
  };
}

const NOW = new Date("2026-09-24T00:05:00Z");

describe("CoverageFreshnessPanel", () => {
  it("소스가 없으면 빈 상태 문구를 보여준다", () => {
    render(<CoverageFreshnessPanel sources={[]} now={NOW} />);

    expect(screen.getByText("표시할 소스가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByTestId("coverage-freshness-alert")).not.toBeInTheDocument();
  });

  it("최근 적재된 정상 소스는 정상 배지만 보여주고 알림을 띄우지 않는다", () => {
    render(<CoverageFreshnessPanel sources={[source()]} now={NOW} staleAfterSec={3600} />);

    expect(screen.getByTestId("coverage-freshness-ok-opendart")).toHaveTextContent("정상");
    expect(screen.queryByTestId("coverage-freshness-alert")).not.toBeInTheDocument();
  });

  it("최근 적재 시각이 임계값을 넘기면 지연 배지와 지연 알림을 보여준다", () => {
    render(
      <CoverageFreshnessPanel
        sources={[source({ lastIngestedAt: "2026-09-23T00:00:00Z" })]}
        now={NOW}
        staleAfterSec={3600}
      />,
    );

    expect(screen.getByTestId("coverage-freshness-delayed-opendart")).toHaveTextContent("지연");
    expect(screen.getByTestId("coverage-freshness-alert")).toHaveTextContent("수집 지연 1건");
  });

  it("health가 down인 소스는 장애 배지와 장애 알림을 보여준다", () => {
    render(<CoverageFreshnessPanel sources={[source({ health: "down" })]} now={NOW} staleAfterSec={3600} />);

    expect(screen.getByTestId("coverage-freshness-down-opendart")).toHaveTextContent("장애");
    expect(screen.getByTestId("coverage-freshness-alert")).toHaveTextContent("소스 장애 1건");
  });

  it("health가 degraded인 소스는 저하 배지를 보여주되(지연/장애가 아니면) 알림은 띄우지 않는다", () => {
    render(<CoverageFreshnessPanel sources={[source({ health: "degraded" })]} now={NOW} staleAfterSec={3600} />);

    expect(screen.getByTestId("coverage-freshness-degraded-opendart")).toHaveTextContent("저하");
    expect(screen.queryByTestId("coverage-freshness-alert")).not.toBeInTheDocument();
  });

  it("negative: lastIngestedAt과 health가 모두 없는 소스를 정상으로 침묵 처리하지 않고 판정 불가로 표시한다", () => {
    render(
      <CoverageFreshnessPanel
        sources={[source({ lastIngestedAt: undefined, health: undefined })]}
        now={NOW}
        staleAfterSec={3600}
      />,
    );

    expect(screen.getByTestId("coverage-freshness-unknown-opendart")).toHaveTextContent("판정 불가");
    expect(screen.queryByTestId("coverage-freshness-ok-opendart")).not.toBeInTheDocument();
    expect(screen.queryByTestId("coverage-freshness-alert")).not.toBeInTheDocument();
  });

  it("negative: lastIngestedAt이 파싱 불가능한 값이어도 예외를 던지지 않고 판정 불가로 접힌다", () => {
    render(
      <CoverageFreshnessPanel sources={[source({ lastIngestedAt: "not-a-date" })]} now={NOW} staleAfterSec={3600} />,
    );

    expect(screen.getByTestId("coverage-freshness-unknown-opendart")).toBeInTheDocument();
  });

  it("negative: staleAfterSec 이내면 지연 배지도 알림도 뜨지 않는다(경계값)", () => {
    render(
      <CoverageFreshnessPanel
        sources={[source({ lastIngestedAt: "2026-09-24T00:04:30Z" })]}
        now={NOW}
        staleAfterSec={3600}
      />,
    );

    expect(screen.queryByTestId("coverage-freshness-delayed-opendart")).not.toBeInTheDocument();
    expect(screen.queryByTestId("coverage-freshness-alert")).not.toBeInTheDocument();
  });

  it("failure injection: 지연 소스와 장애 소스가 동시에 섞여 있어도 두 알림 모두 각자의 건수로 드러나고 서로를 가리지 않는다", () => {
    render(
      <CoverageFreshnessPanel
        sources={[
          source({ sourceId: "opendart", lastIngestedAt: "2026-09-23T00:00:00Z" }),
          source({ sourceId: "gdelt", health: "down", lastIngestedAt: "2026-09-24T00:04:59Z" }),
          source({ sourceId: "fred" }),
        ]}
        now={NOW}
        staleAfterSec={3600}
      />,
    );

    const alert = screen.getByTestId("coverage-freshness-alert");
    expect(alert).toHaveTextContent("소스 장애 1건");
    expect(alert).toHaveTextContent("수집 지연 1건");
    expect(screen.getByTestId("coverage-freshness-down-gdelt")).toBeInTheDocument();
    expect(screen.getByTestId("coverage-freshness-delayed-opendart")).toBeInTheDocument();
    expect(screen.getByTestId("coverage-freshness-ok-fred")).toBeInTheDocument();
  });

  it("수치/성능: 500개 소스(절반은 지연)를 렌더해도 정확한 지연 건수를 계산하고 짧은 시간 안에 끝난다", () => {
    const SOURCE_COUNT = 500;
    const sources: ResearchSourceStatusView[] = [];
    for (let i = 0; i < SOURCE_COUNT; i += 1) {
      sources.push(
        source({
          sourceId: `source-${i}`,
          lastIngestedAt: i % 2 === 0 ? "2026-09-24T00:04:59Z" : "2026-09-23T00:00:00Z",
        }),
      );
    }

    const startedAt = performance.now();
    render(<CoverageFreshnessPanel sources={sources} now={NOW} staleAfterSec={3600} />);
    const elapsedMs = performance.now() - startedAt;

    expect(screen.getByTestId("coverage-freshness-alert")).toHaveTextContent(`수집 지연 ${SOURCE_COUNT / 2}건`);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(1500));
  });
});

describe("[gate-red repro] CoverageFreshnessPanel.tsx 같은 i18n 0-리터럴 파일이 하드코딩 문자열을 다시 들이면 즉시 걸린다", () => {
  function makeWorkspace() {
    const root = mkdtempSync(join(tmpdir(), "rd18-i18n-gatered-"));
    const src = join(root, "src");
    mkdirSync(src, { recursive: true });
    return { root, src };
  }

  function run(root: string, src: string, baselinePath: string) {
    try {
      const stdout = execFileSync(
        process.execPath,
        [
          join(process.cwd(), "..", "..", "scripts", "check_i18n_literals.mjs"),
          "--root",
          src,
          "--base",
          root,
          "--baseline",
          baselinePath,
        ],
        { encoding: "utf-8" },
      );
      return { status: 0, stdout };
    } catch (err) {
      const e = err as { status?: number; stdout?: string };
      return { status: e.status ?? 1, stdout: e.stdout ?? "" };
    }
  }

  it("baseline 0건인 파일에 하드코딩 리터럴이 다시 생기면 gate가 즉시 실패한다", () => {
    const { root, src } = makeWorkspace();
    try {
      writeFileSync(
        join(src, "CoverageFreshnessPanel.tsx"),
        `export function Panel() {\n  return <span>장애</span>;\n}\n`,
      );
      const baselinePath = join(root, "baseline.json");
      writeFileSync(baselinePath, JSON.stringify({ files: {} }));
      const { status, stdout } = run(root, src, baselinePath);
      expect(status).toBe(1);
      expect(stdout).toMatch(/FAIL: src\/CoverageFreshnessPanel\.tsx:1 — new hardcoded literal/);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
