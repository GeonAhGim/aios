import { vi } from "vitest";

export const ECHO_TASK = "test/echo";

export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Minimal `Worker`-shaped double: postMessage records the message, tests drive replies via onmessage/onerror. */
export class FakeWorker {
  posted: unknown[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  terminate = vi.fn();

  postMessage(message: unknown): void {
    this.posted.push(message);
  }
}

/** A `FakeWorker` whose `postMessage` throws once `terminate()` has been called,
 * mirroring a real browser `Worker`'s `DOMException: "... worker has been terminated"`. */
export class TerminatingFakeWorker extends FakeWorker {
  #terminated = false;

  constructor() {
    super();
    this.terminate = vi.fn(() => {
      this.#terminated = true;
    });
  }

  override postMessage(message: unknown): void {
    if (this.#terminated) {
      throw new DOMException("Worker has been terminated", "InvalidStateError");
    }
    super.postMessage(message);
  }
}
