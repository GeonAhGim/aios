import type { AiClient } from "@aios/api-client";
import type { ExperimentView } from "@aios/shared-types";
import { EmptyState, LoadingState, Select } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AiErrorBanner } from "./AiErrorBanner";

// task-2657(AI-22): AiStudioPage.tsx "실험 비교" 섹션 — 두 실험을 골라 지표
// (§2.4 Experiment.metrics)를 나란히 보여준다. 서버가 계산한 값을 그대로
// 표시하고 프론트에서 재계산하지 않는다.
function ExperimentComparison({ experiments }: { experiments: ExperimentView[] }) {
  const { t } = useTranslation();
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");

  useEffect(() => {
    if (experiments.length > 0 && !experiments.some((e) => e.experimentId === leftId)) {
      setLeftId(experiments[0].experimentId);
    }
    if (experiments.length > 1 && !experiments.some((e) => e.experimentId === rightId)) {
      setRightId(experiments[1].experimentId);
    }
  }, [experiments, leftId, rightId]);

  const left = experiments.find((e) => e.experimentId === leftId);
  const right = experiments.find((e) => e.experimentId === rightId);
  const metricKeys = useMemo(() => {
    const keys = new Set<string>();
    if (left) Object.keys(left.metrics).forEach((k) => keys.add(k));
    if (right) Object.keys(right.metrics).forEach((k) => keys.add(k));
    return [...keys].sort();
  }, [left, right]);

  if (experiments.length === 0) return <EmptyState>{t("ai.experiments.empty")}</EmptyState>;

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Select value={leftId} onChange={(e) => setLeftId(e.target.value)} aria-label={t("ai.experiments.compareA")}>
          {experiments.map((exp) => (
            <option key={exp.experimentId} value={exp.experimentId}>
              {exp.experimentId} ({exp.kind})
            </option>
          ))}
        </Select>
        <Select value={rightId} onChange={(e) => setRightId(e.target.value)} aria-label={t("ai.experiments.compareB")}>
          {experiments.map((exp) => (
            <option key={exp.experimentId} value={exp.experimentId}>
              {exp.experimentId} ({exp.kind})
            </option>
          ))}
        </Select>
      </div>
      {left && right && metricKeys.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="ai-experiment-comparison-table">
            <thead>
              <tr className="text-left text-fg-muted">
                <th>{t("ai.experiments.metricColumn")}</th>
                <th>{left.experimentId}</th>
                <th>{right.experimentId}</th>
              </tr>
            </thead>
            <tbody>
              {metricKeys.map((key) => (
                <tr key={key} className="border-t border-border">
                  <td className="py-1">{key}</td>
                  <td className="tabular">{left.metrics[key] ?? "—"}</td>
                  <td className="tabular">{right.metrics[key] ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-fg-muted">{t("ai.experiments.noMetrics")}</p>
      )}
    </div>
  );
}

export function ExperimentsSection({ client }: { client: AiClient }) {
  const query = useQuery({ queryKey: ["ai-experiments"], queryFn: () => client.listExperiments() });

  if (query.isError) return <AiErrorBanner error={query.error} onRetry={() => query.refetch()} />;
  if (query.isLoading) return <LoadingState />;
  return <ExperimentComparison experiments={query.data ?? []} />;
}
