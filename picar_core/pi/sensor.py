from abc import ABC, abstractmethod
import psutil


class Sensor(ABC):

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def setup(self):
        # Initializes the sensor
        pass

    @abstractmethod
    def read(self) -> dict:
        # Takes a reading from the sensor
        pass

    @abstractmethod
    def cleanup(self):
        # Cleans up resources
        pass


class CPUSensor(Sensor):
    # Emulated CPU usage sensor

    def __init__(self, name="cpu_core"):
        super().__init__(name)

    def setup(self):
        psutil.cpu_percent(interval=None)
        print(f"[SENSOR] {self.name} initialized.")

    def read(self) -> dict:
        # Returns the current CPU usage percentage
        usage = psutil.cpu_percent(interval=None)
        return {"usage_percent": usage}

    def cleanup(self):
        print(f"[SENSOR] {self.name} cleaned up.")