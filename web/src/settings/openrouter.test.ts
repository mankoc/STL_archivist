import { describe, expect, it } from "vitest";

import { freeVisionModels } from "./openrouter";

describe("freeVisionModels", () => {
  it("keeps free models that read images, by name", () => {
    const vision = { input_modalities: ["text", "image"] };
    expect(
      freeVisionModels({
        data: [
          { id: "z/vl:free", name: "Zeta VL (free)", architecture: vision },
          { id: "a/text:free", name: "Alpha", architecture: { input_modalities: ["text"] } },
          { id: "b/vl", name: "Paid VL", architecture: vision },
          { id: "c/vl:free", architecture: vision },
        ],
      }),
    ).toEqual([
      { value: "c/vl:free", label: "c/vl:free" },
      { value: "z/vl:free", label: "Zeta VL (free)" },
    ]);
    expect(freeVisionModels({})).toEqual([]);
  });
});
