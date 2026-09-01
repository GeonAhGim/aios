import { useCreateListing, useMyStrategies } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, EmptyState, Field, Input, LoadingState, PageHeader, Select } from "@aios/ui-web";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

function strategyKey(strategyId: string, version: string): string {
  return `${strategyId}@${version}`;
}

export function SellStrategyPage() {
  const { data: strategies, isLoading } = useMyStrategies();
  const [selectedKey, setSelectedKey] = useState("");
  const [price, setPrice] = useState("10.00");
  const [error, setError] = useState<string | null>(null);
  const createListing = useCreateListing();
  const navigate = useNavigate();

  useEffect(() => {
    if (strategies && strategies.length > 0 && !selectedKey) {
      setSelectedKey(strategyKey(strategies[0].strategyId, strategies[0].version));
    }
  }, [strategies, selectedKey]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const [strategyId, strategyVersion] = selectedKey.split("@");
    if (!strategyId || !strategyVersion) {
      setError("판매할 전략을 선택해주세요.");
      return;
    }
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
        {isLoading ? (
          <LoadingState />
        ) : !strategies || strategies.length === 0 ? (
          <EmptyState>
            등록된 전략이 없습니다.{" "}
            <Link to="/strategy-builder" className="text-accent-hover hover:underline">
              전략 편집기에서 먼저 만들어보세요
            </Link>
            .
          </EmptyState>
        ) : (
          <form
            onSubmit={handleSubmit}
            className="space-y-3 rounded-lg border border-border bg-surface p-6"
          >
            <Field label="판매할 전략">
              <Select value={selectedKey} onChange={(e) => setSelectedKey(e.target.value)}>
                {strategies.map((s) => (
                  <option key={strategyKey(s.strategyId, s.version)} value={strategyKey(s.strategyId, s.version)}>
                    {s.strategyId}@{s.version} ({s.lifecycleStatus})
                  </option>
                ))}
              </Select>
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
        )}
      </div>
    </AppShell>
  );
}
