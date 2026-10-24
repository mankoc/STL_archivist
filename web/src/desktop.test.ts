import { changeWorkerComponents, inDesktopShell, clearMeshPeek, meshpeekState, openFolder, openInMeshPeek, pickMeshPeek, pickWorkerFolder, startWorker, stopWorker, workerState } from "./desktop";

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

  it("opens archives in MeshPeek through the shell", async () => {
    expect(await meshpeekState()).toBe(null);
    await expect(openInMeshPeek("C:/x.zip")).rejects.toThrow("desktop app");
    const calls: unknown[] = [];
    const state = { chosen: "D:/Tools/meshpeek.exe", program: "D:/Tools/meshpeek.exe" };
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
      invoke: async (cmd: string, args: unknown) => {
        calls.push([cmd, args]);
        return state;
      },
    };
    expect(await meshpeekState()).toEqual(state);
    expect(await pickMeshPeek()).toEqual(state);
    await clearMeshPeek();
    await openInMeshPeek("D:/STL/a.zip", "parts/b.stl");
    await openInMeshPeek("D:/STL/a.zip");
    expect(calls).toEqual([
      ["meshpeek_state", undefined],
      ["pick_meshpeek", undefined],
      ["clear_meshpeek", undefined],
      ["open_in_meshpeek", { path: "D:/STL/a.zip", entry: "parts/b.stl" }],
      ["open_in_meshpeek", { path: "D:/STL/a.zip", entry: null }],
    ]);
  });

  it("finds no MeshPeek in a shell without the command", async () => {
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
      invoke: async () => {
        throw new Error("unknown command");
      },
    };
    expect(await meshpeekState()).toBe(null);
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

  it("manages this computer's components through the shell", async () => {
    const calls: unknown[] = [];
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
      invoke: async (cmd: string, args: unknown) => {
        calls.push([cmd, args]);
        return null;
      },
    };
    await changeWorkerComponents("install", ["onnxruntime"], { onnxruntime: "directml" });
    await pickWorkerFolder();
    expect(calls).toEqual([
      ["change_worker_components", { action: "install", components: ["onnxruntime"], variants: { onnxruntime: "directml" } }],
      ["pick_worker_folder", undefined],
    ]);
  });

  it("has no worker in a browser", async () => {
    await expect(workerState()).rejects.toThrow("desktop app");
  });
});
