import { useSubmitRiskAssessment } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import type { InvestmentGoal, LiquidityNeed } from "@aios/shared-types";
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
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4 py-12">
      <form onSubmit={handleSubmit} className="w-full max-w-lg space-y-6 text-slate-100">
        <div>
          <h1 className="text-2xl font-semibold">투자자 적합성평가 (필수)</h1>
          <p className="mt-1 text-sm text-slate-400">
            투자 경험과 위험 감내도를 바탕으로 회원님께 맞는 위험등급을 안내해드립니다.
            강제 차단이 아니라 참고용 조언과 불일치 경고 목적으로만 사용됩니다.
          </p>
        </div>

        <div className="space-y-1">
          <label className="text-sm text-slate-300">투자 경험 연수</label>
          <input
            type="number"
            min={0}
            required
            value={yearsOfExperience}
            onChange={(e) => setYearsOfExperience(Number(e.target.value))}
            className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 outline-none focus:border-slate-400"
          />
        </div>

        <div className="space-y-1">
          <label className="text-sm text-slate-300">
            순자산 대비 투자 가능 비중 (%): {investableRatioPct}%
          </label>
          <input
            type="range"
            min={0}
            max={100}
            value={investableRatioPct}
            onChange={(e) => setInvestableRatioPct(Number(e.target.value))}
            className="w-full"
          />
        </div>

        <div className="space-y-1">
          <label className="text-sm text-slate-300">
            원금 대비 감내 가능한 손실 수준 (%): {lossTolerancePct}%
          </label>
          <input
            type="range"
            min={0}
            max={100}
            value={lossTolerancePct}
            onChange={(e) => setLossTolerancePct(Number(e.target.value))}
            className="w-full"
          />
        </div>

        <div className="space-y-1">
          <label className="text-sm text-slate-300">투자 목표</label>
          <select
            value={investmentGoal}
            onChange={(e) => setInvestmentGoal(e.target.value as InvestmentGoal)}
            className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 outline-none focus:border-slate-400"
          >
            <option value="SHORT_TERM_PROFIT">단기 수익 추구</option>
            <option value="LONG_TERM_GROWTH">장기 자산 성장</option>
          </select>
        </div>

        <div className="space-y-1">
          <label className="text-sm text-slate-300">자금 필요 시점</label>
          <select
            value={liquidityNeed}
            onChange={(e) => setLiquidityNeed(e.target.value as LiquidityNeed)}
            className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 outline-none focus:border-slate-400"
          >
            <option value="WITHIN_1_YEAR">1년 이내</option>
            <option value="1_TO_3_YEARS">1~3년</option>
            <option value="OVER_3_YEARS">3년 이상</option>
          </select>
        </div>

        {error && <p className="text-sm text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={submit.isPending}
          className="w-full rounded bg-slate-100 px-3 py-2 font-medium text-slate-950 hover:bg-white disabled:opacity-50"
        >
          {submit.isPending ? "제출 중..." : "제출하기"}
        </button>
      </form>
    </div>
  );
}
