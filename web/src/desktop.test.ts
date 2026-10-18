import { inDesktopShell, openFolder } from "./desktop";

describe("desktop shell bridge", () => {
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  });

  it("is absent in a browser", async () => {
    expect(inDesktopShell()).toBe(false);
    await expect(openFolder("C:/x")).rejects.toThrow("desktop app");
  });

  it("calls open_folder in the shell", async () => {
    const calls: unknown[] = [];
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
      invoke: async (cmd: string, args: unknown) => {
        calls.push([cmd, args]);
      },
    };
    expect(inDesktopShell()).toBe(true);
    await openFolder("D:/STL/a.zip");
    expect(calls).toEqual([["open_folder", { path: "D:/STL/a.zip" }]]);
  });
});
