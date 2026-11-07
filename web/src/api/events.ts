// The server's event stream (`/api/events`): library changes invalidate what
// the views show; job events update the job list. Reconnects with backoff.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import type { Schemas } from "./client";

export type ServerEvent = Schemas["ServerEvent"];
type Job = Schemas["JobInfo"];

/** The query keys a library change makes stale. */
export const LIBRARY_KEYS = ["ids", "rows", "tree", "model", "editor", "lookups", "libraries", "grid", "folders", "folder-cells"] as const;

/** What a change of `what: "rows"` makes stale: a cell outside the filter pane. */
const ROW_KEYS = ["ids", "rows", "model", "editor", "grid", "folder-cells"] as const;

/** The query keys to reload after the changes `whats`. */
export function staleKeys(whats: Set<string>): readonly string[] {
  return whats.size > 0 && [...whats].every((w) => w === "rows") ? ROW_KEYS : LIBRARY_KEYS;
}

/** How long library changes are gathered into one reload. */
export const RELOAD_DELAY = 300;

/** `run`, once, `ms` after the first of any number of calls. */
export function coalesced(run: () => void, ms: number): { call: () => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return {
    call: () => {
      timer ??= setTimeout(() => {
        timer = undefined;
        run();
      }, ms);
    },
    cancel: () => clearTimeout(timer),
  };
}

/** Every event, for components that follow one (a job's log lines). */
const bus = new EventTarget();

export function useServerEvent(handler: (e: ServerEvent) => void): void {
  useEffect(() => {
    const listener = (e: Event) => handler((e as CustomEvent<ServerEvent>).detail);
    bus.addEventListener("event", listener);
    return () => bus.removeEventListener("event", listener);
  }, [handler]);
}

/** What following the event stream reports. */
export interface StreamHandlers {
  event: (e: ServerEvent) => void;
  /** The stream is back after a break (a server restart, sleep, the network): what
   * happened meanwhile was missed. */
  reconnected: () => void;
}

/**
 * Follow the event stream at `url`, reconnecting with backoff. Returns what stops
 * following it. `open` makes the socket (tests give a fake one).
 */
export function followEvents(url: string, on: StreamHandlers, open: (url: string) => WebSocket = (u) => new WebSocket(u)): () => void {
  let socket: WebSocket | undefined;
  let retry = 1000;
  let stopped = false;
  let opened = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const connect = () => {
    socket = open(url);
    socket.onopen = () => {
      retry = 1000;
      if (opened) on.reconnected();
      opened = true;
    };
    socket.onmessage = (msg) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(msg.data as string) as ServerEvent;
      } catch {
        return;
      }
      on.event(event);
    };
    socket.onclose = () => {
      if (!stopped) {
        timer = setTimeout(connect, retry);
        retry = Math.min(retry * 2, 30000);
      }
    };
  };
  connect();
  return () => {
    stopped = true;
    clearTimeout(timer);
    socket?.close();
  };
}

/** Connect to the event stream (once, in the signed-in shell). */
export function useServerEvents(onEvent?: (e: ServerEvent) => void): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    // A burst of changes (a mass edit, another client typing) is one reload.
    const whats = new Set<string>();
    const reload = coalesced(() => {
      for (const key of staleKeys(whats)) void queryClient.invalidateQueries({ queryKey: [key] });
      whats.clear();
    }, RELOAD_DELAY);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const stop = followEvents(`${proto}://${location.host}/api/events`, {
      event: (event) => {
        if (event.type === "library_changed" || event.type === "resync") {
          whats.add(event.type === "resync" ? "resync" : event.what);
          reload.call();
        }
        if (event.type === "job") {
          // Progress arrives twice a second: patch the cached list instead of refetching.
          const job = event.job;
          queryClient.setQueryData<Job[]>(["jobs"], (old) =>
            old ? (old.some((j) => j.id === job.id) ? old.map((j) => (j.id === job.id ? job : j)) : [...old, job]) : old,
          );
        }
        if (event.type === "resync" || event.type === "jobs_cleared") void queryClient.invalidateQueries({ queryKey: ["jobs"] });
        onEvent?.(event);
        bus.dispatchEvent(new CustomEvent("event", { detail: event }));
      },
      // As for a resync: everything shown may be stale, and a restarted server
      // numbers its jobs from 1 again.
      reconnected: () => {
        whats.add("resync");
        reload.call();
        void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      },
    });
    return () => {
      stop();
      reload.cancel();
    };
  }, [queryClient, onEvent]);
}
