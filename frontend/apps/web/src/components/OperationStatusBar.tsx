import { usePauseExecution } from "@aios/shared-hooks";
import type { ExecutionCardResponse } from "@aios/shared-types";
import { Badge, Button } from "@aios/ui-web";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

// J8 LP-6: 대시보드·실행 화면 상단에 항상 보이는 운용 상태 띠.
// 모의/실전 배지, 실전 배정 자본, 실행 중 개수, "모두 멈춤"(확인 단계 없는 1클릭 -- 정지는 안전을
// 높이는 동작이고 위기 상황에서 추가 단계는 사용감을 해친다, DashboardPage 긴급 정지와 같은 원칙)을 보여 준다.
// 조회 전이거나 실패하면 0이나 "실행 없음"이 아니라 "알 수 없음"으로 보여 준다 --
// 모르는 상태를 안전한 상태로 보이게 하지 않는다.
interface OperationStatusBarProps {
  executions: readonly ExecutionCardResponse[] | undefined;
  isLoading: boolean;
  isError: boolean;
}

type ModeKind = "paper" | "live" | "mixed" | "none";

function sumAllocated(items: readonly ExecutionCardResponse[]): number {
  return items.reduce((sum, e) => sum + (Number(e.allocatedCapital) || 0), 0);
}

function summarize(executions: readonly ExecutionCardResponse[]) {
  const active = executions.filter((e) => e.status !== "RETIRED");
  const live = active.filter((e) => e.mode === "LIVE");
  const paper = active.filter((e) => e.mode === "PAPER");
  const running = active.filter((e) => e.status === "RUNNING");
  const mode: ModeKind =
    live.length > 0 && paper.length > 0
      ? "mixed"
      : live.length > 0
        ? "live"
        : paper.length > 0
          ? "paper"
          : "none";
  return {
    mode,
    running,
    // 실제로 도는 실전 실행에 배정된 자본(LP-4의 단계적 노출이 반영된 값)이다.
    liveExposure: sumAllocated(live.filter((e) => e.status === "RUNNING")),
  };
}

function modeText(mode: ModeKind, t: TFunction): string {
  switch (mode) {
    case "live":
      return t("operationStatusBar.modeLive");
    case "mixed":
      return t("operationStatusBar.modeMixed");
    case "paper":
      return t("operationStatusBar.modePaper");
    default:
      return t("operationStatusBar.modeNone");
  }
}

const MODE_TONE = { live: "warning", mixed: "warning", paper: "accent", none: "neutral" } as const;

export function OperationStatusBar({ executions, isLoading, isError }: OperationStatusBarProps) {
  const { t } = useTranslation();
  const pause = usePauseExecution();
  const [result, setResult] = useState<string | null>(null);

  const known = !isLoading && !isError && executions !== undefined;
  const summary = known ? summarize(executions) : null;

  async function handleStopAll() {
    if (summary === null) return;
    const ids = summary.running.map((e) => e.executionId);
    if (ids.length === 0) return;
    const settled = await Promise.allSettled(ids.map((id) => pause.mutateAsync(id)));
    const failed = settled.filter((s) => s.status === "rejected").length;
    setResult(
      failed === 0
        ? t("operationStatusBar.stopAllDone", { n: ids.length })
        : t("operationStatusBar.stopAllFailed", { n: ids.length, failed }),
    );
  }

  const unknown = t("operationStatusBar.unknown");
  const modeLabel = summary === null ? unknown : modeText(summary.mode, t);

  return (
    <section
      aria-label={t("operationStatusBar.label")}
      className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-border bg-surface px-4 py-3 text-sm"
    >
      <Badge tone={summary === null ? "neutral" : MODE_TONE[summary.mode]}>{modeLabel}</Badge>
      <p className="tabular">
        <span className="text-fg-muted">{t("operationStatusBar.exposure")} </span>
        <span className="text-fg">
          {summary === null
            ? unknown
            : t("operationStatusBar.exposureValue", { amount: summary.liveExposure.toFixed(2) })}
        </span>
      </p>
      <p className="tabular">
        <span className="text-fg-muted">{t("operationStatusBar.running")} </span>
        <span className="text-fg">
          {summary === null
            ? unknown
            : t("operationStatusBar.runningValue", { n: summary.running.length })}
        </span>
      </p>
      {isError && <p className="text-danger">{t("operationStatusBar.loadFailed")}</p>}
      <div className="ml-auto flex items-center gap-3">
        {result !== null && (
          <p role="status" className="text-xs text-fg-muted">
            {result}
          </p>
        )}
        <Button
          type="button"
          variant="danger"
          size="sm"
          disabled={summary === null || summary.running.length === 0}
          loading={pause.isPending}
          onClick={() => void handleStopAll()}
        >
          {t("operationStatusBar.stopAll")}
        </Button>
      </div>
    </section>
  );
}
