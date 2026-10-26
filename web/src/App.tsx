// Who is signed in decides what is shown: setup, sign-in, or the application.
import { Center, Loader, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, unwrap } from "./api/client";
import { LoginScreen, SetupScreen } from "./auth/AuthScreens";
import { Shell } from "./shell/Shell";

export function App() {
  const status = useQuery({
    queryKey: ["auth"],
    queryFn: async () => unwrap(await api.GET("/api/auth/status")),
  });
  // The server's version in the tab's (and the desktop window's) title.
  const version = status.data?.version;
  useEffect(() => {
    if (version) document.title = `StlArchivist ${version}`;
  }, [version]);
  if (status.isPending) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  }
  if (status.isError) {
    return (
      <Center h="100vh">
        <Text c="red">Cannot reach the server: {status.error.message}</Text>
      </Center>
    );
  }
  if (status.data.setup_required) return <SetupScreen />;
  if (!status.data.user) return <LoginScreen />;
  return <Shell user={status.data.user} />;
}
