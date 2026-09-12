"""Contained, atomic persistence for profile JSON documents."""
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Windows device names, which cannot be used as filenames with any extension.
# Spelled out rather than calling pathlib's is_reserved(), which is deprecated
# in Python 3.13 and removed in 3.15.
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{d}" for d in "123456789"]
    + [f"LPT{d}" for d in "123456789"]
)


def _is_reserved(name: str) -> bool:
    """Whether a filename stem is a reserved Windows device name."""
    return name.split(".", 1)[0].upper() in _RESERVED_NAMES


class ProfileRepository:
    """Own profile files without owning active controller settings.

    Parameters
    ----------
    directory : Path
        Writable user profile directory.
    bundled_directory : Path
        Read-only bundled defaults.
    """

    def __init__(self, directory: Path, bundled_directory: Path) -> None:
        self.directory = Path(directory)
        self.bundled_directory = Path(bundled_directory)

    @staticmethod
    def _path(directory: Path, profile_id: str) -> Path:
        if (not isinstance(profile_id, str) or not profile_id
                or profile_id.endswith((".", " "))
                or any(character in '<>:"/\\|?*' or ord(character) < 32
                       for character in profile_id)
                or _is_reserved(profile_id)):
            raise ValueError("Invalid profile identifier")
        path = directory / (profile_id + ".json")
        if path.resolve().parent != directory.resolve():
            raise ValueError("Profile path escapes its directory")
        return path

    def load(self, profile_id: str, *, bundled: bool = False) -> Optional[Dict[str, Any]]:
        """Return a profile object, or None for an invalid or unreadable file."""
        try:
            directory = self.bundled_directory if bundled else self.directory
            with self._path(directory, profile_id).open(encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def save(self, profile_id: str, data: Dict[str, Any]) -> bool:
        """Atomically replace a profile, preserving the old file on failure."""
        temporary = None
        try:
            path = self._path(self.directory, profile_id)
            if not isinstance(data, dict):
                return False
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self.directory, suffix=".tmp",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, indent=4)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            return True
        except (OSError, ValueError, TypeError):
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def is_builtin(self, profile_id: str) -> bool:
        """Return whether an identifier belongs to a bundled profile."""
        try:
            return self._path(self.bundled_directory, profile_id).is_file()
        except (OSError, ValueError):
            return False

    def list_profiles(self) -> List[Dict[str, Any]]:
        """List readable profile metadata for the profile picker."""
        profiles = []
        for path in self.directory.glob("*.json"):
            data = self.load(path.stem)
            if data is not None:
                profiles.append({
                    "id": path.stem,
                    "name": data.get("name", path.stem),
                    "description": data.get("description", ""),
                    "layout_type": data.get("layout_type", "flight_sim"),
                    "is_builtin": self.is_builtin(path.stem),
                })
        return profiles

    def unique_id(self, name: str) -> str:
        """Return an unused, filesystem-safe identifier for a display name."""
        base = "".join(character for character in name.lower().replace(" ", "_")
                       if character.isalnum() or character == "_") or "custom_profile"
        if _is_reserved(base):
            base = "profile_" + base
        candidate = base
        counter = 1
        while self._path(self.directory, candidate).exists():
            candidate = f"{base}_{counter}"
            counter += 1
        return candidate

    def delete(self, profile_id: str) -> bool:
        """Delete a user profile, never a bundled profile."""
        try:
            if self.is_builtin(profile_id):
                return False
            self._path(self.directory, profile_id).unlink()
            return True
        except (OSError, ValueError):
            return False

    def reset(self, profile_id: str) -> bool:
        """Replace a user copy with its bundled defaults."""
        data = self.load(profile_id, bundled=True)
        return data is not None and self.save(profile_id, data)

    def modified_at(self, profile_id: str) -> Optional[float]:
        """Return a profile's filesystem modification time, when it exists."""
        try:
            return self._path(self.directory, profile_id).stat().st_mtime
        except (OSError, ValueError):
            return None

    def ensure_defaults(self, deprecated_ids: set) -> None:
        """Apply the existing bundled-profile installation policy."""
        self.directory.mkdir(parents=True, exist_ok=True)
        for profile_id in deprecated_ids:
            try:
                self._path(self.directory, profile_id).unlink(missing_ok=True)
            except OSError:
                pass
        for source in self.bundled_directory.glob("*.json"):
            destination = self._path(self.directory, source.stem)
            if not destination.exists():
                shutil.copy2(source, destination)