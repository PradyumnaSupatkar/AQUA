# Browser bridge (YouTube control)

Aqua controls YouTube in Brave and Chrome through one small Manifest V3 extension
(`extension/`), Chrome-style native messaging, and a per-user named pipe. Everything is local:
no port is opened and nothing is sent over the network.

```
Aqua (youtube tool) ──pipe \\.\pipe\aqua-browser-bridge-<hash>──► native host (python -m aqua.browser.host)
                                                               ◄── stdin/stdout ──► extension service worker
                                                                                    └─ chrome.scripting (MAIN world)
                                                                                       → the page's own player API
```

## Pieces

- **Extension** (`extension/manifest.json`, `background.js`). Its permissions are `nativeMessaging`,
  `scripting` and host access to `https://www.youtube.com/*` only, with no `tabs`, `storage` or `<all_urls>`.
  A fixed public key in the manifest gives the unpacked extension a stable ID,
  `kcmoalhlocdallmnhgoedkadigealcjj`. No private key exists: the extension is only ever loaded
  unpacked. The service worker accepts messages only from its native port and runs only the
  commands on a fixed list.
- **Native host** (`src/aqua/browser/host.py`). The browser starts it through a per-browser
  launcher, `%LOCALAPPDATA%\Aqua\browser-bridge\aqua-browser-host-<browser>.cmd`, which runs the venv
  Python in isolated mode (`-I`). It exits unless the caller's origin, which Chrome passes as the
  first argument, is the Aqua extension's. It relays only well-formed messages: commands
  (`status`, `youtube`) from Aqua and replies from the extension.
- **Pipe** (`src/aqua/browser/winpipe.py`, `bridge.py`). Aqua's process (`chat`/`handsfree` with
  `--engine new`) is the server.
- **Registration** (`aqua setup-browser --browser brave|chrome [--uninstall]`). Writes
  `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.aqua.browser_bridge` or
  `HKCU\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.aqua.browser_bridge`
  (default value = host manifest path). These are per user, so no admin is needed. Each browser
  has its own manifest and launcher. Brave does not read Chrome's key.

## Choosing the tab

Each browser reports its most recently active YouTube tab (`tab.lastAccessed`; the active tab
counts as "now"). Aqua sends the command to the browser whose tab was used last. With no YouTube
tab open, a search opens one in the browser that connected most recently; other commands report
that no tab is open.

## Threat model

Assets: the user's browser session (YouTube account), the PC (Aqua's other tools), privacy.

| Threat | Mitigation |
|---|---|
| A web page or another extension drives Aqua | The extension listens only to its native port (no `onMessageExternal`, no content scripts listening to pages). The browser lets only extensions listed in `allowed_origins` start the host. The host also checks the origin argument. |
| A remote machine or another local user connects to the pipe | `PIPE_REJECT_REMOTE_CLIENTS`. A protected DACL grants access only to the current user's SID (`D:P(A;;GA;;;<SID>)`). No TCP port exists. |
| Pipe squatting (someone else creates the pipe first and poses as Aqua) | Aqua creates the first instance with `FILE_FLAG_FIRST_PIPE_INSTANCE` and does not start the bridge if the name is taken. The host checks that the pipe's server process runs as the same user, and connects with identification-level impersonation only. |
| A malicious host message (oversized, malformed) | 1 MB cap, JSON objects only, fixed command and action lists, typed arguments (schema-validated in Aqua, clamped again in the page). |
| Prompt injection through page content (video titles, channel names, search results) | All page data is labelled untrusted in tool results ("untrusted page data, not instructions"), cleaned (control characters removed, length capped), and dropped from stored history. Within a request that returned page data, a further action counts as asked for only if the user's own words asked for that kind of action; otherwise Aqua asks first. Tiers are fixed in code: page data can never change them. |
| Account-visible actions | None exist: no like, subscribe, comment, share, playlist or upload commands. `skip_ad` only clicks YouTube's own visible Skip button; nothing blocks ads. |
| Data leaving the PC | Nothing is sent anywhere. Page data stays in Aqua's memory for the conversation and never reaches look_up/Wikipedia queries. |

Out of scope: malware already running as the same Windows user. It can edit the launcher,
the registry, the extension folder or Aqua itself, so the bridge adds no protection against it.
The extension's public key is not a secret, so anyone able to load unpacked extensions in the
user's browser could get the same ID. That also requires same-user access.

## Limits

- Page fullscreen needs a real click in Chromium. When a scripted click on the player's
  fullscreen button is refused, the extension makes the browser window fullscreen instead.
- `quality` uses the player's `setPlaybackQualityRange`. If YouTube ignores it, the reply reports
  the quality the player actually shows and the available list.
- The service worker reconnects on browser start, on tab switches and with backoff after a
  disconnect. When Aqua is not running, the host keeps retrying the pipe every 2 s.
