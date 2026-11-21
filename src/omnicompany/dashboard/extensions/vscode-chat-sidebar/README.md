# Omni Chat VSCode Extension

This extension embeds the omnicompany dashboard BOSS SIGHT cockpit in VSCode.

Mount points:

- Editor tab: command `Omni Chat: Open in Editor Tab`.
- Sidebar: Activity Bar `Omni Chat` view.

## Default Entry

The default webview URL is:

```text
http://127.0.0.1:8210/
```

That route renders the BOSS SIGHT cockpit. Legacy `/chat-standalone` is kept only for embedded iframe consumers and explicit compatibility use.

## Settings

- `omniChat.dashboardUrl`: optional override. Empty uses `http://127.0.0.1:<omniChat.dashboardPort>/`.
- `omniChat.dashboardPort`: default `8210`.
- `omniChat.daemonPort`: default `8201`.
- `omniChat.backendRoot`: optional omnicompany repo root override.
- `omniChat.autoStartBackend`: starts dashboard and ccdaemon when Omni Chat opens.

## Development

```bash
npm install
npm run compile
```

The extension does not bundle the dashboard UI. It supervises local backend processes and embeds the dashboard iframe, so cockpit/frontend changes live under `src/omnicompany/dashboard/frontend`.
