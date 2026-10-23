import { installProgress, modeText, workerSummary, type WorkerInfo } from "./workers";

const worker = (over: Partial<WorkerInfo>): WorkerInfo => ({
  id: 1, name: "gpu-pc", user: "ed", version: "1", clip_provider: null, chat_model: null, note: null, connected_at: 0, busy: 0, done: 0, ...over,
});

describe("compute workers", () => {
  it("describes what a worker does", () => {
    expect(workerSummary(worker({ clip_provider: "DirectML", chat_model: "Qwen", done: 3, busy: 1 }))).toBe(
      "CLIP on DirectML · AI tagger (Qwen) · 3 requests answered, 1 running",
    );
    expect(workerSummary(worker({ done: 1 }))).toBe("nothing this server can use · 1 request answered");
  });

  it("shows an install's progress", () => {
    const base = { running: true, progress: null, log: [], done: null, error: null };
    expect(installProgress(base)).toEqual({ percent: null, text: "Starting…" });
    expect(installProgress({ ...base, progress: [50, 200], log: ["ONNX Runtime: downloading 220 MB…"] })).toEqual({
      percent: 25,
      text: "ONNX Runtime: downloading 220 MB…",
    });
    expect(installProgress({ ...base, running: false, done: "2 component(s) installed" }).text).toBe("2 component(s) installed");
    expect(installProgress({ ...base, running: false, error: "checksum" }).text).toBe("Failed: checksum");
  });

  it("explains the compute mode", () => {
    expect(modeText("workers")).toContain("wait for a worker");
    expect(modeText("local")).toContain("else on this server");
  });
});
