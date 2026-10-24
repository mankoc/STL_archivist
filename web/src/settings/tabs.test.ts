import { settingsTab } from "./tabs";

describe("settings tabs", () => {
  it("come from the URL", () => {
    const tabs = ["account", "library", "components"];
    expect(settingsTab("#/settings/components", tabs)).toBe("components");
    expect(settingsTab("#settings/library", tabs)).toBe("library");
    expect(settingsTab("#/settings", tabs)).toBe("account");
    expect(settingsTab("#/settings/users", tabs)).toBe("account");
  });
});
