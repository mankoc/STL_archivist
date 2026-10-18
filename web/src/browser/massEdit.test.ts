import { CLEAR_PRESUPPORTS, emptyForm, toRequest } from "./massEdit";

describe("mass edit request", () => {
  it("sends nothing for an untouched form", () => {
    const r = toRequest(emptyForm(), [1, 2]);
    expect(r.ids).toEqual([1, 2]);
    expect(r.manual).toEqual({});
    expect(r.clearing).toEqual([]);
    expect(r.presupports).toBeNull();
    expect(r.find).toBeNull();
  });

  it("applies typed fields and clears ticked ones", () => {
    const f = emptyForm();
    f.manual.creator = "  Onicron ";
    f.manual.title = "ignored";
    f.clear.title = true;
    f.presupports = CLEAR_PRESUPPORTS;
    const r = toRequest(f, [1]);
    expect(r.manual).toEqual({ creator: "Onicron", title: null });
    expect(r.clearing).toEqual(["title"]);
    expect(r.presupports).toBe("");
  });

  it("sends find & replace only with a find text and fields", () => {
    const f = emptyForm();
    f.find = "x";
    expect(toRequest(f, [1]).find).toBeNull();
    f.findFields = ["title"];
    f.findRegex = true;
    expect(toRequest(f, [1]).find).toEqual({ find: "x", replace: "", case: false, regex: true, fields: ["title"] });
  });
});
