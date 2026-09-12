"""Hardware-free regression tests for controller instance ownership."""
import unittest
from unittest.mock import Mock

from src.controller_output import ControllerOutput


class ControllerOutputTests(unittest.TestCase):
    def setUp(self):
        self.config = Mock()
        self.config.get_layout_type.return_value = "custom"
        self.config.get.side_effect = lambda key, default=None: default
        self.vjoy_factory = Mock(return_value=Mock(is_connected=True))
        self.vigem_factory = Mock(return_value=Mock(is_connected=True))
        self.output = ControllerOutput(self.config, self.vjoy_factory,
                                       self.vigem_factory, True)

    def test_custom_profile_creates_both_backends_and_selects_vigem(self):
        self.output.initialize()
        self.assertIs(self.output.active, self.vigem_factory.return_value)
        self.assertTrue(self.output.connected)
        self.output.initialize()
        self.vjoy_factory.assert_called_once_with(self.config)
        self.vigem_factory.assert_called_once_with(self.config)

    def test_flight_profile_only_creates_vjoy(self):
        self.config.get_layout_type.return_value = "flight_sim"
        self.output.initialize()
        self.assertEqual(self.output.mode, "vjoy")
        self.vigem_factory.assert_not_called()

    def test_unavailable_vigem_cannot_be_selected(self):
        self.output.vigem_available = False
        self.output.initialize()
        self.assertFalse(self.output.select("vigem"))
        self.assertEqual(self.output.mode, "vjoy")
        self.vigem_factory.assert_not_called()

    def test_selection_reuses_devices_without_input_or_persistence(self):
        self.output.initialize()
        self.assertTrue(self.output.select(" vjoy "))
        self.assertTrue(self.output.select("vigem"))
        self.assertFalse(self.output.select("invalid"))
        self.assertFalse(self.output.select("vigem"))
        self.assertEqual(self.vigem_factory.return_value.mock_calls, [])
        self.assertEqual(self.vjoy_factory.return_value.mock_calls, [])
        self.config.save_config.assert_not_called()

    def test_connection_failure_does_not_change_selection_policy(self):
        self.vigem_factory.return_value.is_connected = False
        self.output.initialize()
        self.assertEqual(self.output.mode, "vigem")
        self.assertFalse(self.output.connected)

    def test_game_mode_backend_creation_does_not_select_output(self):
        self.config.get_layout_type.return_value = "flight_sim"
        self.output.initialize()
        self.assertIs(self.output.ensure_vigem(), self.vigem_factory.return_value)
        self.output.ensure_vigem()
        self.vigem_factory.assert_called_once_with(self.config)
        self.assertEqual(self.output.mode, "vjoy")

    def test_game_mode_backend_respects_unavailability(self):
        self.output.vigem_available = False
        self.assertIsNone(self.output.ensure_vigem())
        self.vigem_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()