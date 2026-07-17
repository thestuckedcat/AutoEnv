(() => {
  "use strict";
  if (!/^https?:$/.test(location.protocol)) return;
  let active = false;
  let cursor = 0;
  let callbacks = null;
  let timer = null;

  async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    return response.json();
  }

  async function poll() {
    if (!active) return;
    try {
      const data = await api(`/api/events?cursor=${cursor}`);
      cursor = data.next;
      for (const event of data.events) {
        if (event.type === "output") callbacks.onOutput(event.text);
        if (event.type === "prompt") callbacks.onPrompt(event);
        if (event.type === "complete") {
          active = false;
          callbacks.onComplete({ success: event.success, status: event.status });
          return;
        }
      }
      timer = setTimeout(poll, 120);
    } catch (error) {
      active = false;
      callbacks.onOutput(`LOCAL BRIDGE ERROR: ${error.message}`);
      callbacks.onComplete({ success: false, status: "bridge_error" });
    }
  }

  async function probe() {
    try {
      await api("/api/health");
      window.AutoEnvBridge = {
        async listScripts() { return (await api("/api/scripts")).scripts; },
        async run(options) {
          callbacks = options;
          cursor = 0;
          await api("/api/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ script: options.script, mode: options.mode })
          });
          active = true;
          poll();
        },
        async submitPrompt(value) {
          await api("/api/input", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value })
          });
        },
        async stop() {
          active = false;
          clearTimeout(timer);
          await api("/api/stop", { method: "POST" });
        }
      };
      window.dispatchEvent(new Event("autoenv-bridge-ready"));
    } catch (_) {
      // A normal static web server has no bridge; app.js keeps Demo mode.
    }
  }
  probe();
})();
