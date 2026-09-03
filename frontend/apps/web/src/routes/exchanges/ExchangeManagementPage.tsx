import {
  useExchangeBalance,
  useExchangeCredentials,
  useRegisterExchangeCredential,
  useRevokeExchangeCredential,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  classifyServerError,
  getApiErrorMessage,
  parseSecretRef,
  redactSecret,
  routeApiError,
} from "@aios/shared-types";
import {
  Badge,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  Select,
} from "@aios/ui-web";
import { useRef, useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useConflictRetry } from "../../hooks/useConflictRetry";
import { useFieldErrors } from "../../hooks/useFieldErrors";
import { exchangeLabel } from "../../lib/exchangeLabels";

const EXCHANGES = ["bitget", "kis"];

// §3.6: 백엔드가 SecretRef 문자열을 아직 안 내려줄 수 있어 이 필드는 선택이다
// (PLT-33 이전). 목록 응답 타입(CredentialResponse)을 건드리지 않기 위해 여기서만
// 확장한다 — 값이 없거나 파싱 실패하면 scope를 추측하지 않고 "알 수 없음"으로 둔다.
interface CredentialWithSecretRef {
  secretRef?: string;
}

const LIVE_BLOCKED_NOTICE = getApiErrorMessage("POLICY_LIVE_BLOCKED");

function credentialScope(secretRef: string | undefined) {
  const parsed = secretRef ? parseSecretRef(secretRef) : null;
  if (!parsed) return { label: "알 수 없음", tone: "neutral" as const, isLive: false };
  return parsed.scope === "live"
    ? { label: "LIVE", tone: "danger" as const, isLive: true }
    : { label: "PAPER", tone: "success" as const, isLive: false };
}

// spec §3.3 에러 taxonomy: err.message를 직접 노출하지 않고 routeApiError(task-483)
// 경유 BadRequestNotice/ForbiddenNotice/ErrorMessage로만 보여준다(task-901/910 패턴).
// task-473 §3.6: 등록 폼은 서버가 에러 메시지에 apiKey/apiSecret 원문을 반향할 수
// 있어(예: "invalid field api_secret=...") ErrorMessage의 fallbackMessage 경로에는
// redactSecret을 통과시킨다. BadRequestNotice/ForbiddenNotice는 알려진 error_code일
// 때만 고정 한국어 문구(EXACT_MESSAGES)를 쓰므로 비밀 반향 위험이 없다 — "unknown"
// (미지 코드 또는 코드 없음)만 계속 redactSecret 경로로 보낸다.
// classifyServerError(task-937)가 재시도 가능(5xx)으로 판정할 때만 onRetry를 넘긴다.
//
// task-943: VALIDATION_INVALID_FIELD는 classifyBadRequest가 "field"로 분류해
// badRequestKind !== "unknown"에 걸려 BadRequestNotice로 가는데, 그 컴포넌트는
// field 갈래에서 null을 렌더한다(task-364 설계) — 지금까지 이 경로는 완전히
// 조용했다. fieldErrors를 ErrorMessage에 넘겨 계약을 지키고, 실제 표시는 아래
// 입력 옆 Field.error로 한다.
function RegisterCredentialError({
  error,
  onRetry,
  fieldErrors,
}: {
  error: unknown;
  onRetry: () => void;
  fieldErrors: Record<string, string>;
}) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const badRequestKind = classifyBadRequest(error);
  if (badRequestKind && badRequestKind !== "unknown") return <BadRequestNotice error={error} />;
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : null}
      message={error instanceof Error ? redactSecret(error.message) : null}
      traceId={error instanceof ApiError ? error.traceId : null}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={serverError.kind === "retryable" ? onRetry : undefined}
      fieldErrors={fieldErrors}
    />
  );
}

function RevokeCredentialError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const badRequestKind = classifyBadRequest(error);
  if (badRequestKind && badRequestKind !== "unknown") return <BadRequestNotice error={error} />;
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : null}
      message={error instanceof Error ? redactSecret(error.message) : null}
      traceId={error instanceof ApiError ? error.traceId : null}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={serverError.kind === "retryable" ? onRetry : undefined}
    />
  );
}

export function ExchangeManagementPage() {
  const { data: credentials, isLoading, refetch } = useExchangeCredentials();
  const register = useRegisterExchangeCredential();
  const revoke = useRevokeExchangeCredential();
  const [exchange, setExchange] = useState("bitget");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [apiPassphrase, setApiPassphrase] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [revokeError, setRevokeError] = useState<unknown>(null);
  const [selectedExchange, setSelectedExchange] = useState<string | null>(null);
  const { data: balances } = useExchangeBalance(selectedExchange);
  const { fieldErrors, setFromError, clearField } = useFieldErrors();

  // §3.3 409 STATE_CONCURRENCY_CONFLICT → useConflictRetry(task-937)가 refetch 후 1회
  // 재시도한다. 해지 대상은 행마다 달라 ref로 넘긴다(mutate는 인자를 받지 않는다).
  const revokeExchangeRef = useRef("");
  const { run: revokeWithRetry } = useConflictRetry(
    () => revoke.mutateAsync(revokeExchangeRef.current),
    refetch,
  );

  function performRevoke() {
    revokeWithRetry().catch((err: unknown) => {
      setRevokeError(err instanceof ApiError ? err : new Error("해지에 실패했습니다."));
    });
  }

  function handleRevoke(exchangeToRevoke: string) {
    setRevokeError(null);
    revokeExchangeRef.current = exchangeToRevoke;
    performRevoke();
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

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="거래소 연동" />

        <Card>
          <CardTitle>연동된 거래소</CardTitle>
          {isLoading ? (
            <LoadingState />
          ) : credentials && credentials.length > 0 ? (
            <ul className="divide-y divide-border">
              {credentials.map((c) => {
                const scope = credentialScope((c as CredentialWithSecretRef).secretRef);
                return (
                  <li key={c.id} className="flex items-center justify-between py-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="font-medium text-fg">{exchangeLabel(c.exchange)}</p>
                        <Badge tone={c.isActive ? "success" : "neutral"}>
                          {c.isActive ? "활성" : "비활성"}
                        </Badge>
                        <Badge tone={scope.tone}>{scope.label}</Badge>
                      </div>
                      <p className="text-sm text-fg-muted">
                        연동일 {new Date(c.linkedAt).toLocaleDateString()}
                      </p>
                      {c.withdrawalPermissionWarning && (
                        <p className="mt-1 text-sm text-warning">⚠ {c.withdrawalPermissionWarning}</p>
                      )}
                      {scope.isLive && <p className="mt-1 text-sm text-danger">{LIVE_BLOCKED_NOTICE}</p>}
                    </div>
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => setSelectedExchange(c.exchange)}
                      >
                        잔고 조회
                      </Button>
                      <Button
                        type="button"
                        variant="danger"
                        size="sm"
                        disabled={scope.isLive}
                        onClick={() => handleRevoke(c.exchange)}
                      >
                        해지
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState>연동된 거래소가 없습니다.</EmptyState>
          )}

          {revokeError !== null && (
            <div className="mt-4">
              <RevokeCredentialError
                error={revokeError}
                onRetry={() => {
                  setRevokeError(null);
                  performRevoke();
                }}
              />
            </div>
          )}

          {selectedExchange && balances && (
            <div className="mt-4 rounded-md border border-border bg-surface-hover p-4">
              <p className="mb-2 text-sm text-fg-secondary">{exchangeLabel(selectedExchange)} 잔고</p>
              {balances.length > 0 ? (
                <ul className="tabular space-y-1 text-sm text-fg">
                  {balances.map((b) => (
                    <li key={b.asset}>
                      {b.asset}: {b.available} / {b.total}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-fg-muted">잔고 정보가 없습니다.</p>
              )}
            </div>
          )}
        </Card>

        <Card className="max-w-lg">
          <CardTitle>새 거래소 연동</CardTitle>
          <form onSubmit={handleSubmit} className="space-y-3">
            <Field label="거래소" error={fieldErrors.exchange}>
              <Select
                value={exchange}
                onChange={(e) => {
                  setExchange(e.target.value);
                  clearField("exchange");
                }}
              >
                {EXCHANGES.map((ex) => (
                  <option key={ex} value={ex}>
                    {exchangeLabel(ex)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="API Key" error={fieldErrors.api_key}>
              <Input
                type="password"
                required
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  clearField("api_key");
                }}
              />
            </Field>
            <Field label="API Secret" error={fieldErrors.api_secret}>
              <Input
                type="password"
                required
                value={apiSecret}
                onChange={(e) => {
                  setApiSecret(e.target.value);
                  clearField("api_secret");
                }}
              />
            </Field>
            {exchange === "bitget" && (
              <Field label="API Passphrase" error={fieldErrors.api_passphrase}>
                <Input
                  type="password"
                  required
                  value={apiPassphrase}
                  onChange={(e) => {
                    setApiPassphrase(e.target.value);
                    clearField("api_passphrase");
                  }}
                />
              </Field>
            )}
            {error !== null && (
              <RegisterCredentialError
                error={error}
                onRetry={() => void submitRegistration()}
                fieldErrors={fieldErrors}
              />
            )}
            <Button type="submit" loading={register.isPending} className="w-full">
              등록
            </Button>
          </form>
        </Card>
      </div>
    </AppShell>
  );
}
