import { useGenerateFromPrompt, useGenerateWizardStrategy } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  routeApiError,
  type GeneratedConditions,
  type RiskTolerance,
  type StrategyGoal,
} from "@aios/shared-types";
import { Button, Card, Select, Textarea } from "@aios/ui-web";
import { useState } from "react";
import { BadRequestNotice } from "../../../components/BadRequestNotice";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { ForbiddenNotice } from "../../../components/ForbiddenNotice";

const GOAL_LABELS: Record<StrategyGoal, string> = {
  STEADY_GROWTH: "안정적 성장",
  AGGRESSIVE_GROWTH: "공격적 성장",
  HEDGE: "헤지(방어적 대응)",
};

const RISK_LABELS: Record<RiskTolerance, string> = {
  LOW: "낮음",
  MEDIUM: "보통",
  HIGH: "높음",
};

type Mode = "WIZARD" | "PROMPT";

// spec §3.3 에러 taxonomy: 생성 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴).
function GenerateError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

// ADR-2026-08-29 §3 — 조건식을 직접 조립하는 것보다 쉬운 고차원 전략 생성.
// 결과는 항상 기존 조건 에디터(ConditionGroup)로 그대로 넘어가 검토·수정
// 화면으로 계속 쓰인다 — 여기서 결과를 직접 저장하지 않는다.
export function StrategyWizardPanel({
  onApply,
}: {
  onApply: (generated: GeneratedConditions) => void;
}) {
  const [mode, setMode] = useState<Mode>("WIZARD");
  const [goal, setGoal] = useState<StrategyGoal>("STEADY_GROWTH");
  const [riskTolerance, setRiskTolerance] = useState<RiskTolerance>("MEDIUM");
  const [prompt, setPrompt] = useState("");
  const [result, setResult] = useState<GeneratedConditions | null>(null);
  const [error, setError] = useState<unknown>(null);
  const generateWizard = useGenerateWizardStrategy();
  const generateFromPrompt = useGenerateFromPrompt();

  async function handleGenerate() {
    setError(null);
    setResult(null);
    try {
      const generated =
        mode === "WIZARD"
          ? await generateWizard.mutateAsync({ goal, riskTolerance })
          : await generateFromPrompt.mutateAsync({ prompt });
      setResult(generated);
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("생성에 실패했습니다."));
    }
  }

  const isPending = generateWizard.isPending || generateFromPrompt.isPending;

  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-fg">고차원 전략 생성</h2>
        <div className="flex gap-1 rounded-md bg-bg p-1">
          <button
            type="button"
            onClick={() => {
              setMode("WIZARD");
              setResult(null);
              setError(null);
            }}
            className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
              mode === "WIZARD" ? "bg-accent-muted text-accent-hover" : "text-fg-muted"
            }`}
          >
            마법사
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("PROMPT");
              setResult(null);
              setError(null);
            }}
            className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
              mode === "PROMPT" ? "bg-accent-muted text-accent-hover" : "text-fg-muted"
            }`}
          >
            AI 프롬프트
          </button>
        </div>
      </div>

      {mode === "WIZARD" ? (
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-sm">
            <span className="text-fg-secondary">투자 목표</span>
            <Select value={goal} onChange={(e) => setGoal(e.target.value as StrategyGoal)}>
              {(Object.keys(GOAL_LABELS) as StrategyGoal[]).map((g) => (
                <option key={g} value={g}>
                  {GOAL_LABELS[g]}
                </option>
              ))}
            </Select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-fg-secondary">위험 허용도</span>
            <Select
              value={riskTolerance}
              onChange={(e) => setRiskTolerance(e.target.value as RiskTolerance)}
            >
              {(Object.keys(RISK_LABELS) as RiskTolerance[]).map((r) => (
                <option key={r} value={r}>
                  {RISK_LABELS[r]}
                </option>
              ))}
            </Select>
          </label>
        </div>
      ) : (
        <label className="block space-y-1 text-sm">
          <span className="text-fg-secondary">전략을 말로 설명해주세요</span>
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="예: RSI 과매도에서 반등 매수하고 과매수에서 매도하는 전략"
            rows={3}
          />
        </label>
      )}

      <Button
        type="button"
        variant="secondary"
        onClick={handleGenerate}
        loading={isPending}
        disabled={mode === "PROMPT" && !prompt.trim()}
      >
        생성하기
      </Button>

      {error !== null && <GenerateError error={error} />}

      {result && (
        <div className="space-y-3 rounded-lg border border-border-strong bg-bg p-4">
          <p className="text-sm text-fg-secondary">{result.explanation}</p>
          <Button type="button" onClick={() => onApply(result)}>
            이 조건 적용하기
          </Button>
        </div>
      )}
    </Card>
  );
}
