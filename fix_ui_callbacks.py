import re

with open('ui/dpg_manager.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('callback=mt4_manual_control_callback', 'callback=_ui_mt4_manual_control_callback')
text = text.replace('callback=mt4_move_to_coord_callback', 'callback=_ui_mt4_move_to_coord_callback')
text = text.replace('callback=lambda s,a,u: toggle_mt4_record()', 'callback=_ui_toggle_mt4_record')
text = text.replace('callback=play_mt4_path', 'callback=_ui_play_mt4_path')
text = text.replace('callback=mt4_homing_callback', 'callback=_ui_mt4_homing_callback')

with open('ui/dpg_manager.py', 'w', encoding='utf-8') as f:
    f.write(text)
