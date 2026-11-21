// The table's Columns menu: shown columns, relative paths and the column order,
// per-user settings (so they follow the account).
import { ActionIcon, Checkbox, Menu, Text, Tooltip } from "@mantine/core";
import { IconColumns } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { RELATIVE_PATHS } from "./cells";
import { ALWAYS_SHOWN, type ColumnChoice, COLUMN_ORDER_KEY, HIDDEN_COLUMNS_KEY, isShown, SHOWN_COLUMNS_KEY, toggle } from "./columns";
import { COLUMNS } from "./ModelTable";

type Prefs = Record<string, unknown>;

export function ColumnsMenu({ choice, relativePaths }: { choice: ColumnChoice; relativePaths: boolean }) {
  const queryClient = useQueryClient();
  const put = async (key: string, value: unknown) => {
    queryClient.setQueryData<Prefs>(["me-settings"], (old) => ({ ...old, [key]: value }));
    await api.PUT("/api/me/settings/{key}", { params: { path: { key } }, body: value as never });
  };
  const toggleColumn = (c: (typeof COLUMNS)[number]) => {
    const next = toggle(choice, c);
    return c.optional ? put(SHOWN_COLUMNS_KEY, next.shown) : put(HIDDEN_COLUMNS_KEY, next.hidden);
  };
  const resetOrder = async () => {
    queryClient.setQueryData<Prefs>(["me-settings"], (old) => {
      const next = { ...old };
      delete next[COLUMN_ORDER_KEY];
      return next;
    });
    await api.DELETE("/api/me/settings/{key}", { params: { path: { key: COLUMN_ORDER_KEY } } });
  };
  const columnItem = (c: (typeof COLUMNS)[number]) => (
    <Menu.Item key={c.id} disabled={c.id === ALWAYS_SHOWN} onClick={() => toggleColumn(c)}
      leftSection={<Checkbox size="xs" readOnly checked={isShown(c, choice)} tabIndex={-1} />}>
      {c.header}
    </Menu.Item>
  );
  return (
    <Menu closeOnItemClick={false} position="bottom-end">
      <Menu.Target>
        <Tooltip label="Columns" openDelay={400}>
          <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Columns" data-testid="columns-menu">
            <IconColumns size={16} />
          </ActionIcon>
        </Tooltip>
      </Menu.Target>
      <Menu.Dropdown mah="70vh" style={{ overflowY: "auto" }}>
        {COLUMNS.filter((c) => !c.optional).map(columnItem)}
        <Menu.Label>More columns</Menu.Label>
        {COLUMNS.filter((c) => c.optional).map(columnItem)}
        <Menu.Divider />
        <Menu.Item onClick={() => put(RELATIVE_PATHS, !relativePaths)} leftSection={<Checkbox size="xs" readOnly checked={relativePaths} tabIndex={-1} />}
          data-testid="relative-paths">
          Paths relative to their library
        </Menu.Item>
        <Menu.Divider />
        <Text size="xs" c="dimmed" px="sm" py={4} maw={250}>
          To move a column, drag the ⋮⋮ grip left of its name in the table header and drop it on the
          column whose place it should take.
        </Text>
        <Menu.Item disabled={!choice.order.length} onClick={resetOrder} data-testid="reset-column-order">
          Reset column order
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
