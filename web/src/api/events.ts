// The server's event stream (`/api/events`): library changes invalidate what
// the views show; job events update the job list. Reconnects with backoff.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import type { Schemas } from "./client";

export type ServerEvent = Schemas["ServerEvent"];
type Job = Schemas["JobInfo"];

/** The query keys a library change makes stale. */
export const LIBRARY_KEYS = ["ids", "rows", "tree", "model", "editor", "lookups", "crawled-folders", "grid", "folders", "folder-cells"] as const;

/** Every event, for components that follow one (a job's log lines). */
const bus = new EventTarget();

export function useServerEvent(handler: (e: ServerEvent) => void): void {
  useEffect(() => {
    const listener = (e: Event) => handler((e as CustomEvent<ServerEvent>).detail);
    bus.addEventListener("event", listener);
    return () => bus.removeEventListener("event", listener);
  }, [handler]);
}

/** Connect to the event stream (once, in the signed-in shell). */
export function useServerEvents(onEvent?: (e: ServerEvent) => void): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    let socket: WebSocket | undefined;
    let retry = 1000;
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${location.host}/api/events`);
      socket.onopen = () => {
        retry = 1000;
      };
      socket.onmessage = (msg) => {
        const event = JSON.parse(msg.data as string) as ServerEvent;
        if (event.type === "library_changed" || event.type === "resync") {
          for (const key of LIBRARY_KEYS) {
            void queryClient.invalidateQueries({ queryKey: [key] });
          }
        }
        if (event.type === "job") {
          // Progress arrives twice a second: patch the cached list instead of refetching.
          const job = event.job;
          queryClient.setQueryData<Job[]>(["jobs"], (old) =>
            old ? (old.some((j) => j.id === job.id) ? old.map((j) => (j.id === job.id ? job : j)) : [...old, job]) : old,
          );
        }
        if (event.type === "resync") void queryClient.invalidateQueries({ queryKey: ["jobs"] });
        onEvent?.(event);
        bus.dispatchEvent(new CustomEvent("event", { detail: event }));
      };
      socket.onclose = () => {
        if (!closed) {
          timer = setTimeout(connect, retry);
          retry = Math.min(retry * 2, 30000);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [queryClient, onEvent]);
}
