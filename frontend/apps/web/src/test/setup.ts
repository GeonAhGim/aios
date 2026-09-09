import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// task-2479: vitest.config.ts's testTimeout/hookTimeout (task-1968) cover the *test body*,
// but @testing-library/dom's waitFor/findBy* run their own internal poll loop with a
// default 1000ms budget regardless of that — so a shared/contended CI host still flakes
// individual assertions (reproduced on SessionsPage, ChartPage, PayoutsPage — the same
// "unrelated files, same signal" pattern task-1968 already documented) even though the
// surrounding test has 20s to spare. Raise that budget too so host load doesn't flake
// whichever assertion happens to poll during a contended moment.
configure({ asyncUtilTimeout: 10000 });

afterEach(() => {
  cleanup();
});
