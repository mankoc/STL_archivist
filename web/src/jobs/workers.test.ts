import { modeText, workerSummary, type WorkerInfo } from "./workers";

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

  it("explains the compute mode", () => {
    expect(modeText("workers")).toContain("wait for a worker");
    expect(modeText("local")).toContain("else on this server");
  });
});
