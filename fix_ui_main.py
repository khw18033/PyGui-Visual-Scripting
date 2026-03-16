import re

with open('main.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Insert the UI updates for the Record Button/ComboBox into the dashboard loop
replacement = '''        if dpg.does_item_exist("sys_tab_net"): dpg.set_value("sys_tab_net", sys_net_str)

        from nodes.robots.mt4 import mt4_mode, get_mt4_paths
        if dpg.does_item_exist("btn_mt4_record"):
            current_label = "Stop Recording" if mt4_mode["recording"] else "Start Recording"
            if dpg.get_item_label("btn_mt4_record") != current_label:
                dpg.set_item_label("btn_mt4_record", current_label)
                if not mt4_mode["recording"] and dpg.does_item_exist("combo_mt4_path"):
                    dpg.configure_item("combo_mt4_path", items=get_mt4_paths())'''

text = text.replace('        if dpg.does_item_exist("sys_tab_net"): dpg.set_value("sys_tab_net", sys_net_str)', replacement)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)
