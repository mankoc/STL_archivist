import { describe, expect, it } from "vitest";

import { versionToShow } from "./updates";

describe("versionToShow", () => {
  it("announces a newer version", () => {
    expect(versionToShow("2026.46.0", null)).toBe("2026.46.0");
  });

  it("stays quiet without one", () => {
    expect(versionToShow(null, null)).toBeNull();
    expect(versionToShow(undefined, "2026.46.0")).toBeNull();
  });

  it("stays quiet about a dismissed version, not a later one", () => {
    expect(versionToShow("2026.46.0", "2026.46.0")).toBeNull();
    expect(versionToShow("2026.46.1", "2026.46.0")).toBe("2026.46.1");
  });
});
