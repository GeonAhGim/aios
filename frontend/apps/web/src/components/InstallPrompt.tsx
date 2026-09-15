import { Button } from "@aios/ui-web";
import { useInstallPrompt } from "../hooks/useInstallPrompt";

// UX-16: 앱 루트 1곳에 마운트한다 — beforeinstallprompt는 브라우저가 페이지당
// 한 번만 쏘는 전역 이벤트라, 화면마다 개별로 구독하면 소비 경쟁이 생긴다.
export function InstallPrompt() {
  const { canInstall, promptInstall } = useInstallPrompt();
  if (!canInstall) return null;

  return (
    <div className="fixed inset-x-0 bottom-0 z-40 flex justify-center px-4 py-3">
      <div className="flex w-full max-w-xl items-center justify-between gap-3 rounded-md border border-border bg-surface px-4 py-3 text-sm shadow-lg">
        <p>AIOS를 앱으로 설치하면 더 빠르게 열 수 있습니다.</p>
        <Button size="sm" onClick={() => void promptInstall()}>
          설치
        </Button>
      </div>
    </div>
  );
}
