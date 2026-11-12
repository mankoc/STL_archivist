// The AI drawers rendered with a stand-in API: asking, the job finishing, the result shown.
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";


const model = { id: 1, archive_path: "lib/Studio/Dragon.zip", title: "Dragon", date: "", scales: [], tags: [], images: [], to_print: false };
const issues = { type: "ai_issues", rows: [{ model_id: 1, title: "Dragon", archive_path: "lib/Dragon.zip", field: "Scale", value: "280 mm", problem: "bad", suggestion: "28 mm" }] };
const result = { type: "ai_fields", model_id: 1, title: "Red Dragon", creator: "Studio", source: "", date: "2024-03", sculptor: "", release: "", collection: "", subcollection: "", category: "Monster", description: "", scales: ["32 mm"], tags: ["dragon"] };
let state = "running";
let kind = "ai_fields";
const job = () => ({ id: 7, kind, state, request: { kind, id: 1 }, done: 0, total: 1, paused: false, created_at: 0, user: "ed" });

vi.mock("../api/client", () => ({
  unwrap: (r: { data: unknown }) => r.data,
  api: {
    GET: vi.fn(async (path: string) => {
      if (path === "/api/models/{id}/editor") return { data: model };
      if (path === "/api/editor/choices") return { data: {} };
      if (path === "/api/jobs") return { data: [job()] };
      if (path === "/api/jobs/{id}") return { data: { job: job(), log: [], result: job().kind === "ai_check" ? issues : result } };
      return { data: [] };
    }),
    POST: vi.fn(async () => ({ data: job() })),
    PUT: vi.fn(async () => ({ data: {} })),
  },
}));

it("AI data does not crash the editor", async () => {
  window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as never;
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
  const errors: unknown[] = [];
  const { EditorDrawer } = await import("./EditorDrawer");
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el, { onUncaughtError: (e) => errors.push(e) });
  const qc = new QueryClient();
  const tree = () =>
    createElement(QueryClientProvider, { client: qc },
      createElement(MantineProvider, null, createElement(Notifications), createElement(EditorDrawer, { id: 1, onClose: () => {} })));
  await act(async () => root.render(tree()));
  await act(async () => new Promise((r) => setTimeout(r, 300)));
  const btn = document.querySelector("[data-testid=editor-ai-data]") as HTMLButtonElement;
  expect(btn).toBeTruthy();
  await act(async () => btn.click());
  await act(async () => new Promise((r) => setTimeout(r, 300)));
  state = "done";
  await act(async () => { await qc.invalidateQueries({ queryKey: ["jobs"] }); });
  await act(async () => new Promise((r) => setTimeout(r, 300)));
  expect(errors).toEqual([]);
  const title = document.querySelector("[data-testid=editor] input") as HTMLInputElement;
  expect(title.value).toBe("Dragon");
  expect(document.body.textContent).toContain("Filled:");
});

it("the AI check drawer shows its result", async () => {
  kind = "ai_check";
  state = "running";
  const errors: unknown[] = [];
  const { AiCheckDrawer } = await import("./AiCheckDrawer");
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el, { onUncaughtError: (e) => errors.push(e), onCaughtError: (e) => errors.push(e) });
  const qc = new QueryClient();
  await act(async () => root.render(createElement(QueryClientProvider, { client: qc }, createElement(MantineProvider, null, createElement(Notifications),
    createElement(AiCheckDrawer, { ids: [1], opened: true, onClose: () => {}, onEdit: () => {} })))));
  await act(async () => new Promise((r) => setTimeout(r, 300)));
  await act(async () => (document.querySelector("[data-testid=ai-check-start]") as HTMLButtonElement).click());
  await act(async () => new Promise((r) => setTimeout(r, 300)));
  state = "done";
  await act(async () => { await qc.invalidateQueries({ queryKey: ["jobs"] }); });
  await act(async () => new Promise((r) => setTimeout(r, 300)));
  expect(errors).toEqual([]);
  expect(document.querySelectorAll("[data-testid=ai-issue]").length).toBe(1);
  await act(async () => (document.querySelector("[data-testid=ai-check-apply]") as HTMLButtonElement).click());
  await act(async () => new Promise((r) => setTimeout(r, 300)));
  const { api } = await import("../api/client");
  const put = vi.mocked(api.PUT).mock.calls.at(-1) as unknown as [string, { body: { scales: string[]; title: string } }];
  expect(put[0]).toBe("/api/models/{id}/editor");
  expect(put[1].body.scales).toEqual(["28 mm"]);
  expect(put[1].body.title).toBe("Dragon");
  expect(document.body.textContent).toContain("Applied 1 change(s)");
});
