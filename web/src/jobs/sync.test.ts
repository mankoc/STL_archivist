import { describe, expect, it } from "vitest";

import { cleanRules, lastSyncText, previewSummary, type RemoteInfo, type SyncPreview, syncWarning } from "./sync";

const preview = (folders: SyncPreview["folders"]): SyncPreview => ({
  direction: "pull",
  local_version: "2026.44.5",
  remote_version: "2026.44.5",
  problem: null,
  folders,
});
const folder = (target: string | null, exists: boolean) => ({ uuid: "u", name: "Minis", library: "Minis", root: "/library", target, exists, models: 3 });

describe("sync texts", () => {
  it("names the side that is replaced", () => {
    expect(syncWarning("pull", "nas")).toContain("This server's library will be replaced by nas's");
    expect(syncWarning("push", "nas")).toContain("nas's library will be replaced by this server's");
  });

  it("summarises a preview", () => {
    expect(previewSummary(preview([folder("D:\\Minis", true)]))).toBe("1 folder, all mapped to existing folders");
    expect(previewSummary(preview([folder(null, false), folder("D:\\X", false)]))).toBe(
      "2 folders, 1 not mapped by a rule (its records would show as missing), 1 mapped to a folder that does not exist there",
    );
  });

  it("tells the last sync", () => {
    const r: RemoteInfo = { id: 1, name: "nas", url: "http://nas", user: "boss", rules: [], backup_first: true, last_sync_at: null };
    expect(lastSyncText(r, String)).toBeNull();
    expect(lastSyncText({ ...r, last_sync_at: 5, last_direction: "push", last_result: "pushed to nas: ok" }, (t) => `at ${t}`)).toBe(
      "Pushed at 5: pushed to nas: ok",
    );
  });
});

describe("cleanRules", () => {
  it("drops blank rows and trims", () => {
    expect(cleanRules([{ local: " D:\\Minis ", remote: "/library" }, { local: "", remote: " " }])).toEqual({
      rules: [{ local: "D:\\Minis", remote: "/library" }],
      error: null,
    });
  });

  it("refuses a half-filled row", () => {
    expect(cleanRules([{ local: "D:\\Minis", remote: "" }]).error).toBe("Each rule needs a folder on both servers.");
  });
});
