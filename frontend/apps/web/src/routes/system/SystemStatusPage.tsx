import { parseReadiness, summarizeReadiness, type ReadinessSummary } from "@aios/api-client";
import { usePlatformReadiness } from "@aios/shared-hooks";
import { Alert, LoadingState, PageHeader } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";
import { DataFreshness } from "../../components/DataFreshness";
import { ReadinessChecksTable } from "../../components/ReadinessChecksTable";
import { useTranslation } from "react-i18next";

// spec §3.2/§9 PLT-09: 읽기 전용 진단 화면 — 재시작·복구 조작 버튼은 두지 않는다
// (task-494 decision). fetch·파싱은 usePlatformReadiness(apiClient.getReadiness)와
// parseReadiness/summarizeReadiness(둘 다 재구현 금지, task-466 그대로 소비)에 위임한다.
const STALE_AFTER_SEC = 300;

function StatusSummary({ summary }: { summary: ReadinessSummary }) {
  const { t } = useTranslation();
  if (summary.status === "ready") {
    return <Alert tone="success">{t("legacy.systemStatusPage.t1")}</Alert>;
  }

  if (summary.status === "unknown") {
    return (
      <Alert tone="warning">
        {t("legacy.systemStatusPage.t2")}</Alert>
    );
  }

  return (
    <Alert tone="danger">
      <div data-testid="readiness-failure-summary">
        <p className="font-medium">{t("legacy.systemStatusPage.t3", { length: summary.failedChecks.length })}</p>
        {summary.failedChecks.length > 0 && (
          <ul className="mt-1 list-disc pl-5">
            {summary.failedChecks.map((fc) => (
              <li key={fc.name}>{fc.detail ? `${fc.name}: ${fc.detail}` : fc.name}</li>
            ))}
          </ul>
        )}
      </div>
    </Alert>
  );
}

export function SystemStatusPage() {
  const { t } = useTranslation();
  const { data, isLoading, isError } = usePlatformReadiness();

  if (isLoading) {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title={t("legacy.systemStatusPage.title4")} />
          <LoadingState />
        </div>
      </AppShell>
    );
  }

  // isError(네트워크 실패·JSON 파싱 실패)는 unknown과 동일하게 취급한다 —
  // getReadiness는 HTTP 상태를 판정하지 않고 몸체를 그대로 돌려주므로(fetchRaw
  // 주석 참고) 여기 도달하는 실패는 응답 자체를 못 받은 경우뿐이다.
  const parsed = isError ? ({ kind: "unknown" } as const) : parseReadiness(data);
  const summary = summarizeReadiness(parsed);
  const asOf = parsed.kind === "ok" ? parsed.report.as_of : null;
  const checks = parsed.kind === "ok" ? parsed.report.checks : {};

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader
          title={t("legacy.systemStatusPage.title5")}
          action={<DataFreshness asOf={asOf} staleAfterSec={STALE_AFTER_SEC} />}
        />
        <StatusSummary summary={summary} />
        <ReadinessChecksTable checks={checks} />
      </div>
    </AppShell>
  );
}
