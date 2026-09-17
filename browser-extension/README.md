# Evanovar RAM Bridge (browser extension)

A Manifest V3 browser extension that links your browser to the Evanovar RAM
desktop application, so you can launch saved Roblox accounts into whatever page
you are looking at.

## What it does

| Action | What happens |
| :--- | :--- |
| **Account list** | Mirrors the desktop application: 22px circular avatar, account name, and the note after a `|` in the same gold the app uses. Invalid cookies are shown in amber italics and running accounts get a green corner dot. Groups are available as filter chips. |
| **Launch here** | Launches the selected account into the Place ID of the current tab. Private server links and job IDs in the URL are passed along. |
| **Launch selected** | Check any number of accounts and launch them all into the current Place ID in one request. Private server links are passed along; the application applies its configured launch delay between accounts. |
| **Join server** | Launches the selected account into the specific server (`gameInstanceId`) of the current tab. |
| **Join friend's game** | Resolves a username or user ID and joins their current game. On a profile page the user ID is filled in for you. |
| **Save logged-in account** | Reads the `.ROBLOSECURITY` cookie from this browser and imports the account into Evanovar RAM. |
| **Unlink** | Drops the local token and asks the application to revoke it. |

The popup also shows each account's running state, using the application's
process scan.

## Install

### Chrome, Edge, Brave or Opera

1. Open the extensions page (`chrome://extensions` or `edge://extensions`).
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this `browser-extension` folder.
4. Pin the extension so the popup is one click away.

### Firefox

1. Open `about:debugging#/runtime/this-firefox`.
2. Choose **Load Temporary Add-on** and select `manifest.json` in this folder.

Firefox loads it until the browser restarts unless you add
`"browser_specific_settings": { "gecko": { "id": "..." } }` and sign the add-on.

> The extension declares no toolbar icons, so the browser shows its default
> puzzle-piece icon. Add an `icons` block plus `action.default_icon` pointing at
> 16/48/128 px PNGs if you want an icon.

## Link it to the application

1. In Evanovar RAM, open **Settings** → **Developer**.
2. Turn on **Developer Mode** if it is not already on.
3. Turn on **Browser Extension**. This also starts the WebSocket server the
   extension talks to.
4. Click **Generate Pairing Code**. A code such as `K7M3-QP9X` appears and is
   valid for five minutes and a single use.
5. Open the extension popup, make sure the port matches the application
   (default `7963`), enter the code and click **Link extension**.

The application then issues a long-lived access token which the extension keeps
in its own extension storage. The token is stored in Evanovar RAM's encrypted
secure settings and is revoked when you click **Unlink** in either place, when
you turn off **Browser Extension**, or when you turn off Developer Mode.

## Permissions

| Permission | Why it is needed |
| :--- | :--- |
| `storage` | Keeps the port, token and last selected account. |
| `cookies` | Reads the `.ROBLOSECURITY` cookie so **Save logged-in account** can import it. HttpOnly cookies are only visible to extension APIs. |
| `activeTab` | Reads the Place ID, private server link, job ID and user ID of the tab you are on. |
| `http://localhost/*`, `http://127.0.0.1/*` | Lets the popup open a WebSocket to the application's local server. |
| `https://*.roblox.com/*` | Reads Roblox cookies and detects Roblox tabs. |

The extension talks only to `127.0.0.1`/`localhost` and to Roblox. It has no
background service worker, so nothing runs while the popup is closed.

## Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| "Could not connect to 127.0.0.1:7963" | Start Evanovar RAM, check **Developer Mode**, **Browser Extension** and **Enable WebSocket Server**, and confirm the port in the popup matches the port in the application. |
| "Timed out connecting" | Another program may own the port. Change the port in Developer settings and in the popup. |
| "Authentication failed" / "Not linked" | The token was revoked or the extension was disabled. Generate a new pairing code and link again. |
| Pairing code is rejected | The code expired after five minutes or was already used. Generate a new one. Five wrong attempts also invalidate a code. |
| The WebSocket scheme is rejected | Chrome match patterns only accept `http`/`https` schemes. The `http://127.0.0.1/*` entry covers the `ws://` connection to the same host and port; do not add `ws://` to `host_permissions`. |

## Protocol reference

The extension speaks the application's existing WebSocket protocol. Pairing
happens before authentication, everything else is sent as
`AUTH <token> | <command>`.

```
Pair <pairing_code>                            -> { token, app_version }
AUTH <token> | Ping                            -> "Pong"
AUTH <token> | AccountList                     -> { accounts, count, entries, groups }
AUTH <token> | GetStatus                       -> [ { pid, username, user_id } ]
AUTH <token> | Launch <account> <place_id> [private_server] [job_id]
AUTH <token> | MultiLaunch <place_id> [private_server] <account> [account ...]
AUTH <token> | JoinUser <account> <username|user_id>
AUTH <token> | Add <cookie>
AUTH <token> | Unpair
```

The application also exposes `SmallServer`, `JobId`, `SetNote`, `Delete`,
`Groups`, `SetGroup`, `Favorites`, `FavoriteAdd`, `FavoriteRemove`,
`RecentGames`, `KillAll`, `MultiRoblox`, `Session <list|start>`, `AutoRejoin`
and `Help`, so scripts can do everything the interface can. `Help` returns
every command with its usage and description.

Arguments use Python `shlex` rules, so arguments containing spaces or quotes are
wrapped in double quotes. The `Add` command is the exception: the application
reads everything after `Add` verbatim, so the cookie is sent unquoted.

`AccountList` keeps `accounts` as a plain sorted list of names for existing
script clients, and adds `entries` for display data plus the `groups` list:

```json
{
  "accounts": ["Alt", "My Account"],
  "count": 2,
  "groups": ["Main"],
  "entries": [
    {
      "name": "Alt",
      "note": "",
      "group": "Main",
      "user_id": 222,
      "avatar_url": "https://tr.rbxcdn.com/...",
      "cookie_valid": false,
      "added_date": "2026-01-01 00:00:00"
    }
  ]
}
```

Notes, groups and `avatar_url` (cached by the application) are read from the
saved account data. When `avatar_url` is empty the popup looks the headshot up
itself through the Roblox thumbnails API and caches the URL in extension
storage. Cookies and passwords are never included in any response.

## Files

```
manifest.json          Manifest V3 definition
src/popup.html         Popup markup
src/popup.css          Popup styling
src/popup.js           Popup controller
src/lib/bridge.js      WebSocket client with request/response matching
src/lib/roblox.js      Roblox URL parsing and shlex-safe command builders
```
