// Settings → Workers: the compute worker sessions (editors their own, administrators
// everyone's). Each start of a worker from the desktop app makes one; it opens only the
// worker connection, and ending it disconnects that worker within a minute.
import { Button, Card, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "../api/client";

const when = (secs: number) => new Date(secs * 1000).toLocaleString();

export function WorkerTokensCard() {
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["worker-tokens"], queryFn: async () => unwrap(await api.GET("/api/workers/tokens")) });
  const end = async (id: string) => {
    try {
      unwrap(await api.DELETE("/api/workers/tokens/{id}", { params: { path: { id } } }));
    } catch (err) {
      notifications.show({ color: "red", title: "Worker sessions", message: (err as Error).message });
    }
    await queryClient.invalidateQueries({ queryKey: ["worker-tokens"] });
  };
  const rows = sessions.data ?? [];
  return (
    <Card withBorder data-testid="worker-tokens">
      <Title order={5} mb="xs">Worker sessions</Title>
      <Text size="xs" c="dimmed" mb="xs">
        Each time a computer starts working for this server it gets a session that only connects a worker. A session
        ends after 30 days unused, when its user's password changes, or here.
      </Text>
      {rows.length === 0 ? (
        <Text size="sm" c="dimmed">No worker session.</Text>
      ) : (
        <Table fz="sm" verticalSpacing={4}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>User</Table.Th>
              <Table.Th>Started</Table.Th>
              <Table.Th>Last used</Table.Th>
              <Table.Th w={80} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td>{s.user}</Table.Td>
                <Table.Td>{when(s.created_at)}</Table.Td>
                <Table.Td>{when(s.last_used)}</Table.Td>
                <Table.Td>
                  <Button size="compact-xs" variant="subtle" color="red" onClick={() => void end(s.id)}>
                    End
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}
