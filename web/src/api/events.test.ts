import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { coalesced } from "./events";

describe("coalesced", () => {
  beforeEach(() => void vi.useFakeTimers());
  afterEach(() => void vi.useRealTimers());

  it("runs once for a burst, and again for the next one", () => {
    const run = vi.fn();
    const reload = coalesced(run, 300);
    reload.call();
    vi.advanceTimersByTime(200);
    reload.call();
    reload.call();
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(100);
    expect(run).toHaveBeenCalledTimes(1);
    reload.call();
    vi.advanceTimersByTime(300);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("does nothing once cancelled", () => {
    const run = vi.fn();
    const reload = coalesced(run, 300);
    reload.call();
    reload.cancel();
    vi.advanceTimersByTime(300);
    expect(run).not.toHaveBeenCalled();
  });
});
