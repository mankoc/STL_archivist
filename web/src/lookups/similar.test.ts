import { describe, expect, it } from "vitest";

import { defaultThreshold, thresholdRange, usedFirst } from "./similar";

const name = (n: string, models: number) => ({ id: 1, name: n, models });

describe("merge similar", () => {
  it("offers the name more models use first", () => {
    const [a, b] = [name("dragon", 2), name("dragons", 9)];
    expect(usedFirst({ a, b, score: "1" })).toEqual([b, a]);
    expect(usedFirst({ a: b, b: a, score: "1" })).toEqual([b, a]);
    expect(usedFirst({ a, b: name("drakon", 2), score: "1" })[0]).toBe(a);
  });

  it("gives each method its threshold", () => {
    expect(thresholdRange("sound")).toBeNull();
    expect(defaultThreshold("spelling")).toBeGreaterThanOrEqual(thresholdRange("spelling")![0]);
    expect(defaultThreshold("words")).toBeLessThanOrEqual(thresholdRange("words")![1]);
  });
});
