import { ApiError, postForm } from "./client";

describe("postForm", () => {
  const answer = (body: string, init: ResponseInit) => vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, init));
  afterEach(() => vi.restoreAllMocks());

  it("returns the JSON answer", async () => {
    answer('{"added":[1]}', { status: 200 });
    await expect(postForm("/x", new FormData())).resolves.toEqual({ added: [1] });
  });

  it("gives the server's message on an error", async () => {
    answer('{"error":"not an image"}', { status: 400 });
    await expect(postForm("/x", new FormData())).rejects.toEqual(new ApiError(400, "not an image"));
  });

  it("gives the status text when the error is not JSON", async () => {
    answer("<html>413 Request Entity Too Large</html>", { status: 413, statusText: "Payload Too Large" });
    await expect(postForm("/x", new FormData())).rejects.toEqual(new ApiError(413, "Payload Too Large"));
  });
});
