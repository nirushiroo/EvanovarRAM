"""
features/websocket_server.py
Websocket Server core logic.

Commands are case-insensitive and shlex-parsed. The full list, with usage and
descriptions, lives in WebSocketServer.COMMANDS and is served by `Help`:

  accounts   AccountList, Add, Delete, SetNote, Groups, SetGroup
  launching  Launch, MultiLaunch, JobId, JoinUser, SmallServer, KillAll,
             MultiRoblox, Session <list|start>
  options    AutoRejoin <start|stop>
  history    Favorites, FavoriteAdd, FavoriteRemove, RecentGames
  server     Ping, GetStatus, Help
  linking    Unpair

AccountList returns both a plain `accounts` name list and richer `entries`
(name, note, group, user_id, avatar_url, cookie_valid) plus `groups`.

Pairing (browser extension linking, before authentication):
  Pair <pairing_code>

Authentication (when websocket_require_password is true):
  AUTH <password> | <command>

The browser extension authenticates with its own token instead of the
WebSocket password:
  AUTH <extension_token> | <command>
"""

from __future__ import annotations

import asyncio
import json
import secrets
import shlex
import threading
import websockets
from typing import Callable
import features.account_actions as actions
import features.auto_rejoin as _ar
import features.favorites as favorites_mod
import features.groups as groups_mod
import features.presence as presence_mod
import features.sessions as sessions_mod
from classes.roblox_api import RobloxAPI


class WebSocketServer:
    #: command -> (usage, description). Help renders this and
    #: SUPPORTED_COMMANDS is derived from it so the two cannot drift.
    COMMANDS = {
        "AccountList": (
            "AccountList",
            "List saved accounts with notes, groups and avatars.",
        ),
        "Add": (
            "Add <cookie> [cookie2 ...]",
            "Import one or more Roblox security cookies.",
        ),
        "AutoRejoin": (
            "AutoRejoin <start|stop> <account>",
            "Start or stop Auto-Rejoin for a configured account.",
        ),
        "Delete": (
            "Delete <account>",
            "Remove a saved account.",
        ),
        "FavoriteAdd": (
            "FavoriteAdd <place_id> [name] [private_server]",
            "Save a game to favorites.",
        ),
        "FavoriteRemove": (
            "FavoriteRemove <place_id> [private_server]",
            "Remove a game from favorites.",
        ),
        "Favorites": (
            "Favorites",
            "List favorite games.",
        ),
        "GetStatus": (
            "GetStatus",
            "List running Roblox processes with their resolved accounts.",
        ),
        "Groups": (
            "Groups",
            "List groups and the account assigned to each.",
        ),
        "Help": (
            "Help [command]",
            "List every command, or show the usage for one command.",
        ),
        "JobId": (
            "JobId <account> <place_id> <job_id>",
            "Join a specific running server.",
        ),
        "JoinUser": (
            "JoinUser <account> <username|user_id>",
            "Join the game a user is currently playing.",
        ),
        "KillAll": (
            "KillAll",
            "Close every validated Roblox client.",
        ),
        "Launch": (
            "Launch <account> <place_id> [private_server] [job_id]",
            "Launch one account.",
        ),
        "MultiLaunch": (
            'MultiLaunch <place_id> [private_server] <account> [account ...]',
            "Launch several accounts with the configured launch delay.",
        ),
        "MultiRoblox": (
            "MultiRoblox <on|off> [default|handle64]",
            "Enable or disable Multi Roblox.",
        ),
        "Pair": (
            "Pair <pairing_code>",
            "Exchange a pairing code for a browser extension token.",
        ),
        "Ping": (
            "Ping",
            "Check that the server is reachable.",
        ),
        "RecentGames": (
            "RecentGames",
            "List recently launched games.",
        ),
        "Session": (
            "Session <list|start> [name]",
            "List saved launch profiles, or start one.",
        ),
        "SetGroup": (
            "SetGroup <account> <group|none>",
            "Assign an account to a group, or clear it with 'none'.",
        ),
        "SetNote": (
            "SetNote <account> <note|->",
            "Set an account note. Use '-' to clear it.",
        ),
        "SmallServer": (
            "SmallServer <account> <place_id>",
            "Join the least populated public server for a place.",
        ),
        "Unpair": (
            "Unpair",
            "Revoke the browser extension token.",
        ),
    }
    SUPPORTED_COMMANDS = list(COMMANDS)

    def __init__(
        self,
        manager: "RobloxAccountManager",
        ar_workers: dict,
        ar_configs: dict,
        get_settings: Callable[[], dict],
        refresh_ui_callback: Callable[[], None] | None = None,
        extension_bridge=None,
    ):
        self.manager = manager
        self._ar_workers = ar_workers
        self._ar_configs = ar_configs
        self._settings_fn = get_settings
        self._refresh_ui = refresh_ui_callback
        self._extension_bridge = extension_bridge

        self._thread: threading.Thread | None = None
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._async_stop: asyncio.Event | None = None
        self.running = False


    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="WebSocketServer"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._signal_async_stop)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._loop = None
        self.running = False

    def restart(self) -> None:
        self.stop()
        s = self._get_settings()
        if s.get("websocket_enabled") and s.get("developer_mode"):
            self.start()

    def _signal_async_stop(self) -> None:
        if self._async_stop is not None:
            self._async_stop.set()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._server_main())
        except Exception as exc:
            print(f"[ERROR] WebSocket server crashed: {exc}")
        finally:
            self.running = False
            self._loop = None
            try:
                loop.close()
            except Exception:
                pass

    async def _server_main(self) -> None:
        port = self._get_port()
        host = "localhost"
        try:
            async with websockets.serve(self._client_handler, host, port):
                self.running = True
                self._async_stop = asyncio.Event()
                print(f"[INFO] WebSocket server started at ws://{host}:{port}")
                if self._stop.is_set():
                    self._async_stop.set()
                await self._async_stop.wait()
        except OSError as exc:
            print(f"[ERROR] WebSocket server failed on port {port}: {exc}")
        finally:
            if self.running:
                print("[INFO] WebSocket server stopped")
            self._async_stop = None
            self.running = False

    async def _client_handler(self, websocket) -> None:
        try:
            async for raw in websocket:
                message = str(raw or "")
                max_len = self._get_max_message_len()
                if max_len > 0 and len(message) > max_len:
                    resp = {"ok": False, "error": f"Message too long (max {max_len} chars)"}
                else:
                    loop = asyncio.get_running_loop()
                    resp = await loop.run_in_executor(None, self._execute, message)
                await websocket.send(json.dumps(resp, ensure_ascii=False))
        except Exception as exc:
            print(f"[ERROR] WebSocket client error: {exc}")

    def _execute(self, raw: str) -> dict:
        message = str(raw or "").strip()
        if not message:
            return {"ok": False, "error": "Empty command"}

        # Pairing is handled before authentication so a new extension can
        # obtain its token without knowing the WebSocket password.
        parts = self._split(message)
        if parts and parts[0].lower() == "pair":
            try:
                return self._cmd_pair(parts)
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        ok, command, err = self._extract_auth(message)
        if not ok:
            return {"ok": False, "error": err}
        if not command.strip():
            return {"ok": False, "error": "Empty command"}
        parts = self._split(command)
        if not parts:
            return {"ok": False, "error": "Empty command"}

        action = parts[0].lower()
        try:
            handlers = {
                "ping":           lambda: {"ok": True, "result": "Pong"},
                "accountlist":    lambda: self._cmd_account_list(),
                "add":            lambda: self._cmd_add(command),
                "launch":         lambda: self._cmd_launch(parts),
                "multilaunch":    lambda: self._cmd_multi_launch(parts),
                "joinuser":       lambda: self._cmd_join_user(parts),
                "jobid":          lambda: self._cmd_job_id(parts),
                "smallserver":    lambda: self._cmd_small_server(parts),
                "autorejoin":     lambda: self._cmd_auto_rejoin(parts),
                "getstatus":      lambda: self._cmd_get_status(),
                "setnote":        lambda: self._cmd_set_note(parts),
                "delete":         lambda: self._cmd_delete(parts),
                "groups":         lambda: self._cmd_groups(),
                "setgroup":       lambda: self._cmd_set_group(parts),
                "favorites":      lambda: self._cmd_favorites(),
                "favoriteadd":    lambda: self._cmd_favorite_add(parts),
                "favoriteremove": lambda: self._cmd_favorite_remove(parts),
                "recentgames":    lambda: self._cmd_recent_games(),
                "killall":        lambda: self._cmd_kill_all(),
                "multiroblox":    lambda: self._cmd_multi_roblox(parts),
                "session":        lambda: self._cmd_session(parts),
                "help":           lambda: self._cmd_help(parts),
                "pair":           lambda: self._cmd_pair(parts),
                "unpair":         lambda: self._cmd_unpair(),
            }
            handler = handlers.get(action)
            if handler:
                return handler()
            return {
                "ok": False,
                "error": "Unknown command",
                "supported": self.SUPPORTED_COMMANDS,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _split(text: str) -> list:
        try:
            return shlex.split(text)
        except Exception:
            return str(text or "").split()

    def _extract_auth(self, raw: str):
        message = str(raw or "").strip()
        s = self._get_settings()
        req_pw = bool(s.get("websocket_require_password", False))

        lowered = message.lower()
        if not (lowered == "auth" or lowered.startswith("auth ")):
            if req_pw:
                return False, "", "Auth format: AUTH <password> | <command>"
            return True, message, None

        if "|" not in message:
            return False, "", "Auth format: AUTH <password> | <command>"

        auth_seg, cmd_seg = message.split("|", 1)
        auth_seg = auth_seg.strip()
        cmd_seg = cmd_seg.strip()

        auth_parts = self._split(auth_seg)
        if len(auth_parts) < 2 or auth_parts[0].lower() != "auth":
            return False, "", "Auth format: AUTH <password> | <command>"

        provided = " ".join(auth_parts[1:])
        if self._credential_valid(provided):
            return True, cmd_seg, None

        if req_pw and not self._get_password() and not self._get_extension_token():
            return False, "", "Password required but not configured in Developer settings"

        return False, "", "Authentication failed"

    def _credential_valid(self, provided: str) -> bool:
        """Accept the WebSocket password or a paired extension token."""
        provided = str(provided or "")
        if not provided:
            return False

        stored = self._get_password()
        if stored:
            try:
                if secrets.compare_digest(provided, stored):
                    return True
            except Exception:
                pass

        bridge = self._extension_bridge
        if bridge is not None:
            try:
                if bridge.verify_credential(provided):
                    return True
            except Exception:
                pass

        return False

    def _cmd_pair(self, parts: list) -> dict:
        if len(parts) < 2:
            return {"ok": False, "error": "Usage: Pair <pairing_code>"}

        if self._extension_bridge is None:
            return {"ok": False, "error": "Browser extension support is unavailable"}

        # Tolerate codes typed with spaces or dashes; the bridge normalizes them.
        result = self._extension_bridge.complete_pairing(" ".join(parts[1:]))
        if not result:
            return {
                "ok": False,
                "error": result.message or "Pairing failed",
                "code": result.code,
            }

        return {"ok": True, "result": {"action": "Pair", **(result.data or {})}}

    def _cmd_unpair(self) -> dict:
        if self._extension_bridge is None:
            return {"ok": False, "error": "Browser extension support is unavailable"}

        result = self._extension_bridge.revoke()
        if not result:
            return {
                "ok": False,
                "error": result.message or "Failed to unlink the extension",
                "code": result.code,
            }

        return {"ok": True, "result": {"action": "Unpair"}}

    def _cmd_account_list(self) -> dict:
        """Return account names plus the metadata the UI shows for each one.

        `accounts` stays a plain list of names so existing script clients keep
        working. `entries` carries the display data (note, group, avatar) used
        by the browser extension and any other rich client.
        """
        try:
            accounts = dict(self.manager.accounts)
        except Exception:
            accounts = {}

        assignments = self._get_group_assignments()
        entries = []
        for username in sorted(str(name) for name in accounts.keys()):
            data = accounts.get(username)
            data = data if isinstance(data, dict) else {}
            entries.append({
                "name": username,
                "note": str(data.get("note") or ""),
                "group": str(assignments.get(username) or ""),
                "user_id": data.get("user_id") or 0,
                "avatar_url": str(data.get("avatar_url") or ""),
                "cookie_valid": data.get("cookie_valid", data.get("valid")),
                "added_date": str(data.get("added_date") or ""),
            })

        names = [entry["name"] for entry in entries]
        return {
            "ok": True,
            "result": {
                "action": "AccountList",
                "accounts": names,
                "count": len(names),
                "entries": entries,
                "groups": self._get_group_names(),
            },
        }

    @staticmethod
    def _get_group_assignments() -> dict:
        try:
            return dict(groups_mod.get_assignments())
        except Exception:
            return {}

    @staticmethod
    def _get_group_names() -> list:
        try:
            return [str(name) for name in groups_mod.get_group_names()]
        except Exception:
            return []

    def _cmd_add(self, command_text: str) -> dict:
        payload = str(command_text or "").strip()
        if len(payload) <= 3:
            return {"ok": False, "error": "Usage: Add <cookie> [cookie2 ...]"}

        cookie_payload = payload[3:].strip()
        cookies = self._parse_cookies(cookie_payload)
        if not cookies:
            return {"ok": False, "error": "No cookies provided"}

        max_c = self._get_max_cookies()
        if len(cookies) > max_c:
            return {"ok": False, "error": f"Too many cookies (max {max_c})"}

        imported, failed = [], []
        for index, cookie in enumerate(cookies):
            if not cookie:
                failed.append({"index": index, "error": "Empty cookie"})
                continue
            if len(cookie) > 4096:
                failed.append({"index": index, "error": "Cookie too long"})
                continue
            try:
                result = self.manager.import_cookie_account_result(cookie)
                if result:
                    imported.append(str(result.data))
                else:
                    failed.append({
                        "index": index,
                        "error": result.message,
                        "code": result.code,
                    })
            except Exception as exc:
                failed.append({
                    "index": index,
                    "error": "Unexpected import error",
                    "detail": f"{type(exc).__name__}: {exc}",
                })

        if not imported:
            return {"ok": False, "error": "Failed to import any accounts", "failed": failed}

        if self._refresh_ui:
            try:
                self._refresh_ui()
            except Exception:
                pass

        print(f"[SUCCESS] WebSocket: imported {len(imported)} account(s)")
        return {
            "ok": True,
            "result": {
                "action": "Add",
                "imported": imported,
                "imported_count": len(imported),
                "failed_count": len(failed),
            },
            "failed": failed,
        }

    def _cmd_launch(self, parts: list) -> dict:
        if len(parts) < 3:
            return {"ok": False, "error": "Usage: Launch <account> <place_id> [private_server] [job_id]"}

        account = parts[1]
        place_id = str(parts[2]).strip()
        private_srv = str(parts[3]).strip() if len(parts) >= 4 else ""
        job_id = str(parts[4]).strip() if len(parts) >= 5 else ""

        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}
        if not place_id.isdigit():
            return {"ok": False, "error": "place_id must be numeric"}

        s = self._get_settings()
        launcher = s.get("roblox_launcher", "default")
        custom = s.get("custom_launcher_path", "")
        launched = self.manager.launch_roblox(account, place_id, private_srv, launcher, job_id, custom)

        if launched:
            print(f"[SUCCESS] WebSocket: launched {account} in place {place_id}")
            return {
                "ok": True,
                "result": {
                    "action": "Launch",
                    "account": account,
                    "place_id": place_id,
                    "private_server": private_srv,
                    "job_id": job_id,
                },
            }
        return {
            "ok": False,
            "error": launched.message or f"Failed to launch {account}",
            "code": launched.code or "ROBLOX_LAUNCH_FAILED",
            "detail": launched.detail,
        }

    def _cmd_join_user(self, parts: list) -> dict:
        if len(parts) < 3:
            return {"ok": False, "error": "Usage: JoinUser <account> <target_username>"}

        account = parts[1]
        target_user = parts[2]

        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}

        # Accept a numeric user ID so clients can join the profile they are on.
        if target_user.isdigit():
            user_id = int(target_user)
        else:
            user_id = RobloxAPI.get_user_id_from_username(target_user)
        if not user_id:
            return {"ok": False, "error": f"Roblox user not found: {target_user}"}

        acc_data = self.manager.accounts.get(account)
        cookie = acc_data.get("cookie") if isinstance(acc_data, dict) else None
        if not cookie:
            return {"ok": False, "error": f"No cookie for account: {account}"}

        presence = RobloxAPI.get_player_presence(user_id, cookie)
        if not presence:
            return {"ok": False, "error": "Failed to fetch player presence"}
        if not presence.get("in_game"):
            return {
                "ok": False,
                "error": f"{target_user} is not currently in a game",
                "status": presence.get("last_location", "Unknown"),
            }

        place_id = str(presence.get("place_id", "") or "")
        game_id = str(presence.get("game_id",  "") or "")
        if not place_id:
            return {"ok": False, "error": "Missing place_id in presence data"}

        s = self._get_settings()
        launcher = s.get("roblox_launcher", "default")
        custom = s.get("custom_launcher_path", "")
        launched = self.manager.launch_roblox(account, place_id, "", launcher, game_id, custom)

        if launched:
            print(f"[SUCCESS] WebSocket: {account} joined {target_user} in place {place_id}")
            return {
                "ok": True,
                "result": {
                    "action": "JoinUser",
                    "account": account,
                    "target_user": target_user,
                    "place_id": place_id,
                    "job_id": game_id,
                },
            }
        return {
            "ok": False,
            "error": launched.message or f"Failed to join {target_user} with {account}",
            "code": launched.code or "ROBLOX_LAUNCH_FAILED",
            "detail": launched.detail,
        }

    def _cmd_auto_rejoin(self, parts: list) -> dict:
        if len(parts) < 3:
            return {"ok": False, "error": "Usage: AutoRejoin <start|stop> <account>"}

        mode = parts[1].lower()
        account = parts[2]

        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}

        if mode == "start":
            if account not in self._ar_configs:
                return {"ok": False, "error": f"No auto-rejoin config for: {account}"}
            started, _ = _ar.arm_accounts(
                self.manager,
                [account],
                self._ar_configs,
                self._ar_workers,
                on_status=lambda name, status: print(f"[Auto-Rejoin] [{name}] {status}"),
            )
            if not started:
                return {"ok": False, "error": f"No auto-rejoin config for: {account}"}
            return {"ok": True, "result": {"action": "AutoRejoin", "mode": "start", "account": account}}

        if mode == "stop":
            worker = self._ar_workers.get(account)
            if worker:
                worker.stop()
                self._ar_workers.pop(account, None)
            return {"ok": True, "result": {"action": "AutoRejoin", "mode": "stop", "account": account}}

        return {"ok": False, "error": "mode must be 'start' or 'stop'"}

    def _cmd_get_status(self) -> dict:
        data = []
        try:
            pids = sorted(presence_mod.get_roblox_processes())
            for pid in pids:
                uid = presence_mod._get_user_id_from_pid(pid)
                if uid:
                    username = RobloxAPI.get_username_from_user_id(uid)
                    data.append({"pid": pid, "username": username or None, "user_id": uid})
                else:
                    data.append({"pid": pid, "username": None})
        except Exception as exc:
            print(f"[WARNING] GetStatus scan error: {exc}")
        return {"ok": True, "action": "GetStatus", "result": data}

    # ---- help and introspection ------------------------------------------

    def _cmd_help(self, parts: list) -> dict:
        if len(parts) >= 2:
            requested = str(parts[1]).strip().lower()
            for name, (usage, description) in self.COMMANDS.items():
                if name.lower() == requested:
                    return {
                        "ok": True,
                        "result": {
                            "action": "Help",
                            "command": name,
                            "usage": usage,
                            "description": description,
                        },
                    }
            return {"ok": False, "error": f"Unknown command: {parts[1]}"}

        return {
            "ok": True,
            "result": {
                "action": "Help",
                "commands": [
                    {"command": name, "usage": usage, "description": description}
                    for name, (usage, description) in self.COMMANDS.items()
                ],
            },
        }

    # ---- launching -------------------------------------------------------

    def _cmd_multi_launch(self, parts: list) -> dict:
        if len(parts) < 3:
            return {
                "ok": False,
                "error": 'Usage: MultiLaunch <place_id> [private_server|""] <account> [account ...]',
            }

        place_id = str(parts[1]).strip()
        if not place_id.isdigit():
            return {"ok": False, "error": "place_id must be numeric"}

        rest = list(parts[2:])
        private_server = ""
        # A private server is either empty or a URL, and account names never
        # contain "/", so anything else must be the first account.
        if rest and (rest[0] == "" or "/" in rest[0] or rest[0].lower().startswith("http")):
            private_server = rest[0]
            rest = rest[1:]

        accounts = [name for name in rest if name in self.manager.accounts]
        missing = [name for name in rest if name not in self.manager.accounts]
        if not accounts:
            return {
                "ok": False,
                "error": "No known accounts were given",
                "missing": missing,
            }

        outcome = self._await_callback(
            lambda on_done: actions.join_place_all(
                self.manager, accounts, place_id, private_server, on_done
            ),
            timeout=self._bulk_timeout(len(accounts)),
        )

        result = outcome["result"] if outcome else None
        # Note: an OperationResult for a failure is falsy, so test for None.
        message = (
            result.message
            if result is not None and result.message
            else "Launching continues in the background."
        )
        payload = {
            "action": "MultiLaunch",
            "place_id": place_id,
            "private_server": private_server,
            "accounts": accounts,
            "missing": missing,
            "completed": outcome is not None,
            "message": message,
        }

        if outcome is None:
            return {"ok": True, "result": payload}
        if not outcome["ok"]:
            return {
                "ok": False,
                "error": message,
                "code": result.code or "ROBLOX_LAUNCH_FAILED",
                "detail": result.detail,
                "accounts": accounts,
                "missing": missing,
            }

        print(f"[SUCCESS] WebSocket: launched {len(accounts)} account(s) in place {place_id}")
        return {"ok": True, "result": payload}

    def _cmd_small_server(self, parts: list) -> dict:
        if len(parts) < 3:
            return {"ok": False, "error": "Usage: SmallServer <account> <place_id>"}

        account = parts[1]
        place_id = str(parts[2]).strip()
        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}
        if not place_id.isdigit():
            return {"ok": False, "error": "place_id must be numeric"}

        outcome = self._await_callback(
            lambda on_done: actions.join_small_server(
                self.manager, [account], place_id, on_done
            ),
            timeout=120.0,
        )
        if outcome is None:
            return {
                "ok": False,
                "error": "Timed out looking for a small server",
                "code": "SMALL_SERVER_TIMEOUT",
                "retryable": True,
            }
        if not outcome["ok"]:
            result = outcome["result"]
            return {
                "ok": False,
                "error": result.message or "Failed to join a small server",
                "code": result.code or "ROBLOX_LAUNCH_FAILED",
                "detail": result.detail,
            }
        return {
            "ok": True,
            "result": {
                "action": "SmallServer",
                "account": account,
                "place_id": place_id,
                "message": outcome["result"].message,
            },
        }

    def _cmd_job_id(self, parts: list) -> dict:
        if len(parts) < 4:
            return {"ok": False, "error": "Usage: JobId <account> <place_id> <job_id>"}

        account = parts[1]
        place_id = str(parts[2]).strip()
        job_id = str(parts[3]).strip()

        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}
        if not place_id.isdigit():
            return {"ok": False, "error": "place_id must be numeric"}

        s = self._get_settings()
        launcher = s.get("roblox_launcher", "default")
        custom = s.get("custom_launcher_path", "")
        launched = self.manager.launch_roblox(account, place_id, "", launcher, job_id, custom)

        if launched:
            print(f"[SUCCESS] WebSocket: {account} joined server {job_id}")
            return {
                "ok": True,
                "result": {
                    "action": "JobId",
                    "account": account,
                    "place_id": place_id,
                    "job_id": job_id,
                },
            }
        return {
            "ok": False,
            "error": launched.message or f"Failed to join server with {account}",
            "code": launched.code or "ROBLOX_LAUNCH_FAILED",
            "detail": launched.detail,
        }

    def _cmd_kill_all(self) -> dict:
        result = actions.kill_roblox()
        if not result:
            return {
                "ok": False,
                "error": result.message or "Roblox clients could not be closed",
                "code": result.code or "ROBLOX_KILL_FAILED",
                "detail": result.detail,
            }
        print("[SUCCESS] WebSocket: closed all Roblox clients")
        return {"ok": True, "result": {"action": "KillAll", "message": result.message}}

    def _cmd_multi_roblox(self, parts: list) -> dict:
        if len(parts) < 2:
            return {"ok": False, "error": "Usage: MultiRoblox <on|off> [default|handle64]"}

        mode = str(parts[1]).strip().lower()
        method = str(parts[2]).strip().lower() if len(parts) >= 3 else "default"
        if method not in ("default", "handle64"):
            return {"ok": False, "error": "method must be 'default' or 'handle64'"}

        if mode == "on":
            ok, message = actions.enable_multi_roblox(method)
            if not ok:
                return {
                    "ok": False,
                    "error": message or "Multi Roblox could not be enabled",
                    "code": "MULTI_ROBLOX_FAILED",
                }
            return {
                "ok": True,
                "result": {
                    "action": "MultiRoblox",
                    "mode": "on",
                    "method": method,
                    "running": bool(actions.is_multi_roblox_running(method)),
                },
            }

        if mode == "off":
            actions.disable_multi_roblox()
            return {
                "ok": True,
                "result": {"action": "MultiRoblox", "mode": "off", "method": method},
            }

        return {"ok": False, "error": "mode must be 'on' or 'off'"}

    # ---- account mutations -----------------------------------------------

    def _cmd_set_note(self, parts: list) -> dict:
        if len(parts) < 2:
            return {"ok": False, "error": "Usage: SetNote <account> <note|->"}

        account = parts[1]
        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}

        note = " ".join(str(part) for part in parts[2:]).strip()
        if note in ("-", "--", "none"):
            note = ""

        actions.set_note(self.manager, account, note)
        self._refresh()
        print(f"[SUCCESS] WebSocket: updated note for {account}")
        return {
            "ok": True,
            "result": {"action": "SetNote", "account": account, "note": note},
        }

    def _cmd_delete(self, parts: list) -> dict:
        if len(parts) < 2:
            return {"ok": False, "error": "Usage: Delete <account>"}

        account = parts[1]
        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}

        deleted, error = actions.remove_account(self.manager, account)
        if not deleted:
            return {"ok": False, "error": error or f"Failed to delete {account}"}

        self._refresh()
        print(f"[SUCCESS] WebSocket: deleted account {account}")
        return {"ok": True, "result": {"action": "Delete", "account": account}}

    def _cmd_groups(self) -> dict:
        return {
            "ok": True,
            "result": {
                "action": "Groups",
                "groups": self._get_group_names(),
                "assignments": self._get_group_assignments(),
            },
        }

    def _cmd_set_group(self, parts: list) -> dict:
        if len(parts) < 3:
            return {"ok": False, "error": "Usage: SetGroup <account> <group|none>"}

        account = parts[1]
        if account not in self.manager.accounts:
            return {"ok": False, "error": f"Account not found: {account}"}

        group = " ".join(str(part) for part in parts[2:]).strip()
        try:
            if group.lower() in ("none", "-", ""):
                groups_mod.set_account_group(account, None)
                group = ""
            else:
                if group not in groups_mod.get_group_names():
                    groups_mod.create_group(group)
                groups_mod.set_account_group(account, group)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        self._refresh()
        return {
            "ok": True,
            "result": {"action": "SetGroup", "account": account, "group": group},
        }

    # ---- favourites and history ------------------------------------------

    def _cmd_favorites(self) -> dict:
        try:
            favorites = favorites_mod.load_favorites()
        except Exception:
            favorites = []
        return {
            "ok": True,
            "result": {"action": "Favorites", "favorites": favorites, "count": len(favorites)},
        }

    def _cmd_favorite_add(self, parts: list) -> dict:
        if len(parts) < 2:
            return {
                "ok": False,
                "error": "Usage: FavoriteAdd <place_id> [name] [private_server]",
            }

        place_id = str(parts[1]).strip()
        if not place_id.isdigit():
            return {"ok": False, "error": "place_id must be numeric"}

        name = str(parts[2]).strip() if len(parts) >= 3 else ""
        private_server = str(parts[3]).strip() if len(parts) >= 4 else ""
        favorites_mod.add_favorite(place_id, name or place_id, private_server)
        return {
            "ok": True,
            "result": {
                "action": "FavoriteAdd",
                "place_id": place_id,
                "name": name or place_id,
                "private_server": private_server,
            },
        }

    def _cmd_favorite_remove(self, parts: list) -> dict:
        if len(parts) < 2:
            return {"ok": False, "error": "Usage: FavoriteRemove <place_id> [private_server]"}

        place_id = str(parts[1]).strip()
        private_server = str(parts[2]).strip() if len(parts) >= 3 else ""
        favorites_mod.remove_favorite(place_id, private_server)
        return {
            "ok": True,
            "result": {
                "action": "FavoriteRemove",
                "place_id": place_id,
                "private_server": private_server,
            },
        }

    def _cmd_recent_games(self) -> dict:
        try:
            games = list(actions.load_recent_games())
        except Exception:
            games = []
        return {
            "ok": True,
            "result": {"action": "RecentGames", "games": games, "count": len(games)},
        }

    # ---- launch profiles -------------------------------------------------

    def _cmd_session(self, parts: list) -> dict:
        mode = str(parts[1]).strip().lower() if len(parts) >= 2 else "list"

        if mode in ("list", "ls"):
            sessions = sessions_mod.load_sessions()
            return {
                "ok": True,
                "result": {
                    "action": "Session",
                    "mode": "list",
                    "names": [str(s.get("name")) for s in sessions],
                    "sessions": sessions,
                },
            }

        if mode != "start":
            return {"ok": False, "error": "Usage: Session <list|start> [name]"}

        if len(parts) < 3:
            return {"ok": False, "error": "Usage: Session start <name>"}

        name = " ".join(str(part) for part in parts[2:]).strip()
        session = sessions_mod.get_session(name)
        if not session:
            return {"ok": False, "error": f"Session not found: {name}"}

        configured = list(session.get("accounts") or [])
        accounts = [a for a in configured if a in self.manager.accounts]
        missing = [a for a in configured if a not in self.manager.accounts]
        if not accounts:
            return {
                "ok": False,
                "error": f"No launchable accounts in session: {name}",
                "missing": missing,
            }

        launch_target = dict(session)
        launch_target["accounts"] = accounts

        outcome = self._await_callback(
            lambda on_done: actions.start_session(self.manager, launch_target, on_done),
            timeout=self._bulk_timeout(len(accounts)),
        )

        armed, ar_skipped = [], []
        if session.get("auto_rejoin"):
            armed, ar_skipped = _ar.arm_accounts(
                self.manager,
                accounts,
                self._ar_configs,
                self._ar_workers,
                on_status=lambda acct, status: print(f"[Auto-Rejoin] [{acct}] {status}"),
            )
        if session.get("anti_afk"):
            actions.start_anti_afk_from_settings()

        result = outcome["result"] if outcome else None
        # Note: an OperationResult for a failure is falsy, so test for None.
        message = (
            result.message
            if result is not None and result.message
            else "Launching continues in the background."
        )
        payload = {
            "action": "Session",
            "mode": "start",
            "name": name,
            "accounts": accounts,
            "missing": missing,
            "completed": outcome is not None,
            "auto_rejoin_armed": armed,
            "auto_rejoin_skipped": ar_skipped,
            "anti_afk": bool(session.get("anti_afk")),
            "message": message,
        }

        if outcome is None:
            return {"ok": True, "result": payload}
        if not outcome["ok"]:
            return {
                "ok": False,
                "error": message,
                "code": result.code or "SESSION_START_FAILED",
                "detail": result.detail,
                "accounts": accounts,
                "missing": missing,
            }

        print(f"[SUCCESS] WebSocket: started session '{name}'")
        return {"ok": True, "result": payload}

    # ---- shared helpers --------------------------------------------------

    def _refresh(self) -> None:
        """Ask the UI to reload the account list after a mutation."""
        if self._refresh_ui is None:
            return
        try:
            self._refresh_ui()
        except Exception:
            pass

    @staticmethod
    def _bulk_timeout(count: int) -> float:
        # Launching a Roblox client takes a few seconds, and the configured
        # delay is added between accounts, so scale with the batch size.
        return max(60.0, min(900.0, 30.0 + 20.0 * max(1, int(count))))

    @staticmethod
    def _await_callback(call, timeout: float):
        """Run a callback-style feature helper and wait for its result.

        The launch helpers report through on_done(bool, OperationResult) on a
        worker thread. Blocking here keeps command responses synchronous, like
        the single-account Launch command. Returns None on timeout.
        """
        finished = threading.Event()
        box: dict = {}

        def _on_done(ok, result):
            box["ok"] = bool(ok)
            box["result"] = result
            finished.set()

        call(_on_done)
        if not finished.wait(timeout):
            return None
        return box

    def _get_settings(self) -> dict:
        try:
            return self._settings_fn()
        except Exception:
            return {}

    def _get_port(self) -> int:
        try:
            return int(self._get_settings().get("websocket_port", 7963))
        except Exception:
            return 7963

    def _get_max_message_len(self) -> int:
        try:
            return max(0, int(self._get_settings().get("websocket_max_message_length", 4096)))
        except Exception:
            return 4096

    def _get_max_cookies(self) -> int:
        try:
            return max(1, int(self._get_settings().get("websocket_max_add_cookies", 25)))
        except Exception:
            return 25

    def _get_password(self) -> str:
        try:
            return str(self.manager.get_secure_setting("websocket_password", "") or "")
        except Exception:
            return ""

    def _get_extension_token(self) -> str:
        bridge = self._extension_bridge
        if bridge is None:
            return ""
        try:
            return "" if not bridge.is_enabled() else str(bridge.get_token() or "")
        except Exception:
            return ""

    @staticmethod
    def _parse_cookies(text: str) -> list[str]:
        text = str(text or "").strip()
        if not text:
            return []
        if "_|WARNING:-" in text:
            parts = text.split("_|WARNING:-")
            return ["_|WARNING:-" + p.strip() for p in parts if p.strip()]
        return [c.strip() for c in text.split() if c.strip()]
