import { useListingReviews, usePurchaseListing, useSubmitForVerification } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import type { ListingSummary } from "@aios/shared-types";
import { Alert, Badge, Button, Card, EmptyState } from "@aios/ui-web";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { RiskWarningModal } from "../../components/RiskWarningModal";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";

export function ListingDetailPage() {
  const { listingId } = useParams<{ listingId: string }>();
  const location = useLocation();
  const listing = (location.state as { listing?: ListingSummary } | null)?.listing;
  const { data: reviews } = useListingReviews(listingId ? Number(listingId) : null);
  const purchase = usePurchaseListing();
  const submitForVerification = useSubmitForVerification();
  const [error, setError] = useState<string | null>(null);
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
        if (!acknowledged && err.message.includes("위험등급")) {
          setRiskWarningReason(err.message);
          return;
        }
        setError(err.message);
      } else {
        setError("구매에 실패했습니다.");
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
            {error && (
              <Alert>
                {error}
                {error.includes("잔액") && (
                  <>
                    {" "}
                    <Link to="/wallet" className="underline">
                      지갑 충전하기
                    </Link>
                  </>
                )}
              </Alert>
            )}
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
          {reviews && reviews.reviewCount > 0 ? (
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
