import { inDesktopShell, openFolder, startWorker, stopWorker, workerState } from "./desktop";

describe("desktop shell bridge", () => {
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  });

  it("is absent in a browser", async () => {
    expect(inDesktopShell()).toBe(false);
    await expect(openFolder("C:/x")).rejects.toThrow("desktop app");
  });

  it("calls open_folder in the shell", async () => {
    const calls: unknown[] = [];
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
      invoke: async (cmd: string, args: unknown) => {
        calls.push([cmd, args]);
      },
    };
    expect(inDesktopShell()).toBe(true);
    await openFolder("D:/STL/a.zip");
    expect(calls).toEqual([["open_folder", { path: "D:/STL/a.zip" }]]);
  });

  it("runs the compute worker through the shell", async () => {
    const calls: unknown[] = [];
    const state = { running: true, other_server: null, data_dir: "C:/lib", stopped: null };
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
      invoke: async (cmd: string, args: unknown) => {
        calls.push([cmd, args]);
        return state;
      },
    };
    expect(await startWorker("ab12")).toEqual(state);
    await workerState();
    await stopWorker();
    expect(calls).toEqual([["start_worker", { token: "ab12" }], ["worker_state", undefined], ["stop_worker", undefined]]);
  });

  it("has no worker in a browser", async () => {
    await expect(workerState()).rejects.toThrow("desktop app");
  });
});
