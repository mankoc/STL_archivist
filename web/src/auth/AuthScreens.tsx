// Signing in, and creating the first administrator on a new server.
import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api, ApiError, unwrap } from "../api/client";

function Form(props: { title: string; intro?: string; submit: string; onSubmit: (name: string, password: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await props.onSubmit(name, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Center h="100vh" p="md">
      <Paper withBorder p="lg" w={360} component="form" onSubmit={submit}>
        <Stack>
          <Title order={3}>{props.title}</Title>
          {props.intro && (
            <Text size="sm" c="dimmed">
              {props.intro}
            </Text>
          )}
          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required autoFocus />
          <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} required />
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}
          <Button type="submit" loading={busy}>
            {props.submit}
          </Button>
        </Stack>
      </Paper>
    </Center>
  );
}

export function LoginScreen() {
  const queryClient = useQueryClient();
  return (
    <Form
      title="stlLibrary"
      submit="Sign in"
      onSubmit={async (name, password) => {
        unwrap(await api.POST("/api/auth/login", { body: { name, password } }));
        await queryClient.invalidateQueries({ queryKey: ["auth"] });
      }}
    />
  );
}

export function SetupScreen() {
  const queryClient = useQueryClient();
  return (
    <Form
      title="Welcome to stlLibrary"
      intro="Create the administrator account. This works only on the server's own machine; elsewhere, run `stl-server create-admin` there."
      submit="Create administrator"
      onSubmit={async (name, password) => {
        try {
          unwrap(await api.POST("/api/auth/setup", { body: { name, password } }));
        } catch (e) {
          if (e instanceof ApiError && e.status === 403) {
            throw new Error("Setup is only possible on the server's own machine (or with `stl-server create-admin`).");
          }
          throw e;
        }
        await queryClient.invalidateQueries({ queryKey: ["auth"] });
      }}
    />
  );
}
