// Help: in-depth usage and characteristics of StlArchivist, as topics with a
// searchable table of contents. A topic is addressed as `#/help/<id>`, so
// other pages can link to it (HelpLink).
import { ActionIcon, Alert, Anchor, Badge, Box, Code, Image, List, NavLink, ScrollArea, Stack, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconBulb, IconHelp, IconSearch } from "@tabler/icons-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { inline, searchTopics, topicFromHash, topicHash } from "./help";
import { type Block, GROUPS, type Topic, TOPICS } from "./topics";

/** Text with **bold**, `code` and links to other topics. */
function Rich({ text }: { text: string }) {
  return (
    <>
      {inline(text).map((s, i) =>
        s.kind === "bold" ? (
          <b key={i}>{s.text}</b>
        ) : s.kind === "code" ? (
          <Code key={i}>{s.text}</Code>
        ) : s.kind === "link" ? (
          <Anchor key={i} href={topicHash(s.topic)}>{s.text}</Anchor>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

function BlockView({ block }: { block: Block }): ReactNode {
  if ("h" in block) return <Title order={4} mt="sm">{block.h}</Title>;
  if ("img" in block) return <Image src={block.img} alt={block.alt} radius="md" />;
  if ("p" in block) return <Text size="sm" lh={1.65}><Rich text={block.p} /></Text>;
  if ("tip" in block) {
    return (
      <Alert variant="light" color="blue" icon={<IconBulb size={18} />} p="sm">
        <Text size="sm"><Rich text={block.tip} /></Text>
      </Alert>
    );
  }
  if ("warn" in block) {
    return (
      <Alert variant="light" color="orange" icon={<IconAlertTriangle size={18} />} p="sm">
        <Text size="sm"><Rich text={block.warn} /></Text>
      </Alert>
    );
  }
  if ("list" in block || "steps" in block) {
    const items = "list" in block ? block.list : block.steps;
    return (
      <List type={"steps" in block ? "ordered" : "unordered"} spacing={6} size="sm" withPadding>
        {items.map((it, i) => (
          <List.Item key={i}><Text size="sm" lh={1.6} span><Rich text={it} /></Text></List.Item>
        ))}
      </List>
    );
  }
  return (
    <Table.ScrollContainer minWidth={480}>
      <Table striped withTableBorder fz="sm" verticalSpacing={6}>
        <Table.Thead>
          <Table.Tr>{block.table.head.map((h, i) => <Table.Th key={i}>{h}</Table.Th>)}</Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {block.table.rows.map((r, i) => (
            <Table.Tr key={i}>{r.map((c, j) => <Table.Td key={j}><Rich text={c} /></Table.Td>)}</Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function TopicView({ topic }: { topic: Topic }) {
  const index = TOPICS.indexOf(topic);
  const prev = TOPICS[index - 1];
  const next = TOPICS[index + 1];
  return (
    <Stack gap="sm" maw={860} data-testid="help-topic">
      <Badge variant="light" size="sm">{topic.group}</Badge>
      <Title order={2}>{topic.title}</Title>
      <Text c="dimmed">{topic.summary}</Text>
      {topic.blocks.map((b, i) => <BlockView key={i} block={b} />)}
      <Box mt="lg" style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        {prev ? <Anchor href={topicHash(prev.id)} size="sm">← {prev.title}</Anchor> : <span />}
        {next && <Anchor href={topicHash(next.id)} size="sm">{next.title} →</Anchor>}
      </Box>
    </Stack>
  );
}

export function HelpPage() {
  const [topic, setTopic] = useState<Topic>(() => topicFromHash(location.hash));
  const [query, setQuery] = useState("");
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onHash = () => {
      setTopic(topicFromHash(location.hash));
      content.current?.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const found = useMemo(() => searchTopics(query), [query]);

  return (
    <div style={{ display: "flex", flexWrap: "wrap", height: "100%", minHeight: 0 }}>
      <Box p="md" style={{ flex: "1 1 260px", maxWidth: 320, minWidth: 0, borderRight: "1px solid var(--mantine-color-default-border)", display: "flex", flexDirection: "column", minHeight: 0, maxHeight: "100%" }}>
        <TextInput
          placeholder="Search the help"
          leftSection={<IconSearch size={14} />}
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          mb="sm"
          aria-label="Search the help"
          data-testid="help-search"
        />
        <ScrollArea style={{ flex: 1 }}>
          {query.trim() ? (
            found.length ? (
              found.map((t) => (
                <NavLink key={t.id} href={topicHash(t.id)} label={t.title} description={t.summary} active={t.id === topic.id} />
              ))
            ) : (
              <Text size="sm" c="dimmed">Nothing matches.</Text>
            )
          ) : (
            GROUPS.map((g) => (
              <Box key={g} mb="xs">
                <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="sm" mb={2}>{g}</Text>
                {TOPICS.filter((t) => t.group === g).map((t) => (
                  <NavLink key={t.id} href={topicHash(t.id)} label={t.title} active={t.id === topic.id} />
                ))}
              </Box>
            ))
          )}
        </ScrollArea>
      </Box>
      <div ref={content} style={{ flex: "999 1 480px", minWidth: 0, height: "100%", overflowY: "auto", padding: "var(--mantine-spacing-lg) var(--mantine-spacing-xl)" }}>
        <TopicView topic={topic} />
      </div>
    </div>
  );
}

/** A small "?" that opens the help at `topic`. */
export function HelpLink({ topic, label }: { topic: string; label?: string }) {
  return (
    <Tooltip label={label ?? "Help"}>
      <ActionIcon component="a" href={topicHash(topic)} variant="subtle" size="sm" aria-label={label ?? "Help"}>
        <IconHelp size={16} />
      </ActionIcon>
    </Tooltip>
  );
}
