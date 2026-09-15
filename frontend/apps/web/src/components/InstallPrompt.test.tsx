import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InstallPrompt } from "./InstallPrompt";

afterEach(() => {
  cleanup();
});

function fireBeforeInstallPrompt() {
  const event = new Event("beforeinstallprompt", { cancelable: true }) as Event & {
    prompt: () => Promise<void>;
    userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
  };
  event.prompt = vi.fn(async () => undefined);
  event.userChoice = Promise.resolve({ outcome: "accepted" as const, platform: "web" });
  act(() => {
    window.dispatchEvent(event);
  });
  return event;
}

describe("InstallPrompt", () => {
  it("설치 가능한 상태가 아니면 아무것도 렌더링하지 않는다", () => {
    const { container } = render(<InstallPrompt />);
    expect(container).toBeEmptyDOMElement();
  });

  it("negative: beforeinstallprompt 이후 설치 버튼을 누르면 prompt()가 호출되고 배너가 사라진다", async () => {
    render(<InstallPrompt />);
    const event = fireBeforeInstallPrompt();

    const button = await screen.findByRole("button", { name: "설치" });
    await act(async () => {
      fireEvent.click(button);
      await Promise.resolve();
    });

    expect(event.prompt).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "설치" })).not.toBeInTheDocument();
  });
});
