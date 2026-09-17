"""
features/extension_bridge.py
Browser extension pairing and access token management.

Pairing flow:
  1. The user generates a short-lived pairing code in
     Settings -> Developer -> Browser Extension.
  2. The extension sends `Pair <code>` to the local WebSocket server.
  3. A high-entropy token is issued, stored in the encrypted secure settings,
     and kept by the extension in its own local storage.
  4. Later commands authenticate with `AUTH <token> | <command>`.

The token is only accepted while browser extension support stays enabled, so
unchecking the setting immediately disconnects a previously linked extension.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Callable

from classes.operation_result import OperationResult
from utils.version import APP_VERSION

TOKEN_SETTING_KEY = "extension_token"

PAIRING_TTL_SECONDS = 300
PAIRING_MAX_ATTEMPTS = 5
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789" # no I, O, 0 or 1
_GROUP_SIZE = 4
_CODE_GROUPS = 2
_TOKEN_BYTES = 32


def _format_code(raw: str) -> str:
    return "-".join(
        raw[index:index + _GROUP_SIZE]
        for index in range(0, len(raw), _GROUP_SIZE)
    )


def _normalize_code(value: str) -> str:
    text = str(value or "").upper()
    return "".join(ch for ch in text if ch in _CODE_ALPHABET)


class ExtensionBridge:
    """Issues, stores and validates the browser extension access token."""

    def __init__(
        self,
        manager: "RobloxAccountManager",
        get_settings: Callable[[], dict],
        on_change: Callable[[dict], None] | None = None,
    ):
        self.manager = manager
        self._settings_fn = get_settings
        self._on_change = on_change

        self._lock = threading.RLock()
        self._code: str = ""
        self._expires_at: float = 0.0
        self._attempts: int = 0

    # ---- state -----------------------------------------------------------

    def is_enabled(self) -> bool:
        """Browser extension support is on and Developer Mode is still enabled."""
        try:
            settings = self._settings_fn()
        except Exception:
            return False
        return bool(settings.get("extension_enabled", False)) and bool(
            settings.get("developer_mode", False)
        )

    def _read_token(self) -> str:
        try:
            return str(self.manager.get_secure_setting(TOKEN_SETTING_KEY, "") or "")
        except Exception:
            return ""

    def get_token(self) -> str:
        """Return the active token, or an empty string when unavailable."""
        if not self.is_enabled():
            return ""
        return self._read_token()

    def is_paired(self) -> bool:
        return bool(self._read_token())

    def verify_credential(self, credential: str) -> bool:
        token = self.get_token()
        if not token:
            return False
        try:
            return secrets.compare_digest(str(credential or ""), token)
        except Exception:
            return False

    def pairing_state(self) -> dict:
        with self._lock:
            active = bool(self._code) and time.time() < self._expires_at
            remaining = int(max(0.0, self._expires_at - time.time())) if active else 0
            code = _format_code(self._code) if active else ""
        return {
            "enabled": self.is_enabled(),
            "paired": self.is_paired(),
            "pairing_active": active,
            "pairing_code": code,
            "seconds_remaining": remaining,
        }

    # ---- pairing ---------------------------------------------------------

    def begin_pairing(self) -> OperationResult:
        if not self.is_enabled():
            return OperationResult.failure(
                "EXTENSION_DISABLED",
                "Browser Extension Support Is Off",
                "Enable browser extension support before generating a pairing code.",
            )

        raw = "".join(
            secrets.choice(_CODE_ALPHABET)
            for _ in range(_GROUP_SIZE * _CODE_GROUPS)
        )
        with self._lock:
            self._code = raw
            self._expires_at = time.time() + PAIRING_TTL_SECONDS
            self._attempts = 0

        code = _format_code(raw)
        print(
            "[INFO] Browser extension pairing code generated "
            f"(valid for {PAIRING_TTL_SECONDS // 60} minutes)"
        )
        self._notify()
        return OperationResult.success(data={
            "code": code,
            "expires_at": self._expires_at,
            "ttl_seconds": PAIRING_TTL_SECONDS,
        })

    def cancel_pairing(self) -> None:
        with self._lock:
            self._code = ""
            self._expires_at = 0.0
            self._attempts = 0
        self._notify()

    def complete_pairing(self, code: str) -> OperationResult:
        """Exchange a valid pairing code for a long-lived access token."""
        if not self.is_enabled():
            return OperationResult.failure(
                "EXTENSION_DISABLED",
                "Browser Extension Support Is Off",
                "Browser extension support is turned off in Developer settings.",
            )

        provided = _normalize_code(code)
        if not provided:
            return OperationResult.failure(
                "PAIRING_CODE_REQUIRED",
                "Pairing Code Required",
                "Enter the pairing code shown in Settings -> Developer.",
            )

        with self._lock:
            if not self._code:
                return OperationResult.failure(
                    "PAIRING_NOT_STARTED",
                    "No Pairing Code Is Active",
                    "Generate a pairing code in Settings -> Developer -> "
                    "Browser Extension.",
                )
            if time.time() >= self._expires_at:
                self._code = ""
                self._expires_at = 0.0
                self._attempts = 0
                return OperationResult.failure(
                    "PAIRING_CODE_EXPIRED",
                    "Pairing Code Expired",
                    "Generate a new pairing code and try again.",
                )
            self._attempts += 1
            if self._attempts > PAIRING_MAX_ATTEMPTS:
                self._code = ""
                self._expires_at = 0.0
                self._attempts = 0
                return OperationResult.failure(
                    "PAIRING_ATTEMPTS_EXCEEDED",
                    "Too Many Pairing Attempts",
                    "Generate a new pairing code and try again.",
                )
            expected = self._code

        if not secrets.compare_digest(provided, expected):
            return OperationResult.failure(
                "PAIRING_CODE_INVALID",
                "Invalid Pairing Code",
                "The pairing code did not match. Check the code shown in the "
                "application and try again.",
            )

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        result = self.manager.set_secure_setting(TOKEN_SETTING_KEY, token)
        if not result:
            return result

        with self._lock:
            self._code = ""
            self._expires_at = 0.0
            self._attempts = 0

        print("[SUCCESS] Browser extension linked")
        self._notify()
        return OperationResult.success(data={
            "token": token,
            "app_version": APP_VERSION,
        })

    def revoke(self) -> OperationResult:
        """Drop the token so any linked extension must pair again."""
        self.cancel_pairing()
        if not self._read_token():
            return OperationResult.success()
        result = self.manager.remove_secure_setting(TOKEN_SETTING_KEY)
        if result:
            print("[INFO] Browser extension unlinked")
        self._notify()
        return result

    # ---- notification ----------------------------------------------------

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self.pairing_state())
        except Exception:
            pass
