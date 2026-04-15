const APP_CONFIG = {
    BACKEND_URL: "ws://mono.inf.elte.hu:3333",
    DEFAULT_STEER_STEP: 0.06,
    DEFAULT_THROTTLE_STEP: 0.06,
    DEFAULT_DECAY: 0.00,
    SENSOR_FORMATS: {
        "cpu_core": {
            data: "usage_percent",
            suffix: "%",
            decimals: 1
        }
    }
};