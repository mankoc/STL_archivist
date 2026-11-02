import { describe, expect, it } from "vitest";

import { shownPath, webHref } from "./cells";

describe("shownPath", () => {
  it("shows the absolute path, or the path below the library folder", () => {
    const abs = "F:\\STL\\00 - Kickstarters\\a.7z";
    expect(shownPath(abs, "00 - Kickstarters/a.7z", false)).toBe(abs);
    expect(shownPath(abs, "00 - Kickstarters/a.7z", true)).toBe("00 - Kickstarters\\a.7z");
    expect(shownPath("/srv/stl/x/a.7z", "x/a.7z", true)).toBe("x/a.7z");
    expect(shownPath(null, "a.7z", false)).toBe("");
  });
});

describe("webHref", () => {
  it("links web addresses only", () => {
    expect(webHref("https://www.kickstarter.com/projects/x")).toBe("https://www.kickstarter.com/projects/x");
    expect(webHref(" http://example.com ")).toBe("http://example.com/");
    expect(webHref("javascript:alert(1)")).toBe(null);
    expect(webHref("file:///C:/x")).toBe(null);
    expect(webHref("kickstarter.com/x")).toBe(null);
    expect(webHref("")).toBe(null);
    expect(webHref(null)).toBe(null);
  });
});
