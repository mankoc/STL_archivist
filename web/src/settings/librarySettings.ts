// The library settings query, shared by the Library tab and the sidecar card.
import { api, type Schemas, unwrap } from "../api/client";

export type LibrarySettings = Schemas["Settings"];

export const SETTINGS_QUERY = {
  queryKey: ["settings"],
  queryFn: async (): Promise<LibrarySettings> => unwrap(await api.GET("/api/settings/library")),
};
