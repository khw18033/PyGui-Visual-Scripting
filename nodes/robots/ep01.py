import time
import socket
import threading
from nodes.base import BaseNode, BaseRobotDriver
from core.engine import generate_uuid, PortType, write_log, HwStatus

# ================= [EP Globals & Network] =================
ep_cmd_sock = None
EP_IP = "192.168.42.2" # USB 테더링 기본 IP (라우터 연결 시 해당 IP로 변경 필요)
EP_PORT = 40924

ep_dashboard = {"hw_link": "Offline", "sn": "Unknown"}
ep_state = {"pos_x": 0.0, "pos_y": 0.0, "yaw": 0.0, "battery": -1}
ep_target_vel = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0} # vz = yaw

def init_ep_network(ip=EP_IP):
    global ep_cmd_sock, EP_IP
    EP_IP = ip
    try:
        ep_cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ep_cmd_sock.settimeout(0.5)
        # SDK 모드 진입 명령
        ep_cmd_sock.sendto(b"command;", (EP_IP, EP_PORT))
        write_log(f"EP: Network Initialized ({EP_IP})")
        ep_dashboard["hw_link"] = "Connecting"
    except Exception as e:
        ep_dashboard["hw_link"] = "Offline"
        write_log(f"EP Net Error: {e}")

def ep_status_thread():
    global ep_cmd_sock
    while True:
        if ep_cmd_sock:
            try:
                # 배터리 정보 폴링 (기본 SDK 명세)
                ep_cmd_sock.sendto(b"battery ?;", (EP_IP, EP_PORT))
                data, _ = ep_cmd_sock.recvfrom(1024)
                res = data.decode('utf-8').strip()
                if res.isdigit():
                    ep_state['battery'] = int(res)
                    ep_dashboard["hw_link"] = "Online"
                
                # 위치 정보 폴링 (필요 시 push 명령으로 대체 가능)
                ep_cmd_sock.sendto(b"chassis position ?;", (EP_IP, EP_PORT))
                pos_data, _ = ep_cmd_sock.recvfrom(1024)
                pos_res = pos_data.decode('utf-8').strip().split()
                if len(pos_res) >= 3:
                    ep_state['pos_x'] = float(pos_res[0])
                    ep_state['pos_y'] = float(pos_res[1])
            except:
                pass
        time.sleep(2.0)

# ================= [EP Hardware Nodes] =================

class EPRobotDriver(BaseRobotDriver):
    def __init__(self):
        self.last_write_time = 0
        self.write_interval = 0.05 # 20Hz
        self.current_sent_vel = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0}

    def get_ui_schema(self):
        return [('vx', "Vx (F/B)", 0.0), ('vy', "Vy (L/R)", 0.0), ('vz', "Yaw (Turn)", 0.0)]
        
    def get_settings_schema(self):
        return [('speed_scale', "Speed Max", 1.0)]

    def execute_command(self, inputs, settings):
        global ep_target_vel, ep_cmd_sock
        
        for key, _, _ in self.get_ui_schema():
            val = inputs.get(key)
            if val is not None: ep_target_vel[key] = float(val)

        scale = float(settings.get('speed_scale', 1.0))
        
        if time.time() - self.last_write_time >= self.write_interval:
            if ep_cmd_sock and ep_dashboard["hw_link"] == "Online":
                tx = ep_target_vel['vx'] * scale
                ty = ep_target_vel['vy'] * scale
                tz = ep_target_vel['vz'] * 100.0 * scale # EP SDK Yaw requires larger scale
                
                # 변동이 있거나 0이 아닐 때만 명령 전송
                if abs(tx) > 0.01 or abs(ty) > 0.01 or abs(tz) > 0.1 or sum(abs(v) for v in self.current_sent_vel.values()) > 0:
                    cmd_str = f"chassis speed x {tx:.2f} y {ty:.2f} z {tz:.1f};"
                    try:
                        ep_cmd_sock.sendto(cmd_str.encode(), (EP_IP, EP_PORT))
                        self.current_sent_vel = {'vx': tx, 'vy': ty, 'vz': tz}
                    except:
                        pass
            self.last_write_time = time.time()
            
        return ep_target_vel

class EPActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "EP Action", "EP_ACTION")
        self.in_flow = generate_uuid(); self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid(); self.outputs[self.out_flow] = PortType.FLOW
        self.state['action'] = "LED Red"

    def execute(self):
        global ep_cmd_sock
        action = self.state.get("action", "LED Red")
        
        cmd_str = ""
        if action == "LED Red": cmd_str = "led control comp all r 255 g 0 b 0 effect solid;"
        elif action == "LED Blue": cmd_str = "led control comp all r 0 g 0 b 255 effect solid;"
        elif action == "Blaster Fire": cmd_str = "blaster fire;"
        elif action == "Arm Center": cmd_str = "robotic_arm moveto x 100 y 100;"
        elif action == "Grip Open": cmd_str = "robotic_gripper open 1;"
        elif action == "Grip Close": cmd_str = "robotic_gripper close 1;"
        
        if cmd_str and ep_cmd_sock:
            try: ep_cmd_sock.sendto(cmd_str.encode(), (EP_IP, EP_PORT))
            except: pass
            write_log(f"EP Action: {action}")
            
        return self.out_flow