// The typed API client. Types come from the server's OpenAPI document
// (`npm run gen:api`); requests are same-origin, so the session cookie set at
// sign-in authenticates them.
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });

/** An API error: the HTTP status and the server's message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** The data of an openapi-fetch result, or throw its error as an {@link ApiError}. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const e = result.error as { error?: string } | undefined;
    throw new ApiError(result.response.status, e?.error ?? result.response.statusText);
  }
  return result.data as T;
}
