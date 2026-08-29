import { useListingReviews, usePurchaseListing, useSubmitForVerification } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import type { ListingSummary } from "@aios/shared-types";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { RiskWarningModal } from "../../components/RiskWarningModal";

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

  if (!listingId) return null;
  const id = Number(listingId);

  async function attemptPurchase(acknowledged: boolean) {
    setError(null);
    try {
      const result = await purchase.mutateAsync({
        listingId: id,
        body: { riskWarningAcknowledged: acknowledged },
        idempotencyKey: crypto.randomUUID(),
      });
      setPurchased({ purchaseId: result.purchaseId, status: result.status });
      setRiskWarningReason(null);
    } catch (err) {
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
          <h1 className="text-2xl font-semibold text-slate-100">
            {listing?.strategyId ?? `리스팅 #${id}`}
          </h1>
          {listing && (
            <p className="mt-1 text-sm text-slate-500">
              v{listing.strategyVersion} · {listing.price ? `${listing.price} USDT` : "가격 미정"}
            </p>
          )}
        </div>

        {purchased ? (
          <div className="rounded border border-emerald-900 bg-emerald-950/30 p-4 text-sm text-emerald-300">
            구매가 완료됐습니다 (구매ID {purchased.purchaseId}, 상태: {purchased.status}).
            관리자의 결제 확인 후 실행 권한이 부여됩니다.
          </div>
        ) : (
          <div className="space-y-2">
            {error && <p className="text-sm text-red-400">{error}</p>}
            <button
              type="button"
              onClick={() => attemptPurchase(false)}
              disabled={purchase.isPending}
              className="rounded bg-slate-100 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-white disabled:opacity-50"
            >
              {purchase.isPending ? "처리 중..." : "구매하기"}
            </button>
          </div>
        )}

        <div className="flex gap-3 text-sm">
          <button
            type="button"
            onClick={() => submitForVerification.mutate(id)}
            className="text-slate-400 hover:text-slate-200"
          >
            검수 제출 (판매자)
          </button>
          <Link to="/disputes/submit" className="text-slate-400 hover:text-slate-200">
            분쟁 신고
          </Link>
        </div>

        <section className="rounded-lg border border-slate-800 p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-medium text-slate-100">리뷰</h2>
            {purchased && (
              <Link
                to={`/reviews/write/${purchased.purchaseId}`}
                className="text-sm text-slate-400 hover:text-slate-200"
              >
                리뷰 작성
              </Link>
            )}
          </div>
          {reviews && reviews.reviewCount > 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-slate-400">
                평균 {reviews.averageRating?.toFixed(1)} · {reviews.reviewCount}개 리뷰
              </p>
              <ul className="space-y-2">
                {reviews.reviews.map((r) => (
                  <li key={r.reviewId} className="rounded border border-slate-800 p-3 text-sm">
                    <p className="text-slate-200">★ {r.rating}</p>
                    {r.comment && <p className="mt-1 text-slate-400">{r.comment}</p>}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-slate-500">아직 리뷰가 없습니다.</p>
          )}
        </section>
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
