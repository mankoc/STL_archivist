import { DEFAULT_ACCENT, isAccent, rememberAccent, savedAccent } from "./accent";

describe("accent colour", () => {
  afterEach(() => localStorage.clear());

  it("accepts only Mantine's palette", () => {
    expect(isAccent("teal")).toBe(true);
    expect(isAccent("#ff0000")).toBe(false);
    expect(isAccent(3)).toBe(false);
  });

  it("remembers the last accent in this browser", () => {
    expect(savedAccent()).toBe(DEFAULT_ACCENT);
    rememberAccent("grape");
    expect(savedAccent()).toBe("grape");
    localStorage.setItem("stl.accent", "nonsense");
    expect(savedAccent()).toBe(DEFAULT_ACCENT);
  });
});
