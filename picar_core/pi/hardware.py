import time
import math
import smbus2

# PCA9685 driver class for controlling the servo hat via I2C
class PCA9685:
    __SUBADR1 = 0x02
    __SUBADR2 = 0x03
    __SUBADR3 = 0x04
    __MODE1 = 0x00
    __PRESCALE = 0xFE
    __LED0_ON_L = 0x06
    __LED0_ON_H = 0x07
    __LED0_OFF_L = 0x08
    __LED0_OFF_H = 0x09
    __ALLLED_ON_L = 0xFA
    __ALLLED_ON_H = 0xFB
    __ALLLED_OFF_L = 0xFC
    __ALLLED_OFF_H = 0xFD

    def __init__(self, address=0x40, debug=False):
        self.bus = smbus2.SMBus(1)
        self.address = address
        self.debug = debug
        self.pwrange = {}
        if (self.debug):
            print("Reseting PCA9685")
        self.write(self.__MODE1, 0x00)

    def write(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def read(self, reg):
        return self.bus.read_byte_data(self.address, reg)

    # Set the pulse width range for a given channel (in microseconds)
    def setPulseWidthRange(self, channel, minvalue, maxvalue):
        self.pwrange[channel] = (minvalue, maxvalue)

    # Set the PWM frequency (in Hz)
    def setPWMFreq(self, freq):
        prescaleval = 25000000.0 / 4096.0 / float(freq) - 1.0
        prescale = math.floor(prescaleval + 0.5)
        oldmode = self.read(self.__MODE1)
        newmode = (oldmode & 0x7F) | 0x10
        self.write(self.__MODE1, newmode)
        self.write(self.__PRESCALE, int(math.floor(prescale)))
        self.write(self.__MODE1, oldmode)
        time.sleep(0.005)
        self.write(self.__MODE1, oldmode | 0x80)

    # Set the PWM on/off values for a given channel (0-15)
    def setPWM(self, channel, on, off):
        self.write(self.__LED0_ON_L + 4 * channel, on & 0xFF)
        self.write(self.__LED0_ON_H + 4 * channel, on >> 8)
        self.write(self.__LED0_OFF_L + 4 * channel, off & 0xFF)
        self.write(self.__LED0_OFF_H + 4 * channel, off >> 8)

    # Set the pulse width for a given channel (in microseconds)
    def setServoPulse(self, channel, pulse):
        if channel in self.pwrange:
            minv, maxv = self.pwrange[channel]
            if pulse < minv:
                pulse = minv
            elif pulse > maxv:
                pulse = maxv
        pulse = pulse * 4096 / 20000
        self.setPWM(channel, 0, int(pulse))

class OutputDriver:
    def set_steer_throttle(self, steer: float, throttle: float) -> None: ...

    def arm(self, seconds: float) -> None: ...

    def neutral(self) -> None: ...


class ServoHatDriver(OutputDriver):
    def __init__(self, steer_channel=0, esc_channel=3, i2c_address=0x40, frequency_hz=50,
                 steer_center_us=1500, steer_range_us=300, esc_neutral_us=1500,
                 esc_min_us=1300, esc_max_us=1650, dry_run=False):
        self._dry = dry_run
        self.steer_ch = steer_channel
        self.esc_ch = esc_channel
        self.steer_center = steer_center_us
        self.steer_range = steer_range_us
        self.esc_neutral = esc_neutral_us
        self.esc_min = esc_min_us
        self.esc_max = esc_max_us

        if not self._dry:
            try:
                self.pwm = PCA9685(i2c_address, debug=False)
                self.pwm.setPWMFreq(frequency_hz)
                self.pwm.setPulseWidthRange(self.steer_ch, self.steer_center - self.steer_range,
                                            self.steer_center + self.steer_range)
                self.pwm.setPulseWidthRange(self.esc_ch, self.esc_min, self.esc_max)
            except Exception as e:
                print(f"[WARN] PWM control is not available, simulation mode...")
                self._dry = True
                self.pwm = None

    def _write_us(self, channel: int, us: int) -> None:
        if not self._dry: self.pwm.setServoPulse(channel, us)

    def _steer_to_us(self, steer: float) -> int:
        steer = max(-1.0, min(1.0, steer))
        return int(round(self.steer_center + steer * self.steer_range))

    def _throttle_to_us(self, throttle: float) -> int:
        throttle = max(-1.0, min(1.0, throttle))
        if throttle >= 0:
            return int(round(self.esc_neutral + throttle * (self.esc_max - self.esc_neutral)))
        else:
            return int(round(self.esc_neutral + throttle * (self.esc_neutral - self.esc_min)))

    def set_steer_throttle(self, steer: float, throttle: float) -> None:
        self._write_us(self.steer_ch, self._steer_to_us(steer))
        self._write_us(self.esc_ch, self._throttle_to_us(throttle))

    def neutral(self) -> None:
        self._write_us(self.steer_ch, self.steer_center)
        self._write_us(self.esc_ch, self.esc_neutral)

    # Arm the ESC by sending neutral throttle
    def arm(self, seconds: float) -> None:
        print(f"[INFO] Arming ESC for {seconds:.1f}s at neutral...")
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.neutral()
            time.sleep(0.05)
        print("[INFO] Arming complete.")
