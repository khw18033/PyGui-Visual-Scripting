import re

with open('nodes/robots/mt4.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace get_mt4_paths (already correct but we need to remove dpg from callbacks)
# Replace mt4_manual_control_callback
text = re.sub(r'def mt4_manual_control_callback\(sender, app_data, user_data\):', 
'''def mt4_manual_control(axis, step):''', text)

# Replace lines inside mt4_manual_control
text = re.sub(r'    axis, step = user_data\n', '', text)

# Replace mt4_move_to_coord_callback
text = re.sub(r'def mt4_move_to_coord_callback\(sender, app_data, user_data\):\n.*?(    mt4_apply_limits\(\))',
'''def mt4_move_to_coord(x, y, z, roll, gripper):
    global mt4_manual_override_until, mt4_target_goal
    mt4_manual_override_until = time.time() + 2.0
    mt4_target_goal['x'] = float(x)
    mt4_target_goal['y'] = float(y)
    mt4_target_goal['z'] = float(z)
    mt4_target_goal['gripper'] = float(gripper)
    if roll is not None:
        mt4_target_goal['roll'] = float(roll)
\1''', text, flags=re.DOTALL)

# Refactor toggle_mt4_record
text = re.sub(r'def toggle_mt4_record\(custom_name=None\):.*?def play_mt4_path\(',
'''def toggle_mt4_record(custom_name=None):
    global mt4_record_f, mt4_record_writer, mt4_record_temp_name

    if mt4_mode["recording"]:
        mt4_mode["recording"] = False
        if mt4_record_f: mt4_record_f.close()
        if custom_name and mt4_record_temp_name:
            if not custom_name.endswith(".csv"): custom_name += ".csv"
            final_path = os.path.join("path_record", custom_name)
            try: os.rename(mt4_record_temp_name, final_path)
            except: pass
    else:
        mt4_mode["recording"] = True
        os.makedirs("path_record", exist_ok=True)
        fname = os.path.join("path_record", f"path_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        mt4_record_temp_name = fname
        mt4_record_f = open(fname, 'w', newline='')
        import csv
        mt4_record_writer = csv.writer(mt4_record_f)
        mt4_record_writer.writerow(['x', 'y', 'z', 'roll','gripper'])

def play_mt4_path(''', text, flags=re.DOTALL)

text = re.sub(r'def play_mt4_path\(sender=None, app_data=None, user_data=None, filename=None\):\s*if not filename: filename = dpg\.get_value\("combo_mt4_path"\)\s*if not filename or mt4_mode\["playing"\]',
'''def play_mt4_path(filename=None):
    if not filename or mt4_mode["playing"]''', text, flags=re.DOTALL)

text = re.sub(r'def mt4_homing_callback\(sender, app_data, user_data\):',
'''def mt4_homing_logic():''', text)

# Remove 'import dearpygui.dearpygui as dpg' at the top of the callbacks section
text = re.sub(r'import dearpygui\.dearpygui as dpg\n', '', text)

with open('nodes/robots/mt4.py', 'w', encoding='utf-8') as f:
    f.write(text)
