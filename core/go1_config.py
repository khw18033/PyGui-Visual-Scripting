import copy
import json
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(BASE_DIR, 'nodes', 'go1_config')


NETWORK_CONFIG_DEFAULT = {
    'highlevel': 0xEE,
    'local_port': 8090,
    'go1_ip': '192.168.50.41',
    'go1_port': 8082,
    'go1_unity_ip': '192.168.50.246',
    'unity_state_port': 15101,
    'unity_cmd_port': 15102,
    'unity_rx_port': 15100,
    'unity_waypoint_tx_port': 15104,
    'unity_path_port': 15110,
    'go1_ap_ip': '192.168.123.161',
    'aruco_udp_port': 5008,
    'server_upload_url': 'http://192.168.1.100:5001/upload',
    'json_cmd_url': 'http://127.0.0.1:5001/cmd',
}

ROBOT_CONTROL_CONFIG_DEFAULT = {
    'dt': 0.02,
    'v_max': 0.4,
    's_max': 0.4,
    'w_max': 2.0,
    'vx_cmd': 0.20,
    'vy_cmd': 0.20,
    'wz_cmd': 1.00,
    'body_height': {
        'min': -0.12,
        'max': 0.12,
        'key_step': 0.005,
    },
    'timing': {
        'hold_timeout_sec': 0.1,
        'repeat_grace_sec': 0.4,
        'min_move_sec': 0.4,
        'stop_brake_sec': 0.0,
        'unity_timeout_sec': 0.15,
    },
    'yaw_control': {
        'unity_yaw_offset_deg': 90.0,
        'yaw_fine_tune_deg': 1.0,
        'yaw_align_kp': 2.0,
        'yaw_align_tol_deg': 2.0,
    },
    'estop': {
        'hold_sec': 2.0,
    },
    'foot_raise_height': 0.08,
}

AUTO_AVOIDANCE_CONFIG_DEFAULT = {
    'class_to_group': {
        'person': 'AGENT',
        'pedestrian': 'AGENT',
        'child': 'AGENT',
        'dog': 'AGENT',
        'cat': 'AGENT',
        'robot': 'AGENT',
        'quadruped robot': 'AGENT',
        'robot dog': 'AGENT',
        'car': 'VEHICLE',
        'truck': 'VEHICLE',
        'bus': 'VEHICLE',
        'motorcycle': 'VEHICLE',
        'bicycle': 'VEHICLE',
        'scooter': 'VEHICLE',
        'large_obstacle': 'HARD_OBSTACLE',
        'box': 'HARD_OBSTACLE',
        'cardboard box': 'HARD_OBSTACLE',
        'chair': 'HARD_OBSTACLE',
        'table': 'HARD_OBSTACLE',
        'bench': 'HARD_OBSTACLE',
        'barrier': 'HARD_OBSTACLE',
        'fence': 'HARD_OBSTACLE',
        'guardrail': 'HARD_OBSTACLE',
        'wall': 'HARD_OBSTACLE',
        'pillar': 'HARD_OBSTACLE',
        'door': 'HARD_OBSTACLE',
        'pole': 'HARD_OBSTACLE',
        'bollard': 'HARD_OBSTACLE',
        'trash bin': 'HARD_OBSTACLE',
        'fire extinguisher': 'HARD_OBSTACLE',
        'umbrella': 'HARD_OBSTACLE',
        'rock': 'HARD_OBSTACLE',
        'backpack': 'SOFT_PUSHABLE',
        'bag': 'SOFT_PUSHABLE',
        'paper bag': 'SOFT_PUSHABLE',
        'tissue box': 'SOFT_PUSHABLE',
        'toilet paper roll': 'SOFT_PUSHABLE',
        'trash': 'SOFT_PUSHABLE',
        'plastic bag': 'SOFT_PUSHABLE',
        'laptop': 'LOW_OBSTACLE',
        'card': 'LOW_OBSTACLE',
        'power strip': 'LOW_OBSTACLE',
        'small_object': 'LOW_OBSTACLE',
        'movable_object': 'LOW_OBSTACLE',
        'wire': 'THIN_OBSTACLE',
        'cable': 'THIN_OBSTACLE',
        'hose': 'THIN_OBSTACLE',
        'branch': 'THIN_OBSTACLE',
        'curb': 'GROUND_HAZARD',
        'stairs': 'GROUND_HAZARD',
        'ramp': 'GROUND_HAZARD',
        'speed bump': 'GROUND_HAZARD',
        'puddle': 'GROUND_HAZARD',
        'traffic cone': 'UNKNOWN_OBSTACLE',
    },
    'policy': {
        'AGENT': {'action': 'stop', 'hold_sec': 4.0},
        'VEHICLE': {'action': 'stop', 'hold_sec': 4.0},
        'HARD_OBSTACLE': {'action': 'avoid'},
        'SOFT_PUSHABLE': {'action': 'avoid'},
        'LOW_OBSTACLE': {'action': 'avoid'},
        'THIN_OBSTACLE': {'action': 'stop_then_back', 'back_sec': 0.5},
        'GROUND_HAZARD': {'action': 'stop_then_back', 'back_sec': 1.0},
        'UNKNOWN_OBSTACLE': {'action': 'stop', 'hold_sec': 2.0},
    },
    'group_priority': {
        'AGENT': 0,
        'VEHICLE': 1,
        'GROUND_HAZARD': 2,
        'THIN_OBSTACLE': 3,
        'HARD_OBSTACLE': 4,
        'UNKNOWN_OBSTACLE': 5,
        'SOFT_PUSHABLE': 6,
        'LOW_OBSTACLE': 7,
    },
    'image': {
        'width': 464,
        'height': 400,
    },
    'escape': {
        'left_x': 150,
        'right_x': 300,
    },
    'bbox': {
        'min_width': 24,
        'min_height': 24,
    },
    'motion': {
        'move_speed': 0.2,
        'move_duration_sec': 0.5,
    },
}

SPECIAL_ACTIONS_CONFIG_DEFAULT = {
    'special_actions': {
        'backflip': {'mode': 9, 'trigger_sec': 0.4, 'wait_timeout': 5.0, 'recovery': 'stand'},
        'jumpyaw': {'mode': 10, 'trigger_sec': 0.2, 'wait_timeout': 4.0, 'recovery': 'stand'},
        'straighthand': {'mode': 11, 'trigger_sec': 0.2, 'wait_timeout': 5.0, 'recovery': 'stand'},
        'dance1': {'mode': 12, 'trigger_sec': 0.2, 'wait_timeout': 8.0, 'recovery': 'idle'},
        'dance2': {'mode': 13, 'trigger_sec': 0.2, 'wait_timeout': 8.0, 'recovery': 'idle'},
    },
    'phase_timing': {
        'prep_stand_sec': 1.5,
        'post_wait_sec': 0.3,
        'recover8_sec': 1.5,
        'recover1_sec': 1.5,
        'recover0_sec': 0.5,
    },
}

CAMERA_CONFIG_DEFAULT = {
    'camera_nanos': ['unitree@192.168.123.13'],
    'camera_config': [
        {'folder': 'Captured_Images/go1_front', 'id': 'go1_front'},
    ],
    'camera_save_state_defaults': {
        'status': 'Stopped',
        'folder': 'Captured_Images/go1_saved',
        'duration': 0.0,
        'start_time': None,
        'frame_count': 0,
    },
    'gstreamer': {
        'udp_port': 9400,
        'ssh_key_path': '~/.ssh/id_rsa',
    },
    'timing': {
        'first_frame_wait_sec': 5.0,
        'upload_warmup_sec': 2.0,
        'sender_camera_wait_sec': 10.0,
        'proc_kill_timeout_sec': 2,
    },
}

MISSION_CONFIG_DEFAULT = {
    'pending_url': 'http://100.65.158.54:18080/pending',
    'decision_url': 'http://100.65.158.54:18080/decision',
    'poll_interval_sec': 1.0,
    'request_timeout_sec': 3.0,
    'decision_mode': 'accept_if_destination_present',
    'allowed_mission_types': ['go1', 'unity_path', 'robot_action'],
    'schema': {
        'mission_id_keys': ['mission_id', 'id'],
        'mission_type_keys': ['mission_type', 'type', 'kind'],
        'destination_keys': ['destination', 'goal', 'target'],
        'waypoint_keys': ['waypoints', 'points', 'path'],
        'post_action_keys': ['post_action', 'robot_action', 'action'],
    },
    'defaults': {
        'destination_frame': 'go1_local_start',
        'start_yaw_deg': 0.0,
        'post_action_mode': 'Stand',
        'post_action_value': 0.2,
    },
}

MODEL_CONFIG_DEFAULT = {
    'da2_models': {
        'vits': {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]},
        'vitb': {'encoder': 'vitb', 'features': 128, 'out_channels': [96, 192, 384, 768]},
        'vitl': {'encoder': 'vitl', 'features': 256, 'out_channels': [256, 512, 1024, 1024]},
        'vitg': {'encoder': 'vitg', 'features': 384, 'out_channels': [1536, 1536, 1536, 1536]},
    },
    'runtime': {
        'target_fps': 30,
        'interval': 1.0 / 30.0,
    },
    'aruco': {
        'enabled': False,
        'marker_size': 0.03,
    },
}


def _deep_merge(base, override):
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
    path = os.path.join(CONFIG_DIR, filename)
    if not os.path.isfile(path):
        return copy.deepcopy(default)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Remove comments: strip lines that start with # or contain # after whitespace
        lines = content.split('\n')
        cleaned_lines = []
        for line in lines:
            # Remove inline comments (but be careful with # inside strings)
            comment_idx = line.find('#')
            if comment_idx != -1:
                # Simple heuristic: if # is not inside quotes, remove from there
                before_hash = line[:comment_idx]
                quote_count = before_hash.count('"') + before_hash.count("'")
                if quote_count % 2 == 0:  # Even number of quotes = # is outside strings
                    line = before_hash
            cleaned_lines.append(line)
        
        cleaned_content = '\n'.join(cleaned_lines)
        loaded = json.loads(cleaned_content)
        
        if isinstance(loaded, dict):
            return _deep_merge(default, loaded)
    except Exception as e:
        pass

    return copy.deepcopy(default)


NETWORK_CONFIG = _load_json_compatible_config('network_config.yaml', NETWORK_CONFIG_DEFAULT)
ROBOT_CONTROL_CONFIG = _load_json_compatible_config('robot_control.yaml', ROBOT_CONTROL_CONFIG_DEFAULT)
AUTO_AVOIDANCE_CONFIG = _load_json_compatible_config('auto_avoidance_config.yaml', AUTO_AVOIDANCE_CONFIG_DEFAULT)
SPECIAL_ACTIONS_CONFIG = _load_json_compatible_config('special_actions.yaml', SPECIAL_ACTIONS_CONFIG_DEFAULT)
CAMERA_CONFIG = _load_json_compatible_config('camera_config.yaml', CAMERA_CONFIG_DEFAULT)
MISSION_CONFIG = _load_json_compatible_config('mission_config.yaml', MISSION_CONFIG_DEFAULT)
MODEL_CONFIG = _load_json_compatible_config('model_config.yaml', MODEL_CONFIG_DEFAULT)
