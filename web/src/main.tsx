import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "@mantine/dropzone/styles.css";

import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { type Accent, AccentContext, rememberAccent, savedAccent } from "./accent";
import { ApiError } from "./api/client";
import { theme } from "./theme";
import { App } from "./App";

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    // A session that ended (signed out elsewhere, password changed) shows the sign-in screen.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) void queryClient.invalidateQueries({ queryKey: ["auth"] });
    },
  }),
  defaultOptions: { queries: { retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2, refetchOnWindowFocus: false } },
});

/** The theme in the user's accent colour (accent.ts), which the shell and Settings change. */
function Root() {
  const [accent, setAccent] = useState<Accent>(savedAccent);
  const themed = useMemo(() => ({ ...theme, primaryColor: accent }), [accent]);
  const change = useMemo(
    () => (a: Accent) => {
      rememberAccent(a);
      setAccent(a);
    },
    [],
  );
  return (
    <MantineProvider theme={themed} defaultColorScheme="auto">
      <Notifications />
      <AccentContext.Provider value={change}>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </AccentContext.Provider>
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
