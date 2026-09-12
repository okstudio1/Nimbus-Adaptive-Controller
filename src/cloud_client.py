"""
Cloud client for Nimbus Adaptive Controller.

Handles user authentication (email, Google, Facebook), token management,
profile cloud sync, and entitlement checking against the Nimbus Cloud API
powered by Supabase.

Authentication Flow
-------------------
1. User opens File → Account → Sign In
2. App opens system browser to the Nimbus auth page
3. User signs in via email/password, Google OAuth, or Facebook OAuth
4. Browser redirects to ``nimbus://auth?token=...`` (custom URL scheme)
5. App receives the token and stores it in the OS credential vault (keyring)
6. All subsequent API calls use the bearer token
7. Token is refreshed silently in the background

Security
--------
- OAuth tokens are stored in the OS native credential vault via ``keyring``
  (Windows Credential Manager, macOS Keychain, Linux Secret Service).
- **No plaintext tokens are ever written to disk.**
- Refresh tokens are rotated on each use.

Offline Behaviour
-----------------
- All local features work with no network connection.
- Cloud features (sync, entitlements) degrade gracefully — cached values used.

Usage::

    from src.cloud_client import CloudClient

    client = CloudClient(config)
    client.login_with_browser()          # opens browser for OAuth
    client.login_with_email(email, pwd)  # direct email/password login
    print(client.user)                   # {"id": "...", "email": "...", ...}
    client.sync_profiles()               # push/pull profiles
    client.logout()
"""

from __future__ import annotations

import json
import logging
import os
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from PySide6.QtCore import QObject, Signal, Slot, QTimer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Nimbus Cloud API base URL (Supabase project).
# Override with NIMBUS_API_URL for local development.
DEFAULT_API_URL = "https://api.nimbus.app"
API_URL = os.environ.get("NIMBUS_API_URL", DEFAULT_API_URL)

# Supabase Auth endpoints
AUTH_URL = f"{API_URL}/auth/v1"
REST_URL = f"{API_URL}/rest/v1"

# Nimbus web auth page — opened in the user's default browser
WEB_AUTH_URL = os.environ.get("NIMBUS_WEB_AUTH_URL", "https://nimbus.app/auth")

# Custom URL scheme registered in the NSIS installer for OAuth callback
CALLBACK_SCHEME = "nimbus"
CALLBACK_URL = f"{CALLBACK_SCHEME}://auth/callback"

# Keyring service name for credential storage
KEYRING_SERVICE = "NimbusAdaptiveController"
KEYRING_ACCESS_TOKEN = "access_token"
KEYRING_REFRESH_TOKEN = "refresh_token"

# Token refresh interval: 50 minutes (tokens typically expire in 60 min)
TOKEN_REFRESH_INTERVAL_MS = 50 * 60 * 1000

# App data directory
APP_NAME = "ProjectNimbus"

# Supported OAuth providers
OAUTH_PROVIDERS = ("google", "facebook", "email")


def _get_cache_dir() -> Path:
    """Return the directory used to cache user/entitlement data offline."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    cache_dir = base / APP_NAME / "cloud_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


# ---------------------------------------------------------------------------
# CloudClient
# ---------------------------------------------------------------------------

class CloudClient(QObject):
    """
    Authentication, profile sync, and entitlement client for Nimbus Cloud.

    Signals
    -------
    authStateChanged(bool)
        Emitted when the user logs in or out.  ``True`` = authenticated.
    userChanged(str)
        Emitted with the user's display name or email when auth state changes.
    syncCompleted(bool)
        Emitted after a profile sync attempt.  ``True`` = success.
    profileUpdated(str)
        Emitted after a remote profile is persisted, including partial syncs.
    entitlementChanged(str)
        Emitted when the user's tier changes (e.g. ``"free"``, ``"nimbus_plus"``).

    Parameters
    ----------
    config : ControllerConfig
        Application configuration object.
    """

    # Signals
    authStateChanged = Signal(bool)
    userChanged = Signal(str)
    syncCompleted = Signal(bool)
    profileUpdated = Signal(str)
    entitlementChanged = Signal(str)

    def __init__(self, config: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._cache_dir = _get_cache_dir()

        # Auth state
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._user: Optional[Dict[str, Any]] = None
        self._tier: str = "free"

        # Token refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(TOKEN_REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._refresh_access_token)

        # Attempt to restore session from OS credential vault
        self._restore_session()

    # ------------------------------------------------------------------
    # Properties (exposed to QML via bridge)
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop session refresh without changing stored credentials."""
        self._refresh_timer.stop()

    @property
    def is_authenticated(self) -> bool:
        """Whether the user is currently logged in with a valid token."""
        return self._access_token is not None and self._user is not None

    @property
    def user(self) -> Optional[Dict[str, Any]]:
        """
        Current user info dict or None.

        Keys (when authenticated):
        - ``id`` (str) — Supabase user UUID
        - ``email`` (str) — user email
        - ``display_name`` (str) — display name or email prefix
        - ``avatar_url`` (str) — profile picture URL (from OAuth provider)
        - ``provider`` (str) — ``"email"``, ``"google"``, or ``"facebook"``
        """
        return self._user

    @property
    def display_name(self) -> str:
        """User's display name, email, or 'Not signed in'."""
        if self._user:
            return self._user.get("display_name") or self._user.get("email", "User")
        return "Not signed in"

    @property
    def tier(self) -> str:
        """Account tier: ``'free'``, ``'nimbus_plus'``, ``'research'``, ``'institutional'``."""
        return self._tier

    @property
    def is_premium(self) -> bool:
        """Whether the user has an active Nimbus+ (or higher) subscription."""
        return self._tier in ("nimbus_plus", "institutional")

    # ------------------------------------------------------------------
    # Login Methods
    # ------------------------------------------------------------------

    def login_with_browser(self, provider: str = "google") -> None:
        """
        Open the system browser to the Nimbus auth page for OAuth login.

        Parameters
        ----------
        provider : str
            OAuth provider: ``"google"``, ``"facebook"``, or ``"email"``.
            For ``"email"``, the web page shows an email/password form.

        The browser will redirect back to ``nimbus://auth/callback?token=...``
        which the app intercepts via the registered custom URL scheme.
        """
        if provider not in OAUTH_PROVIDERS:
            logger.warning("Unsupported OAuth provider: %s", provider)
            return

        params = {
            "provider": provider,
            "redirect_to": CALLBACK_URL,
        }
        url = f"{WEB_AUTH_URL}?{urlencode(params)}"
        logger.info("Opening browser for %s login: %s", provider, url)
        webbrowser.open(url)

    def login_with_email(self, email: str, password: str) -> bool:
        """
        Sign in directly with email and password (no browser needed).

        Parameters
        ----------
        email : str
            User's email address.
        password : str
            User's password.

        Returns
        -------
        bool
            True if login succeeded, False otherwise.
        """
        try:
            import httpx

            response = httpx.post(
                f"{AUTH_URL}/token?grant_type=password",
                json={"email": email, "password": password},
                headers=self._anon_headers(),
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                self._handle_auth_response(data)
                logger.info("Email login successful for %s", email)
                return True
            else:
                logger.warning("Email login failed: %d — %s",
                               response.status_code, response.text)
                return False
        except ImportError:
            logger.error("httpx not installed — cannot perform email login.")
            return False
        except Exception as e:
            logger.error("Email login error: %s", e)
            return False

    def signup_with_email(self, email: str, password: str) -> bool:
        """
        Create a new account with email and password.

        Parameters
        ----------
        email : str
            User's email address.
        password : str
            Chosen password (minimum 6 characters).

        Returns
        -------
        bool
            True if signup succeeded (user may need to confirm email).
        """
        try:
            import httpx

            response = httpx.post(
                f"{AUTH_URL}/signup",
                json={"email": email, "password": password},
                headers=self._anon_headers(),
                timeout=10.0,
            )

            if response.status_code in (200, 201):
                data = response.json()
                # Some Supabase configs require email confirmation
                if data.get("access_token"):
                    self._handle_auth_response(data)
                logger.info("Signup successful for %s", email)
                return True
            else:
                logger.warning("Signup failed: %d — %s",
                               response.status_code, response.text)
                return False
        except ImportError:
            logger.error("httpx not installed — cannot sign up.")
            return False
        except Exception as e:
            logger.error("Signup error: %s", e)
            return False

    def handle_oauth_callback(self, token_fragment: str) -> bool:
        """
        Process the OAuth callback from the custom URL scheme.

        Parameters
        ----------
        token_fragment : str
            The URL fragment or query string from ``nimbus://auth/callback?...``
            Expected to contain ``access_token`` and ``refresh_token``.

        Returns
        -------
        bool
            True if the callback was processed successfully.
        """
        try:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(token_fragment)
            params = parse_qs(parsed.query or parsed.fragment)

            access_token = params.get("access_token", [None])[0]
            refresh_token = params.get("refresh_token", [None])[0]

            if not access_token:
                logger.warning("OAuth callback missing access_token.")
                return False

            # Fetch user info with the token
            self._access_token = access_token
            self._refresh_token = refresh_token
            self._fetch_user()
            self._store_tokens()
            self._refresh_timer.start()
            self.authStateChanged.emit(True)
            return True
        except Exception as e:
            logger.error("OAuth callback processing failed: %s", e)
            return False

    def logout(self) -> None:
        """
        Sign out: clear tokens from memory and OS credential vault.
        """
        # Attempt server-side logout
        if self._access_token:
            try:
                import httpx
                httpx.post(
                    f"{AUTH_URL}/logout",
                    headers=self._auth_headers(),
                    timeout=5.0,
                )
            except Exception:
                pass  # Best effort

        # Clear local state
        self._access_token = None
        self._refresh_token = None
        self._user = None
        self._tier = "free"
        self._refresh_timer.stop()

        # Remove from OS credential vault
        self._clear_stored_tokens()

        # Clear cached user data
        cache_file = self._cache_dir / "user.json"
        if cache_file.exists():
            cache_file.unlink(missing_ok=True)

        logger.info("User logged out.")
        self.authStateChanged.emit(False)
        self.userChanged.emit("Not signed in")
        self.entitlementChanged.emit("free")

    # ------------------------------------------------------------------
    # Profile Sync
    # ------------------------------------------------------------------

    def sync_profiles(self) -> bool:
        """
        Synchronise local profiles with the cloud (Nimbus+ only).

        Strategy: **last-write-wins per profile_id**.
        - Profiles modified locally since last sync are pushed.
        - Profiles modified remotely since last sync are pulled.
        - Conflicts (both sides modified) use the most recent timestamp.

        Returns
        -------
        bool
            True if sync completed successfully.
        """
        if not self.is_authenticated:
            logger.debug("Profile sync skipped — not authenticated.")
            return False
        if not self.is_premium:
            logger.debug("Profile sync skipped — requires Nimbus+ subscription.")
            return False

        try:
            import httpx

            # Pull remote profiles
            response = httpx.get(
                f"{REST_URL}/profiles",
                params={"user_id": f"eq.{self._user['id']}", "select": "*"},
                headers=self._auth_headers(),
                timeout=15.0,
            )

            if response.status_code != 200:
                logger.warning("Profile sync pull failed: %d", response.status_code)
                self.syncCompleted.emit(False)
                return False

            remote_profiles = response.json()
            self._merge_profiles(remote_profiles)

            logger.info("Profile sync completed: %d remote profiles.",
                        len(remote_profiles))
            self.syncCompleted.emit(True)
            return True
        except ImportError:
            logger.error("httpx not installed — cannot sync profiles.")
            self.syncCompleted.emit(False)
            return False
        except Exception as e:
            logger.error("Profile sync error: %s", e)
            self.syncCompleted.emit(False)
            return False

    def upsert_profile(self, profile_id: str, profile_data: Dict[str, Any]) -> bool:
        """
        Push a single profile to the cloud.

        Parameters
        ----------
        profile_id : str
            The local profile identifier.
        profile_data : dict
            The full profile JSON data.

        Returns
        -------
        bool
            True if the upload succeeded.
        """
        if not self.is_authenticated or not self.is_premium:
            return False

        try:
            import httpx

            payload = {
                "user_id": self._user["id"],
                "profile_id": profile_id,
                "data": profile_data,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            response = httpx.post(
                f"{REST_URL}/profiles",
                json=payload,
                headers={
                    **self._auth_headers(),
                    "Prefer": "resolution=merge-duplicates",
                },
                timeout=10.0,
            )
            return response.status_code in (200, 201)
        except Exception as e:
            logger.error("Profile upsert failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Entitlements
    # ------------------------------------------------------------------

    def check_entitlements(self) -> str:
        """
        Check the user's subscription tier from the server.

        Returns
        -------
        str
            Account tier: ``"free"``, ``"nimbus_plus"``, ``"research"``,
            or ``"institutional"``.
        """
        if not self.is_authenticated:
            return "free"

        try:
            import httpx

            response = httpx.get(
                f"{REST_URL}/entitlements",
                params={"user_id": f"eq.{self._user['id']}", "select": "tier"},
                headers=self._auth_headers(),
                timeout=10.0,
            )
            if response.status_code == 200:
                rows = response.json()
                if rows:
                    self._tier = rows[0].get("tier", "free")
                else:
                    self._tier = "free"
            else:
                logger.debug("Entitlement check returned %d — using cached tier.",
                             response.status_code)
        except Exception as e:
            logger.debug("Entitlement check failed: %s — using cached tier.", e)

        # Cache for offline use
        self._cache_user_data()
        self.entitlementChanged.emit(self._tier)
        return self._tier

    # ------------------------------------------------------------------
    # Internal — Auth Helpers
    # ------------------------------------------------------------------

    def _anon_headers(self) -> Dict[str, str]:
        """Headers for unauthenticated Supabase requests."""
        api_key = os.environ.get("NIMBUS_SUPABASE_ANON_KEY", "")
        return {
            "apikey": api_key,
            "Content-Type": "application/json",
        }

    def _auth_headers(self) -> Dict[str, str]:
        """Headers for authenticated Supabase requests."""
        headers = self._anon_headers()
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _handle_auth_response(self, data: Dict[str, Any]) -> None:
        """Process a successful auth response (login or signup)."""
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token")

        user_data = data.get("user", {})
        meta = user_data.get("user_metadata", {})

        self._user = {
            "id": user_data.get("id", ""),
            "email": user_data.get("email", ""),
            "display_name": (
                meta.get("full_name")
                or meta.get("name")
                or user_data.get("email", "").split("@")[0]
            ),
            "avatar_url": meta.get("avatar_url", ""),
            "provider": user_data.get("app_metadata", {}).get("provider", "email"),
        }

        self._store_tokens()
        self._cache_user_data()
        self._refresh_timer.start()

        # Check entitlements in background
        self.check_entitlements()

        self.authStateChanged.emit(True)
        self.userChanged.emit(self.display_name)

    def _fetch_user(self) -> None:
        """Fetch current user info using the stored access token."""
        if not self._access_token:
            return
        try:
            import httpx

            response = httpx.get(
                f"{AUTH_URL}/user",
                headers=self._auth_headers(),
                timeout=10.0,
            )
            if response.status_code == 200:
                user_data = response.json()
                meta = user_data.get("user_metadata", {})
                self._user = {
                    "id": user_data.get("id", ""),
                    "email": user_data.get("email", ""),
                    "display_name": (
                        meta.get("full_name")
                        or meta.get("name")
                        or user_data.get("email", "").split("@")[0]
                    ),
                    "avatar_url": meta.get("avatar_url", ""),
                    "provider": user_data.get("app_metadata", {}).get("provider", "email"),
                }
                self._cache_user_data()
                self.userChanged.emit(self.display_name)
        except Exception as e:
            logger.debug("Failed to fetch user: %s — using cached data.", e)
            self._load_cached_user()

    def _refresh_access_token(self) -> None:
        """Silently refresh the access token using the refresh token."""
        if not self._refresh_token:
            return
        try:
            import httpx

            response = httpx.post(
                f"{AUTH_URL}/token?grant_type=refresh_token",
                json={"refresh_token": self._refresh_token},
                headers=self._anon_headers(),
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                self._access_token = data.get("access_token", self._access_token)
                self._refresh_token = data.get("refresh_token", self._refresh_token)
                self._store_tokens()
                logger.debug("Access token refreshed successfully.")
            else:
                logger.warning("Token refresh failed (%d) — user may need to re-login.",
                               response.status_code)
                # Don't logout immediately — token might still work
        except Exception as e:
            logger.debug("Token refresh error: %s", e)

    # ------------------------------------------------------------------
    # Internal — Token Storage (OS Credential Vault)
    # ------------------------------------------------------------------

    def _store_tokens(self) -> None:
        """Store tokens in the OS native credential vault via keyring."""
        try:
            import keyring

            if self._access_token:
                keyring.set_password(KEYRING_SERVICE, KEYRING_ACCESS_TOKEN, self._access_token)
            if self._refresh_token:
                keyring.set_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN, self._refresh_token)
            logger.debug("Tokens stored in OS credential vault.")
        except ImportError:
            logger.debug("keyring not installed — tokens stored in memory only.")
        except Exception as e:
            logger.warning("Failed to store tokens in credential vault: %s", e)

    def _restore_session(self) -> None:
        """Attempt to restore a previous session from stored tokens."""
        try:
            import keyring

            self._access_token = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCESS_TOKEN)
            self._refresh_token = keyring.get_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN)

            if self._access_token:
                logger.info("Restoring previous session from credential vault.")
                self._fetch_user()
                if self._user:
                    self._refresh_timer.start()
                    self.authStateChanged.emit(True)
                    self.check_entitlements()
                else:
                    # Token expired or invalid — try refresh
                    self._refresh_access_token()
                    if self._access_token:
                        self._fetch_user()
                        if self._user:
                            self._refresh_timer.start()
                            self.authStateChanged.emit(True)
        except ImportError:
            logger.debug("keyring not installed — no session to restore.")
        except Exception as e:
            logger.debug("Session restore failed: %s", e)
            # Fall back to cached user data for display
            self._load_cached_user()

    def _clear_stored_tokens(self) -> None:
        """Remove tokens from the OS credential vault."""
        try:
            import keyring

            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCESS_TOKEN)
            except keyring.errors.PasswordDeleteError:
                pass
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN)
            except keyring.errors.PasswordDeleteError:
                pass
            logger.debug("Tokens removed from credential vault.")
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Failed to clear stored tokens: %s", e)

    # ------------------------------------------------------------------
    # Internal — Offline Cache
    # ------------------------------------------------------------------

    def _cache_user_data(self) -> None:
        """Cache user info and tier locally for offline display."""
        if not self._user:
            return
        try:
            cache = {
                "user": self._user,
                "tier": self._tier,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
            cache_file = self._cache_dir / "user.json"
            cache_file.write_text(json.dumps(cache, indent=2), "utf-8")
        except Exception as e:
            logger.debug("Failed to cache user data: %s", e)

    def _load_cached_user(self) -> None:
        """Load cached user info for offline display (not authentication)."""
        try:
            cache_file = self._cache_dir / "user.json"
            if cache_file.exists():
                cache = json.loads(cache_file.read_text("utf-8"))
                self._user = cache.get("user")
                self._tier = cache.get("tier", "free")
                logger.debug("Loaded cached user data for offline display.")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal — Profile Merge
    # ------------------------------------------------------------------

    def _merge_profiles(self, remote_profiles: List[Dict[str, Any]]) -> None:
        """
        Merge remote profiles with local profiles using last-write-wins.

        Strategy:
        - For each remote profile, compare ``updated_at`` with local file mtime.
        - If remote is newer, overwrite local file.
        - If local is newer, push to remote.
        - If no local counterpart exists, pull from remote.
        - If no remote counterpart exists, push to remote.
        """
        repository = self._config.profiles
        # The directory is created during ControllerConfig startup, so a missing
        # one means something is genuinely wrong with user storage. Creating it
        # here keeps a first sync from failing over an empty profile set, and a
        # directory that still cannot be made is a real error.
        try:
            repository.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError("Profile directory is unavailable") from exc

        remote_by_id = {p["profile_id"]: p for p in remote_profiles}
        # Every profile is attempted, and the failures are collected rather than
        # thrown at the first one. Raising mid-loop abandoned the remaining
        # pulls and pushes, so one unreadable file left the set half synced and
        # reported the whole run as a clean failure.
        failures: List[str] = []

        # Pull newer remote profiles
        for pid, rp in remote_by_id.items():
            remote_updated = rp.get("updated_at", "")
            modified_at = repository.modified_at(pid)

            if modified_at is not None:
                local_mtime = datetime.fromtimestamp(
                    modified_at, tz=timezone.utc
                ).isoformat()
                if remote_updated > local_mtime:
                    # Remote is newer — pull
                    if repository.save(pid, rp["data"]):
                        self.profileUpdated.emit(pid)
                        logger.debug("Pulled profile %s (remote newer).", pid)
                    else:
                        failures.append(f"could not save remote profile {pid}")
                elif local_mtime > remote_updated:
                    # Local is newer — push
                    local_data = repository.load(pid)
                    if local_data is None:
                        failures.append(f"could not read local profile {pid}")
                    elif not self.upsert_profile(pid, local_data):
                        failures.append(f"upload failed for profile {pid}")
                    else:
                        logger.debug("Pushed profile %s (local newer).", pid)
            else:
                # No local copy — pull
                if repository.save(pid, rp["data"]):
                    self.profileUpdated.emit(pid)
                    logger.debug("Pulled new profile %s from cloud.", pid)
                else:
                    failures.append(f"could not save remote profile {pid}")

        # Push local-only profiles
        for profile in repository.list_profiles():
            pid = profile["id"]
            if pid not in remote_by_id:
                local_data = repository.load(pid)
                if local_data is None:
                    failures.append(f"could not read local profile {pid}")
                elif not self.upsert_profile(pid, local_data):
                    failures.append(f"upload failed for profile {pid}")
                else:
                    logger.debug("Pushed local-only profile %s to cloud.", pid)
        if failures:
            raise RuntimeError("Profile sync incomplete: " + "; ".join(failures))
