import { describe, expect, it } from "vitest";

import { freeVisionModels, modelSlug } from "./openrouter";

describe("freeVisionModels", () => {
  it("keeps free models that read images, sorted", () => {
    const vision = { input_modalities: ["text", "image"] };
    expect(
      freeVisionModels({
        data: [
          { id: "z/vl:free", architecture: vision },
          { id: "a/text:free", architecture: { input_modalities: ["text"] } },
          { id: "b/vl", architecture: vision },
          { id: "c/vl:free", architecture: vision },
        ],
      }),
    ).toEqual(["c/vl:free", "z/vl:free"]);
    expect(freeVisionModels({})).toEqual([]);
  });
});

describe("modelSlug", () => {
  it("takes a slug as it is, or the slug of a model page", () => {
    expect(modelSlug("  qwen/qwen2.5-vl-72b-instruct ")).toBe("qwen/qwen2.5-vl-72b-instruct");
    expect(modelSlug("https://openrouter.ai/google/gemma-4-31b-it:free")).toBe("google/gemma-4-31b-it:free");
    expect(modelSlug("openrouter.ai/models/x/y?tab=api")).toBe("x/y");
    expect(modelSlug("")).toBe("");
  });
});
