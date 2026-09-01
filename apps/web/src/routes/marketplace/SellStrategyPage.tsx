import { useCreateListing } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, Input, PageHeader } from "@aios/ui-web";
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
        <PageHeader title="내 전략 판매하기" />
        <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-border bg-surface p-6">
          <Field label="전략 ID">
            <Input type="text" required value={strategyId} onChange={(e) => setStrategyId(e.target.value)} />
          </Field>
          <Field label="버전">
            <Input
              type="text"
              required
              value={strategyVersion}
              onChange={(e) => setStrategyVersion(e.target.value)}
            />
          </Field>
          <Field label="가격 (크레딧)">
            <Input
              type="number"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
          </Field>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" loading={createListing.isPending} className="w-full">
            리스팅 등록 (초안)
          </Button>
        </form>
      </div>
    </AppShell>
  );
}
