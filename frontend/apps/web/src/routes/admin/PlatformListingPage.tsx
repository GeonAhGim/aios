import { useCreatePlatformListing } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Button, Field, Input, PageHeader } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// ADR-2026-08-29 §2 — 플랫폼이 하우스 계정 명의로 직접 등록하는 리스팅.
// 제3자 판매자용 검증 절차 없이 등록 즉시 LISTED로 게시된다.
//
// spec §3.3 에러 taxonomy: 등록 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 403/그 외를 각각 ForbiddenNotice/
// ErrorMessage 경로로만 보여준다(task-911).
function CreateListingError({ error }: { error: unknown }) {
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

export function PlatformListingPage() {
  const [strategyId, setStrategyId] = useState("");
  const [strategyVersion, setStrategyVersion] = useState("1.0.0");
  const [price, setPrice] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [created, setCreated] = useState<{ id: number } | null>(null);
  const createPlatformListing = useCreatePlatformListing();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setCreated(null);
    try {
      const listing = await createPlatformListing.mutateAsync({
        strategyId,
        strategyVersion,
        price: price || undefined,
      });
      setCreated({ id: listing.id });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("리스팅 등록에 실패했습니다."));
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <PageHeader title="플랫폼 전략 등록" />
        <p className="text-xs text-fg-muted">
          플랫폼(하우스 계정) 명의로 직접 판매하는 전략입니다 — 검증 절차 없이 등록 즉시
          마켓플레이스에 게시됩니다.
        </p>
        <form
          onSubmit={handleSubmit}
          className="space-y-3 rounded-lg border border-border bg-surface p-6"
        >
          <Field label="전략 ID">
            <Input
              type="text"
              required
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            />
          </Field>
          <Field label="버전">
            <Input
              type="text"
              required
              value={strategyVersion}
              onChange={(e) => setStrategyVersion(e.target.value)}
            />
          </Field>
          <Field label="가격 (크레딧, 비워두면 무료)">
            <Input
              type="number"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
          </Field>
          {error !== null && <CreateListingError error={error} />}
          {created && <Alert tone="success">리스팅 #{created.id} 게시 완료</Alert>}
          <Button type="submit" loading={createPlatformListing.isPending} className="w-full">
            등록
          </Button>
        </form>
      </div>
    </AppShell>
  );
}
