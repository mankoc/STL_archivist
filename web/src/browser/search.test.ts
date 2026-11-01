import { customised, DEFAULT_FIELDS, DEFAULT_OPTIONS, parseOptions, placeholder } from "./search";

describe("search options", () => {
  it("reads stored options, dropping unknown fields", () => {
    expect(parseOptions(null)).toEqual(DEFAULT_OPTIONS);
    expect(parseOptions("not json")).toEqual(DEFAULT_OPTIONS);
    expect(parseOptions('{"fields": ["tags", "nope", "title"], "fuzzy": true}')).toEqual({ fields: ["title", "tags"], fuzzy: true });
    expect(parseOptions('{"fields": ["nope"]}')).toEqual({ fields: DEFAULT_FIELDS, fuzzy: false });
  });

  it("names the fields in the placeholder", () => {
    expect(placeholder({ fields: ["title", "tags"], fuzzy: false })).toBe("Search title, tags");
    expect(placeholder({ fields: DEFAULT_FIELDS, fuzzy: true })).toBe("Fuzzy search title, creator, sculptor, collection +2");
  });

  it("tells customised options", () => {
    expect(customised(DEFAULT_OPTIONS)).toBe(false);
    expect(customised({ fields: DEFAULT_FIELDS, fuzzy: true })).toBe(true);
    expect(customised({ fields: ["title"], fuzzy: false })).toBe(true);
  });
});
