// The stlLibrary desktop shell (rust/stl-desktop): when the web client runs in
// its window, the shell lets this page show folders in the file manager and run
// a compute worker for this server. Those are the only shell commands a
// server's page may call.

import type { ComponentsInfo } from "./jobs/components";

type Invoke =(cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

function shellInvoke(): Invoke | null {
  const internals = (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__;
  return typeof internals?.invoke === "function" ? internals.invoke : null;
}

/** Whether the page runs inside the desktop shell. */
export function inDesktopShell(): boolean {
  return shellInvoke() != null;
}

/** Show the folder holding `path` (an archive, or a folder) in the file manager. */
export async function openFolder(path: string): Promise<void> {
  const invoke = shellInvoke();
  if (!invoke) throw new Error("only the desktop app can open folders");
  await invoke("open_folder", { path });
}

/** The desktop app's compute worker, as this server's page sees it. */
export interface WorkerState {
  /** A worker runs for this server. */
  running: boolean;
  /** The server a worker runs for, when it is another one. */
  other_server: string | null;
  /** Where its CLIP encoder and AI model come from. */
  data_dir: string;
  /** Why it stopped (exit status and last log lines), when it did. */
  stopped: string | null;
}

async function workerCall(cmd: string, args?: Record<string, unknown>): Promise<WorkerState> {
  const invoke = shellInvoke();
  if (!invoke) throw new Error("only the desktop app can run a compute worker");
  return (await invoke(cmd, args)) as WorkerState;
}

/** The components in the worker's folder on this computer. */
export interface WorkerComponents {
  data_dir: string;
  info: ComponentsInfo;
}

/** The last install or removal in the worker's folder. */
export interface InstallState {
  running: boolean;
  /** Bytes done and in all. */
  progress: [number, number] | null;
  log: string[];
  done: string | null;
  error: string | null;
}

async function shellCall<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const invoke = shellInvoke();
  if (!invoke) throw new Error("only the desktop app can manage this computer's components");
  return (await invoke(cmd, args)) as T;
}

export const workerComponents = () => shellCall<WorkerComponents>("worker_components");
export const changeWorkerComponents = (action: "install" | "remove", components: string[], variants: Record<string, string> = {}) =>
  shellCall<InstallState>("change_worker_components", { action, components, variants });
export const workerInstallState = () => shellCall<InstallState>("worker_install_state");
/** Choose the worker's folder (a dialog in the shell); null when cancelled. */
export const pickWorkerFolder = () => shellCall<string | null>("pick_worker_folder");

export const workerState = () => workerCall("worker_state");
/** Run a worker for this server with `token` (from `POST /api/workers/token`). */
export const startWorker = (token: string) => workerCall("start_worker", { token });
export const stopWorker = () => workerCall("stop_worker");
