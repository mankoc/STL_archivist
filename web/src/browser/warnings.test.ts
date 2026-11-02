import { rowTone } from "./warnings";

describe("rowTone", () => {
  it("colours rows by their warnings", () => {
    expect(rowTone(null)).toBeNull();
    expect(rowTone(" \n")).toBeNull();
    expect(rowTone("Archive without images")).toBe("warning");
    expect(rowTone("Archive without images\nUnreadable archive")).toBe("unreadable");
  });
});
