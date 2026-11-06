// The filter pane: one collapsible section per facet, as in the desktop app.
// A value cycles include (+) → exclude (−) → off; the circle on a section
// header cycles "has a value" → "has no value" → off. A row with children
// (a folder, a year, a collection) opens on a click and filters on a
// double-click or a click on its mark. Long lists are grouped by first letter.
// Counts are scoped by the other sections while a filter is active.
import { ActionIcon, Box, Group, ScrollArea, Text, TextInput, Tooltip, UnstyledButton } from "@mantine/core";
import {
  IconChevronDown,
  IconChevronRight,
  IconCircle,
  IconCircleCheck,
  IconCircleMinus,
  IconFilter,
  IconMinus,
  IconPlus,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { type MouseEvent, type ReactNode, useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import {
  countOf,
  cycleGroup,
  cycleValue,
  type FilterSpec,
  type FilterTree,
  type FilterValue,
  folderTree,
  type FolderNode,
  groupByLetter,
  groupCount,
  groupMarkOf,
  libraryFolderTree,
  rowClick,
  type LibraryNode,
  markOf,
  pathCount,
  sumCounts,
} from "./filters";
import classes from "./FilterPane.module.css";

interface Props {
  tree: FilterTree;
  spec: FilterSpec;
  onChange: (spec: FilterSpec) => void;
  /** A folder of the Path section was right-clicked (none: the browser's own menu). */
  onFolderMenu?: (folder: string, e: MouseEvent) => void;
}

/** Sections with more values than this get their own search box, and their list letter groups. */
const SEARCH_FROM = 15;

/** A row's filter mark, clickable: + include, − exclude; a faint funnel on a row
 * with children (whose own click opens it) while it filters nothing. */
function MarkIcon({ mark, expandable, onClick }: { mark: ReturnType<typeof markOf>; expandable: boolean; onClick?: () => void }) {
  const icon =
    mark === "include" ? <IconPlus size={14} color="var(--mantine-color-green-6)" />
    : mark === "exclude" ? <IconMinus size={14} color="var(--mantine-color-red-6)" />
    : expandable ? <IconFilter size={12} />
    : null;
  if (!icon) return <Box w={14} />;
  return (
    <ActionIcon
      size={14}
      variant="transparent"
      color="gray"
      component="span"
      className={mark ? undefined : classes.unmarked}
      onClick={(e: MouseEvent) => {
        e.stopPropagation();
        onClick?.();
      }}
      onDoubleClick={(e: MouseEvent) => e.stopPropagation()}
      aria-label="Filter: include, exclude, off"
      title="Filter: include, exclude, off (or double-click the row)"
    >
      {icon}
    </ActionIcon>
  );
}

function ValueRow(props: {
  label: ReactNode;
  count: number | undefined;
  mark: ReturnType<typeof markOf>;
  /** Filter by the value (cycle its mark); none on a row that only opens. */
  onClick?: () => void;
  onContextMenu?: (e: MouseEvent) => void;
  depth?: number;
  /** The row has children: whether they show, and showing or hiding them. */
  expand?: { open: boolean; onToggle: () => void };
}) {
  const { expand } = props;
  return (
    <UnstyledButton
      className={classes.value}
      data-mark={props.mark}
      onClick={(e: MouseEvent) => {
        const action = rowClick(e.detail, expand != null);
        if (action === "toggle") expand?.onToggle();
        if (action === "filter") props.onClick?.();
      }}
      onDoubleClick={expand && props.onClick}
      onContextMenu={props.onContextMenu && ((e: MouseEvent) => {
        e.preventDefault();
        props.onContextMenu?.(e);
      })}
      style={{ paddingLeft: 8 + (props.depth ?? 0) * 14 }}
    >
      <Group gap={6} wrap="nowrap">
        {expand ? <Toggle open={expand.open} onClick={expand.onToggle} /> : <Box w={16} />}
        <MarkIcon mark={props.mark} expandable={expand != null && props.onClick != null} onClick={props.onClick} />
        <Text size="sm" truncate flex={1}>
          {props.label}
        </Text>
        {props.count !== undefined && (
          <Text size="xs" c="dimmed">
            {props.count}
          </Text>
        )}
      </Group>
    </UnstyledButton>
  );
}

function GroupIcon({ mark }: { mark: ReturnType<typeof groupMarkOf> }) {
  if (mark === "has") return <IconCircleCheck size={16} color="var(--mantine-color-green-6)" />;
  if (mark === "no") return <IconCircleMinus size={16} color="var(--mantine-color-red-6)" />;
  return <IconCircle size={16} />;
}

function Section(props: {
  title: string;
  group?: string;
  spec: FilterSpec;
  tree: FilterTree;
  onChange: (s: FilterSpec) => void;
  count?: number;
  children: (search: string) => ReactNode;
  searchable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const mark = props.group ? groupMarkOf(props.spec, props.group) : undefined;
  const count = props.group ? groupCount(props.tree, props.group) : props.count;
  return (
    <Box className={classes.section}>
      <Group gap={4} wrap="nowrap" className={classes.sectionHeader}>
        <ActionIcon variant="subtle" size="sm" onClick={() => setOpen(!open)} aria-label={open ? "Collapse" : "Expand"}>
          {open ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
        </ActionIcon>
        {props.group ? (
          <Tooltip label="Has a value / has no value" openDelay={600}>
            <ActionIcon
              variant="subtle"
              size="sm"
              onClick={() => props.onChange(cycleGroup(props.spec, props.group!))}
              aria-label={`${props.title}: has a value or not`}
            >
              <GroupIcon mark={mark} />
            </ActionIcon>
          </Tooltip>
        ) : (
          <Box w={22} />
        )}
        <UnstyledButton onClick={() => setOpen(!open)} flex={1}>
          <Text size="sm" fw={500}>
            {props.title}
            {count !== undefined && (
              <Text span size="xs" c="dimmed">
                {" "}
                ({count})
              </Text>
            )}
          </Text>
        </UnstyledButton>
      </Group>
      {open && (
        <Box pb={4}>
          {props.searchable && (
            <TextInput
              size="xs"
              mx={8}
              mb={4}
              placeholder="Filter values"
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
            />
          )}
          <ScrollArea.Autosize mah={320}>{props.children(search.toLowerCase())}</ScrollArea.Autosize>
        </Box>
      )}
    </Box>
  );
}

function matches(label: string, search: string) {
  return !search || label.toLowerCase().includes(search);
}

/** A long list under a row per first letter; a short one as it is. A letter holding
 * a filtered value starts open, so that no filter hides in a closed group. */
function Letters<T>(props: {
  items: T[];
  name: (item: T) => string;
  count: (item: T) => number | undefined;
  marked: (item: T) => boolean;
  searching: boolean;
  render: (item: T, depth: number) => ReactNode;
}) {
  const { items, name, count, marked, searching, render } = props;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (items.length <= SEARCH_FROM) return <>{items.map((item) => render(item, 0))}</>;
  return (
    <>
      {groupByLetter(items, name).map((group) => {
        const shown = searching || (open[group.letter] ?? group.items.some(marked));
        return (
          <Box key={group.letter}>
            <ValueRow
              label={group.letter}
              count={sumCounts(group.items.map(count))}
              mark={undefined}
              expand={{ open: shown, onToggle: () => setOpen({ ...open, [group.letter]: !shown }) }}
            />
            {shown && group.items.map((item) => render(item, 1))}
          </Box>
        );
      })}
    </>
  );
}

export function FilterPane({ tree, spec, onChange, onFolderMenu }: Props) {
  const value = (facet: string, v: FilterValue, label: string, kind: string, countKey: string, depth = 0) => (
    <ValueRow
      key={`${facet}:${JSON.stringify(v)}`}
      label={label}
      count={countOf(tree, kind, countKey)}
      mark={markOf(spec, facet, v)}
      onClick={() => onChange(cycleValue(spec, facet, v))}
      depth={depth}
    />
  );
  const texts = (facet: string, list: string[], search: string) =>
    list.filter((t) => matches(t, search)).map((t) => value(facet, t, t, facet, t));
  const named = (facet: string, list: FilterTree["sculptors"], search: string) =>
    list.filter((n) => matches(n.name, search)).map((n) => value(facet, n.id, n.name, facet, String(n.id)));
  const lettered = (facet: string, list: string[], search: string) => (
    <Letters
      items={list.filter((t) => matches(t, search))}
      name={(t) => t}
      count={(t) => countOf(tree, facet, t)}
      marked={(t) => markOf(spec, facet, t) !== undefined}
      searching={!!search}
      render={(t, depth) => value(facet, t, t, facet, t, depth)}
    />
  );
  const letteredNames = (facet: string, list: FilterTree["sculptors"], search: string) => (
    <Letters
      items={list.filter((n) => matches(n.name, search))}
      name={(n) => n.name}
      count={(n) => countOf(tree, facet, String(n.id))}
      marked={(n) => markOf(spec, facet, n.id) !== undefined}
      searching={!!search}
      render={(n, depth) => value(facet, n.id, n.name, facet, String(n.id), depth)}
    />
  );
  const common = { spec, tree, onChange };

  return (
    <div className={classes.pane}>
      {tree.libraries.length > 1 && (
        <Section title="Library" group="library" {...common} searchable={tree.libraries.length > SEARCH_FROM}>
          {(s) => named("library", tree.libraries, s)}
        </Section>
      )}
      <Section title="Creators" group="creator" {...common} searchable={tree.creators.length > SEARCH_FROM}>
        {(s) => lettered("creator", tree.creators, s)}
      </Section>
      <Section title="Tags" group="tag" {...common} searchable={tree.tags.length > SEARCH_FROM}>
        {(s) => lettered("tag", tree.tags, s)}
      </Section>
      <Section title="Type" group="source" {...common}>
        {(s) => texts("source", tree.sources, s)}
      </Section>
      <Section title="Date" group="date" {...common}>
        {() => <Dates tree={tree} spec={spec} onChange={onChange} />}
      </Section>
      <Section title="Sculptor" group="sculptor" {...common} searchable={tree.sculptors.length > SEARCH_FROM}>
        {(s) => letteredNames("sculptor", tree.sculptors, s)}
      </Section>
      <Section title="Collection" group="collection" {...common} searchable={tree.collections.length > SEARCH_FROM}>
        {(s) => <Collections tree={tree} spec={spec} onChange={onChange} search={s} />}
      </Section>
      <Section title="Category" group="category" {...common}>
        {(s) => named("category", tree.categories, s)}
      </Section>
      <Section title="Release" group="release" {...common} searchable={tree.releases.length > SEARCH_FROM}>
        {(s) => letteredNames("release", tree.releases, s)}
      </Section>
      <Section title="Scale" group="scale" {...common}>
        {(s) => named("scale", tree.scales, s)}
      </Section>
      <Section title="Presupports" group="presupports" {...common}>
        {(s) => texts("presupports", tree.presupports, s)}
      </Section>
      <Section title="Warnings" group="warning" {...common}>
        {(s) => texts("warning", Object.keys(tree.counts.warning ?? {}).sort(), s)}
      </Section>
      <Section title="File Type" {...common}>
        {(s) => texts("filetype", tree.filetypes, s)}
      </Section>
      <Section title="Path" group="path" {...common}>
        {() => <PathTree tree={tree} spec={spec} onChange={onChange} onFolderMenu={onFolderMenu} />}
      </Section>
    </div>
  );
}

function Toggle({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <ActionIcon
      size={16}
      variant="transparent"
      component="span"
      onClick={(e: MouseEvent) => {
        e.stopPropagation();
        onClick();
      }}
      onDoubleClick={(e: MouseEvent) => e.stopPropagation()}
      aria-label={open ? "Collapse" : "Expand"}
    >
      {open ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
    </ActionIcon>
  );
}

function Dates({ tree, spec, onChange }: Omit<Props, never>) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const years = new Map<number, (number | null | undefined)[]>();
  for (const d of tree.dates) {
    const months = years.get(d.year) ?? [];
    if (d.month != null) months.push(d.month);
    years.set(d.year, months);
  }
  const pad = (m: number) => String(m).padStart(2, "0");
  return (
    <>
      {[...years.entries()].map(([year, months]) => (
        <Box key={year}>
          <ValueRow
            label={String(year)}
            count={countOf(tree, "date_year", String(year))}
            mark={markOf(spec, "date", { year })}
            onClick={() => onChange(cycleValue(spec, "date", { year }))}
            expand={months.length ? { open: !!open[year], onToggle: () => setOpen({ ...open, [year]: !open[year] }) } : undefined}
          />
          {open[year] &&
            (months as number[]).map((m) => (
              <ValueRow
                key={m}
                label={`${year}-${pad(m)}`}
                count={countOf(tree, "date_month", `${year}-${pad(m)}`)}
                mark={markOf(spec, "date", { year, month: m })}
                onClick={() => onChange(cycleValue(spec, "date", { year, month: m }))}
                depth={1}
              />
            ))}
        </Box>
      ))}
    </>
  );
}

function Collections({ tree, spec, onChange, search }: Props & { search: string }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const subs = new Map(tree.subcollections.map((c) => [c.id, c.subcollections]));
  type Collection = FilterTree["collections"][number];
  const row = (c: Collection, depth: number) => {
    const children = subs.get(c.id) ?? [];
    return (
      <Box key={c.id}>
        <ValueRow
          label={c.name}
          count={countOf(tree, "collection", String(c.id))}
          mark={markOf(spec, "collection", c.id)}
          onClick={() => onChange(cycleValue(spec, "collection", c.id))}
          depth={depth}
          expand={children.length ? { open: !!open[c.id] || !!search, onToggle: () => setOpen({ ...open, [c.id]: !open[c.id] }) } : undefined}
        />
        {(open[c.id] || !!search) &&
          children
            .filter((s) => matches(s.name, search) || matches(c.name, search))
            .map((s) => (
              <ValueRow
                key={s.id}
                label={s.name}
                count={countOf(tree, "subcollection", String(s.id))}
                mark={markOf(spec, "subcollection", s.id)}
                onClick={() => onChange(cycleValue(spec, "subcollection", s.id))}
                depth={depth + 1}
              />
            ))}
      </Box>
    );
  };
  return (
    <Letters
      items={tree.collections.filter((c) => matches(c.name, search) || (subs.get(c.id) ?? []).some((s) => matches(s.name, search)))}
      name={(c) => c.name}
      count={(c) => countOf(tree, "collection", String(c.id))}
      marked={(c) => markOf(spec, "collection", c.id) !== undefined || (subs.get(c.id) ?? []).some((s) => markOf(spec, "subcollection", s.id) !== undefined)}
      searching={!!search}
      render={row}
    />
  );
}

/** The Path section: each library, its folders' roots and the folders below
 * them; folders outside every library folder as a plain tree. */
function PathTree({ tree, spec, onChange, onFolderMenu }: Props) {
  const libs = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
  const t = useMemo(() => (libs.data ? libraryFolderTree(tree.folders, libs.data) : null), [tree.folders, libs.data]);
  if (!t) return <>{folderTree(tree.folders).map((n) => <Folder key={n.path} node={n} depth={0} tree={tree} spec={spec} onChange={onChange} onFolderMenu={onFolderMenu} />)}</>;
  return (
    <>
      {t.libraries.map((l) => <LibraryFolders key={l.id} lib={l} tree={tree} spec={spec} onChange={onChange} onFolderMenu={onFolderMenu} />)}
      {t.other.map((n) => <Folder key={n.path} node={n} depth={0} tree={tree} spec={spec} onChange={onChange} onFolderMenu={onFolderMenu} />)}
    </>
  );
}

/** A library in the Path section: a value of the Library facet, over its folders' roots. */
function LibraryFolders({ lib, tree, spec, onChange, onFolderMenu }: { lib: LibraryNode } & Props) {
  const [open, setOpen] = useState(true);
  return (
    <Box>
      <ValueRow
        label={<b>{lib.name}</b>}
        count={countOf(tree, "library", String(lib.id))}
        mark={markOf(spec, "library", lib.id)}
        onClick={() => onChange(cycleValue(spec, "library", lib.id))}
        expand={{ open, onToggle: () => setOpen(!open) }}
      />
      {open && lib.roots.map((n) => <Folder key={n.path} node={n} depth={1} tree={tree} spec={spec} onChange={onChange} onFolderMenu={onFolderMenu} />)}
    </Box>
  );
}

function Folder({ node, depth, tree, spec, onChange, onFolderMenu }: { node: FolderNode; depth: number } & Props) {
  const [open, setOpen] = useState(depth < 1);
  return (
    <Box>
      <ValueRow
        label={node.name}
        count={pathCount(tree, node.path)}
        mark={markOf(spec, "path", node.path)}
        onClick={() => onChange(cycleValue(spec, "path", node.path))}
        onContextMenu={onFolderMenu && ((e) => onFolderMenu(node.path, e))}
        depth={depth}
        expand={node.children.length ? { open, onToggle: () => setOpen(!open) } : undefined}
      />
      {open && node.children.map((c) => <Folder key={c.path} node={c} depth={depth + 1} tree={tree} spec={spec} onChange={onChange} onFolderMenu={onFolderMenu} />)}
    </Box>
  );
}
