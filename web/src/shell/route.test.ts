import { pageFromHash } from "./route";

describe("pages from the hash", () => {
  it("maps known pages and falls back to the library", () => {
    expect(pageFromHash("")).toBe("library");
    expect(pageFromHash("#/jobs")).toBe("jobs");
    expect(pageFromHash("#jobs")).toBe("jobs");
    expect(pageFromHash("#/jobs/12")).toBe("jobs");
    expect(pageFromHash("#/settings")).toBe("settings");
    expect(pageFromHash("#/nope")).toBe("library");
  });
});
