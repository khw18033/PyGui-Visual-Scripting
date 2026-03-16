import re

with open('ui/dpg_manager.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Update import from nodes.robots.mt4
text = re.sub(r'from nodes\.robots\.mt4 import .*?mt4_homing_callback',
'''from nodes.robots.mt4 import mt4_manual_control, mt4_move_to_coord, toggle_mt4_record, play_mt4_path, mt4_homing_logic, get_mt4_paths''', text, flags=re.DOTALL)

# Insert the callback wrappers before UIManager
text = re.sub(r'class UIManager:',
'''def _ui_mt4_manual_control_callback(sender, app_data, user_data):
    axis, step = user_data
    mt4_manual_control(axis, step)

def _ui_mt4_move_to_coord_callback(sender, app_data, user_data):
    x = dpg.get_value("input_x") if dpg.does_item_exist("input_x") else 200.0
    y = dpg.get_value("input_y") if dpg.does_item_exist("input_y") else 0.0
    z = dpg.get_value("input_z") if dpg.does_item_exist("input_z") else 120.0
    g = dpg.get_value("input_g") if dpg.does_item_exist("input_g") else 40.0
    r = dpg.get_value("input_r") if dpg.does_item_exist("input_r") else 0.0
    mt4_move_to_coord(x, y, z, r, g)

def _ui_toggle_mt4_record(sender, app_data, user_data):
    custom_name = dpg.get_value("path_name_input") if dpg.does_item_exist("path_name_input") else None
    toggle_mt4_record(custom_name)

def _ui_play_mt4_path(sender, app_data, user_data):
    filename = dpg.get_value("combo_mt4_path") if dpg.does_item_exist("combo_mt4_path") else None
    if filename:
        play_mt4_path(filename)

def _ui_mt4_homing_callback(sender, app_data, user_data):
    mt4_homing_logic()

class UIManager:''', text)

with open('ui/dpg_manager.py', 'w', encoding='utf-8') as f:
    f.write(text)
