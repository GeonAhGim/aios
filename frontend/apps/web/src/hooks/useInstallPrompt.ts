import { useCallback, useEffect, useState } from "react";

// lib.dom.d.ts에는 없는 표준 초안 이벤트 — beforeinstallprompt/appinstalled.
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

export interface UseInstallPromptResult {
  canInstall: boolean;
  installed: boolean;
  promptInstall: () => Promise<"accepted" | "dismissed" | null>;
}

// UX-16: 설치 가능 여부는 브라우저의 beforeinstallprompt 이벤트가 있어야만 알 수
// 있다 — 이벤트가 오기 전까지는 canInstall이 항상 false다(스스로 판단해 배너를
// 억지로 띄우지 않는다, fail-closed와 동형).
export function useInstallPrompt(): UseInstallPromptResult {
  const [deferredEvent, setDeferredEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    const handleBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      setDeferredEvent(event as BeforeInstallPromptEvent);
    };
    const handleAppInstalled = () => {
      setInstalled(true);
      setDeferredEvent(null);
    };
    window.addEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
    window.addEventListener("appinstalled", handleAppInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
      window.removeEventListener("appinstalled", handleAppInstalled);
    };
  }, []);

  const promptInstall = useCallback(async () => {
    if (!deferredEvent) return null;
    await deferredEvent.prompt();
    const choice = await deferredEvent.userChoice;
    setDeferredEvent(null);
    return choice.outcome;
  }, [deferredEvent]);

  return {
    canInstall: deferredEvent !== null && !installed,
    installed,
    promptInstall,
  };
}
