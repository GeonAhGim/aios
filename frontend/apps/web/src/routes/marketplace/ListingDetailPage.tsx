import { useListingReviews, usePurchaseListing, useSubmitForVerification } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  getApiErrorMessage,
  isResourceNotFound,
  routeApiError,
  type ListingSummary,
} from "@aios/shared-types";
import { Alert, Badge, Button, Card, EmptyState } from "@aios/ui-web";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { NotFoundState } from "../../components/NotFoundState";
import { RiskWarningModal } from "../../components/RiskWarningModal";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";

// spec §3.3 에러 taxonomy: 구매 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901). 402(잔액 부족)는
// statusCode로만 판별해 지갑 충전 링크를 붙인다 — 메시지 문자열 매칭은 하지 않는다.
function PurchaseError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const isInsufficientBalance = error instanceof ApiError && error.statusCode === 402;
  return (
    <div className="space-y-1">
      <ErrorMessage
        errorCode={error instanceof ApiError ? error.errorCode : undefined}
        message={error instanceof Error ? error.message : undefined}
        traceId={error instanceof ApiError ? error.traceId : undefined}
        retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      />
      {isInsufficientBalance && (
        <Link to="/wallet" className="text-sm underline">
          지갑 충전하기
        </Link>
      )}
    </div>
  );
}

export function ListingDetailPage() {
  const { listingId } = useParams<{ listingId: string }>();
  const location = useLocation();
  const listing = (location.state as { listing?: ListingSummary } | null)?.listing;
  const { data: reviews, error: reviewsError } = useListingReviews(
    listingId ? Number(listingId) : null,
  );
  const purchase = usePurchaseListing();
  const submitForVerification = useSubmitForVerification();
  const [error, setError] = useState<unknown>(null);
  const [riskWarningReason, setRiskWarningReason] = useState<string | null>(null);
  const [purchased, setPurchased] = useState<{ purchaseId: number; status: string } | null>(null);
  const { submit } = useIdempotentSubmit(`marketplace.purchase:listing-${listingId ?? "unknown"}`);

  if (!listingId) return null;
  const id = Number(listingId);

  async function attemptPurchase(acknowledged: boolean) {
    setError(null);
    try {
      const result = await submit((idempotencyKey) =>
        purchase.mutateAsync({
          listingId: id,
          body: { riskWarningAcknowledged: acknowledged },
          idempotencyKey,
        }),
      );
      setPurchased({ purchaseId: result.purchaseId, status: result.status });
      setRiskWarningReason(null);
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      if (err instanceof ApiError) {
        // 마켓플레이스 구매 실패(PurchaseError)는 아직 §3.3 ApiError 봉투를 쓰지 않아
        // (backend HTTPException(400, str(exc)) 그대로) error_code가 없다 — 위험등급
        // 불일치 경고만 유일하게 서버가 문구로 구분해 보내므로, getApiErrorMessage로
        // 매핑한 결과(폴백 시 서버 message와 동일)를 판별에 쓴다. 새 error_code를
        // 만들지 않는다(task-901 DoD).
        const mapped = getApiErrorMessage(err.errorCode, err.message);
        if (!acknowledged && mapped.includes("위험등급")) {
          setRiskWarningReason(mapped);
          return;
        }
        setError(err);
      } else {
        setError(new Error("구매에 실패했습니다."));
      }
    }
  }

  return (
    <AppShell>
      <div className="max-w-2xl space-y-6">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-semibold text-fg">
              {listing?.strategyId ?? `리스팅 #${id}`}
            </h1>
            {listing?.sellerType === "PLATFORM" && <Badge tone="accent">플랫폼</Badge>}
          </div>
          {listing && (
            <p className="tabular mt-1 text-sm text-fg-muted">
              v{listing.strategyVersion} · {listing.price ? `${listing.price} 크레딧` : "무료"}
            </p>
          )}
        </div>

        {purchased ? (
          <Alert tone="success">
            구매가 완료됐습니다 (구매ID {purchased.purchaseId}, 상태: {purchased.status}) — 지갑
            잔액에서 즉시 결제되어 실행 권한이 바로 부여됩니다.
          </Alert>
        ) : (
          <div className="space-y-2">
            {error !== null && <PurchaseError error={error} />}
            <Button type="button" onClick={() => attemptPurchase(false)} loading={purchase.isPending}>
              구매하기
            </Button>
          </div>
        )}

        <div className="flex gap-4 text-sm">
          <button
            type="button"
            onClick={() => submitForVerification.mutate(id)}
            className="text-fg-muted hover:text-fg"
          >
            검수 제출 (판매자)
          </button>
          <Link to="/disputes/submit" className="text-fg-muted hover:text-fg">
            분쟁 신고
          </Link>
        </div>

        <Card>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-fg">리뷰</h2>
            {purchased && (
              <Link
                to={`/reviews/write/${purchased.purchaseId}`}
                className="text-sm text-accent-hover hover:underline"
              >
                리뷰 작성
              </Link>
            )}
          </div>
          {reviewsError ? (
            isResourceNotFound(reviewsError) ? (
              <NotFoundState
                title="리스팅을 찾을 수 없습니다"
                description="삭제되었거나 존재하지 않는 리스팅입니다."
              />
            ) : (
              <ErrorMessage
                errorCode={reviewsError instanceof ApiError ? reviewsError.errorCode : undefined}
                traceId={reviewsError instanceof ApiError ? reviewsError.traceId : null}
              />
            )
          ) : reviews && reviews.reviewCount > 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-fg-muted">
                평균 {reviews.averageRating?.toFixed(1)} · {reviews.reviewCount}개 리뷰
              </p>
              <ul className="space-y-2">
                {reviews.reviews.map((r) => (
                  <li key={r.reviewId} className="rounded-md border border-border p-3 text-sm">
                    <p className="text-fg">★ {r.rating}</p>
                    {r.comment && <p className="mt-1 text-fg-muted">{r.comment}</p>}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <EmptyState>아직 리뷰가 없습니다.</EmptyState>
          )}
        </Card>
      </div>

      {riskWarningReason && (
        <RiskWarningModal
          reason={riskWarningReason}
          isPending={purchase.isPending}
          onConsent={() => attemptPurchase(true)}
          onCancel={() => setRiskWarningReason(null)}
        />
      )}
    </AppShell>
  );
}
