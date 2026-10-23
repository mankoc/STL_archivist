// The launcher: remembered servers and the local library (Tauri commands in
// stl-desktop/src/main.rs). Library data never passes through here.
const invoke = window.__TAURI__.core.invoke;
const $ = (id) => document.getElementById(id);

function status(text, error = false) {
  const el = $("status");
  el.textContent = text;
  el.className = error ? "error" : "";
}

async function busy(button, label, work) {
  const old = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    await work();
  } catch (err) {
    status(String(err), true);
  } finally {
    button.disabled = false;
    button.textContent = old;
  }
}

let servers = [];

function render() {
  const list = $("servers");
  list.replaceChildren();
  if (!servers.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No servers yet.";
    list.append(li);
  }
  servers.forEach((s, i) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = s.name || s.url;
    const url = document.createElement("span");
    url.className = "url";
    url.textContent = s.name ? s.url : "";
    const go = document.createElement("button");
    go.textContent = "Connect";
    go.addEventListener("click", () => busy(go, "Connecting…", async () => {
      status(`Connecting to ${s.url}…`);
      await invoke("connect", { url: s.url });
    }));
    const remove = document.createElement("button");
    remove.className = "link";
    remove.textContent = "Remove";
    remove.addEventListener("click", async () => {
      servers = servers.filter((_, k) => k !== i);
      await invoke("save_servers", { servers });
      render();
    });
    li.append(name, url, go, remove);
    list.append(li);
  });
}

async function init() {
  servers = await invoke("list_servers");
  render();
  const local = await invoke("local_state");
  $("data-dir").value = local.running || local.last || (await invoke("default_data_dir"));
  if (local.running) $("local-note").textContent = `A local server is running on ${local.running}.`;
}

$("browse").addEventListener("click", async () => {
  const dir = await invoke("pick_folder");
  if (dir) $("data-dir").value = dir;
});

$("open-local").addEventListener("click", (e) => busy(e.currentTarget, "Starting…", async () => {
  status("Starting the local library… (the first start of an older library backs it up and converts it, which can take several minutes)");
  await invoke("start_local", { dataDir: $("data-dir").value });
}));

$("add").addEventListener("submit", (e) => {
  e.preventDefault();
  const button = e.currentTarget.querySelector("button");
  busy(button, "Connecting…", async () => {
    const url = $("url").value.trim();
    status(`Connecting to ${url}…`);
    const entry = { name: $("name").value.trim(), url };
    await invoke("save_servers", { servers: [...servers.filter((s) => s.url !== url), entry] });
    servers = await invoke("list_servers");
    render();
    await invoke("connect", { url });
  });
});

init().catch((err) => status(String(err), true));
