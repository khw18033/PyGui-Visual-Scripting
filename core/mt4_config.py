import copy
import json
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(BASE_DIR, 'nodes', 'mt4_config')

# ================= [MT4 Network Config] =================
MT4_NETWORK_CONFIG_DEFAULT = {
    'serial': {
        'port': '/dev/ttyUSB0',
        'baudrate': 115200,
        'timeout_sec': 0.05,
    },
    'unity': {
        'ip': '192.168.50.63',
        'feedback_port': 5005,
        'ui_port': 5007,
    },
    'paths': {
        'record_dir': 'path_record',
        'log_dir': 'result_log',
    },
}

# ================= [MT4 Hardware Config] =================
MT4_HARDWARE_CONFIG_DEFAULT = {
    'limits': {
        'x': {'min': 200, 'max': 280},
        'y': {'min': -200, 'max': 200},
        'z': {'min': 0, 'max': 280},
        'roll': {'min': -180.0, 'max': 180.0},
    },
    'gripper': {
        'min': 30.0,
        'max': 60.0,
    },
    'offset': {
        'z_offset': 90.0,
    },
    'motion': {
        'smooth_factor': 1.0,
        'gripper_speed': 50.0,  # units/sec
        'roll_speed': 50.0,     # deg/sec
    },
}

# ================= [MT4 G-Code Config] =================
MT4_GCODE_CONFIG_DEFAULT = {
    'gcode': {
        'homing_command': '$H',
        'setup_commands': [
            'M20',
            'G90',
            'G1 F2000',
        ],
    },
    'home_position': {
        'x': 200.0,
        'y': 0.0,
        'z': 120.0,
        'roll': 0.0,
        'gripper': 40.0,
    },
    'timing': {
        'homing_wait_sec': 15.0,
        'startup_delay_sec': 2.0,
        'command_interval_sec': 0.05,
        'reconnect_interval_sec': 3.0,
        'manual_override_timeout_sec': 20.0,
    },
}

# ================= [MT4 Keyboard Config] =================
MT4_KEYBOARD_CONFIG_DEFAULT = {
    'keyboard': {
        'keys_mode': 'WASD',  # or 'Arrow'
        'step_size': 10.0,
        'grip_step': 5.0,
        'roll_step': 5.0,
        'cooldown_sec': 0.2,
    },
}


def _deep_merge(base, override):
    """Recursively merge override dict into base dict"""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)

    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_json_compatible_config(filename, default):
    """Load JSON/YAML config file, fallback to default if not found or error"""
    path = os.path.join(CONFIG_DIR, filename)
    if not os.path.isfile(path):
        return copy.deepcopy(default)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            return _deep_merge(default, loaded)
    except Exception:
        pass

    return copy.deepcopy(default)


# Load configs from YAML/JSON files
MT4_NETWORK_CONFIG = _load_json_compatible_config('network_config.yaml', MT4_NETWORK_CONFIG_DEFAULT)
MT4_HARDWARE_CONFIG = _load_json_compatible_config('hardware_config.yaml', MT4_HARDWARE_CONFIG_DEFAULT)
MT4_GCODE_CONFIG = _load_json_compatible_config('gcode_config.yaml', MT4_GCODE_CONFIG_DEFAULT)
MT4_KEYBOARD_CONFIG = _load_json_compatible_config('keyboard_config.yaml', MT4_KEYBOARD_CONFIG_DEFAULT)
