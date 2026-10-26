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

/**
 * Upload a library package (`POST /api/backups/upload`, the file as the body),
 * reporting its progress (0 to 1), which fetch cannot do.
 */
export function uploadPackage(file: Blob, onProgress: (fraction: number) => void): Promise<Schemas["BackupFile"]> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/backups/upload");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // Not JSON: the status text explains.
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Schemas["BackupFile"]);
      else reject(new ApiError(xhr.status, (body as { error?: string } | null)?.error ?? xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, "the upload broke off"));
    xhr.send(file);
  });
}

/** The data of an openapi-fetch result, or throw its error as an {@link ApiError}. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const e = result.error as { error?: string } | undefined;
    throw new ApiError(result.response.status, e?.error ?? result.response.statusText);
  }
  return result.data as T;
}
