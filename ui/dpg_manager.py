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
    from nodes.robots.go1 import go1_dashboard, go1_target_vel, GO1_IP, GO1_PORT
    import nodes.robots.go1 as go1_module
    HAS_GO1 = True
except ImportError:
    HAS_GO1 = False

# --- [추가] RoboMaster EP 연동 ---
try:
    from nodes.robots.ep01 import ep_dashboard, ep_state, ep_target_vel, EP_IP, EP_PORT, ep_cmd_sock
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
    cmd_str = user_data
    if go1_module.go1_sock:
        try: go1_module.go1_sock.sendto(cmd_str.encode(), (GO1_IP, GO1_PORT))
        except: pass

def ep_manual_control_callback(sender, app_data, user_data):
    if not HAS_EP: return
    axis, step = user_data
    ep_target_vel[axis] = ep_target_vel.get(axis, 0.0) + step

def ep_action_callback(sender, app_data, user_data):
    if not HAS_EP or not ep_cmd_sock: return
    try: ep_cmd_sock.sendto(user_data.encode(), (EP_IP, EP_PORT))
    except: pass

class NodeUIRenderer:
    key_map = {"A": 65, "B": 66, "C": 67, "S": 83, "W": 87, "SPACE": 32}

    @staticmethod
    def sync_ui_to_state():
        is_focused = dpg.is_item_focused("file_name_input") or (dpg.does_item_exist("path_name_input") and dpg.is_item_focused("path_name_input"))
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
            elif t == "GO1_ACTION" and hasattr(node, 'combo_act'):
                node.state['action'] = dpg.get_value(node.combo_act)
            elif t == "EP_ACTION" and hasattr(node, 'combo_act'):
                node.state['action'] = dpg.get_value(node.combo_act)
            elif t == "VIDEO_SRC" and hasattr(node, 'ui_url'):
                node.state['url'] = dpg.get_value(node.ui_url)
                node.state['is_running'] = dpg.get_value(node.ui_run)
            elif t == "VIS_FLASK" and hasattr(node, 'ui_port'):
                node.state['port'] = dpg.get_value(node.ui_port)
                node.state['is_running'] = dpg.get_value(node.ui_run)

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
        elif t == "GO1_ACTION" and hasattr(node, 'combo_act'):
            dpg.set_value(node.combo_act, node.state.get('action', 'Stand Up'))
        elif t == "VIDEO_SRC" and hasattr(node, 'ui_url'):
            dpg.set_value(node.ui_url, node.state.get('url', 'rtsp://192.168.12.1:8554/live'))
            dpg.set_value(node.ui_run, node.state.get('is_running', False))
        elif t == "VIS_FLASK" and hasattr(node, 'ui_port'):
            dpg.set_value(node.ui_port, node.state.get('port', 5000))
            dpg.set_value(node.ui_run, node.state.get('is_running', False))

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
        elif t == "GO1_ACTION": NodeUIRenderer._render_go1_action(node)
        elif t == "VIDEO_SRC": NodeUIRenderer._render_video_src(node)
        elif t == "VIS_FISHEYE": NodeUIRenderer._render_fisheye(node)
        elif t == "VIS_ARUCO": NodeUIRenderer._render_aruco(node)
        elif t == "VIS_FLASK": NodeUIRenderer._render_flask(node)
        # --- EP01 ---
        elif t == "EP_ACTION": NodeUIRenderer._render_ep_action(node)


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
    def _render_go1_action(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Go1 Action"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_act = dpg.add_combo(["Stand Up", "Lie Down", "Walk Mode", "Dance"], default_value="Stand Up", width=120)
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

    @staticmethod
    def _render_video_src(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Video Source"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_url = dpg.add_input_text(label="RTSP/Cam", width=150, default_value="rtsp://192.168.12.1:8554/live")
                node.ui_run = dpg.add_checkbox(label="Run Stream")
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Frame Data", color=(255,255,0))

    @staticmethod
    def _render_fisheye(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Fisheye Undistort"):
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Frame Out", color=(255,255,0))

    @staticmethod
    def _render_aruco(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="ArUco Detect"):
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(tag=node.out_frame, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Draw Frame", color=(255,255,0))
            with dpg.node_attribute(tag=node.out_data, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Marker Info", color=(100,200,255))

    @staticmethod
    def _render_flask(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Flask Stream"):
            with dpg.node_attribute(tag=node.in_frame, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Frame In", color=(255,255,0))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.ui_port = dpg.add_input_int(label="Port", width=80, default_value=5000)
                node.ui_run = dpg.add_checkbox(label="Start Server")

    @staticmethod
    def _render_ep_action(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="EP Action"):
            _f_in = generate_uuid(); node.inputs[_f_in] = PortType.FLOW
            with dpg.node_attribute(tag=_f_in, attribute_type=dpg.mvNode_Attr_Input): dpg.add_text("Flow In")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_act = dpg.add_combo(["LED Red", "LED Blue", "Blaster Fire", "Arm Center", "Grip Open", "Grip Close"], default_value="LED Red", width=120)
            with dpg.node_attribute(tag=node.out_flow, attribute_type=dpg.mvNode_Attr_Output): dpg.add_text("Flow Out")

# Callback functions
def toggle_exec(s, a): 
    engine_module.is_running = not engine_module.is_running
    dpg.set_item_label("btn_run", "STOP" if engine_module.is_running else "RUN SCRIPT")

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
    dpg.delete_item(a); link_registry.pop(a, None)
def add_node_cb(s, a, u): 
    node = NodeFactory.create_node(u)
    if node: NodeUIRenderer.render(node)

def save_cb(s, a): save_graph(dpg.get_value("file_name_input"))
def load_cb(s, a): load_graph(dpg.get_value("file_list_combo"))
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
    for lid in list(link_registry.keys()): 
        if dpg.does_item_exist(lid): dpg.delete_item(lid)
    for nid in list(node_registry.keys()): 
        if dpg.does_item_exist(nid): dpg.delete_item(nid)
    link_registry.clear(); node_registry.clear()

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
                    with dpg.child_window(width=250, height=140, border=True):
                        dpg.add_text("Go1 Status", color=(150,150,150))
                        dpg.add_text("Status: Idle", tag="go1_dash_status", color=(0,255,0))
                        dpg.add_text("HW: Offline", tag="go1_dash_link", color=(255,0,0))
                        dpg.add_text("Camera: Node Managed", tag="go1_dash_cam", color=(200,200,200))
                    
                    with dpg.child_window(width=300, height=140, border=True):
                        dpg.add_text("Manual Control", color=(255,200,0))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Vx+", width=60, callback=go1_manual_control_callback, user_data=('vx', 0.1))
                            dpg.add_button(label="Vx-", width=60, callback=go1_manual_control_callback, user_data=('vx', -0.1))
                            dpg.add_text("|")
                            dpg.add_button(label="Vy+", width=60, callback=go1_manual_control_callback, user_data=('vy', 0.1))
                            dpg.add_button(label="Vy-", width=60, callback=go1_manual_control_callback, user_data=('vy', -0.1))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Yaw+", width=60, callback=go1_manual_control_callback, user_data=('vyaw', 0.2))
                            dpg.add_button(label="Yaw-", width=60, callback=go1_manual_control_callback, user_data=('vyaw', -0.2))
                            dpg.add_text("|")
                            dpg.add_button(label="STOP", width=130, callback=lambda s,a,u: go1_target_vel.update({'vx':0.0, 'vy':0.0, 'vyaw':0.0}) if HAS_GO1 else None)

                    with dpg.child_window(width=300, height=140, border=True):
                        dpg.add_text("Direct Speed & Actions", color=(0,255,255))
                        with dpg.group(horizontal=True):
                            dpg.add_text("Vx"); dpg.add_input_float(tag="go1_input_vx", width=50, default_value=0.0, step=0)
                            dpg.add_text("Vy"); dpg.add_input_float(tag="go1_input_vy", width=50, default_value=0.0, step=0)
                            dpg.add_text("Yaw"); dpg.add_input_float(tag="go1_input_vyaw", width=50, default_value=0.0, step=0)
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Move", width=50, callback=go1_move_to_coord_callback)
                            dpg.add_button(label="Stand", width=50, callback=go1_action_callback, user_data="stand")
                            dpg.add_button(label="Down", width=50, callback=go1_action_callback, user_data="down")
                            dpg.add_button(label="Walk", width=50, callback=go1_action_callback, user_data="walk")
                            dpg.add_button(label="Dance", width=50, callback=go1_action_callback, user_data="dance")

                    with dpg.child_window(width=150, height=140, border=True):
                        dpg.add_text("Speeds", color=(0,255,255))
                        dpg.add_text("Vx: 0.0", tag="go1_dash_vx")
                        dpg.add_text("Vy: 0.0", tag="go1_dash_vy")
                        dpg.add_text("Vyaw: 0.0", tag="go1_dash_vyaw")

            # ================= [EP Dashboard Tab] =================
            with dpg.tab(label="EP Dashboard"):
                with dpg.group(horizontal=True):
                    with dpg.child_window(width=250, height=140, border=True):
                        dpg.add_text("EP Status", color=(150,150,150))
                        dpg.add_text("HW: Offline", tag="ep_dash_link", color=(255,0,0))
                        dpg.add_text("Battery: -%", tag="ep_dash_battery", color=(0,255,0))
                        dpg.add_text("SN: Unknown", tag="ep_dash_sn", color=(200,200,200))
                    
                    with dpg.child_window(width=300, height=140, border=True):
                        dpg.add_text("Manual Control", color=(255,200,0))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Vx+", width=60, callback=ep_manual_control_callback, user_data=('vx', 0.5))
                            dpg.add_button(label="Vx-", width=60, callback=ep_manual_control_callback, user_data=('vx', -0.5))
                            dpg.add_text("|")
                            dpg.add_button(label="Vy+", width=60, callback=ep_manual_control_callback, user_data=('vy', 0.5))
                            dpg.add_button(label="Vy-", width=60, callback=ep_manual_control_callback, user_data=('vy', -0.5))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Yaw+", width=60, callback=ep_manual_control_callback, user_data=('vz', 0.5))
                            dpg.add_button(label="Yaw-", width=60, callback=ep_manual_control_callback, user_data=('vz', -0.5))
                            dpg.add_text("|")
                            dpg.add_button(label="STOP", width=130, callback=lambda s,a,u: ep_target_vel.update({'vx':0.0, 'vy':0.0, 'vz':0.0}) if HAS_EP else None)

                    with dpg.child_window(width=200, height=140, border=True):
                        dpg.add_text("Actions", color=(0,255,255))
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Fire", width=60, callback=ep_action_callback, user_data="blaster fire;")
                            dpg.add_button(label="Grip Open", width=80, callback=ep_action_callback, user_data="robotic_gripper open 1;")
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="Red", width=60, callback=ep_action_callback, user_data="led control comp all r 255 g 0 b 0 effect solid;")
                            dpg.add_button(label="Grip Close", width=80, callback=ep_action_callback, user_data="robotic_gripper close 1;")

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
                dpg.add_button(label="KEY", callback=add_node_cb, user_data="MT4_KEYBOARD")
                dpg.add_button(label="UNITY", callback=add_node_cb, user_data="MT4_UNITY")
                dpg.add_button(label="UDP", callback=add_node_cb, user_data="UDP_RECV")
                
            with dpg.group(horizontal=True):
                dpg.add_text("Go1 & Vision:", color=(100,200,255))
                dpg.add_button(label="GO1 DRIVER", callback=add_node_cb, user_data="GO1_DRIVER")
                dpg.add_button(label="GO1 ACTION", callback=add_node_cb, user_data="GO1_ACTION")
                dpg.add_button(label="VIDEO SRC", callback=add_node_cb, user_data="VIDEO_SRC")
                dpg.add_button(label="FISHEYE", callback=add_node_cb, user_data="VIS_FISHEYE")
                dpg.add_button(label="ARUCO", callback=add_node_cb, user_data="VIS_ARUCO")
                dpg.add_button(label="FLASK", callback=add_node_cb, user_data="VIS_FLASK")
                dpg.add_spacer(width=50)
                dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)
            
            with dpg.group(horizontal=True):
                dpg.add_text("EP01 Tools:", color=(100,200,255))
                dpg.add_button(label="EP DRIVER", callback=add_node_cb, user_data="EP_DRIVER")
                dpg.add_button(label="EP ACTION", callback=add_node_cb, user_data="EP_ACTION")

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
            go1_link = go1_dashboard.get('hw_link', HwStatus.OFFLINE)
            if go1_link == HwStatus.ONLINE: 
                dpg.set_value("go1_dash_link", "HW: Online")
                dpg.configure_item("go1_dash_link", color=(0,255,0))
            else: 
                dpg.set_value("go1_dash_link", "HW: Offline")
                dpg.configure_item("go1_dash_link", color=(255,0,0))
            
            dpg.set_value("go1_dash_vx", f"Vx: {go1_target_vel['vx']:.2f}")
            dpg.set_value("go1_dash_vy", f"Vy: {go1_target_vel['vy']:.2f}")
            dpg.set_value("go1_dash_vyaw", f"Vyaw: {go1_target_vel['vyaw']:.2f}")

        if HAS_EP:
            ep_link = ep_dashboard.get('hw_link', 'Offline')
            dpg.set_value("ep_dash_link", f"HW: {ep_link}")
            if "Online" in ep_link: dpg.configure_item("ep_dash_link", color=(0,255,0))
            elif "Connecting" in ep_link: dpg.configure_item("ep_dash_link", color=(255,200,0))
            else: dpg.configure_item("ep_dash_link", color=(255,0,0))
            
            bat = ep_state.get('battery', -1)
            dpg.set_value("ep_dash_battery", f"Battery: {bat}%" if bat >= 0 else "Battery: -%")

        # --- Node Engine Tick ---
        if engine_module.is_running and (time.time() - last_logic_time > LOGIC_RATE):
            NodeUIRenderer.sync_ui_to_state()
            engine_module.execute_graph_once()           
            last_logic_time = time.time()
            
        dpg.render_dearpygui_frame()

    dpg.destroy_context()