import dearpygui.dearpygui as dpg
import sys
import os
import time
import socket
import json
import threading
import subprocess

from core.engine import node_registry, link_registry, system_log_buffer, generate_uuid, PortType, HwStatus
from core.input_manager import input_manager
from core.factory import NodeFactory
from core.serializer import save_graph, load_graph, get_save_files
from nodes.robots.mt4 import (
    mt4_current_pos, mt4_dashboard, mt4_target_goal, mt4_apply_limits,
    toggle_mt4_record, get_mt4_paths, play_mt4_path, mt4_manual_override_until,
    MT4_Z_OFFSET, MT4_UNITY_IP, MT4_FEEDBACK_PORT, mt4_homing_callback
)
import core.engine as engine_module
import nodes.robots.mt4 as mt4_module

# --- [추가] Go1 연동 ---
try:
    from nodes.robots.go1 import (
        go1_dashboard, go1_target_vel, go1_state, go1_node_intent,
        camera_state, aruco_settings, go1_estop_callback, ServerSenderNode
    )
    import nodes.robots.go1 as go1_module
    HAS_GO1 = True
except ImportError:
    HAS_GO1 = False

# --- [추가] RoboMaster EP 연동 ---
try:
    from nodes.robots.ep01 import (
        ep_dashboard, ep_state, ep_target_vel, ep_node_intent,
        btn_connect_ep_sta, btn_connect_ep_ap, stop_ep_camera_pipeline
    )
    import nodes.robots.ep01 as ep_module
    HAS_EP = True
except ImportError:
    HAS_EP = False

sys_net_str = "Loading Network..."
def network_monitor_thread():
    global sys_net_str
    while True:
        try:
            out = subprocess.check_output("ip -o -4 addr show", shell=True).decode('utf-8')
            info = [f"[{p.split()[1]}] {p.split()[3].split('/')[0]}" for p in out.strip().split('\n') if ' lo ' not in p and len(p.split()) >= 4]
            sys_net_str = "\n".join(info) if info else "Offline"
        except: pass
        time.sleep(2)

# --- MT4 Callbacks ---
def mt4_manual_control_callback(sender, app_data, user_data):
    mt4_module.mt4_manual_override_until = time.time() + 1.5
    axis, step = user_data
    mt4_module.mt4_target_goal[axis] = mt4_module.mt4_current_pos[axis] + step
    mt4_apply_limits()

def mt4_move_to_coord_callback(sender, app_data, user_data):
    mt4_module.mt4_manual_override_until = time.time() + 2.0
    mt4_module.mt4_target_goal['x'] = float(dpg.get_value("input_x"))
    mt4_module.mt4_target_goal['y'] = float(dpg.get_value("input_y"))
    mt4_module.mt4_target_goal['z'] = float(dpg.get_value("input_z"))
    mt4_module.mt4_target_goal['gripper'] = float(dpg.get_value("input_g"))
    if dpg.does_item_exist("input_r"): 
        mt4_module.mt4_target_goal['roll'] = float(dpg.get_value("input_r"))
    mt4_apply_limits()

# --- Go1 Callbacks ---
go1_manual_override_until = 0.0
def go1_manual_control_callback(sender, app_data, user_data):
    global go1_manual_override_until
    if not HAS_GO1: return
    go1_manual_override_until = time.time() + 1.5
    axis, step = user_data
    go1_target_vel[axis] = go1_target_vel.get(axis, 0.0) + step

def go1_move_to_coord_callback(sender, app_data, user_data):
    global go1_manual_override_until
    if not HAS_GO1: return
    go1_manual_override_until = time.time() + 2.0
    go1_target_vel['vx'] = float(dpg.get_value("go1_input_vx"))
    go1_target_vel['vy'] = float(dpg.get_value("go1_input_vy"))
    go1_target_vel['vyaw'] = float(dpg.get_value("go1_input_vyaw"))

def go1_action_callback(sender, app_data, user_data):
    if not HAS_GO1: return
    cmd_str = str(user_data or "").strip()
    if not cmd_str:
        return

    if cmd_str.startswith("SPECIAL_") and hasattr(go1_module, 'request_go1_special_action'):
        action_name = cmd_str.split("_", 1)[1].lower()
        go1_module.request_go1_special_action(action_name)
        return

    if go1_module.go1_sock:
        try: go1_module.go1_sock.sendto(cmd_str.encode(), (go1_module.GO1_IP, go1_module.GO1_PORT))
        except: pass

def ep_manual_control_callback(sender, app_data, user_data):
    if not HAS_EP: return
    axis, step = user_data
    if axis in ("vx", "vy"):
        ep_node_intent[axis] = ep_node_intent.get(axis, 0.0) + step
        ep_node_intent['trigger_time'] = time.monotonic()
    elif axis == "wz":
        ep_node_intent['wz'] = ep_node_intent.get('wz', 0.0) + step
        ep_node_intent['trigger_time'] = time.monotonic()
    ep_target_vel['vx'] = ep_node_intent.get('vx', 0.0)
    ep_target_vel['vy'] = ep_node_intent.get('vy', 0.0)
    ep_target_vel['vz'] = ep_node_intent.get('wz', 0.0)

def ep_action_callback(sender, app_data, user_data):
    if not HAS_EP: return
    ep_module.send_ep_command(user_data)

class NodeUIRenderer:
    key_map = {"A": 65, "B": 66, "C": 67, "S": 83, "W": 87, "SPACE": 32}

    @staticmethod
    def _is_text_input_focused():
        # Block keyboard control when user is typing in any text input widget.
        try:
            active_item = dpg.get_active_item()
            if active_item:
                info = dpg.get_item_info(active_item)
                item_type = str(info.get('type', '')).lower()
                if 'mvappitemtype::mvinputtext' in item_type or 'inputtext' in item_type:
                    return True
        except Exception:
            pass

        # Fallback for known global inputs used outside node renderer.
        if dpg.does_item_exist("file_name_input") and dpg.is_item_focused("file_name_input"):
            return True
        if dpg.does_item_exist("path_name_input") and dpg.is_item_focused("path_name_input"):
            return True
        return False

    @staticmethod
    def sync_ui_to_state():
        is_focused = NodeUIRenderer._is_text_input_focused()
        input_manager.set_focused(is_focused)

        for nid, node in node_registry.items():
            t = node.type_str
            if t == "COND_KEY" and hasattr(node, 'field_key'):
                k = dpg.get_value(node.field_key).upper()
                node.state['key'] = k
                node.state['is_down'] = dpg.is_key_down(NodeUIRenderer.key_map.get(k, 0))
            elif t == "LOGIC_LOOP" and hasattr(node, 'field_count'):
                node.state['count'] = dpg.get_value(node.field_count)
            elif t == "MT4_ACTION" and hasattr(node, 'combo_id'):
                node.state['mode'] = dpg.get_value(node.combo_id)
                node.state['v1'] = dpg.get_value(node.field_v1); node.state['v2'] = dpg.get_value(node.field_v2); node.state['v3'] = dpg.get_value(node.field_v3)
            elif t == "CONSTANT" and hasattr(node, 'field_val'):
                node.state['val'] = dpg.get_value(node.field_val)
            elif t == "LOGGER" and hasattr(node, 'txt'):
                if len(system_log_buffer) != node.llen:
                    dpg.set_value(node.txt, "\n".join(list(system_log_buffer)[-8:]))
                    node.llen = len(system_log_buffer)
            elif t == "UDP_RECV" and hasattr(node, 'port'):
                node.state['port'] = dpg.get_value(node.port); node.state['ip'] = dpg.get_value(node.ip)
            elif t == "MT4_KEYBOARD" and hasattr(node, 'combo_keys'):
                node.state['is_focused'] = is_focused
                node.state['keys'] = dpg.get_value(node.combo_keys)
                node.state['W'] = dpg.is_key_down(dpg.mvKey_W); node.state['S'] = dpg.is_key_down(dpg.mvKey_S)
                node.state['A'] = dpg.is_key_down(dpg.mvKey_A); node.state['D'] = dpg.is_key_down(dpg.mvKey_D)
                node.state['UP'] = dpg.is_key_down(dpg.mvKey_Up); node.state['DOWN'] = dpg.is_key_down(dpg.mvKey_Down)
                node.state['LEFT'] = dpg.is_key_down(dpg.mvKey_Left); node.state['RIGHT'] = dpg.is_key_down(dpg.mvKey_Right)
                node.state['Q'] = dpg.is_key_down(dpg.mvKey_Q); node.state['E'] = dpg.is_key_down(dpg.mvKey_E)
                node.state['J'] = dpg.is_key_down(dpg.mvKey_J); node.state['U'] = dpg.is_key_down(dpg.mvKey_U)
                node.state['Z'] = dpg.is_key_down(dpg.mvKey_Z); node.state['X'] = dpg.is_key_down(dpg.mvKey_X)            
            elif t == "GO1_KEYBOARD" and hasattr(node, 'combo_keys'):
                node.state['is_focused'] = is_focused
                node.state['keys'] = dpg.get_value(node.combo_keys)
                node.state['W'] = dpg.is_key_down(dpg.mvKey_W); node.state['S'] = dpg.is_key_down(dpg.mvKey_S)
                node.state['A'] = dpg.is_key_down(dpg.mvKey_A); node.state['D'] = dpg.is_key_down(dpg.mvKey_D)
                node.state['UP'] = dpg.is_key_down(dpg.mvKey_Up); node.state['DOWN'] = dpg.is_key_down(dpg.mvKey_Down)
                node.state['LEFT'] = dpg.is_key_down(dpg.mvKey_Left); node.state['RIGHT'] = dpg.is_key_down(dpg.mvKey_Right)
                node.state['Q'] = dpg.is_key_down(dpg.mvKey_Q); node.state['E'] = dpg.is_key_down(dpg.mvKey_E)
                node.state['Z'] = dpg.is_key_down(dpg.mvKey_Z); node.state['X'] = dpg.is_key_down(dpg.mvKey_X)
                node.state['SPACE'] = dpg.is_key_down(dpg.mvKey_Spacebar)
                node.state['R_pressed'] = dpg.is_key_pressed(dpg.mvKey_R)
                node.state['C_pressed'] = dpg.is_key_pressed(dpg.mvKey_C)
            elif t == "EP_KEYBOARD" and hasattr(node, 'combo_keys'):
                node.state['is_focused'] = is_focused
                node.state['keys'] = dpg.get_value(node.combo_keys)
                node.state['W'] = dpg.is_key_down(dpg.mvKey_W); node.state['S'] = dpg.is_key_down(dpg.mvKey_S)
                node.state['A'] = dpg.is_key_down(dpg.mvKey_A); node.state['D'] = dpg.is_key_down(dpg.mvKey_D)
                node.state['UP'] = dpg.is_key_down(dpg.mvKey_Up); node.state['DOWN'] = dpg.is_key_down(dpg.mvKey_Down)
                node.state['LEFT'] = dpg.is_key_down(dpg.mvKey_Left); node.state['RIGHT'] = dpg.is_key_down(dpg.mvKey_Right)
                node.state['Q'] = dpg.is_key_down(dpg.mvKey_Q); node.state['E'] = dpg.is_key_down(dpg.mvKey_E)
                node.state['Z'] = dpg.is_key_down(dpg.mvKey_Z); node.state['X'] = dpg.is_key_down(dpg.mvKey_X)
                node.state['C'] = dpg.is_key_down(dpg.mvKey_C); node.state['V'] = dpg.is_key_down(dpg.mvKey_V)
                node.state['U_pressed'] = dpg.is_key_pressed(dpg.mvKey_U); node.state['J_pressed'] = dpg.is_key_pressed(dpg.mvKey_J)
                node.state['SPACE'] = dpg.is_key_down(dpg.mvKey_Spacebar)
            elif t in ["MT4_DRIVER", "GO1_DRIVER", "EP_DRIVER"]:
                for k, fid in getattr(node, 'ui_fields', {}).items():
                    pin_id = node.in_pins[k]
                    is_connected = any(l['target'] == pin_id for l in link_registry.values())
                    if not is_connected: 
                        # MT4일 때만 수동 조작 오버라이드 딜레이 적용
                        override_time = getattr(mt4_module, 'mt4_manual_override_until', 0) if t == "MT4_DRIVER" else 0
                        if time.time() < override_time: dpg.set_value(fid, node.state.get(k, 0.0))
                        else: node.state[k] = dpg.get_value(fid)
                for k, fid in getattr(node, 'setting_fields', {}).items():
                    pin_id = node.setting_pins[k]
                    is_connected = any(l['target'] == pin_id for l in link_registry.values())
                    if not is_connected: node.state[k] = dpg.get_value(fid)
            elif t == "MT4_SAG" and hasattr(node, 'ui_sag'): node.state['sag_factor'] = dpg.get_value(node.ui_sag)
            elif t == "MT4_CALIB" and hasattr(node, 'ui_x'):
                node.state['x_offset'] = dpg.get_value(node.ui_x); node.state['y_offset'] = dpg.get_value(node.ui_y)
                node.state['z_offset'] = dpg.get_value(node.ui_z); node.state['scale'] = dpg.get_value(node.ui_s)
            elif t == "MT4_TOOLTIP" and hasattr(node, 'ui_len'):
                node.state['tool_length'] = dpg.get_value(node.ui_len); node.state['tool_angle'] = dpg.get_value(node.ui_ang)
            elif t == "MT4_BACKLASH" and hasattr(node, 'ui_dist'):
                node.state['decel_dist'] = dpg.get_value(node.ui_dist); node.state['stop_delay'] = dpg.get_value(node.ui_dly)
            
            # --- [Vision & Go1] UI -> State ---
            elif t == "GO1_ACTION" and hasattr(node, 'combo_id'):
                node.state['mode'] = dpg.get_value(node.combo_id)
                node.state['v1'] = dpg.get_value(node.field_v1)
            elif t == "GO1_SERVER_SENDER" and hasattr(node, 'combo_action'):
                node.state['action'] = dpg.get_value(node.combo_action)
                node.state['server_url'] = dpg.get_value(node.field_url)
            elif t == "GO1_SERVER_JSON_RECV" and hasattr(node, 'combo_mode'):
                node.state['mode'] = dpg.get_value(node.combo_mode)
                node.state['source'] = dpg.get_value(node.field_source)
                node.state['poll_interval_sec'] = dpg.get_value(node.field_poll)
                node.state['request_timeout_sec'] = dpg.get_value(node.field_timeout)
                node.state['fresh_timeout_sec'] = dpg.get_value(node.field_fresh)
                node.state['move_speed'] = dpg.get_value(node.field_move_speed)
                node.state['move_duration_sec'] = dpg.get_value(node.field_move_duration)
            elif t == "GO1_UNITY" and hasattr(node, 'field_ip'):
                node.state['unity_ip'] = dpg.get_value(node.field_ip)
                node.state['enable_teleop_rx'] = dpg.get_value(node.chk_enable)
                node.state['send_aruco'] = dpg.get_value(node.chk_aruco)
            elif t == "VIS_ARUCO" and hasattr(node, 'ui_camera_id'):
                node.state['camera_id'] = dpg.get_value(node.ui_camera_id)
                node.state['marker_size_m'] = dpg.get_value(node.ui_marker_size_m)
                node.state['input_undistorted'] = dpg.get_value(node.ui_input_undistorted)
                node.state['draw_axes'] = dpg.get_value(node.ui_draw_axes)
                node.state['draw_overlay_text'] = dpg.get_value(node.ui_draw_overlay_text)
                node.state['json_path'] = dpg.get_value(node.ui_json_path)
            elif t == "EP_ACTION" and hasattr(node, 'combo_act'):
                node.state['action'] = dpg.get_value(node.combo_act)
            elif t == "VIDEO_SRC" and hasattr(node, 'ui_target_ip'):
                node.state['target_ip'] = dpg.get_value(node.ui_target_ip)
                if hasattr(node, 'ui_receiver_folder'):
                    node.state['receiver_folder'] = dpg.get_value(node.ui_receiver_folder)
            elif t == "VIS_FLASK" and hasattr(node, 'ui_port'):
                node.state['port'] = dpg.get_value(node.ui_port)
                node.state['is_running'] = dpg.get_value(node.ui_run)
            elif t == "VIS_FISHEYE" and hasattr(node, 'ui_enabled'):
                node.state['enabled'] = dpg.get_value(node.ui_enabled)
                node.state['crop_enabled'] = dpg.get_value(node.ui_crop_enabled)
                node.state['crop_mode'] = dpg.get_value(node.ui_crop_mode)
                node.state['crop_ratio'] = dpg.get_value(node.ui_crop_ratio)
            elif t == "VIS_DEPTH_DA2" and hasattr(node, 'ui_enabled'):
                node.state['enabled'] = dpg.get_value(node.ui_enabled)
                node.state['backend'] = dpg.get_value(node.ui_backend)
                node.state['encoder'] = dpg.get_value(node.ui_encoder)
                node.state['checkpoint_path'] = dpg.get_value(node.ui_checkpoint)
                node.state['hf_model_id'] = dpg.get_value(node.ui_hf_model)
                node.state['prefer_cuda'] = dpg.get_value(node.ui_prefer_cuda)
                node.state['input_size'] = dpg.get_value(node.ui_input_size)
                node.state['inference_interval_sec'] = dpg.get_value(node.ui_infer_interval)
                node.state['closer_is_brighter'] = dpg.get_value(node.ui_closer_is_brighter)
                node.state['risk_threshold'] = dpg.get_value(node.ui_risk_threshold)
                node.state['consecutive_frames_for_stop'] = dpg.get_value(node.ui_hits_for_stop)
                node.state['use_stop_signal'] = dpg.get_value(node.ui_use_stop_signal)
                node.state['save_json'] = dpg.get_value(node.ui_save_json)
                node.state['json_path'] = dpg.get_value(node.ui_json_path)
                node.state['roi_x0'] = dpg.get_value(node.ui_roi_x0)
                node.state['roi_y0'] = dpg.get_value(node.ui_roi_y0)
                node.state['roi_x1'] = dpg.get_value(node.ui_roi_x1)
                node.state['roi_y1'] = dpg.get_value(node.ui_roi_y1)
            elif t == "VIS_SAVE" and hasattr(node, 'ui_folder'):
                node.state['folder'] = dpg.get_value(node.ui_folder)
                node.state['duration'] = dpg.get_value(node.ui_duration)
                node.state['use_timer'] = dpg.get_value(node.ui_use_timer)
                node.state['max_frames'] = dpg.get_value(node.ui_max_frames)
            elif t == "EP_CAM_SRC" and hasattr(node, 'ui_url'):
                node.state['url'] = dpg.get_value(node.ui_url)
                node.state['prefer_sdk'] = dpg.get_value(node.chk_sdk)
            elif t == "EP_CAM_STREAM" and hasattr(node, 'ui_port'):
                node.state['port'] = dpg.get_value(node.ui_port)
                node.state['is_running'] = dpg.get_value(node.ui_run)
            elif t == "EP_VIS_SAVE" and hasattr(node, 'ui_folder'):
                node.state['folder'] = dpg.get_value(node.ui_folder)
                node.state['duration'] = dpg.get_value(node.ui_duration)
                node.state['use_timer'] = dpg.get_value(node.ui_use_timer)
                node.state['max_frames'] = dpg.get_value(node.ui_max_frames)
            elif t == "EP_SERVER_SENDER" and hasattr(node, 'combo_action'):
                node.state['action'] = dpg.get_value(node.combo_action)
                node.state['server_url'] = dpg.get_value(node.field_url)

    @staticmethod
    def sync_state_to_ui(node):
        t = node.type_str
        if t == "COND_KEY" and hasattr(node, 'field_key'): dpg.set_value(node.field_key, node.state.get('key', 'SPACE'))
        elif t == "LOGIC_LOOP" and hasattr(node, 'field_count'): dpg.set_value(node.field_count, node.state.get('count', 3))
        elif t == "MT4_ACTION" and hasattr(node, 'combo_id'):
            dpg.set_value(node.combo_id, node.state.get('mode', 'Move Relative (XYZ)'))
            dpg.set_value(node.field_v1, node.state.get('v1', 0)); dpg.set_value(node.field_v2, node.state.get('v2', 0)); dpg.set_value(node.field_v3, node.state.get('v3', 0))
        elif t == "CONSTANT" and hasattr(node, 'field_val'): dpg.set_value(node.field_val, node.state.get('val', 1.0))
        elif t == "UDP_RECV" and hasattr(node, 'port'):
            dpg.set_value(node.port, node.state.get('port', 6000)); dpg.set_value(node.ip, node.state.get('ip', '192.168.50.63'))
        elif t == "MT4_KEYBOARD" and hasattr(node, 'combo_keys'): dpg.set_value(node.combo_keys, node.state.get('keys', 'WASD'))
        elif t in ["MT4_DRIVER", "GO1_DRIVER", "EP_DRIVER"]:
            for k, fid in getattr(node, 'ui_fields', {}).items(): dpg.set_value(fid, node.state.get(k, 0.0))
            for k, fid in getattr(node, 'setting_fields', {}).items(): dpg.set_value(fid, node.state.get(k, 1.0))
            
        # --- [Vision & Go1] State -> UI ---
        elif t == "GO1_KEYBOARD" and hasattr(node, 'combo_keys'): 
            dpg.set_value(node.combo_keys, node.state.get('keys', 'WASD'))
        elif t == "EP_KEYBOARD" and hasattr(node, 'combo_keys'):
            dpg.set_value(node.combo_keys, node.state.get('keys', 'WASD'))
        elif t == "GO1_ACTION" and hasattr(node, 'combo_id'):
            dpg.set_value(node.combo_id, node.state.get('mode', 'Stand'))
            dpg.set_value(node.field_v1, node.state.get('v1', 0.2))
        elif t == "GO1_SERVER_SENDER" and hasattr(node, 'combo_action'):
            dpg.set_value(node.combo_action, node.state.get('action', 'Start Sender'))
            dpg.set_value(node.field_url, node.state.get('server_url', "http://192.168.1.100:5001/upload"))
        elif t == "GO1_SERVER_JSON_RECV" and hasattr(node, 'combo_mode'):
            dpg.set_value(node.combo_mode, node.state.get('mode', 'HTTP'))
            dpg.set_value(node.field_source, node.state.get('source', 'http://127.0.0.1:5001/cmd'))
            dpg.set_value(node.field_poll, float(node.state.get('poll_interval_sec', 0.05)))
            dpg.set_value(node.field_timeout, float(node.state.get('request_timeout_sec', 2.0)))
            dpg.set_value(node.field_fresh, float(node.state.get('fresh_timeout_sec', 0.2)))
            dpg.set_value(node.field_move_speed, float(node.state.get('move_speed', 0.2)))
            dpg.set_value(node.field_move_duration, float(node.state.get('move_duration_sec', 0.5)))
        elif t == "GO1_UNITY" and hasattr(node, 'field_ip'):
            dpg.set_value(node.field_ip, node.state.get('unity_ip', getattr(go1_module, 'GO1_UNITY_IP', '192.168.50.246')))
            dpg.set_value(node.chk_enable, node.state.get('enable_teleop_rx', True))
            dpg.set_value(node.chk_aruco, node.state.get('send_aruco', False))
        elif t == "VIS_ARUCO" and hasattr(node, 'ui_camera_id'):
            dpg.set_value(node.ui_camera_id, node.state.get('camera_id', 'go1_front'))
            dpg.set_value(node.ui_marker_size_m, node.state.get('marker_size_m', 0.03))
            dpg.set_value(node.ui_input_undistorted, node.state.get('input_undistorted', False))
            dpg.set_value(node.ui_draw_axes, node.state.get('draw_axes', True))
            dpg.set_value(node.ui_draw_overlay_text, node.state.get('draw_overlay_text', True))
            dpg.set_value(node.ui_json_path, node.state.get('json_path', 'aruco_data.json'))
        elif t == "VIDEO_SRC" and hasattr(node, 'ui_target_ip'):
            default_target_ip = '127.0.0.1'
            if HAS_GO1 and hasattr(go1_module, 'get_local_ip'):
                default_target_ip = go1_module.get_local_ip()
            dpg.set_value(node.ui_target_ip, node.state.get('target_ip', default_target_ip))
            if hasattr(node, 'ui_receiver_folder'):
                dpg.set_value(node.ui_receiver_folder, node.state.get('receiver_folder', 'Captured_Images/go1_front'))
        elif t == "VIS_FLASK" and hasattr(node, 'ui_port'):
            dpg.set_value(node.ui_port, node.state.get('port', 5000))
            dpg.set_value(node.ui_run, node.state.get('is_running', False))
        elif t == "VIS_FISHEYE" and hasattr(node, 'ui_enabled'):
            dpg.set_value(node.ui_enabled, node.state.get('enabled', True))
            dpg.set_value(node.ui_crop_enabled, node.state.get('crop_enabled', True))
            dpg.set_value(node.ui_crop_mode, node.state.get('crop_mode', 'left_half'))
            dpg.set_value(node.ui_crop_ratio, node.state.get('crop_ratio', 0.5))
        elif t == "VIS_DEPTH_DA2" and hasattr(node, 'ui_enabled'):
            dpg.set_value(node.ui_enabled, node.state.get('enabled', True))
            dpg.set_value(node.ui_backend, node.state.get('backend', 'transformers'))
            dpg.set_value(node.ui_encoder, node.state.get('encoder', 'vits'))
            dpg.set_value(node.ui_checkpoint, node.state.get('checkpoint_path', 'checkpoints/depth_anything_v2_vits.pth'))
            dpg.set_value(node.ui_hf_model, node.state.get('hf_model_id', 'depth-anything/Depth-Anything-V2-Small-hf'))
            dpg.set_value(node.ui_prefer_cuda, node.state.get('prefer_cuda', True))
            dpg.set_value(node.ui_input_size, int(node.state.get('input_size', 518)))
            dpg.set_value(node.ui_infer_interval, float(node.state.get('inference_interval_sec', 0.2)))
            dpg.set_value(node.ui_closer_is_brighter, node.state.get('closer_is_brighter', True))
            dpg.set_value(node.ui_risk_threshold, float(node.state.get('risk_threshold', 0.65)))
            dpg.set_value(node.ui_hits_for_stop, int(node.state.get('consecutive_frames_for_stop', 2)))
            dpg.set_value(node.ui_use_stop_signal, bool(node.state.get('use_stop_signal', False)))
            dpg.set_value(node.ui_save_json, bool(node.state.get('save_json', False)))
            dpg.set_value(node.ui_json_path, node.state.get('json_path', 'depth_da2_data.json'))
            dpg.set_value(node.ui_roi_x0, float(node.state.get('roi_x0', 0.3)))
            dpg.set_value(node.ui_roi_y0, float(node.state.get('roi_y0', 0.5)))
            dpg.set_value(node.ui_roi_x1, float(node.state.get('roi_x1', 0.7)))
            dpg.set_value(node.ui_roi_y1, float(node.state.get('roi_y1', 0.95)))
        elif t == "VIS_SAVE" and hasattr(node, 'ui_folder'):
            dpg.set_value(node.ui_folder, node.state.get('folder', 'Captured_Images/go1_saved'))
            dpg.set_value(node.ui_duration, node.state.get('duration', 10.0))
            dpg.set_value(node.ui_use_timer, node.state.get('use_timer', False))
            dpg.set_value(node.ui_max_frames, node.state.get('max_frames', 100))
        elif t == "EP_CAM_SRC" and hasattr(node, 'ui_url'):
            dpg.set_value(node.ui_url, node.state.get('url', 'rtsp://192.168.42.2/live'))
            dpg.set_value(node.chk_sdk, node.state.get('prefer_sdk', True))
        elif t == "EP_CAM_STREAM" and hasattr(node, 'ui_port'):
            dpg.set_value(node.ui_port, node.state.get('port', 5050))
            dpg.set_value(node.ui_run, node.state.get('is_running', False))
        elif t == "EP_VIS_SAVE" and hasattr(node, 'ui_folder'):
            dpg.set_value(node.ui_folder, node.state.get('folder', 'Captured_Images/ep01_saved'))
            dpg.set_value(node.ui_duration, node.state.get('duration', 10.0))
            dpg.set_value(node.ui_use_timer, node.state.get('use_timer', False))
            dpg.set_value(node.ui_max_frames, node.state.get('max_frames', 100))
        elif t == "EP_SERVER_SENDER" and hasattr(node, 'combo_action'):
            dpg.set_value(node.combo_action, node.state.get('action', 'Start Sender'))
            dpg.set_value(node.field_url, node.state.get('server_url', 'http://210.110.250.33:5002/upload'))

        elif t == "EP_ACTION" and hasattr(node, 'combo_act'): 
            dpg.set_value(node.combo_act, node.state.get('action', 'LED Red'))

    @staticmethod
    def render(node):
        t = node.type_str
        if t == "START": NodeUIRenderer._render_start(node)
        elif t == "COND_KEY": NodeUIRenderer._render_cond_key(node)
        elif t == "LOGIC_IF": NodeUIRenderer._render_logic_if(node)
        elif t == "LOGIC_LOOP": NodeUIRenderer._render_logic_loop(node)
        elif t == "MT4_ACTION": NodeUIRenderer._render_mt4_action(node)
        elif t == "CONSTANT": NodeUIRenderer._render_constant(node)
        elif t == "PRINT": NodeUIRenderer._render_print(node)
        elif t == "LOGGER": NodeUIRenderer._render_logger(node)
        elif t in ["MT4_DRIVER", "GO1_DRIVER", "EP_DRIVER"]: NodeUIRenderer._render_universal(node)
        elif t == "MT4_KEYBOARD": NodeUIRenderer._render_mt4_keyboard(node)
        elif t == "MT4_UNITY": NodeUIRenderer._render_mt4_unity(node)
        elif t == "UDP_RECV": NodeUIRenderer._render_udp(node)
        elif t == "MT4_SAG": NodeUIRenderer._render_sag(node)
        elif t == "MT4_CALIB": NodeUIRenderer._render_calib(node)
        elif t == "MT4_TOOLTIP": NodeUIRenderer._render_tooltip(node)
        elif t == "MT4_BACKLASH": NodeUIRenderer._render_backlash(node)
        # --- Go1 & Vision ---
        elif t == "GO1_KEYBOARD": NodeUIRenderer._render_go1_keyboard(node)
        elif t == "EP_KEYBOARD": NodeUIRenderer._render_ep_keyboard(node)
        elif t == "GO1_UNITY": NodeUIRenderer._render_go1_unity(node)
        elif t == "GO1_ACTION": NodeUIRenderer._render_go1_action(node)
        elif t == "GO1_SERVER_SENDER": NodeUIRenderer._render_go1_server_sender(node)
        elif t == "GO1_SERVER_JSON_RECV": NodeUIRenderer._render_go1_server_json_recv(node)
        elif t == "VIDEO_SRC": NodeUIRenderer._render_video_src(node)
        elif t == "VIS_FISHEYE": NodeUIRenderer._render_fisheye(node)
        elif t == "VIS_DEPTH_DA2": NodeUIRenderer._render_depth_da2(node)
        elif t == "VIS_ARUCO": NodeUIRenderer._render_aruco(node)
        elif t == "VIS_FLASK": NodeUIRenderer._render_flask(node)
        elif t == "VIS_SAVE": NodeUIRenderer._render_video_save(node)
        # --- EP01 ---
        elif t == "EP_ACTION": NodeUIRenderer._render_ep_action(node)
        elif t == "EP_CAM_SRC": NodeUIRenderer._render_ep_cam_src(node)
        elif t == "EP_CAM_STREAM": NodeUIRenderer._render_ep_cam_stream(node)
        elif t == "EP_VIS_SAVE": NodeUIRenderer._render_video_save(node)
        elif t == "EP_SERVER_SENDER": NodeUIRenderer._render_ep_server_sender(node)


    @staticmethod
    def _render_start(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(tag=node.out, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")
    @staticmethod
    def _render_cond_key(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Check Key (One-Shot)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): 
                dpg.add_text("Key (A-Z, SPACE):"); node.field_key = dpg.add_input_text(width=60, default_value="SPACE")
            with dpg.node_attribute(tag=node.out_res, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Is Pressed?")
    @staticmethod
    def _render_logic_if(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="IF Condition"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(tag=node.in_cond, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Condition", color=(255,100,100))
            with dpg.node_attribute(tag=node.out_true, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("True", color=(100,255,100))
            with dpg.node_attribute(tag=node.out_false, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("False", color=(255,100,100))
    @staticmethod
    def _render_logic_loop(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="For Loop"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            _f_in2 = generate_uuid(); node.inputs[_f_in2] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in2, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Loop Back", color=(255,200,100))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): 
                dpg.add_text("Count:"); node.field_count = dpg.add_input_int(width=80, default_value=3, min_value=1)
            with dpg.node_attribute(tag=node.out_loop, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Loop Body", color=(100,200,255))
            with dpg.node_attribute(tag=node.out_finish, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Finished", color=(200,200,200))
    @staticmethod
    def _render_mt4_action(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="MT4 Action"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_id = dpg.add_combo(["Move Relative (XYZ)", "Move Absolute (XYZ)", "Set Gripper (Abs)", "Grip Relative (Add)", "Homing"], default_value="Move Relative (XYZ)", width=150)
            with dpg.node_attribute(tag=node.in_val1, attribute_type=dpg.mvNode_Attr_Input): 
                dpg.add_text("X / Grip"); node.field_v1 = dpg.add_input_float(width=60, default_value=0)
            with dpg.node_attribute(tag=node.in_val2, attribute_type=dpg.mvNode_Attr_Input): 
                dpg.add_text("Y"); node.field_v2 = dpg.add_input_float(width=60, default_value=0)
            with dpg.node_attribute(tag=node.in_val3, attribute_type=dpg.mvNode_Attr_Input): 
                dpg.add_text("Z"); node.field_v3 = dpg.add_input_float(width=60, default_value=0)
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")
    @staticmethod
    def _render_constant(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Constant"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): node.field_val = dpg.add_input_float(width=80, default_value=1.0)
            with dpg.node_attribute(tag=node.out_val, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Data")
    @staticmethod
    def _render_print(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Print Log"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(tag=node.inp_data, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Data")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")
    @staticmethod
    def _render_logger(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="System Log (Flowless)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.child_window(width=200, height=100): node.txt=dpg.add_text("", wrap=190)

    @staticmethod
    def _render_universal(node):
        driver_name = getattr(node.driver.__class__, '__name__', 'MT4')
        label_str = "Go1 Core Driver" if "Go1" in driver_name else "EP Core Driver" if "EP" in driver_name else "MT4 Core Driver"
        with dpg.node(tag=node.node_id, parent="node_editor", label=label_str):
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            node.ui_fields = {}; node.setting_fields = {}
            for key, label, default_val in node.driver.get_ui_schema():
                aid = node.in_pins[key]
                with dpg.node_attribute(tag=aid, attribute_type=dpg.mvNode_Attr_Input):
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label, color=(255,255,0)); node.ui_fields[key] = dpg.add_input_float(width=80, default_value=default_val, step=0)
            dpg.add_node_attribute(attribute_type=dpg.mvNode_Attr_Static)
            for key, label, default_val in node.driver.get_settings_schema():
                aid = node.setting_pins[key]
                with dpg.node_attribute(tag=aid, attribute_type=dpg.mvNode_Attr_Input):
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label); node.setting_fields[key] = dpg.add_input_float(width=60, default_value=default_val, step=0)

    @staticmethod
    def _render_mt4_keyboard(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="MT4 Keyboard"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("XY Move / QE: Z / UJ: Grip", color=(255,150,150)); dpg.add_text("ZX: Roll", color=(150,255,150))
            with dpg.node_attribute(tag=node.out_x, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target X")
            with dpg.node_attribute(tag=node.out_y, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Y")
            with dpg.node_attribute(tag=node.out_z, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Z")
            with dpg.node_attribute(tag=node.out_r, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Roll")
            with dpg.node_attribute(tag=node.out_g, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Grip")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")
    @staticmethod
    def _render_mt4_unity(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Unity Logic (MT4)"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(tag=node.data_in_id, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("JSON")
            with dpg.node_attribute(tag=node.out_x, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target X")
            with dpg.node_attribute(tag=node.out_y, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Y")
            with dpg.node_attribute(tag=node.out_z, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Z")
            with dpg.node_attribute(tag=node.out_r, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Roll")
            with dpg.node_attribute(tag=node.out_g, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Grip")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")
    @staticmethod
    def _render_udp(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="UDP Receiver"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_int(label="Port", width=80, default_value=6000, tag=f"p_{node.node_id}"); node.port = f"p_{node.node_id}"
                dpg.add_input_text(label="IP", width=100, default_value="192.168.50.63", tag=f"i_{node.node_id}"); node.ip = f"i_{node.node_id}"
            with dpg.node_attribute(tag=node.out_json, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("JSON Out")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")
    @staticmethod
    def _render_sag(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label=node.label):
            with dpg.node_attribute(tag=node.in_x, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("X In")
            with dpg.node_attribute(tag=node.in_z, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Z In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): 
                node.ui_sag = dpg.add_input_float(label="Sag Factor", width=80, default_value=0.05, step=0.01)
            with dpg.node_attribute(tag=node.out_z, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Z Out (Comp)", color=(100,255,100))
    @staticmethod
    def _render_calib(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label=node.label):
            with dpg.node_attribute(tag=node.in_x, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("X In")
            with dpg.node_attribute(tag=node.in_y, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Y In")
            with dpg.node_attribute(tag=node.in_z, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Z In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_x = dpg.add_input_float(label="X Offset", width=70, default_value=0.0)
                node.ui_y = dpg.add_input_float(label="Y Offset", width=70, default_value=0.0)
                node.ui_z = dpg.add_input_float(label="Z Offset", width=70, default_value=0.0)
                node.ui_s = dpg.add_input_float(label="Scale", width=70, default_value=1.0)
            with dpg.node_attribute(tag=node.out_x, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("X Out", color=(100,255,100))
            with dpg.node_attribute(tag=node.out_y, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Y Out", color=(100,255,100))
            with dpg.node_attribute(tag=node.out_z, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Z Out", color=(100,255,100))
    @staticmethod
    def _render_tooltip(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label=node.label):
            with dpg.node_attribute(tag=node.in_x, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("X In")
            with dpg.node_attribute(tag=node.in_z, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Z In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_len = dpg.add_input_float(label="Tool Len(mm)", width=70, default_value=0.0)
                node.ui_ang = dpg.add_input_float(label="Angle(deg)", width=70, default_value=0.0)
            with dpg.node_attribute(tag=node.out_x, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("X Out (Comp)", color=(100,255,100))
            with dpg.node_attribute(tag=node.out_z, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Z Out (Comp)", color=(100,255,100))
    @staticmethod
    def _render_backlash(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label=node.label):
            with dpg.node_attribute(tag=node.in_x, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("X In")
            with dpg.node_attribute(tag=node.in_y, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Y In")
            with dpg.node_attribute(tag=node.in_z, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Z In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_dist = dpg.add_input_float(label="Decel Dist", width=70, default_value=15.0)
                node.ui_dly = dpg.add_input_float(label="Stop Delay", width=70, default_value=100.0)
            with dpg.node_attribute(tag=node.out_x, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("X Out", color=(100,255,100))
            with dpg.node_attribute(tag=node.out_y, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Y Out", color=(100,255,100))
            with dpg.node_attribute(tag=node.out_z, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Z Out", color=(100,255,100))

    # --- [추가] Go1 & Vision Renderers ---

    @staticmethod
    def _render_go1_keyboard(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Go1 Keyboard"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("Move / QE: Turn\nZ/X: Body Height +/-\nSpace: Stop / R: Yaw Align / C: Reset Yaw", color=(255,150,150))
            with dpg.node_attribute(tag=node.out_vx, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Vx")
            with dpg.node_attribute(tag=node.out_vy, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Vy")
            with dpg.node_attribute(tag=node.out_vyaw, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Yaw")
            with dpg.node_attribute(tag=node.out_body_height, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Body Height")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_go1_unity(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Unity Logic (Go1)"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text("Unity PC IP", color=(100,255,100))
                node.field_ip = dpg.add_input_text(width=140, default_value=getattr(go1_module, 'GO1_UNITY_IP', '192.168.50.246'))
                node.chk_enable = dpg.add_checkbox(label="Enable Teleop Rx", default_value=True)
                node.chk_aruco = dpg.add_checkbox(label="Send ArUco Data (JSON)", default_value=False)
            with dpg.node_attribute(tag=node.data_in_id, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("JSON")
            with dpg.node_attribute(tag=node.out_vx, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Vx")
            with dpg.node_attribute(tag=node.out_vy, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Vy")
            with dpg.node_attribute(tag=node.out_vyaw, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Yaw")
            with dpg.node_attribute(tag=node.out_body_height, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Body Height")
            with dpg.node_attribute(tag=node.out_active, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Is Active?")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_go1_action(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Go1 Action"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_id = dpg.add_combo(
                    [
                        "Stand", "Reset Yaw0", "Walk Fwd/Back", "Walk Strafe", "Turn",
                        "Sit Down", "Stand Tall", "Set Body Height",
                        "Backflip", "Jump Yaw", "Straight Hand", "Dance 1", "Dance 2"
                    ],
                    default_value="Stand",
                    width=150
                )
            with dpg.node_attribute(tag=node.in_val1, attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Speed/Val")
                node.field_v1 = dpg.add_input_float(width=60, default_value=0.2)
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_go1_server_sender(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Server Sender (Go1)"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_action = dpg.add_combo(
                    ["Start Sender", "Stop Sender"],
                    default_value="Start Sender",
                    width=140
                )
                dpg.add_spacer(height=3)
                dpg.add_text("Server URL:")
                node.field_url = dpg.add_input_text(width=160, default_value="http://192.168.1.100:5001/upload")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_go1_server_json_recv(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Server JSON Receiver"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_mode = dpg.add_combo(["HTTP", "FILE"], default_value=node.state.get('mode', 'HTTP'), width=90)
                node.field_source = dpg.add_input_text(width=220, default_value=node.state.get('source', 'http://127.0.0.1:5001/cmd'))
                node.field_poll = dpg.add_input_float(label="Poll (sec)", width=100, default_value=float(node.state.get('poll_interval_sec', 0.05)), step=0.01)
                node.field_timeout = dpg.add_input_float(label="Request Timeout", width=120, default_value=float(node.state.get('request_timeout_sec', 2.0)), step=0.1)
                node.field_fresh = dpg.add_input_float(label="Fresh Timeout", width=120, default_value=float(node.state.get('fresh_timeout_sec', 0.2)), step=0.05)
                node.field_move_speed = dpg.add_input_float(label="Move Speed", width=100, default_value=float(node.state.get('move_speed', 0.2)), step=0.01)
                node.field_move_duration = dpg.add_input_float(label="Move Duration", width=120, default_value=float(node.state.get('move_duration_sec', 0.5)), step=0.05)
                dpg.add_text("Source can be a JSON URL or a local JSON file.", color=(180,180,180))
            with dpg.node_attribute(tag=node.out_raw_json, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Raw JSON")
            with dpg.node_attribute(tag=node.out_vx, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Vx")
            with dpg.node_attribute(tag=node.out_vy, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Vy")
            with dpg.node_attribute(tag=node.out_wz, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Wz")
            with dpg.node_attribute(tag=node.out_stop, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Stop")
            with dpg.node_attribute(tag=node.out_seq, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Seq")
            with dpg.node_attribute(tag=node.out_ts, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Ts")
            with dpg.node_attribute(tag=node.out_confidence, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Confidence")
            with dpg.node_attribute(tag=node.out_connected, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Connected")
            with dpg.node_attribute(tag=node.out_fresh, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Fresh")
            with dpg.node_attribute(tag=node.out_status, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Status")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_video_src(node):
        default_target_ip = "127.0.0.1"
        if HAS_GO1 and hasattr(go1_module, 'get_local_ip'):
            default_target_ip = go1_module.get_local_ip()
        with dpg.node(tag=node.node_id, parent="node_editor", label="Video Source"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_target_ip = dpg.add_input_text(label="Target IP", width=150, default_value=default_target_ip)
                node.ui_receiver_folder = dpg.add_input_text(
                    label="Receiver Folder",
                    width=220,
                    default_value=node.state.get('receiver_folder', 'Captured_Images/go1_front')
                )
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Frame Data", color=(255,255,0))

    @staticmethod
    def _render_fisheye(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Fisheye Undistort"):
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_enabled = dpg.add_checkbox(label="Enable Calibration", default_value=bool(node.state.get('enabled', True)))
                node.ui_crop_enabled = dpg.add_checkbox(label="Crop After Calibration", default_value=bool(node.state.get('crop_enabled', True)))
                node.ui_crop_mode = dpg.add_combo(["left_half", "custom_ratio"], default_value=str(node.state.get('crop_mode', 'left_half')), width=120)
                node.ui_crop_ratio = dpg.add_input_float(label="Crop Ratio", width=100, default_value=float(node.state.get('crop_ratio', 0.5)), step=0.05)
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Frame Out", color=(255,255,0))

    @staticmethod
    def _render_aruco(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="ArUco Detect"):
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Draw Frame", color=(255,255,0))
            with dpg.node_attribute(tag=node.out_data, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Marker Info", color=(100,200,255))
            if hasattr(node, 'out_json'):
                with dpg.node_attribute(tag=node.out_json, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("JSON Payload", color=(255,200,0))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_separator()
                dpg.add_text("ArUco Settings", color=(0,255,255))
                node.ui_camera_id = dpg.add_input_text(label="Camera ID", width=160, default_value=node.state.get('camera_id', 'go1_front'))
                node.ui_marker_size_m = dpg.add_input_float(label="Marker Size (m)", width=120, default_value=float(node.state.get('marker_size_m', 0.03)), step=0.005)
                node.ui_input_undistorted = dpg.add_checkbox(label="Input Already Undistorted", default_value=bool(node.state.get('input_undistorted', False)))
                node.ui_draw_axes = dpg.add_checkbox(label="Draw 3D Axes", default_value=bool(node.state.get('draw_axes', True)))
                node.ui_draw_overlay_text = dpg.add_checkbox(label="Draw Overlay Text", default_value=bool(node.state.get('draw_overlay_text', True)))
                dpg.add_separator()
                dpg.add_text("Record", color=(255,200,0))
                node.ui_json_path = dpg.add_input_text(label="JSON Path", width=220, default_value=node.state.get('json_path', 'aruco_data.json'))
                dpg.add_text("UDP send and JSON write run when Go1 Unity node 'Send ArUco Data' is ON.", color=(180,180,180), wrap=240)

    @staticmethod
    def _render_depth_da2(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Depth Anything V2"):
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Depth Vis", color=(255,255,0))
            with dpg.node_attribute(tag=node.out_depth, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Depth Raw", color=(100,200,255))
            with dpg.node_attribute(tag=node.out_near_score, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Near Score", color=(255,200,0))
            with dpg.node_attribute(tag=node.out_obstacle, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Obstacle", color=(255,120,120))
            with dpg.node_attribute(tag=node.out_json, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Risk JSON", color=(255,220,120))

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_separator()
                dpg.add_text("Inference", color=(0,255,255))
                node.ui_enabled = dpg.add_checkbox(label="Enable", default_value=bool(node.state.get('enabled', True)))
                node.ui_backend = dpg.add_combo(["transformers", "official"], default_value=str(node.state.get('backend', 'transformers')), width=120)
                node.ui_encoder = dpg.add_combo(["vits", "vitb", "vitl"], default_value=str(node.state.get('encoder', 'vits')), width=120)
                node.ui_checkpoint = dpg.add_input_text(label="Checkpoint", width=220, default_value=node.state.get('checkpoint_path', 'checkpoints/depth_anything_v2_vits.pth'))
                node.ui_hf_model = dpg.add_input_text(label="HF Model", width=220, default_value=node.state.get('hf_model_id', 'depth-anything/Depth-Anything-V2-Small-hf'))
                node.ui_prefer_cuda = dpg.add_checkbox(label="Prefer CUDA", default_value=bool(node.state.get('prefer_cuda', True)))
                node.ui_input_size = dpg.add_input_int(label="Input Size", width=100, default_value=int(node.state.get('input_size', 518)), step=16)
                node.ui_infer_interval = dpg.add_input_float(label="Infer Interval(s)", width=100, default_value=float(node.state.get('inference_interval_sec', 0.2)), step=0.05)

                dpg.add_separator()
                dpg.add_text("Risk", color=(255,200,0))
                node.ui_closer_is_brighter = dpg.add_checkbox(label="Closer Is Brighter", default_value=bool(node.state.get('closer_is_brighter', True)))
                node.ui_risk_threshold = dpg.add_input_float(label="Risk Threshold", width=100, default_value=float(node.state.get('risk_threshold', 0.65)), step=0.05)
                node.ui_hits_for_stop = dpg.add_input_int(label="Hits For Stop", width=100, default_value=int(node.state.get('consecutive_frames_for_stop', 2)), step=1)
                node.ui_use_stop_signal = dpg.add_checkbox(label="Use Stop Signal", default_value=bool(node.state.get('use_stop_signal', False)))
                node.ui_save_json = dpg.add_checkbox(label="Save JSON", default_value=bool(node.state.get('save_json', False)))
                node.ui_json_path = dpg.add_input_text(label="JSON Path", width=220, default_value=node.state.get('json_path', 'depth_da2_data.json'))

                dpg.add_separator()
                dpg.add_text("ROI (0.0 - 1.0)", color=(180,180,180))
                node.ui_roi_x0 = dpg.add_input_float(label="ROI X0", width=90, default_value=float(node.state.get('roi_x0', 0.3)), step=0.05)
                node.ui_roi_y0 = dpg.add_input_float(label="ROI Y0", width=90, default_value=float(node.state.get('roi_y0', 0.5)), step=0.05)
                node.ui_roi_x1 = dpg.add_input_float(label="ROI X1", width=90, default_value=float(node.state.get('roi_x1', 0.7)), step=0.05)
                node.ui_roi_y1 = dpg.add_input_float(label="ROI Y1", width=90, default_value=float(node.state.get('roi_y1', 0.95)), step=0.05)

    @staticmethod
    def _render_flask(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Flask Stream"):
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_port = dpg.add_input_int(label="Port", width=80, default_value=5000)
                node.ui_run = dpg.add_checkbox(label="Start Server")

    @staticmethod
    def _render_video_save(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Video Save"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text("Folder:"); node.ui_folder = dpg.add_input_text(width=180, default_value=node.state.get('folder', 'Captured_Images/go1_saved'))
                dpg.add_text("Duration(s):"); node.ui_duration = dpg.add_input_float(width=80, default_value=float(node.state.get('duration', 10.0)), step=1.0)
                node.ui_use_timer = dpg.add_checkbox(label="Use Timer", default_value=bool(node.state.get('use_timer', False)))
                dpg.add_text("Max Frames:"); node.ui_max_frames = dpg.add_input_int(width=80, default_value=int(node.state.get('max_frames', 100)), step=10)
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_ep_action(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="EP Action"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_act = dpg.add_combo(["LED Red", "LED Blue", "Blaster Fire", "Arm Center", "Grip Open", "Grip Close"], default_value="LED Red", width=120)
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_ep_keyboard(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Keyboard (EP)"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("Move / QE: Turn\nZ/X/C/V: Arm Move\nU/J: Gripper Open/Close\nSpace: Stop", color=(100,255,100))
            with dpg.node_attribute(tag=node.out_vx, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Vx")
            with dpg.node_attribute(tag=node.out_vy, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Vy")
            with dpg.node_attribute(tag=node.out_wz, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Target Wz")
            with dpg.node_attribute(tag=node.out_arm_dx, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Arm dX")
            with dpg.node_attribute(tag=node.out_arm_dy, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Arm dY")
            with dpg.node_attribute(tag=node.out_grip_open, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Grip Open")
            with dpg.node_attribute(tag=node.out_grip_close, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Grip Close")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_ep_cam_src(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="EP Camera Source"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_url = dpg.add_input_text(label="URL", width=220, default_value="rtsp://192.168.42.2/live")
                node.chk_sdk = dpg.add_checkbox(label="Prefer SDK Camera", default_value=True)
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Frame Data", color=(255, 255, 0))
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Flow Out")

    @staticmethod
    def _render_ep_cam_stream(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="EP Camera Stream"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Flow In")
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Frame In", color=(255, 255, 0))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_port = dpg.add_input_int(label="Port", width=80, default_value=5050)
                node.ui_run = dpg.add_checkbox(label="Start Server")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Flow Out")

    @staticmethod
    def _render_ep_server_sender(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Server Sender (EP)"):
            with dpg.node_attribute(tag=node.in_flow, attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_action = dpg.add_combo(
                    ["Start Sender", "Stop Sender"],
                    default_value="Start Sender",
                    width=140
                )
                dpg.add_spacer(height=3)
                dpg.add_text("Server URL:")
                node.field_url = dpg.add_input_text(width=180, default_value="http://210.110.250.33:5002/upload")
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("Flow Out")

# Callback functions
def toggle_exec(s, a): 
    engine_module.is_running = not engine_module.is_running
    dpg.set_item_label("btn_run", "STOP" if engine_module.is_running else "RUN SCRIPT")
    if HAS_GO1 and engine_module.is_running:
        for node in node_registry.values():
            if node.type_str == 'VIDEO_SRC' and hasattr(node, '_auto_stopped_by_timer'):
                node._auto_stopped_by_timer = False
            if node.type_str == 'VIS_SAVE':
                if hasattr(node, '_timer_completed_this_run'):
                    node._timer_completed_this_run = False
                if hasattr(node, '_save_start_time'):
                    node._save_start_time = None
                if hasattr(node, '_frame_count'):
                    node._frame_count = 0
                if hasattr(node, '_frame_index'):
                    node._frame_index = 0
    if HAS_GO1 and not engine_module.is_running and hasattr(go1_module, 'camera_command_queue'):
        try:
            go1_module.camera_command_queue.append(('STOP', ''))
        except Exception:
            pass
    if HAS_GO1 and not engine_module.is_running:
        for node in node_registry.values():
            if node.type_str == 'VIDEO_SRC':
                if hasattr(node, '_started'):
                    node._started = False
                if hasattr(node, '_last_file'):
                    node._last_file = None
                if hasattr(node, '_last_frame'):
                    node._last_frame = None
                if hasattr(node, '_auto_stopped_by_timer'):
                    node._auto_stopped_by_timer = False
            if node.type_str == 'VIS_SAVE':
                if hasattr(node, '_save_start_time'):
                    node._save_start_time = None
                if hasattr(node, '_frame_count'):
                    node._frame_count = 0
                if hasattr(node, '_timer_completed_this_run'):
                    node._timer_completed_this_run = False
                if hasattr(node, '_frame_index'):
                    node._frame_index = 0
        if hasattr(go1_module, 'camera_save_state'):
            go1_module.camera_save_state['status'] = 'Stopped'
            go1_module.camera_save_state['start_time'] = None
            go1_module.camera_save_state['frame_count'] = 0
    if HAS_GO1 and not engine_module.is_running:
        go1_dashboard['status'] = 'Idle'
        go1_dashboard['hw_link'] = 'Offline'
        go1_dashboard['unity_link'] = 'Waiting'
        go1_dashboard['special'] = 'Idle'
        if hasattr(go1_module, 'go1_special_queue'):
            go1_module.go1_special_queue.clear()
        go1_target_vel.update({'vx': 0.0, 'vy': 0.0, 'vyaw': 0.0, 'body_height': 0.0})
        go1_node_intent.update({'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'stop': True})
    if HAS_EP and not engine_module.is_running:
        try:
            stop_ep_camera_pipeline()
        except Exception:
            pass

def link_cb(s, a): 
    p1_raw, p2_raw = a[0], a[1]
    p1 = dpg.get_item_alias(p1_raw) or p1_raw
    p2 = dpg.get_item_alias(p2_raw) or p2_raw
    p1_is_out = False
    for node in node_registry.values():
        if p1 in node.outputs.keys(): 
            p1_is_out = True; break
    src, dst = (p1, p2) if p1_is_out else (p2, p1)
    lid = dpg.add_node_link(p1_raw, p2_raw, parent=s)
    
    src_node_id = None; dst_node_id = None
    for nid, node in node_registry.items():
        if src in node.outputs: src_node_id = nid
        if dst in node.inputs: dst_node_id = nid
    link_registry[lid] = {'source': src, 'target': dst, 'src_node_id': src_node_id, 'dst_node_id': dst_node_id}
    
def del_link_cb(s, a): 
    link_registry.pop(a, None)
    if dpg.does_item_exist(a):
        dpg.delete_item(a)
def add_node_cb(s, a, u):
    node = NodeFactory.create_node(u)
    if not node:
        return

    # DPG tag(alias) 충돌 방지: 이미 존재하면 새 uid로 재할당
    if dpg.does_item_exist(node.node_id):
        old_id = node.node_id
        new_id = generate_uuid()
        while dpg.does_item_exist(new_id) or (new_id in node_registry):
            new_id = generate_uuid()
        node.node_id = new_id
        node_registry.pop(old_id, None)
        node_registry[new_id] = node
        engine_module.write_log(f"[UI] duplicate node tag 감지: {old_id} -> {new_id}")

    try:
        NodeUIRenderer.render(node)
    except Exception as e:
        # 렌더 실패 시 레지스트리 오염 방지
        if node_registry.get(node.node_id) is node:
            node_registry.pop(node.node_id, None)
        engine_module.write_log(f"[UI] 노드 렌더 실패: type={u}, id={node.node_id}, err={e}")
        raise

def save_cb(s, a): save_graph(dpg.get_value("file_name_input"))
def load_cb(s, a):
    selected = dpg.get_value("file_list_combo")
    if not selected:
        engine_module.write_log("Load Err: 파일 목록에서 먼저 선택하세요.")
        return
    load_graph(selected)
def update_file_list_ui(): update_ui_file_list()

def update_ui_file_list(): 
    if dpg.does_item_exist("file_list_combo"): dpg.configure_item("file_list_combo", items=get_save_files())
        
def update_mt4_path_combo(items):
    if dpg.does_item_exist("combo_mt4_path"): dpg.configure_item("combo_mt4_path", items=items)

def get_ui_value(tag): return dpg.get_value(tag) if dpg.does_item_exist(tag) else None
    
def set_ui_value(tag, val): 
    if dpg.does_item_exist(tag):
        if tag == "btn_mt4_record_label": dpg.set_item_label("btn_mt4_record", val)
        else: dpg.set_value(tag, val)

def get_item_pos_safe(attr): return dpg.get_item_pos(attr) if dpg.does_item_exist(attr) else [0,0]
def set_item_pos_safe(attr, pos):
    if dpg.does_item_exist(attr): dpg.set_item_pos(attr, pos)

def clear_editor():
    """
    노드 에디터 초기화
    - 순서: 선(link) 먼저 삭제 -> 노드(node) 다음 삭제
    - DPG 특성: parent가 있는 요소를 먼저 삭제해야 "No container to pop" 오류 방지
    """
    # 1. 모든 선 삭제 (노드보다 먼저)
    for lid in list(link_registry.keys()): 
        if dpg.does_item_exist(lid):
            try:
                dpg.delete_item(lid)
            except Exception as e:
                engine_module.write_log(f"Link 삭제 중 오류: {e}")
    
    # 2. 모든 노드 삭제 (선 삭제 후)
    for nid in list(node_registry.keys()): 
        if dpg.does_item_exist(nid):
            try:
                dpg.delete_item(nid)
            except Exception as e:
                engine_module.write_log(f"Node 삭제 중 오류: {e}")
    
    # 3. 레지스트리 초기화
    link_registry.clear()
    node_registry.clear()

def add_dpg_link(src, dst, src_node, dst_node):
    if not dpg.does_item_exist(src) or not dpg.does_item_exist(dst): return
    lid = dpg.add_node_link(src, dst, parent="node_editor")
    link_registry[lid] = {'source': src, 'target': dst, 'src_node_id': src_node, 'dst_node_id': dst_node}

def delete_selection(sender, app_data):
    selected_links = dpg.get_selected_links("node_editor")
    selected_nodes = dpg.get_selected_nodes("node_editor")
    for lid in selected_links:
        if lid in link_registry: del link_registry[lid]
        if dpg.does_item_exist(lid): dpg.delete_item(lid)
    for raw_nid in selected_nodes:
        nid = dpg.get_item_alias(raw_nid) or raw_nid 
        if nid not in node_registry: continue
        node = node_registry[nid]
        my_ports = set(node.inputs.keys()) | set(node.outputs.keys()); links_to_remove = []
        for lid, ldata in link_registry.items():
            if ldata['source'] in my_ports or ldata['target'] in my_ports: links_to_remove.append(lid)
        for lid in links_to_remove:
            if lid in link_registry: del link_registry[lid]
            if dpg.does_item_exist(lid): dpg.delete_item(lid)
        del node_registry[nid]
        if dpg.does_item_exist(nid): dpg.delete_item(nid)

def __init_ui__():
    threading.Thread(target=network_monitor_thread, daemon=True).start()
    
    dpg.create_context()
    with dpg.handler_registry(): 
        dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

    with dpg.window(tag="PrimaryWindow"):
        with dpg.tab_bar():
            # ================= [MT4 Dashboard Tab] =================
            with dpg.tab(label="MT4 Dashboard"):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=250, height=130, border=True):
                        dpg.add_text("MT4 Status", color=(150,150,150)); 
                        dpg.add_text("Status: Idle", tag="mt4_dash_status", color=(0,255,0))
                        dpg.add_text("HW: Offline", tag="mt4_dash_link", color=(255,0,0))
                        dpg.add_text("Latency: 0.0 ms", tag="mt4_dash_latency", color=(255,255,0))
                    with dpg.child_window(width=350, height=130, border=True):
                        dpg.add_text("Manual Control", color=(255,200,0))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="X+", width=60, callback=mt4_manual_control_callback, user_data=('x', 10))
                            dpg.add_button(label="X-", width=60, callback=mt4_manual_control_callback, user_data=('x', -10))
                            dpg.add_text("|")
                            dpg.add_button(label="Y+", width=60, callback=mt4_manual_control_callback, user_data=('y', 10))
                            dpg.add_button(label="Y-", width=60, callback=mt4_manual_control_callback, user_data=('y', -10))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Z+", width=60, callback=mt4_manual_control_callback, user_data=('z', 10))
                            dpg.add_button(label="Z-", width=60, callback=mt4_manual_control_callback, user_data=('z', -10))
                            dpg.add_text("|")
                            dpg.add_button(label="G+", width=60, callback=mt4_manual_control_callback, user_data=('gripper', 5))
                            dpg.add_button(label="G-", width=60, callback=mt4_manual_control_callback, user_data=('gripper', -5))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="R+", width=60, callback=mt4_manual_control_callback, user_data=('roll', 5))
                            dpg.add_button(label="R-", width=60, callback=mt4_manual_control_callback, user_data=('roll', -5))
                    with dpg.child_window(width=300, height=130, border=True):
                        dpg.add_text("Direct Coord", color=(0,255,255))
                        with dpg.group(horizontal=True):
                            dpg.add_text("X"); dpg.add_input_int(tag="input_x", width=50, default_value=200, step=0)
                            dpg.add_text("Y"); dpg.add_input_int(tag="input_y", width=50, default_value=0, step=0)
                        with dpg.group(horizontal=True):
                            dpg.add_text("Z"); dpg.add_input_int(tag="input_z", width=50, default_value=120, step=0)
                            dpg.add_text("G"); dpg.add_input_int(tag="input_g", width=50, default_value=40, step=0)
                            dpg.add_text("R"); dpg.add_input_int(tag="input_r", width=50, default_value=0, step=0)
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Move", width=100, callback=mt4_move_to_coord_callback)
                            dpg.add_button(label="Homing", width=100, callback=mt4_homing_callback)
                    with dpg.child_window(width=150, height=130, border=True):
                        dpg.add_text("Coords", color=(0,255,255))
                        dpg.add_text("X: 0", tag="mt4_x"); dpg.add_text("Y: 0", tag="mt4_y")
                        dpg.add_text("Z: 0", tag="mt4_z"); dpg.add_text("G: 0", tag="mt4_g")
                        dpg.add_text("R: 0.0", tag="mt4_r")
                    with dpg.child_window(width=200, height=130, border=True):
                        dpg.add_text("Record & Play", color=(255,100,200))
                        dpg.add_input_text(tag="path_name_input", default_value="my_path", width=130)
                        dpg.add_button(label="Start Recording", tag="btn_mt4_record", width=130, callback=lambda s,a,u: toggle_mt4_record())
                        dpg.add_combo(items=get_mt4_paths(), tag="combo_mt4_path", width=130)
                        dpg.add_button(label="Play Selected", width=130, callback=play_mt4_path)

            # ================= [Go1 Dashboard Tab] =================
            with dpg.tab(label="Go1 Dashboard"):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=220, height=190, border=True):
                        dpg.add_text("Go1 Status", color=(150,150,150))
                        dpg.add_text("Status: Idle", tag="go1_dash_status", color=(0,255,0))
                        dpg.add_text("HW: Offline", tag="go1_dash_link", color=(255,0,0))
                        dpg.add_text("Unity: Waiting", tag="go1_dash_unity", color=(255,255,0))
                        dpg.add_text("File Cam: Stopped", tag="go1_dash_cam", color=(200,200,200))
                        dpg.add_text("ArUco: OFF", tag="go1_dash_aruco", color=(200,200,200))
                        dpg.add_text("Special: Idle", tag="go1_dash_special", color=(255,200,0))
                        dpg.add_text("Battery: -%", tag="go1_dash_battery", color=(100,255,100))
                        dpg.add_button(label="[ EMERGENCY STOP ]", width=-1, callback=lambda s,a,u: go1_estop_callback() if HAS_GO1 else None)
                    
                    with dpg.child_window(width=220, height=190, border=True):
                        dpg.add_text("Odometry", color=(0,255,255))
                        dpg.add_text("World X: 0.000", tag="go1_dash_wx")
                        dpg.add_text("World Z: 0.000", tag="go1_dash_wz")
                        dpg.add_text("Yaw: 0.000 rad", tag="go1_dash_yaw")
                        dpg.add_text("Mode: 1 | NONE", tag="go1_dash_reason", color=(200,200,200))

                    with dpg.child_window(width=220, height=190, border=True):
                        dpg.add_text("Commands", color=(255,200,0))
                        dpg.add_text("Vx Cmd: 0.00", tag="go1_dash_vx_2")
                        dpg.add_text("Vy Cmd: 0.00", tag="go1_dash_vy_2")
                        dpg.add_text("Wz Cmd: 0.00", tag="go1_dash_wz_2")
                        dpg.add_text("Body H: 0.00", tag="go1_dash_body_h")
                        dpg.add_text("Latency: 0.0 ms", tag="go1_dash_latency")

                    with dpg.child_window(width=220, height=190, border=True):
                        dpg.add_text("Network Info", color=(100,200,255))
                        dpg.add_text("Host IP: Loading...", tag="dash_host_ip", color=(200,200,200))
                        dpg.add_text("Go1 Target: Loading...", tag="dash_relay_ip", color=(200,200,200))
                        dpg.add_text("Unity Target: Loading...", tag="dash_unity_ip", color=(200,200,200))
                        dpg.add_separator()
                        dpg.add_text("Interfaces: Loading...", tag="dash_net_if", color=(170,170,170))

                    with dpg.child_window(width=360, height=190, border=True):
                        dpg.add_text("Special Motions", color=(255,150,150))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="JumpYaw", width=80, callback=go1_action_callback, user_data="SPECIAL_jumpyaw")
                            dpg.add_button(label="StraightHand", width=100, callback=go1_action_callback, user_data="SPECIAL_straighthand")
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Dance1", width=80, callback=go1_action_callback, user_data="SPECIAL_dance1")
                            dpg.add_button(label="Dance2", width=80, callback=go1_action_callback, user_data="SPECIAL_dance2")
                        dpg.add_text("CAUTION: Run only in a safe space", color=(200,180,180))
                        dpg.add_text("CAUTION: Check current mode and run only after the previous action is complete", color=(200,180,180), wrap=340)

            # ================= [EP Dashboard Tab] =================
            with dpg.tab(label="EP Dashboard"):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=250, height=150, border=True):
                        dpg.add_text("EP Status", color=(150,150,150))
                        dpg.add_text("HW: Offline", tag="ep_dash_link", color=(0,255,0))
                        dpg.add_text("Battery: -%", tag="ep_dash_battery", color=(100,255,100))
                        dpg.add_text("SN: Unknown", tag="ep_dash_sn", color=(200,200,200))
                        dpg.add_spacer(height=5)
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Conn STA", callback=btn_connect_ep_sta, width=80)
                            dpg.add_button(label="Conn AP", callback=btn_connect_ep_ap, width=80)

                    with dpg.child_window(width=300, height=150, border=True):
                        dpg.add_text("Odometry", color=(0,255,255))
                        dpg.add_text("Pos X: 0.000", tag="ep_dash_px")
                        dpg.add_text("Pos Y: 0.000", tag="ep_dash_py")
                        dpg.add_text("Speed: 0.000", tag="ep_dash_spd")
                        dpg.add_text("Accel Z: 0.000", tag="ep_dash_acc")

                    with dpg.child_window(width=250, height=150, border=True):
                        dpg.add_text("Commands", color=(255,200,0))
                        dpg.add_text("Vx Cmd: 0.00", tag="ep_dash_vx")
                        dpg.add_text("Vy Cmd: 0.00", tag="ep_dash_vy")
                        dpg.add_text("Wz Cmd: 0.00", tag="ep_dash_wz")

            # ================= [Files & System Tab] =================
            with dpg.tab(label="Files & System"):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=650, height=130, border=True):
                        dpg.add_text("File Manager", color=(0,255,255))
                        with dpg.group(horizontal=True):
                            dpg.add_text("Save:"); dpg.add_input_text(tag="file_name_input", default_value="my_graph", width=120); dpg.add_button(label="SAVE", callback=save_cb, width=60)
                            dpg.add_spacer(width=20)
                            dpg.add_text("Load:"); dpg.add_combo(items=get_save_files(), tag="file_list_combo", width=120); dpg.add_button(label="LOAD", callback=load_cb, width=60); dpg.add_button(label="Refresh", callback=update_file_list_ui, width=60)
                    with dpg.child_window(width=400, height=130, border=True):
                        dpg.add_text("Network Info", color=(100,200,255))
                        dpg.add_text("Loading...", tag="sys_tab_net", color=(180,180,180))

        dpg.add_separator()
        
        # ================= [Node Palette] =================
        with dpg.group():
            with dpg.group(horizontal=True):
                dpg.add_text("Core Nodes:", color=(200,200,200))
                dpg.add_button(label="START", callback=add_node_cb, user_data="START")
                dpg.add_button(label="CHK KEY", callback=add_node_cb, user_data="COND_KEY")
                dpg.add_button(label="IF", callback=add_node_cb, user_data="LOGIC_IF")
                dpg.add_button(label="LOOP", callback=add_node_cb, user_data="LOGIC_LOOP")
                dpg.add_button(label="CONST", callback=add_node_cb, user_data="CONSTANT")
                dpg.add_button(label="PRINT", callback=add_node_cb, user_data="PRINT")
                dpg.add_spacer(width=20)
                
                dpg.add_text("MT4 Tools:", color=(255,200,0))
                dpg.add_button(label="MT4 DRIVER", callback=add_node_cb, user_data="MT4_DRIVER")
                dpg.add_button(label="MT4 ACTION", callback=add_node_cb, user_data="MT4_ACTION")
                dpg.add_button(label="MT4 KEY", callback=add_node_cb, user_data="MT4_KEYBOARD")
                dpg.add_button(label="MT4 UNITY", callback=add_node_cb, user_data="MT4_UNITY")
                dpg.add_button(label="UDP", callback=add_node_cb, user_data="UDP_RECV")
                
            with dpg.group(horizontal=True):
                dpg.add_text("Go1 & Vision:", color=(100,200,255))
                dpg.add_button(label="GO1 KEY", callback=add_node_cb, user_data="GO1_KEYBOARD")
                dpg.add_button(label="GO1 UNITY", callback=add_node_cb, user_data="GO1_UNITY")
                dpg.add_button(label="GO1 DRIVER", callback=add_node_cb, user_data="GO1_DRIVER")
                dpg.add_button(label="GO1 ACTION", callback=add_node_cb, user_data="GO1_ACTION")
                dpg.add_button(label="GO1 SENDER", callback=add_node_cb, user_data="GO1_SERVER_SENDER")
                dpg.add_button(label="GO1 JSON RX", callback=add_node_cb, user_data="GO1_SERVER_JSON_RECV")
                dpg.add_button(label="VIDEO SRC", callback=add_node_cb, user_data="VIDEO_SRC")
                dpg.add_button(label="FISHEYE", callback=add_node_cb, user_data="VIS_FISHEYE")
                dpg.add_button(label="DEPTH DA2", callback=add_node_cb, user_data="VIS_DEPTH_DA2")
                dpg.add_button(label="ARUCO", callback=add_node_cb, user_data="VIS_ARUCO")
                dpg.add_button(label="FLASK", callback=add_node_cb, user_data="VIS_FLASK")
                dpg.add_button(label="SAVE", callback=add_node_cb, user_data="VIS_SAVE")
                dpg.add_spacer(width=50)
                dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)
            
            with dpg.group(horizontal=True):
                dpg.add_text("EP01 Tools:", color=(100,200,255))
                dpg.add_button(label="EP DRIVER", callback=add_node_cb, user_data="EP_DRIVER")
                dpg.add_button(label="EP KEY", callback=add_node_cb, user_data="EP_KEYBOARD")
                dpg.add_button(label="EP ACTION", callback=add_node_cb, user_data="EP_ACTION")
                dpg.add_button(label="EP CAM", callback=add_node_cb, user_data="EP_CAM_SRC")
                dpg.add_button(label="EP STREAM", callback=add_node_cb, user_data="EP_CAM_STREAM")
                dpg.add_button(label="EP SAVE", callback=add_node_cb, user_data="EP_VIS_SAVE")
                dpg.add_button(label="EP SENDER", callback=add_node_cb, user_data="EP_SERVER_SENDER")

        with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

def start_gui():
    __init_ui__()
    dpg.create_viewport(title='PyGui Visual Scripting - MT4 & Go1', width=1280, height=800)
    dpg.setup_dearpygui()
    dpg.set_primary_window("PrimaryWindow", True)
    dpg.show_viewport()

    last_logic_time = 0
    LOGIC_RATE = 0.02
    last_fb_time = 0

    while dpg.is_dearpygui_running():
        # --- MT4 UI Update ---
        if mt4_dashboard["last_pkt_time"] > 0: dpg.set_value("mt4_dash_status", f"Status: {mt4_dashboard['status']}")
        if dpg.does_item_exist("mt4_dash_latency"): 
            dpg.set_value("mt4_dash_latency", f"Latency: {mt4_dashboard.get('latency', 0.0):.1f} ms")
        dpg.set_value("mt4_x", f"X: {mt4_current_pos['x']:.1f}"); dpg.set_value("mt4_y", f"Y: {mt4_current_pos['y']:.1f}")
        dpg.set_value("mt4_z", f"Z: {mt4_current_pos['z']:.1f}"); dpg.set_value("mt4_g", f"G: {mt4_current_pos['gripper']:.1f}")
        if dpg.does_item_exist("mt4_r"): dpg.set_value("mt4_r", f"R: {mt4_current_pos['roll']:.1f}°")
        
        hw_status = mt4_dashboard.get('hw_link', HwStatus.OFFLINE)
        if hw_status == HwStatus.ONLINE: dpg.set_value("mt4_dash_link", "HW: Online"); dpg.configure_item("mt4_dash_link", color=(0,255,0))
        elif hw_status == HwStatus.SIMULATION: dpg.set_value("mt4_dash_link", "HW: Simulation"); dpg.configure_item("mt4_dash_link", color=(255,200,0))
        else: dpg.set_value("mt4_dash_link", "HW: Offline"); dpg.configure_item("mt4_dash_link", color=(255,0,0))
        
        if dpg.does_item_exist("sys_tab_net"): dpg.set_value("sys_tab_net", sys_net_str)

        if time.time() - last_fb_time > 0.05:
            try:
                fb = {"x": -mt4_current_pos['y']/1000.0, "y": (mt4_current_pos['z'] - MT4_Z_OFFSET) / 1000.0, "z": mt4_current_pos['x']/1000.0, "roll": mt4_current_pos['roll'], "gripper": mt4_current_pos['gripper'], "status": "Running"}
                sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock_send.sendto(json.dumps(fb).encode(), (MT4_UNITY_IP, MT4_FEEDBACK_PORT))
            except: pass
            last_fb_time = time.time()

        # --- [추가] Go1 UI Update ---
        if HAS_GO1:
            go1_status = go1_dashboard.get('status', 'Idle')
            dpg.set_value("go1_dash_status", f"Status: {go1_status}")
            dpg.configure_item("go1_dash_status", color=(0,255,0) if go1_status == "Running" else (200,200,200))

            hw_link_str = str(go1_dashboard.get('hw_link', 'Offline'))
            dpg.set_value("go1_dash_link", f"HW: {hw_link_str}")
            if "Online" in hw_link_str:
                dpg.configure_item("go1_dash_link", color=(0,255,0))
            elif hw_link_str in ["Simulation", "Connecting..."]:
                dpg.configure_item("go1_dash_link", color=(255,200,0))
            else:
                dpg.configure_item("go1_dash_link", color=(255,0,0))

            dpg.set_value("go1_dash_unity", f"Unity: {go1_dashboard.get('unity_link', 'Waiting')}")
            dpg.set_value("go1_dash_special", f"Special: {go1_dashboard.get('special', 'Idle')}")

            cam_state = camera_state.get('status', 'Stopped')
            dpg.set_value("go1_dash_cam", f"File Cam: {cam_state}")
            if cam_state == "Running":
                dpg.configure_item("go1_dash_cam", color=(0,255,0))
            else:
                dpg.configure_item("go1_dash_cam", color=(200,200,200))

            aruco_node = next((n for n in node_registry.values() if getattr(n, 'type_str', '') == 'VIS_ARUCO'), None)
            if aruco_node is not None:
                marker_size = aruco_node.state.get('marker_size_m', 0.03)
                send_enabled = bool(go1_node_intent.get('send_aruco', False))
                status_text = f"ArUco: Ready | {float(marker_size):.3f}m"
                status_text += " | TX ON" if send_enabled else " | TX OFF"
                dpg.configure_item("go1_dash_aruco", default_value=status_text, color=(0,255,255))
            else:
                dpg.configure_item("go1_dash_aruco", default_value="ArUco: OFF", color=(200,200,200))

            bat_val = go1_state.get('battery', -1)
            if bat_val >= 0:
                dpg.set_value("go1_dash_battery", f"Battery: {bat_val}%")
            else:
                dpg.set_value("go1_dash_battery", "Battery: 100% (Sim)")

            dpg.set_value("go1_dash_wx", f"World X: {go1_state.get('world_x', 0.0):.3f}")
            dpg.set_value("go1_dash_wz", f"World Z: {go1_state.get('world_z', 0.0):.3f}")
            dpg.set_value("go1_dash_yaw", f"Yaw: {go1_state.get('yaw_unity', 0.0):.3f} rad")
            dpg.set_value("go1_dash_reason", f"Mode: {go1_state.get('mode', 1)} | {go1_state.get('reason', 'NONE')}")
            dpg.set_value("go1_dash_vx_2", f"Vx Cmd: {go1_state.get('vx_cmd', 0.0):.2f}")
            dpg.set_value("go1_dash_vy_2", f"Vy Cmd: {go1_state.get('vy_cmd', 0.0):.2f}")
            dpg.set_value("go1_dash_wz_2", f"Wz Cmd: {go1_state.get('wz_cmd', 0.0):.2f}")
            dpg.set_value("go1_dash_body_h", f"Body H: {go1_state.get('body_height_cmd', 0.0):.2f}")
            dpg.set_value("go1_dash_latency", f"Latency: {go1_state.get('control_latency_ms', 0.0):.1f} ms")

            if dpg.does_item_exist("dash_host_ip"):
                dpg.set_value("dash_host_ip", f"Host IP: {socket.gethostbyname(socket.gethostname())}")
            if dpg.does_item_exist("dash_relay_ip"):
                dpg.set_value("dash_relay_ip", f"Go1 Target: {getattr(go1_module, 'GO1_IP', 'Unknown')}")
            if dpg.does_item_exist("dash_unity_ip"):
                dpg.set_value("dash_unity_ip", f"Unity Target: {getattr(go1_module, 'GO1_UNITY_IP', 'Unknown')}")
            if dpg.does_item_exist("dash_net_if"):
                dpg.set_value("dash_net_if", f"Interfaces: {sys_net_str.replace(chr(10), ' | ')}")
            
        if HAS_EP:
            ep_link = ep_dashboard.get('hw_link', 'Offline')
            dpg.set_value("ep_dash_link", f"HW: {ep_link}")
            if "Online" in ep_link: dpg.configure_item("ep_dash_link", color=(0,255,0))
            elif "Connecting" in ep_link: dpg.configure_item("ep_dash_link", color=(255,200,0))
            else: dpg.configure_item("ep_dash_link", color=(255,0,0))
            
            bat = ep_state.get('battery', -1)
            dpg.set_value("ep_dash_battery", f"Battery: {bat}%" if bat >= 0 else "Battery: -%")
            dpg.set_value("ep_dash_sn", f"SN: {ep_dashboard.get('sn', 'Unknown')}")
            dpg.set_value("ep_dash_px", f"Pos X: {ep_state.get('pos_x', 0.0):.3f}")
            dpg.set_value("ep_dash_py", f"Pos Y: {ep_state.get('pos_y', 0.0):.3f}")
            dpg.set_value("ep_dash_spd", f"Speed: {ep_state.get('speed', 0.0):.3f}")
            dpg.set_value("ep_dash_acc", f"Accel Z: {ep_state.get('accel_z', 0.0):.3f}")
            dpg.set_value("ep_dash_vx", f"Vx Cmd: {ep_node_intent.get('vx', 0.0):.2f}")
            dpg.set_value("ep_dash_vy", f"Vy Cmd: {ep_node_intent.get('vy', 0.0):.2f}")
            dpg.set_value("ep_dash_wz", f"Wz Cmd: {ep_node_intent.get('wz', 0.0):.2f}")

        # --- Node Engine Tick ---
        if engine_module.is_running and (time.time() - last_logic_time > LOGIC_RATE):
            NodeUIRenderer.sync_ui_to_state()
            engine_module.execute_graph_once()           
            last_logic_time = time.time()
            
        dpg.render_dearpygui_frame()

    dpg.destroy_context()