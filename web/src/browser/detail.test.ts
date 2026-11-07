import { detailFacts, loadTab, middlePath, saveTab, webLink } from "./detail";

describe("detail facts", () => {
  it("lists what has a value, joined and formatted", () => {
    const facts = detailFacts({
      creator: "VintageVault", source: "Kickstarter", collection_name: "Beauties", subcollection_name: "Aurora",
      release_year: 2024, release_month: 5, archive_size: 1024 * 1024, compression_rate: 0.612, file_count: 41, stl_count: 20,
      presupports: "Lychee", created_at: "2026-11-02 10:00:00", sculptor_name: null, scales: "",
    });
    expect(Object.fromEntries(facts)).toEqual({
      Creator: "VintageVault", Type: "Kickstarter", Collection: "Beauties › Aurora", Date: "2024-05",
      Size: "1.0 MB (61 %)", Files: "41 · 20 STL", Presupports: "Lychee", Added: "2026-11-02",
    });
  });

  it("leaves out no presupports and an empty file list", () => {
    expect(detailFacts({ presupports: "No", file_count: 0 })).toEqual([]);
  });
});

describe("the pane's tab", () => {
  it("opens on Images, then on the last one chosen", () => {
    localStorage.clear();
    expect(loadTab()).toBe("images");
    saveTab("files");
    expect(loadTab()).toBe("files");
    localStorage.setItem("stl.detailTab", "nonsense");
    expect(loadTab()).toBe("images");
  });
});

describe("links and paths", () => {
  it("links only web addresses", () => {
    expect(webLink("https://example.com/x")).toBe("https://example.com/x");
    expect(webLink("javascript:alert(1)")).toBeNull();
    expect(webLink(null)).toBeNull();
  });

  it("shortens a long path in the middle, keeping the name", () => {
    const p = "F:\\STL\\00 - Kickstarters\\VintageVault\\Deep\\Folder\\VintageVault - Orbiting Beauties.7z";
    const short = middlePath(p, 60);
    expect(short.length).toBeLessThanOrEqual(61);
    expect(short.endsWith("VintageVault - Orbiting Beauties.7z")).toBe(true);
    expect(short).toContain("…");
    expect(middlePath("C:\\a.7z")).toBe("C:\\a.7z");
  });
});
