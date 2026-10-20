import { defaultVariant, installable, sizeOf, totalBytes, type ComponentStatus, type ComponentsInfo } from "./components";

const comp = (id: string, state: string, sizes: [string, number][], variant: string | null = null): ComponentStatus =>
  ({ id, label: id, enables: "", license: "", license_url: "", state, detail: "", sizes: sizes.map(([v, b]) => ({ variant: v, bytes: b })), variant }) as ComponentStatus;

const info = (gpu: boolean, variants: string[]): ComponentsInfo =>
  ({ components: [], platform: "windows-x64", variants, nvidia_gpu: gpu, free_bytes: null }) as ComponentsInfo;

describe("components", () => {
  it("prefers CUDA when an NVIDIA GPU is present", () => {
    expect(defaultVariant(info(true, ["cpu", "cuda"]))).toBe("cuda");
    expect(defaultVariant(info(false, ["cpu", "cuda"]))).toBe("cpu");
    expect(defaultVariant(info(true, ["cpu"]))).toBe("cpu");
  });

  it("sums what will be downloaded", () => {
    const runtime = comp("ai_runtime", "missing", [["cpu", 10], ["cuda", 600]]);
    const model = comp("ai_model", "missing", [["default", 3000]]);
    const pdf = comp("pdfium", "installed", [["default", 4]]);
    expect(sizeOf(runtime, "cuda")).toBe(600);
    expect(sizeOf(model, "cuda")).toBe(3000);
    expect(totalBytes([runtime, model, pdf], ["ai_runtime", "ai_model", "pdfium"], "cpu")).toBe(3010);
  });

  it("offers the runtime again in another variant", () => {
    const runtime = comp("ai_runtime", "installed", [["cpu", 10], ["cuda", 600]], "cpu");
    expect(installable(runtime, "cpu")).toBe(false);
    expect(installable(runtime, "cuda")).toBe(true);
    expect(installable(comp("pdfium", "external", []), "cpu")).toBe(false);
  });
});
