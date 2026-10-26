// The About window (Help → About StlArchivist): the desktop app's version.
window.__TAURI__.app.getVersion().then((v) => {
  document.getElementById("version").textContent = `Version ${v} (desktop app)`;
});
