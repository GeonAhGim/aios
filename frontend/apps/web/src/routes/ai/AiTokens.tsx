import type { AiClient } from "@aios/api-client";
import type { AgentScope, AgentTokenView } from "@aios/shared-types";
import { Badge, Button, Card, CardTitle, EmptyState, Field, Input, LoadingState } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AiErrorBanner } from "./AiErrorBanner";
import { AGENT_SCOPES, SCOPE_LABEL_KEY } from "./aiShared";

// task-2657(AI-22): AiStudioPage.tsx "에이전트 토큰" 섹션 — 발급 폼(§2.1
// contracts/v1.py AgentToken 스코프·명목가 상한·만료)과 목록·폐기.
function IssueTokenForm({ client, onIssued }: { client: AiClient; onIssued: () => void }) {
  const { t } = useTranslation();
  const [scopes, setScopes] = useState<AgentScope[]>(["read"]);
  const [instruments, setInstruments] = useState("");
  const [notionalCap, setNotionalCap] = useState("0");
  const [expiresAt, setExpiresAt] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [issuing, setIssuing] = useState(false);

  function toggleScope(scope: AgentScope) {
    setScopes((prev) => (prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]));
  }

  async function handleIssue() {
    setError(null);
    setIssuing(true);
    try {
      await client.issueAgentToken({
        scopes,
        allowInstruments: instruments
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        notionalCap,
        expiresAt,
      });
      onIssued();
    } catch (err) {
      setError(err);
    } finally {
      setIssuing(false);
    }
  }

  return (
    <Card data-testid="ai-issue-token-form">
      <CardTitle>{t("ai.tokens.issueTitle")}</CardTitle>
      <div className="mt-3 space-y-3">
        <div className="flex flex-wrap gap-3">
          {AGENT_SCOPES.map((scope) => (
            <label key={scope} className="flex items-center gap-2 text-sm text-fg">
              <input
                type="checkbox"
                checked={scopes.includes(scope)}
                onChange={() => toggleScope(scope)}
                className="accent-accent"
              />
              {t(SCOPE_LABEL_KEY[scope])}
            </label>
          ))}
        </div>
        <Field label={t("ai.tokens.instrumentsLabel")}>
          <Input value={instruments} onChange={(e) => setInstruments(e.target.value)} placeholder="BTC-USDT, ETH-USDT" />
        </Field>
        <Field label={t("ai.tokens.notionalCapLabel")}>
          <Input value={notionalCap} onChange={(e) => setNotionalCap(e.target.value)} className="w-40" />
        </Field>
        <Field label={t("ai.tokens.expiresAtLabel")}>
          <Input
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
            placeholder="2026-12-31T00:00:00Z"
          />
        </Field>
        {error !== null && <AiErrorBanner error={error} />}
        <Button type="button" loading={issuing} disabled={scopes.length === 0} onClick={handleIssue}>
          {t("ai.tokens.issue")}
        </Button>
      </div>
    </Card>
  );
}

function TokenRow({
  token,
  client,
  onChanged,
}: {
  token: AgentTokenView;
  client: AiClient;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function handleRevoke() {
    setError(null);
    setRevoking(true);
    try {
      await client.revokeAgentToken(token.tokenId);
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setRevoking(false);
    }
  }

  return (
    <Card data-testid={`ai-token-${token.tokenId}`}>
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm text-fg">{token.tokenId}</span>
            <Badge tone="accent">PAPER</Badge>
            {token.revoked && <Badge tone="danger">REVOKED</Badge>}
          </div>
          <p className="text-xs text-fg-muted">
            {token.scopes.map((s) => t(SCOPE_LABEL_KEY[s])).join(" · ")} · {t("ai.tokens.expiresPrefix")}{" "}
            {new Date(token.expiresAt).toLocaleString()}
          </p>
        </div>
        {!token.revoked && (
          <Button type="button" variant="danger" size="sm" loading={revoking} onClick={handleRevoke}>
            {t("ai.tokens.revoke")}
          </Button>
        )}
      </div>
      {error !== null && <AiErrorBanner error={error} />}
    </Card>
  );
}

export function TokensSection({ client }: { client: AiClient }) {
  const { t } = useTranslation();
  const query = useQuery({ queryKey: ["ai-tokens"], queryFn: () => client.listAgentTokens() });
  const items = query.data ?? [];

  return (
    <div className="space-y-4">
      <IssueTokenForm client={client} onIssued={() => query.refetch()} />
      {query.isError ? (
        <AiErrorBanner error={query.error} onRetry={() => query.refetch()} />
      ) : query.isLoading ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState>{t("ai.tokens.empty")}</EmptyState>
      ) : (
        <div className="space-y-2">
          {items.map((token) => (
            <TokenRow key={token.tokenId} token={token} client={client} onChanged={() => query.refetch()} />
          ))}
        </div>
      )}
    </div>
  );
}
