import { useCreateReview } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Button, Field, PageHeader, Textarea } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useFieldErrors } from "../../hooks/useFieldErrors";

// spec §3.3 에러 taxonomy: 리뷰 작성 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴). listingId 누락
// 등 클라이언트 자체 검증 실패는 ApiError가 아니므로 계속 문자열 그대로 둔다.
//
// task-954: VALIDATION_INVALID_FIELD는 classifyBadRequest가 "field"로 분류해
// BadRequestNotice가 자체적으로 null을 렌더한다(task-364 설계) — 그래서 지금까지
// 이 경로는 배너도 인라인도 없이 완전히 조용했다. fieldErrors를 ErrorMessage에
// 넘겨 계약(비어있지 않으면 배너 생략)을 지키고, 실제 표시는 아래 입력 옆
// Field.error로 한다.
function CreateReviewError({ error, fieldErrors }: { error: unknown; fieldErrors: Record<string, string> }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      fieldErrors={fieldErrors}
    />
  );
}

export function WriteReviewPage() {
  const { purchaseId } = useParams<{ purchaseId: string }>();
  const [searchParams] = useSearchParams();
  const listingId = Number(searchParams.get("listingId") ?? 0);
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const createReview = useCreateReview();
  const navigate = useNavigate();
  const { fieldErrors, setFromError, clearField } = useFieldErrors();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setClientError(null);
    setError(null);
    setFromError(null);
    if (!listingId) {
      setClientError("리스팅 정보가 없습니다 — 마켓플레이스 상세 화면에서 다시 시도해주세요.");
      return;
    }
    try {
      await createReview.mutateAsync({ listingId, body: { rating, comment: comment || undefined } });
      navigate(`/marketplace/${listingId}`);
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("리뷰 작성에 실패했습니다."));
      setFromError(err);
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <PageHeader title={`리뷰 작성 (구매 #${purchaseId})`} />
        <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-border bg-surface p-6">
          <Field label={`평점 — ${rating}`} error={fieldErrors.rating}>
            <input
              type="range"
              min={1}
              max={5}
              value={rating}
              onChange={(e) => {
                setRating(Number(e.target.value));
                clearField("rating");
              }}
              className="w-full accent-accent"
            />
          </Field>
          <Field label="코멘트 (선택)" error={fieldErrors.comment}>
            <Textarea
              value={comment}
              onChange={(e) => {
                setComment(e.target.value);
                clearField("comment");
              }}
              rows={4}
            />
          </Field>
          {clientError && <Alert>{clientError}</Alert>}
          {error !== null && <CreateReviewError error={error} fieldErrors={fieldErrors} />}
          <Button type="submit" loading={createReview.isPending} className="w-full">
            리뷰 제출
          </Button>
        </form>
      </div>
    </AppShell>
  );
}
