"""
Unit tests for AlohaMini CalibrationSession state machine and message dispatching.
"""

import unittest
from unittest.mock import MagicMock, patch
from agent.calibration import CalibrationSession, CalibrationState
from agent.teleop import TeleopInputReceiver


class TestCalibrationSession(unittest.TestCase):
    def setUp(self):
        self.statuses = []
        self.session = CalibrationSession(on_status_change=lambda s: self.statuses.append(s))

    def test_initial_state_is_idle(self):
        self.assertEqual(self.session.state, CalibrationState.IDLE)
        status = self.session.get_status()
        self.assertEqual(status["state"], CalibrationState.IDLE)
        self.assertEqual(status["type"], "calib_status")

    def test_status_notification_callback(self):
        self.session._notify()
        self.assertEqual(len(self.statuses), 1)
        self.assertEqual(self.statuses[0]["state"], CalibrationState.IDLE)

    def test_cancel_returns_to_idle(self):
        self.session.state = CalibrationState.LEFT_MIDDLE
        res = self.session.cancel()
        self.assertTrue(res["success"])
        self.assertEqual(self.session.state, CalibrationState.IDLE)
        self.assertEqual(len(self.session.current_positions), 0)

    def test_teleop_receiver_dispatches_calib_start(self):
        receiver = TeleopInputReceiver()
        mock_calib = MagicMock()
        mock_calib.start.return_value = {"success": True, "state": "left_middle"}
        receiver.calibration_session = mock_calib

        payload = {
            "type": "calib_start",
            "robot_model": "alohamini2",
            "no_follower": False,
        }
        handled = receiver.handle_message(payload)
        self.assertTrue(handled)
        mock_calib.start.assert_called_once_with(robot_model="alohamini2", no_follower=False)

    def test_teleop_receiver_dispatches_calib_confirm_middle(self):
        receiver = TeleopInputReceiver()
        mock_calib = MagicMock()
        mock_calib.confirm_middle.return_value = {"success": True, "state": "left_rom"}
        receiver.calibration_session = mock_calib

        payload = {
            "type": "calib_confirm_middle",
            "arm": "left",
        }
        handled = receiver.handle_message(payload)
        self.assertTrue(handled)
        mock_calib.confirm_middle.assert_called_once_with(arm="left")

    def test_teleop_receiver_dispatches_calib_finish_rom(self):
        receiver = TeleopInputReceiver()
        mock_calib = MagicMock()
        mock_calib.finish_rom.return_value = {"success": True, "state": "right_middle"}
        receiver.calibration_session = mock_calib

        payload = {
            "type": "calib_finish_rom",
            "arm": "left",
        }
        handled = receiver.handle_message(payload)
        self.assertTrue(handled)
        mock_calib.finish_rom.assert_called_once_with(arm="left")

    def test_teleop_receiver_dispatches_calib_save(self):
        receiver = TeleopInputReceiver()
        mock_calib = MagicMock()
        mock_calib.save_and_apply.return_value = {"success": True, "state": "done"}
        receiver.calibration_session = mock_calib

        payload = {
            "type": "calib_save",
            "cloud_api_base": "http://127.0.0.1:8080/api/v1",
            "device_id": "AlohaMini-01",
        }
        handled = receiver.handle_message(payload)
        self.assertTrue(handled)
        mock_calib.save_and_apply.assert_called_once_with(
            cloud_api_base="http://127.0.0.1:8080/api/v1",
            device_id="AlohaMini-01",
        )

    def test_teleop_receiver_dispatches_calib_cancel(self):
        receiver = TeleopInputReceiver()
        mock_calib = MagicMock()
        mock_calib.cancel.return_value = {"success": True, "state": "idle"}
        receiver.calibration_session = mock_calib

        payload = {"type": "calib_cancel"}
        handled = receiver.handle_message(payload)
        self.assertTrue(handled)
        mock_calib.cancel.assert_called_once()

    def test_reset_calibration(self):
        self.session.state = CalibrationState.DONE
        self.session.left_homing = {"arm_left_shoulder_pan": 2048}
        self.session.l_mins = {"arm_left_shoulder_pan": 950}
        self.session.l_maxs = {"arm_left_shoulder_pan": 3100}

        res = self.session.reset_calibration()
        self.assertTrue(res["success"])
        self.assertEqual(self.session.state, CalibrationState.IDLE)
        self.assertEqual(len(self.session.left_homing), 0)
        self.assertEqual(len(self.session.l_mins), 0)
        self.assertEqual(len(self.session.l_maxs), 0)
        self.assertIsNone(self.session.error_message)

    def test_generate_calibration_report(self):
        self.session.robot_model = "alohamini2"
        self.session.left_homing = {"arm_left_shoulder_pan": 2045}
        self.session.right_homing = {"arm_right_shoulder_pan": 2050}
        self.session.l_mins = {"arm_left_shoulder_pan": 950}
        self.session.l_maxs = {"arm_left_shoulder_pan": 3150}

        report = self.session.generate_calibration_report(device_id="AlohaMini-Test")
        self.assertEqual(report["type"], "calib_report")
        self.assertEqual(report["device_id"], "AlohaMini-Test")
        self.assertEqual(report["robot_model"], "alohamini2")
        self.assertEqual(report["homing_offsets"]["arm_left_shoulder_pan"], 2045)
        self.assertEqual(report["homing_offsets"]["arm_right_shoulder_pan"], 2050)
        self.assertEqual(report["rom_ranges"]["arm_left_shoulder_pan"]["min"], 950)
        self.assertEqual(report["rom_ranges"]["arm_left_shoulder_pan"]["max"], 3150)
        self.assertEqual(report["rom_ranges"]["arm_left_shoulder_pan"]["span"], 2200)
        self.assertEqual(report["lift_homed_mm"], 0.0)
        self.assertEqual(report["inspection_result"], "PASS")

    def test_teleop_receiver_dispatches_calib_reset(self):
        receiver = TeleopInputReceiver()
        mock_calib = MagicMock()
        mock_calib.reset_calibration.return_value = {"success": True, "state": "idle"}
        receiver.calibration_session = mock_calib

        payload = {
            "type": "calib_reset",
            "cloud_api_base": "http://127.0.0.1:8080/api/v1",
            "device_id": "AlohaMini-01",
        }
        handled = receiver.handle_message(payload)
        self.assertTrue(handled)
        mock_calib.reset_calibration.assert_called_once_with(
            cloud_api_base="http://127.0.0.1:8080/api/v1",
            device_id="AlohaMini-01",
        )

    def test_teleop_receiver_dispatches_calib_export_report(self):
        receiver = TeleopInputReceiver()
        mock_calib = MagicMock()
        mock_calib.generate_calibration_report.return_value = {
            "type": "calib_report",
            "device_id": "AlohaMini-01",
            "inspection_result": "PASS",
        }
        receiver.calibration_session = mock_calib

        status_reports = []
        receiver.set_status_callback(lambda s: status_reports.append(s))

        payload = {
            "type": "calib_export_report",
            "device_id": "AlohaMini-01",
        }
        handled = receiver.handle_message(payload)
        self.assertTrue(handled)
        mock_calib.generate_calibration_report.assert_called_once_with(device_id="AlohaMini-01")
        self.assertEqual(len(status_reports), 1)
        self.assertEqual(status_reports[0]["inspection_result"], "PASS")


if __name__ == "__main__":
    unittest.main()
