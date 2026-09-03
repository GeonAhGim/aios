import { useCreateListing, useMyStrategies } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Button, EmptyState, Field, Input, LoadingState, PageHeader, Select } from "@aios/ui-web";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

function strategyKey(strategyId: string, version: string): string {
  return `${strategyId}@${version}`;
}

// spec §3.3 에러 taxonomy: 리스팅 생성 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴).
function CreateListingError({ error }: { error: unknown }) {
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

export function SellStrategyPage() {
  const { data: strategies, isLoading } = useMyStrategies();
  const [selectedKey, setSelectedKey] = useState("");
  const [price, setPrice] = useState("10.00");
  const [clientError, setClientError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const createListing = useCreateListing();
  const navigate = useNavigate();

  useEffect(() => {
    if (strategies && strategies.length > 0 && !selectedKey) {
      setSelectedKey(strategyKey(strategies[0].strategyId, strategies[0].version));
    }
  }, [strategies, selectedKey]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setClientError(null);
    setError(null);
    const [strategyId, strategyVersion] = selectedKey.split("@");
    if (!strategyId || !strategyVersion) {
      setClientError("판매할 전략을 선택해주세요.");
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
      setError(err instanceof ApiError ? err : new Error("리스팅 생성에 실패했습니다."));
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
            {clientError && <Alert>{clientError}</Alert>}
            {error !== null && <CreateListingError error={error} />}
            <Button type="submit" loading={createListing.isPending} className="w-full">
              리스팅 등록 (초안)
            </Button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
