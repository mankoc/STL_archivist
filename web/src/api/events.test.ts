import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { coalesced, followEvents, LIBRARY_KEYS, staleKeys } from "./events";

/** Enough of a WebSocket for `followEvents`: the test opens, sends and drops it. */
class FakeSocket {
  static made: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeSocket.made.push(this);
  }
  close() {
    this.closed = true;
  }
}

describe("followEvents", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.made = [];
  });
  afterEach(() => void vi.useRealTimers());

  const follow = () => {
    const on = { event: vi.fn(), reconnected: vi.fn() };
    const stop = followEvents("ws://x/api/events", on, (url) => new FakeSocket(url) as unknown as WebSocket);
    return { on, stop };
  };

  it("passes events on, and reports a reconnection but not the first connection", () => {
    const { on } = follow();
    const first = FakeSocket.made[0];
    first.onopen?.();
    expect(on.reconnected).not.toHaveBeenCalled();
    first.onmessage?.({ data: JSON.stringify({ type: "jobs_cleared" }) });
    first.onmessage?.({ data: "not JSON" });
    expect(on.event).toHaveBeenCalledTimes(1);
    expect(on.event).toHaveBeenCalledWith({ type: "jobs_cleared" });

    first.onclose?.();
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.made).toHaveLength(2);
    FakeSocket.made[1].onopen?.();
    expect(on.reconnected).toHaveBeenCalledTimes(1);
  });

  it("waits longer after each failed attempt, and stops for good when told", () => {
    const { stop } = follow();
    FakeSocket.made[0].onclose?.();
    vi.advanceTimersByTime(1000);
    FakeSocket.made[1].onclose?.();
    vi.advanceTimersByTime(1999);
    expect(FakeSocket.made).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.made).toHaveLength(3);
    stop();
    expect(FakeSocket.made[2].closed).toBe(true);
    FakeSocket.made[2].onclose?.();
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.made).toHaveLength(3);
  });
});

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
