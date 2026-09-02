import { useCreateReview } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, PageHeader, Textarea } from "@aios/ui-web";
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
        <PageHeader title={`리뷰 작성 (구매 #${purchaseId})`} />
        <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-border bg-surface p-6">
          <Field label={`평점 — ${rating}`}>
            <input
              type="range"
              min={1}
              max={5}
              value={rating}
              onChange={(e) => setRating(Number(e.target.value))}
              className="w-full accent-accent"
            />
          </Field>
          <Field label="코멘트 (선택)">
            <Textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={4} />
          </Field>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" loading={createReview.isPending} className="w-full">
            리뷰 제출
          </Button>
        </form>
      </div>
    </AppShell>
  );
}
