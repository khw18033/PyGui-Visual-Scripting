import os
import time
import socket
import json
import threading
from datetime import datetime
from collections import deque
import numpy as np
import cv2

from nodes.base import BaseNode, BaseRobotDriver
from core.engine import generate_uuid, PortType, write_log, HwStatus, node_registry

# ================= [Go1 Globals & Network] =================
go1_sock = None
GO1_IP = "192.168.12.1"
GO1_PORT = 8080

go1_target_vel = {'vx': 0.0, 'vy': 0.0, 'vyaw': 0.0}
go1_dashboard = {"status": "Idle", "hw_link": HwStatus.OFFLINE}

def init_go1_network():
    global go1_sock
    try:
        go1_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        go1_sock.settimeout(0.5)
        go1_dashboard["hw_link"] = HwStatus.ONLINE
        write_log("Go1: Network UDP Initialized")
    except Exception as e:
        go1_dashboard["hw_link"] = HwStatus.OFFLINE
        write_log(f"Go1 Net Error: {e}")

def go1_keepalive_thread():
    while True:
        if go1_sock:
            try:
                # Go1 SDK requires constant heartbeat to keep connection alive
                go1_sock.sendto(b'ping', (GO1_IP, GO1_PORT))
            except: pass
        time.sleep(2.0)

# ================= [Go1 Hardware Nodes] =================

class Go1RobotDriver(BaseRobotDriver):
    def __init__(self): 
        self.last_write_time = 0
        self.write_interval = 0.05 # 20Hz 제한

    def get_ui_schema(self): 
        return [('vx', "Vx (F/B)", 0.0), ('vy', "Vy (L/R)", 0.0), ('vyaw', "Yaw (Turn)", 0.0)]
        
    def get_settings_schema(self): 
        return [('speed_scale', "Speed", 1.0)]
    
    def execute_command(self, inputs, settings):
        global go1_target_vel, go1_sock
        
        inputs_changed = False
        for key, _, _ in self.get_ui_schema():
            val = inputs.get(key)
            if val is not None and abs(float(val) - go1_target_vel.get(key, 0.0)) > 0.001:
                inputs_changed = True
                go1_target_vel[key] = float(val)

        scale = max(0.1, min(float(settings.get('speed_scale', 1.0)), 2.0))
        
        if time.time() - self.last_write_time >= self.write_interval:
            if go1_sock and go1_dashboard["hw_link"] == HwStatus.ONLINE:
                try:
                    payload = {
                        "type": "cmd",
                        "vx": go1_target_vel['vx'] * scale,
                        "vy": go1_target_vel['vy'] * scale,
                        "vyaw": go1_target_vel['vyaw'] * scale
                    }
                    go1_sock.sendto(json.dumps(payload).encode(), (GO1_IP, GO1_PORT))
                    self.last_write_time = time.time()
                except Exception as e:
                    go1_dashboard["hw_link"] = HwStatus.OFFLINE
        
        return go1_target_vel


class Go1ActionNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "Go1 Action", "GO1_ACTION")
        self.in_flow = generate_uuid(); self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid(); self.outputs[self.out_flow] = PortType.FLOW
        self.state['action'] = "Stand Up"
        
    def execute(self):
        global go1_sock
        action = self.state.get("action", "Stand Up")
        
        cmd_str = ""
        if action == "Stand Up": cmd_str = "stand"
        elif action == "Lie Down": cmd_str = "down"
        elif action == "Walk Mode": cmd_str = "walk"
        elif action == "Dance": cmd_str = "dance"
        
        if cmd_str and go1_sock:
            try: go1_sock.sendto(cmd_str.encode(), (GO1_IP, GO1_PORT))
            except: pass
            write_log(f"Go1 Action: {action}")
            
        return self.out_flow


# ================= [Vision Nodes (SRP Applied)] =================

class VideoSourceNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Video Source (RTSP/Webcam)", "VIDEO_SRC")
        self.out_frame = generate_uuid(); self.outputs[self.out_frame] = PortType.DATA
        self.state['url'] = "rtsp://192.168.12.1:8554/live"
        self.state['is_running'] = False
        
        self.cap = None
        self.latest_frame = None
        self.thread = None
        
    def _read_frames(self):
        while self.state.get('is_running', False):
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret: self.latest_frame = frame
                else: time.sleep(0.1)
            else: time.sleep(0.5)
            
    def execute(self):
        current_url = self.state.get('url', "")
        should_run = self.state.get('is_running', False)
        
        # Thread Management
        if should_run and (self.thread is None or not self.thread.is_alive()):
            self.cap = cv2.VideoCapture(current_url)
            self.thread = threading.Thread(target=self._read_frames, daemon=True)
            self.thread.start()
            write_log(f"Video Source Started: {current_url}")
            
        elif not should_run and self.thread is not None:
            if self.cap: self.cap.release()
            self.thread = None
            self.latest_frame = None
            write_log("Video Source Stopped")
            
        # Push numpy array to Data Pin
        self.output_data[self.out_frame] = self.latest_frame
        return None


class FisheyeUndistortNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Fisheye Undistort", "VIS_FISHEYE")
        self.in_frame = generate_uuid(); self.inputs[self.in_frame] = PortType.DATA
        self.out_frame = generate_uuid(); self.outputs[self.out_frame] = PortType.DATA
        
        self.K = None
        self.D = None
        self.map1, self.map2 = None, None
        
        # Load matrices if exist
        try:
            calib_dir = "Calib_data"
            self.K = np.load(os.path.join(calib_dir, "K1.npy"))
            self.D = np.load(os.path.join(calib_dir, "D1.npy"))
        except: pass

    def execute(self):
        frame = self.fetch_input_data(self.in_frame)
        if frame is not None and self.K is not None:
            if self.map1 is None:
                h, w = frame.shape[:2]
                new_K = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(self.K, self.D, (w, h), np.eye(3), balance=1.0)
                self.map1, self.map2 = cv2.fisheye.initUndistortRectifyMap(self.K, self.D, np.eye(3), new_K, (w, h), cv2.CV_16SC2)
            
            undistorted = cv2.remap(frame, self.map1, self.map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            self.output_data[self.out_frame] = undistorted
        else:
            self.output_data[self.out_frame] = frame # Pass-through if no calib data
        return None


class ArUcoDetectNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "ArUco Detect (V4/V5)", "VIS_ARUCO")
        self.in_frame = generate_uuid(); self.inputs[self.in_frame] = PortType.DATA
        self.out_frame = generate_uuid(); self.outputs[self.out_frame] = PortType.DATA
        self.out_data = generate_uuid(); self.outputs[self.out_data] = PortType.DATA # JSON/Dict info
        
        self.aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.parameters = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.ArucoDetector(self.aruco_dict, self.parameters)

    def execute(self):
        frame = self.fetch_input_data(self.in_frame)
        if frame is None:
            self.output_data[self.out_frame] = None
            self.output_data[self.out_data] = None
            return None
            
        process_frame = frame.copy()
        gray = cv2.cvtColor(process_frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self.detector.detectMarkers(gray)
        
        detected_info = []
        if ids is not None:
            cv2.aruco.drawDetectedMarkers(process_frame, corners, ids)
            for i in range(len(ids)):
                c = corners[i][0]
                cx = int((c[0][0] + c[2][0]) / 2)
                cy = int((c[0][1] + c[2][1]) / 2)
                cv2.circle(process_frame, (cx, cy), 5, (0, 0, 255), -1)
                detected_info.append({"id": int(ids[i][0]), "cx": cx, "cy": cy})
                
        self.output_data[self.out_frame] = process_frame
        self.output_data[self.out_data] = detected_info
        return None


# ================= [Flask Broadcast Threading] =================
flask_app = None
flask_current_frame = None

def start_flask_app(port):
    from flask import Flask, Response
    global flask_app
    app = Flask(__name__)
    
    def generate_mjpeg():
        global flask_current_frame
        while True:
            if flask_current_frame is not None:
                ret, jpeg = cv2.imencode('.jpg', flask_current_frame)
                if ret:
                    yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            time.sleep(0.05)
            
    @app.route('/video_feed')
    def video_feed():
        return Response(generate_mjpeg(), mimetype='multipart/x-mixed-replace; boundary=frame')
        
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

class FlaskStreamNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP/HTTP Broadcast", "VIS_FLASK")
        self.in_frame = generate_uuid(); self.inputs[self.in_frame] = PortType.DATA
        self.state['port'] = 5000
        self.state['is_running'] = False
        self.flask_thread = None

    def execute(self):
        global flask_current_frame, flask_app
        
        frame = self.fetch_input_data(self.in_frame)
        if frame is not None:
            flask_current_frame = frame.copy()
            
        should_run = self.state.get('is_running', False)
        if should_run and (self.flask_thread is None or not self.flask_thread.is_alive()):
            port = int(self.state.get('port', 5000))
            self.flask_thread = threading.Thread(target=start_flask_app, args=(port,), daemon=True)
            self.flask_thread.start()
            write_log(f"Flask Stream started on port {port}")
            
        return None