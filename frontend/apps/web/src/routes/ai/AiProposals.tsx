import type { AiClient } from "@aios/api-client";
import type { StrategyProposalView } from "@aios/shared-types";
import { Badge, Button, Card, CardTitle, EmptyState, LoadingState } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AiErrorBanner } from "./AiErrorBanner";
import { OUTCOME_TONE } from "./aiShared";

// task-2657(AI-22): AiStudioPage.tsx "제안 목록" 섹션 — PASS인 제안만 PAPER 승격
// 버튼을 보여주고, 클릭하면 AI-4 domain/confirm.py의 ConfirmTicket 왕복(미리보기
// digest 표시 → 확인 클릭 시에만 실행)을 그대로 화면에 옮긴다.
function PromoteConfirmPanel({
  proposalId,
  client,
  onDone,
  onCancel,
}: {
  proposalId: string;
  client: AiClient;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [ticket, setTicket] = useState<{ ticketId: string; actionDigest: string; expiresAt: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    client
      .requestPromoteTicket(proposalId)
      .then((result) => {
        if (!cancelled) setTicket(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [proposalId, client]);

  async function handleConfirm() {
    if (!ticket) return;
    setPending(true);
    setError(null);
    try {
      await client.confirmPromote(proposalId, ticket.ticketId);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      data-testid={`ai-promote-confirm-${proposalId}`}
      className="mt-3 space-y-2 rounded-md border border-warning/30 bg-surface p-3"
    >
      <p className="text-sm font-medium text-fg">{t("ai.promote.confirmTitle")}</p>
      {error !== null && <AiErrorBanner error={error} />}
      {ticket && (
        <>
          <p className="text-xs text-fg-muted">
            {t("ai.promote.digestLabel")} <span className="font-mono text-fg">{ticket.actionDigest}</span>
          </p>
          <p className="text-xs text-fg-muted">
            {t("ai.promote.expiresPrefix")} {new Date(ticket.expiresAt).toLocaleString()}
          </p>
        </>
      )}
      <div className="flex gap-2">
        <Button type="button" variant="secondary" size="sm" onClick={onCancel} disabled={pending}>
          {t("common.cancel")}
        </Button>
        <Button type="button" size="sm" loading={pending} disabled={!ticket} onClick={handleConfirm}>
          {t("common.confirm")}
        </Button>
      </div>
    </div>
  );
}

function ProposalRow({
  proposal,
  client,
  promoting,
  onStartPromote,
  onCancelPromote,
  onPromoted,
}: {
  proposal: StrategyProposalView;
  client: AiClient;
  promoting: boolean;
  onStartPromote: () => void;
  onCancelPromote: () => void;
  onPromoted: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Card data-testid={`ai-proposal-${proposal.proposalId}`}>
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <CardTitle>{proposal.proposalId}</CardTitle>
            <Badge tone={OUTCOME_TONE[proposal.outcome]}>{proposal.outcome}</Badge>
          </div>
          <p className="text-sm text-fg-muted">{proposal.hypothesis}</p>
          <p className="text-xs text-fg-muted">
            {proposal.dataScopeInstruments.join(", ")} · {t("ai.proposals.providerPrefix")} {proposal.providerRef}
          </p>
        </div>
        {proposal.outcome === "PASS" && !promoting && (
          <Button type="button" size="sm" onClick={onStartPromote}>
            {t("ai.proposals.promote")}
          </Button>
        )}
      </div>
      {promoting && (
        <PromoteConfirmPanel
          proposalId={proposal.proposalId}
          client={client}
          onDone={onPromoted}
          onCancel={onCancelPromote}
        />
      )}
    </Card>
  );
}

export function ProposalsSection({ client, onPromoted }: { client: AiClient; onPromoted: () => void }) {
  const { t } = useTranslation();
  const query = useQuery({ queryKey: ["ai-proposals"], queryFn: () => client.listProposals() });
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const items = query.data ?? [];

  if (query.isError) return <AiErrorBanner error={query.error} onRetry={() => query.refetch()} />;
  if (query.isLoading) return <LoadingState />;
  if (items.length === 0) return <EmptyState>{t("ai.proposals.empty")}</EmptyState>;

  return (
    <div className="space-y-2">
      {items.map((proposal) => (
        <ProposalRow
          key={proposal.proposalId}
          proposal={proposal}
          client={client}
          promoting={promotingId === proposal.proposalId}
          onStartPromote={() => setPromotingId(proposal.proposalId)}
          onCancelPromote={() => setPromotingId(null)}
          onPromoted={() => {
            setPromotingId(null);
            query.refetch();
            onPromoted();
          }}
        />
      ))}
    </div>
  );
}
