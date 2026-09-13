"""Profile persistence checks confined to temporary directories."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock, patch

from src.profile_repository import ProfileRepository
from src.config import ControllerConfig


class ProfileRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = ProfileRepository(self.root / "user", self.root / "bundled")
        self.repository.directory.mkdir()
        self.repository.bundled_directory.mkdir()

    def test_round_trip_preserves_custom_fields(self):
        data = {"name": "Test", "custom_layout": {"widgets": []}, "future_field": 42}
        self.assertTrue(self.repository.save("test", data))
        self.assertEqual(self.repository.load("test"), data)
        self.assertEqual(self.repository.list_profiles()[0]["id"], "test")
        self.assertIsNotNone(self.repository.modified_at("test"))

    def test_invalid_ids_are_rejected_for_every_operation(self):
        for profile_id in ("", "..", "../escape", "..\\escape", "C:\\escape", "bad:stream", "NUL", "name."):
            with self.subTest(profile_id=profile_id):
                self.assertFalse(self.repository.save(profile_id, {}))
                self.assertIsNone(self.repository.load(profile_id))
                self.assertFalse(self.repository.delete(profile_id))
                self.assertFalse(self.repository.reset(profile_id))
                self.assertIsNone(self.repository.modified_at(profile_id))
        self.assertEqual(list(self.repository.directory.iterdir()), [])

    def test_failed_replace_preserves_old_file_and_cleans_temporary(self):
        self.repository.save("test", {"version": 1})
        with patch("src.profile_repository.os.replace", side_effect=PermissionError):
            self.assertFalse(self.repository.save("test", {"version": 2}))
        self.assertEqual(self.repository.load("test"), {"version": 1})
        self.assertEqual(len(list(self.repository.directory.iterdir())), 1)

    def test_serialization_failure_preserves_old_file(self):
        self.repository.save("test", {"version": 1})
        self.assertFalse(self.repository.save("test", {"invalid": object()}))
        self.assertEqual(self.repository.load("test"), {"version": 1})
        self.assertEqual(len(list(self.repository.directory.iterdir())), 1)

    def test_non_object_profile_is_rejected(self):
        self.assertFalse(self.repository.save("test", []))

    def test_unique_ids_handle_collisions_and_reserved_names(self):
        self.repository.save("my_profile", {})
        self.assertEqual(self.repository.unique_id("My Profile"), "my_profile_1")
        self.assertEqual(self.repository.unique_id("!!!"), "custom_profile")
        self.assertEqual(self.repository.unique_id("NUL"), "profile_nul")

    def test_reset_and_delete_respect_bundled_profiles(self):
        bundled = ProfileRepository(self.repository.bundled_directory, self.root / "none")
        bundled.save("default", {"name": "Default"})
        self.repository.ensure_defaults(set())
        self.repository.save("default", {"name": "Edited"})
        self.assertFalse(self.repository.delete("default"))
        self.assertTrue(self.repository.reset("default"))
        self.assertEqual(self.repository.load("default"), {"name": "Default"})
        self.repository.save("custom", {})
        self.assertTrue(self.repository.delete("custom"))

    def test_config_crud_uses_repository_without_schema_changes(self):
        with patch.object(ControllerConfig, "_get_user_data_dir", return_value=self.root), \
                patch.object(ControllerConfig, "_get_bundled_profiles_dir", return_value=self.repository.bundled_directory):
            config = ControllerConfig(str(self.root / "settings.json"))
        profile_id = config.create_profile_as("My Profile")
        self.assertEqual(profile_id, "my_profile")
        self.assertTrue(config.switch_profile(profile_id))
        widgets = [{"id": "stick", "type": "joystick", "mapping": {"axis_x": "x", "axis_y": "y"}}]
        self.assertTrue(config.save_custom_layout(widgets, 20, False))
        self.assertEqual(config.get_current_profile_data()["custom_layout"]["widgets"], widgets)
        duplicate = config.duplicate_profile(profile_id, "My Profile")
        self.assertEqual(duplicate, "my_profile_1")
        self.assertTrue(config.save_current_profile())
        self.assertTrue(config.delete_profile(duplicate))
        self.assertFalse(config.save_profile_as("../escape", {}))

    def test_cloud_merge_uses_repository_and_rejects_remote_traversal(self):
        from src.cloud_client import CloudClient
        client = SimpleNamespace(_config=SimpleNamespace(profiles=self.repository),
                                 upsert_profile=Mock(return_value=True), profileUpdated=Mock())
        CloudClient._merge_profiles(client, [{"profile_id": "remote", "data": {"name": "Remote"}}])
        self.assertEqual(self.repository.load("remote"), {"name": "Remote"})
        # _merge_profiles now collects per-profile failures and raises once at
        # the end, so the rest of the sync still runs. A traversal id is still
        # refused by the repository and still fails the sync, and the message
        # has to name it rather than failing anonymously.
        with self.assertRaises(RuntimeError) as caught:
            CloudClient._merge_profiles(client, [{"profile_id": "../escape", "data": {}}])
        self.assertIn("../escape", str(caught.exception))
        self.assertFalse((self.root / "escape.json").exists())
        self.assertFalse((self.root.parent / "escape.json").exists())

    def test_merge_finishes_every_profile_before_reporting_failure(self):
        """One bad profile must not abandon the profiles after it."""
        from src.cloud_client import CloudClient
        client = SimpleNamespace(_config=SimpleNamespace(profiles=self.repository),
                                 upsert_profile=Mock(return_value=True), profileUpdated=Mock())
        with self.assertRaises(RuntimeError):
            CloudClient._merge_profiles(client, [
                {"profile_id": "../escape", "data": {}},
                {"profile_id": "good_one", "data": {"name": "Good"}},
            ])
        self.assertEqual(self.repository.load("good_one"), {"name": "Good"})

    def test_failed_upload_reports_failed_sync(self):
        from src.cloud_client import CloudClient
        self.repository.save("local", {"name": "Local"})
        client = SimpleNamespace(is_authenticated=True, is_premium=True,
            _user={"id": "test-only"}, _config=SimpleNamespace(profiles=self.repository),
            _auth_headers=lambda: {}, upsert_profile=Mock(return_value=False),
            syncCompleted=Mock(), profileUpdated=Mock())
        client._merge_profiles = MethodType(CloudClient._merge_profiles, client)
        response = Mock(status_code=200)
        response.json.return_value = []
        with patch("httpx.get", return_value=response):
            self.assertFalse(CloudClient.sync_profiles(client))
        client.syncCompleted.emit.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()

class DeprecatedProfileInvariantTest(unittest.TestCase):
    """A bundled profile must never also be listed as deprecated.

    ensure_defaults deletes every deprecated id and then copies the bundled
    ones back, so an id in both sets is deleted and re-copied on every launch,
    silently reverting whatever the user changed in it. xbox_controller was in
    both for several releases.
    """

    def test_no_bundled_profile_is_also_deprecated(self):
        from src.config import ControllerConfig

        # _get_bundled_profiles_dir is an instance method that only resolves a
        # path, so it is safe to call unbound without building a config (which
        # would touch the real user profile directory).
        bundled_dir = ControllerConfig._get_bundled_profiles_dir(ControllerConfig.__new__(ControllerConfig))
        bundled = {p.stem for p in bundled_dir.glob("*.json")}
        overlap = bundled & set(ControllerConfig._DEPRECATED_BUNDLED_PROFILES)
        self.assertEqual(
            overlap, set(),
            f"these are shipped and deprecated at once, so they reset every launch: {sorted(overlap)}")
