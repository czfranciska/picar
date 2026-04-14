import unittest
from unittest.mock import MagicMock, patch
import sys

# Mock smbus2
sys.modules['smbus2'] = MagicMock()

from picar_core.pi.hardware import ServoHatDriver


class TestHardwareDriver(unittest.TestCase):

    def setUp(self):
        self.config = {
            "steer_channel": 0,
            "esc_channel": 3,
            "steer_center_us": 1500,
            "steer_range_us": 300,
            "esc_neutral_us": 1500,
            "esc_min_us": 1350,
            "esc_max_us": 1600,
            "dry_run": True
        }
        self.driver = ServoHatDriver(**self.config)

    def test_steering_conversion_center(self):
        # 0.0 steering results in the center pulse width.
        pulse = self.driver._steer_to_us(0.0)
        self.assertEqual(pulse, 1500)

    def test_steering_conversion_full_left(self):
        # -1.0 steering results in the minimum pulse width.
        pulse = self.driver._steer_to_us(-1.0)
        self.assertEqual(pulse, 1200)  # center (1500) - range (300)

    def test_steering_conversion_full_right(self):
        # 1.0 steering results in the maximum pulse width.
        pulse = self.driver._steer_to_us(1.0)
        self.assertEqual(pulse, 1800)  # center (1500) + range (300)

    def test_throttle_conversion_neutral(self):
        # 0.0 throttle results in the neutral pulse width.
        pulse = self.driver._throttle_to_us(0.0)
        self.assertEqual(pulse, 1500)

    def test_throttle_conversion_full_forward(self):
        # 1.0 throttle results in the max ESC pulse width.
        pulse = self.driver._throttle_to_us(1.0)
        self.assertEqual(pulse, 1600)

    def test_throttle_conversion_full_reverse(self):
        # -1.0 throttle results in the min ESC pulse width.
        pulse = self.driver._throttle_to_us(-1.0)
        self.assertEqual(pulse, 1350)

    @patch('picar_core.pi.hardware.PCA9685')
    def test_hardware_initialization_failure(self, mock_pca):
        # Verify that the driver fails gracefully to dry_run mode if I2C is missing.
        mock_pca.side_effect = Exception("I2C Bus not found")

        # Initialize with dry_run=False to force it to try hardware
        driver = ServoHatDriver(dry_run=False)
        self.assertTrue(driver._dry)
        self.assertIsNone(driver.pwm)

if __name__ == '__main__':
    unittest.main()