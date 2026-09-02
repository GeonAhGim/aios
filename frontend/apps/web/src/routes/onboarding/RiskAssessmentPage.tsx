import { useSubmitRiskAssessment } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import type { InvestmentGoal, LiquidityNeed } from "@aios/shared-types";
import { Alert, Button, Field, Input, Select } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

// FD-15.1 필수 게이트 — 회원가입 직후(MFA 완료 후) 스킵 불가.
export function RiskAssessmentPage() {
  const [yearsOfExperience, setYearsOfExperience] = useState(0);
  const [investableRatioPct, setInvestableRatioPct] = useState(10);
  const [lossTolerancePct, setLossTolerancePct] = useState(10);
  const [investmentGoal, setInvestmentGoal] = useState<InvestmentGoal>("LONG_TERM_GROWTH");
  const [liquidityNeed, setLiquidityNeed] = useState<LiquidityNeed>("WITHIN_1_YEAR");
  const [error, setError] = useState<string | null>(null);
  const submit = useSubmitRiskAssessment();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await submit.mutateAsync({
        yearsOfExperience,
        investableRatioPct,
        lossTolerancePct,
        investmentGoal,
        liquidityNeed,
      });
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "평가 제출에 실패했습니다.");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 py-12">
      <div className="w-full max-w-lg">
        <div className="mb-6 text-center">
          <span className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-lg font-bold text-bg">
            A
          </span>
          <h1 className="text-xl font-semibold text-fg">투자자 적합성평가 (필수)</h1>
          <p className="mt-1 text-sm text-fg-muted">
            투자 경험과 위험 감내도를 바탕으로 맞는 위험등급을 안내해드립니다. 강제 차단이
            아니라 참고용 조언·불일치 경고 목적으로만 사용됩니다.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-xl border border-border bg-surface p-6">
          <Field label="투자 경험 연수">
            <Input
              type="number"
              min={0}
              required
              value={yearsOfExperience}
              onChange={(e) => setYearsOfExperience(Number(e.target.value))}
            />
          </Field>

          <Field label={`순자산 대비 투자 가능 비중 — ${investableRatioPct}%`}>
            <input
              type="range"
              min={0}
              max={100}
              value={investableRatioPct}
              onChange={(e) => setInvestableRatioPct(Number(e.target.value))}
              className="w-full accent-accent"
            />
          </Field>

          <Field label={`원금 대비 감내 가능한 손실 수준 — ${lossTolerancePct}%`}>
            <input
              type="range"
              min={0}
              max={100}
              value={lossTolerancePct}
              onChange={(e) => setLossTolerancePct(Number(e.target.value))}
              className="w-full accent-accent"
            />
          </Field>

          <Field label="투자 목표">
            <Select
              value={investmentGoal}
              onChange={(e) => setInvestmentGoal(e.target.value as InvestmentGoal)}
            >
              <option value="SHORT_TERM_PROFIT">단기 수익 추구</option>
              <option value="LONG_TERM_GROWTH">장기 자산 성장</option>
            </Select>
          </Field>

          <Field label="자금 필요 시점">
            <Select
              value={liquidityNeed}
              onChange={(e) => setLiquidityNeed(e.target.value as LiquidityNeed)}
            >
              <option value="WITHIN_1_YEAR">1년 이내</option>
              <option value="1_TO_3_YEARS">1~3년</option>
              <option value="OVER_3_YEARS">3년 이상</option>
            </Select>
          </Field>

          {error && <Alert>{error}</Alert>}
          <Button type="submit" loading={submit.isPending} className="w-full">
            제출하기
          </Button>
        </form>
      </div>
    </div>
  );
}
