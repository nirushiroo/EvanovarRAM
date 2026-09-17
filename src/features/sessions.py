"""
features/sessions.py
Saved launch profiles: a named place, account set, launcher and options.

A session is the whole "farming setup" in one entry - where to launch, which
accounts, how long to wait between them, and whether Auto-Rejoin and Anti-AFK
should be armed when it starts. Storage mirrors features/groups.py: a cached
dict guarded by a lock and written atomically.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Optional

from utils.app_paths import get_data_dir

_SESSIONS_FILE = os.path.join(get_data_dir(), "sessions.json")
_LOCK = threading.RLock()
_CACHE: Optional[dict] = None

DEFAULT_DELAY_SECONDS = 0.5
MAX_DELAY_SECONDS = 300.0


def _default_data() -> dict:
    return {"sessions": []}


def _coerce_delay(value) -> float:
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DELAY_SECONDS
    if delay != delay: # NaN
        return DEFAULT_DELAY_SECONDS
    return max(0.0, min(MAX_DELAY_SECONDS, delay))


def normalize_session(session) -> Optional[dict]:
    """Return a clean session dict, or None when the entry is unusable."""
    if not isinstance(session, dict):
        return None

    name = str(session.get("name") or "").strip()
    if not name:
        return None

    accounts = session.get("accounts")
    if isinstance(accounts, str):
        accounts = [accounts]
    if not isinstance(accounts, list):
        accounts = []

    launcher = str(session.get("launcher") or "").strip() or "default"

    return {
        "name": name,
        "place_id": str(session.get("place_id") or "").strip(),
        "private_server": str(session.get("private_server") or "").strip(),
        "job_id": str(session.get("job_id") or "").strip(),
        "accounts": [str(account) for account in accounts if str(account).strip()],
        "launcher": launcher,
        "custom_launcher_path": str(session.get("custom_launcher_path") or "").strip(),
        "delay_seconds": _coerce_delay(session.get("delay_seconds", DEFAULT_DELAY_SECONDS)),
        "auto_rejoin": bool(session.get("auto_rejoin", False)),
        "anti_afk": bool(session.get("anti_afk", False)),
        "created_at": str(
            session.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S")
        ),
    }


def _load() -> dict:
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return {"sessions": list(_CACHE.get("sessions", []))}

        data = _default_data()
        if os.path.exists(_SESSIONS_FILE):
            try:
                with open(_SESSIONS_FILE, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError, TypeError):
                pass

        sessions = []
        for entry in data.get("sessions", []) or []:
            normalized = normalize_session(entry)
            if normalized:
                sessions.append(normalized)

        _CACHE = {"sessions": sessions}
        return {"sessions": list(sessions)}


def _save(data: dict) -> None:
    global _CACHE
    with _LOCK:
        normalized = {"sessions": list(data.get("sessions", []))}
        if _CACHE == normalized:
            return
        os.makedirs(get_data_dir(), exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".sessions.", suffix=".tmp", dir=get_data_dir()
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(normalized, handle, indent=2)
            os.replace(temp_path, _SESSIONS_FILE)
            _CACHE = normalized
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                os.remove(temp_path)
            except OSError:
                pass


def load_sessions() -> list[dict]:
    return [dict(session) for session in _load().get("sessions", [])]


def session_names() -> list[str]:
    return [str(session.get("name")) for session in _load().get("sessions", [])]


def get_session(name: str) -> Optional[dict]:
    key = str(name or "").strip()
    if not key:
        return None
    for session in _load().get("sessions", []):
        if str(session.get("name")) == key:
            return dict(session)
    return None


def save_session(session) -> bool:
    """Create or replace a session by name. Returns False when invalid."""
    normalized = normalize_session(session)
    if not normalized:
        return False

    data = _load()
    sessions = [
        existing
        for existing in data.get("sessions", [])
        if str(existing.get("name")) != normalized["name"]
    ]
    sessions.append(normalized)
    sessions.sort(key=lambda entry: str(entry.get("name", "")).casefold())
    data["sessions"] = sessions
    _save(data)
    return True


def delete_session(name: str) -> bool:
    key = str(name or "").strip()
    if not key:
        return False
    data = _load()
    sessions = data.get("sessions", [])
    remaining = [s for s in sessions if str(s.get("name")) != key]
    if len(remaining) == len(sessions):
        return False
    data["sessions"] = remaining
    _save(data)
    return True


def rename_session(old_name: str, new_name: str) -> bool:
    old_key = str(old_name or "").strip()
    new_key = str(new_name or "").strip()
    if not old_key or not new_key or old_key == new_key:
        return False

    data = _load()
    sessions = data.get("sessions", [])
    target = None
    for session in sessions:
        if str(session.get("name")) == new_key:
            return False
        if str(session.get("name")) == old_key:
            target = session
    if target is None:
        return False

    target["name"] = new_key
    data["sessions"] = sessions
    _save(data)
    return True
