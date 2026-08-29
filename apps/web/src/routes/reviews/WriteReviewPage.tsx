import { useCreateReview } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

export function WriteReviewPage() {
  const { purchaseId } = useParams<{ purchaseId: string }>();
  const [searchParams] = useSearchParams();
  const listingId = Number(searchParams.get("listingId") ?? 0);
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const createReview = useCreateReview();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!listingId) {
      setError("리스팅 정보가 없습니다 — 마켓플레이스 상세 화면에서 다시 시도해주세요.");
      return;
    }
    try {
      await createReview.mutateAsync({ listingId, body: { rating, comment: comment || undefined } });
      navigate(`/marketplace/${listingId}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "리뷰 작성에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">리뷰 작성 (구매 #{purchaseId})</h1>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <label className="text-sm text-slate-400">평점: {rating}</label>
            <input
              type="range"
              min={1}
              max={5}
              value={rating}
              onChange={(e) => setRating(Number(e.target.value))}
              className="w-full"
            />
          </div>
          <div className="space-y-1">
            <label className="text-sm text-slate-400">코멘트 (선택)</label>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={4}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={createReview.isPending}
            className="w-full rounded bg-slate-100 px-3 py-2 font-medium text-slate-950 hover:bg-white disabled:opacity-50"
          >
            {createReview.isPending ? "제출 중..." : "리뷰 제출"}
          </button>
        </form>
      </div>
    </AppShell>
  );
}
