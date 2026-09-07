import {
  useExchangeBalance,
  useExchangeCredentials,
  useRegisterExchangeCredential,
  useRevokeExchangeCredential,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { PageHeader } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { useConflictRetry } from "../../hooks/useConflictRetry";
import { useFieldErrors } from "../../hooks/useFieldErrors";
import { CredentialListCard } from "./CredentialListCard";
import { RegisterCredentialForm } from "./RegisterCredentialForm";

export function ExchangeManagementPage() {
  const { data: credentials, isLoading, refetch } = useExchangeCredentials();
  const register = useRegisterExchangeCredential();
  const revoke = useRevokeExchangeCredential();
  const [exchange, setExchange] = useState("bitget");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [apiPassphrase, setApiPassphrase] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [revokeError, setRevokeError] = useState<{ exchange: string; error: unknown } | null>(
    null,
  );
  const [revokingExchange, setRevokingExchange] = useState<string | null>(null);
  const [selectedExchange, setSelectedExchange] = useState<string | null>(null);
  const { data: balances } = useExchangeBalance(selectedExchange);
  const { fieldErrors, setFromError, clearField } = useFieldErrors();

  // §3.3 409 STATE_CONCURRENCY_CONFLICT → useConflictRetry(task-937)가 refetch 후 1회
  // 재시도한다. 해지 대상은 클릭 시점에 run(exchangeToRevoke)의 인자로 캡처해 원호출과
  // 재시도가 같은 값을 쓴다(task-1308: 공유 ref는 재시도 시점에 다른 행 클릭으로 값이
  // 바뀔 수 있어 엉뚱한 거래소를 해지할 수 있었다).
  const { run: revokeWithRetry } = useConflictRetry(
    (exchangeToRevoke: string) => revoke.mutateAsync(exchangeToRevoke),
    refetch,
  );

  function performRevoke(exchangeToRevoke: string) {
    setRevokingExchange(exchangeToRevoke);
    revokeWithRetry(exchangeToRevoke)
      .catch((err: unknown) => {
        setRevokeError({
          exchange: exchangeToRevoke,
          error: err instanceof ApiError ? err : new Error("해지에 실패했습니다."),
        });
      })
      .finally(() => setRevokingExchange(null));
  }

  function handleRevoke(exchangeToRevoke: string) {
    setRevokeError(null);
    performRevoke(exchangeToRevoke);
  }

  function handleRetryRevoke() {
    if (!revokeError) return;
    const exchangeToRevoke = revokeError.exchange;
    setRevokeError(null);
    performRevoke(exchangeToRevoke);
  }

  // 409는 위와 동일하게 useConflictRetry가 처리한다. idempotencyKey를 넘기지 않으므로
  // (postIdempotent가 매 호출마다 자동 생성) 재제출은 항상 새 Idempotency-Key로 나간다.
  const { run: registerWithRetry } = useConflictRetry(
    () =>
      register.mutateAsync({
        exchange,
        apiKey,
        apiSecret,
        apiPassphrase: exchange === "bitget" ? apiPassphrase : undefined,
      }),
    refetch,
  );

  async function submitRegistration() {
    setError(null);
    setFromError(null);
    try {
      await registerWithRetry();
      setApiKey("");
      setApiSecret("");
      setApiPassphrase("");
    } catch (err) {
      setApiSecret("");
      setApiPassphrase("");
      setError(err instanceof ApiError ? err : new Error("등록에 실패했습니다."));
      setFromError(err);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void submitRegistration();
  }

  function handleExchangeChange(value: string) {
    setExchange(value);
    clearField("exchange");
  }

  function handleApiKeyChange(value: string) {
    setApiKey(value);
    clearField("api_key");
  }

  function handleApiSecretChange(value: string) {
    setApiSecret(value);
    clearField("api_secret");
  }

  function handleApiPassphraseChange(value: string) {
    setApiPassphrase(value);
    clearField("api_passphrase");
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="거래소 연동" />

        <CredentialListCard
          credentials={credentials}
          isLoading={isLoading}
          revokingExchange={revokingExchange}
          selectedExchange={selectedExchange}
          balances={balances}
          revokeError={revokeError}
          onSelectExchange={setSelectedExchange}
          onRevoke={handleRevoke}
          onRetryRevoke={handleRetryRevoke}
        />

        <RegisterCredentialForm
          exchange={exchange}
          apiKey={apiKey}
          apiSecret={apiSecret}
          apiPassphrase={apiPassphrase}
          fieldErrors={fieldErrors}
          error={error}
          isPending={register.isPending}
          onExchangeChange={handleExchangeChange}
          onApiKeyChange={handleApiKeyChange}
          onApiSecretChange={handleApiSecretChange}
          onApiPassphraseChange={handleApiPassphraseChange}
          onSubmit={handleSubmit}
          onRetry={() => void submitRegistration()}
        />
      </div>
    </AppShell>
  );
}
