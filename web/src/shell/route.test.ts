import { currentHash, pageFromHash } from "./route";

describe("pages from the hash", () => {
  it("maps known pages and falls back to the library", () => {
    expect(pageFromHash("")).toBe("library");
    expect(pageFromHash("#/jobs")).toBe("jobs");
    expect(pageFromHash("#jobs")).toBe("jobs");
    expect(pageFromHash("#/jobs/12")).toBe("jobs");
    expect(pageFromHash("#/settings")).toBe("settings");
    expect(pageFromHash("#/nope")).toBe("library");
    expect(pageFromHash("#/help/embeddings")).toBe("help");
  });

  it("sends the old Maintenance page to its Settings tab", () => {
    expect(currentHash("#/maintenance")).toBe("#/settings/maintenance");
    expect(currentHash("#maintenance")).toBe("#/settings/maintenance");
    expect(pageFromHash("#/maintenance")).toBe("settings");
    expect(currentHash("#/jobs/12")).toBe("#/jobs/12");
    expect(currentHash("")).toBe("");
  });
});
