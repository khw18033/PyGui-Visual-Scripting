import copy
import json
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(BASE_DIR, 'nodes', 'ep01_config')

# ================= [EP01 Network Config] =================
EP01_NETWORK_CONFIG_DEFAULT = {
    'ep_ip': '192.168.42.2',
    'ep_port': 40924,
    'ep_use_media_mock': False,
    'flask_port': 5050,
    'ep_sender_target_fps': 30,
    'ep_sender_watch_folder': '/dev/shm/ep01',  # fallback to Captured_Images/ep01_saved
    'ep_server_upload_url': 'http://210.110.250.33:5002/upload',
}

# ================= [EP01 Hardware Config] =================
EP01_HARDWARE_CONFIG_DEFAULT = {
    'arm': {
        'step_size': 10.0,
        'min_position': 0.0,
        'max_position': 200.0,
        'action_timeout_sec': 5.0,
        'retry_delay_sec': 0.25,
        'max_retries': 5,
    },
    'gripper': {
        'power_level': 50,
    },
    'keyboard_config': {
        'keys_mode': 'WASD',  # or 'Arrow'
        'v_max': 0.5,  # m/s
        'w_max': 60.0,  # deg/s
    },
    'camera': {
        'default_url': 'rtsp://192.168.42.2/live',
        'prefer_sdk': True,
        'save_folder': 'Captured_Images/ep01_saved',
    },
}

# ================= [EP01 Camera Config] =================
EP01_CAMERA_CONFIG_DEFAULT = {
    'camera_save_state': {
        'status': 'Stopped',
        'folder': 'Captured_Images/ep01_saved',
        'duration': 0.0,
        'start_time': None,
        'frame_count': 0,
    },
    'camera_stream': {
        'port': 5050,
        'is_running': False,
    },
    'sender': {
        'status': 'Stopped',
        'target_fps': 30,
        'interval': 1.0 / 30,  # 0.033
        'watch_folder': '/dev/shm/ep01',
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


# ================= [EP01 Mission Config] =================
EP01_MISSION_CONFIG_DEFAULT = {
    'pending_url': 'http://localhost:18080/ep01/pending',
    'decision_url': 'http://localhost:18080/ep01/decision',
    'poll_interval_sec': 1.0,
    'request_timeout_sec': 0.5,
    'decision_mode': 'accept_all',
    'allowed_mission_types': ['ep01', 'robot_action'],
}

# Load configs from YAML/JSON files
EP01_NETWORK_CONFIG = _load_json_compatible_config('network_config.yaml', EP01_NETWORK_CONFIG_DEFAULT)
EP01_HARDWARE_CONFIG = _load_json_compatible_config('hardware_config.yaml', EP01_HARDWARE_CONFIG_DEFAULT)
EP01_CAMERA_CONFIG = _load_json_compatible_config('camera_config.yaml', EP01_CAMERA_CONFIG_DEFAULT)
EP01_MISSION_CONFIG = _load_json_compatible_config('mission_config.yaml', EP01_MISSION_CONFIG_DEFAULT)
