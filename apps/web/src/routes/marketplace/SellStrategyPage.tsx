import { useCreateListing } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

// 알려진 제약 — 백엔드에 "내 전략 목록" 조회 엔드포인트가 아직 없어
// strategy_id/version을 직접 입력받는다(전략 편집기에서 저장 시 확인한
// 값을 그대로 사용).
export function SellStrategyPage() {
  const [strategyId, setStrategyId] = useState("");
  const [strategyVersion, setStrategyVersion] = useState("1.0.0");
  const [price, setPrice] = useState("10.00");
  const [error, setError] = useState<string | null>(null);
  const createListing = useCreateListing();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const listing = await createListing.mutateAsync({
        strategyId,
        strategyVersion,
        price,
      });
      navigate(`/marketplace/${listing.id}`, { state: { listing } });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "리스팅 생성에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">내 전략 판매하기</h1>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <label className="text-sm text-slate-400">전략 ID</label>
            <input
              type="text"
              required
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
          </div>
          <div className="space-y-1">
            <label className="text-sm text-slate-400">버전</label>
            <input
              type="text"
              required
              value={strategyVersion}
              onChange={(e) => setStrategyVersion(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
          </div>
          <div className="space-y-1">
            <label className="text-sm text-slate-400">가격 (USDT)</label>
            <input
              type="number"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={createListing.isPending}
            className="w-full rounded bg-slate-100 px-3 py-2 font-medium text-slate-950 hover:bg-white disabled:opacity-50"
          >
            {createListing.isPending ? "등록 중..." : "리스팅 등록 (초안)"}
          </button>
        </form>
      </div>
    </AppShell>
  );
}
