import { Alert } from "@aios/ui-web";
import { useOnlineStatus } from "../hooks/useOnlineStatus";
import { useTranslation } from "react-i18next";

// UX-16: 오프라인 셸 — 앱 루트 1곳에 마운트해(MfaStepUpDialog/RateLimitNotice와
// 동일한 배선 관용) 어느 화면이든 오프라인이 되면 이 배너 하나가 뜬다.
export function OfflineBanner() {
  const { t } = useTranslation();
  const online = useOnlineStatus();
  if (online) return null;

  return (
    <div className="fixed inset-x-0 top-0 z-40 flex justify-center px-4 py-2">
      <div className="w-full max-w-xl">
        <Alert tone="warning">
          <p>{t("legacy.offlineBanner.t1")}</p>
        </Alert>
      </div>
    </div>
  );
}
