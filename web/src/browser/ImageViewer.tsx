// A model's images at full size: the original from the archive when the server
// can read it (the thumbnail shows meanwhile, and stays when it cannot).
// Arrow keys or the side buttons step through the images.
import { ActionIcon, Anchor, Box, Group, Loader, Modal, Text } from "@mantine/core";
import { IconChevronLeft, IconChevronRight, IconExternalLink } from "@tabler/icons-react";
import { useEffect, useState } from "react";

export interface ViewerImage {
  id: number;
  thumbnail: string;
}

/** The index `delta` steps from `index`, wrapping around `count`. */
export function step(index: number, delta: number, count: number): number {
  return count ? (((index + delta) % count) + count) % count : 0;
}

export const originalUrl = (id: number) => `/api/images/${id}/original`;

export function ImageViewer({ images, index, onIndex, onClose }: {
  images: ViewerImage[];
  index: number | null;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const img = index != null ? images[index] : undefined;
  const [state, setState] = useState<"loading" | "original" | "thumbnail">("loading");
  useEffect(() => setState("loading"), [img?.id]);
  useEffect(() => {
    if (index == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") onIndex(step(index, 1, images.length));
      if (e.key === "ArrowLeft") onIndex(step(index, -1, images.length));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, images.length, onIndex]);

  return (
    <Modal opened={img != null} onClose={onClose} fullScreen padding="xs"
      title={img && <Text size="sm" c="dimmed">{(index ?? 0) + 1} / {images.length}{state === "thumbnail" ? " · thumbnail (the original is not available)" : ""}</Text>}>
      {img && (
        <Group wrap="nowrap" gap="xs" h="calc(100vh - 70px)" data-testid="image-viewer">
          <ActionIcon variant="subtle" size="xl" onClick={() => onIndex(step(index!, -1, images.length))} disabled={images.length < 2} aria-label="Previous image">
            <IconChevronLeft />
          </ActionIcon>
          <Box style={{ flex: 1, height: "100%", position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
            {state !== "original" && (
              <img src={img.thumbnail} alt="" style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
            )}
            {state !== "thumbnail" && (
              <img
                key={img.id}
                src={originalUrl(img.id)}
                alt=""
                data-testid="original-image"
                style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", display: state === "original" ? "block" : "none" }}
                onLoad={() => setState("original")}
                onError={() => setState("thumbnail")}
              />
            )}
            {state === "loading" && <Loader size="sm" style={{ position: "absolute", top: 8, right: 8 }} />}
            {state === "original" && (
              <Anchor href={originalUrl(img.id)} target="_blank" size="xs" style={{ position: "absolute", top: 8, right: 8 }}>
                <IconExternalLink size={16} />
              </Anchor>
            )}
          </Box>
          <ActionIcon variant="subtle" size="xl" onClick={() => onIndex(step(index!, 1, images.length))} disabled={images.length < 2} aria-label="Next image">
            <IconChevronRight />
          </ActionIcon>
        </Group>
      )}
    </Modal>
  );
}
