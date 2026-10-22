import { defaultVariants, installable, sizeOf, totalBytes, variantLabel, type ComponentStatus, type ComponentsInfo } from "./components";

const comp = (id: string, state: string, sizes: [string, number][], variant: string | null = null, def: string | null = null): ComponentStatus =>
  ({
    id, label: id, enables: "", license: "", license_url: "", state, detail: "",
    sizes: sizes.map(([v, b]) => ({ variant: v, bytes: b })),
    variants: sizes.map(([v]) => v).filter((v) => v !== "default"),
    default_variant: def, variant,
  }) as ComponentStatus;

const info = (components: ComponentStatus[]): ComponentsInfo =>
  ({ components, platform: "windows-x64", gpus: [], clip_provider: null, free_bytes: null }) as ComponentsInfo;

describe("components", () => {
  it("offers each component's default variant", () => {
    const runtime = comp("ai_runtime", "missing", [["cpu", 10], ["cuda", 600]], null, "cuda");
    const onnx = comp("onnxruntime", "missing", [["cpu", 70], ["directml", 220]], null, "directml");
    const pdf = comp("pdfium", "missing", [["default", 4]]);
    expect(defaultVariants(info([runtime, onnx, pdf]))).toEqual({ ai_runtime: "cuda", onnxruntime: "directml" });
    expect(defaultVariants(info([comp("ai_runtime", "missing", [["cpu", 10]])]))).toEqual({ ai_runtime: "cpu" });
    expect(variantLabel("directml")).toBe("GPU (DirectML)");
  });

  it("sums what will be downloaded", () => {
    const runtime = comp("ai_runtime", "missing", [["cpu", 10], ["cuda", 600]]);
    const onnx = comp("onnxruntime", "missing", [["cpu", 70], ["cuda", 230]]);
    const model = comp("ai_model", "missing", [["default", 3000]]);
    const pdf = comp("pdfium", "installed", [["default", 4]]);
    expect(sizeOf(runtime, "cuda")).toBe(600);
    expect(sizeOf(model, undefined)).toBe(3000);
    const all = ["ai_runtime", "onnxruntime", "ai_model", "pdfium"];
    expect(totalBytes([runtime, onnx, model, pdf], all, { ai_runtime: "cpu", onnxruntime: "cuda" })).toBe(3240);
  });

  it("offers a component again in another variant", () => {
    const onnx = comp("onnxruntime", "installed", [["cpu", 70], ["directml", 220]], "cpu");
    expect(installable(onnx, "cpu")).toBe(false);
    expect(installable(onnx, "directml")).toBe(true);
    expect(installable(comp("pdfium", "installed", [["default", 4]]), undefined)).toBe(false);
    expect(installable(comp("pdfium", "external", []), undefined)).toBe(false);
  });
});
