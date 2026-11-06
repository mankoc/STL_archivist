import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { coalesced, LIBRARY_KEYS, staleKeys } from "./events";

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

describe("staleKeys", () => {
  it("leaves the filter pane alone when only rows changed", () => {
    const keys = staleKeys(new Set(["rows"]));
    expect(keys).toContain("rows");
    expect(keys).not.toContain("tree");
    expect(keys).not.toContain("libraries");
  });

  it("reloads everything for anything else, even among rows", () => {
    expect(staleKeys(new Set(["rows", "models"]))).toBe(LIBRARY_KEYS);
    expect(staleKeys(new Set(["crawl"]))).toBe(LIBRARY_KEYS);
    expect(staleKeys(new Set())).toBe(LIBRARY_KEYS);
  });
});
