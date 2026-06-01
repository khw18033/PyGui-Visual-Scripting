import threading
import time

from djitellopy import Tello

from nodes.base import BaseNode, BaseRobotDriver
from core.engine import generate_uuid, PortType, write_log

TELLO_NETWORK_CONFIG = {
    "rc_interval_sec": 0.05,
    "max_vx": 0.5,
    "max_vy": 0.5,
    "max_vz": 0.5,
    "max_vyaw": 100.0,
}

tello_dashboard = {
    "hw_link": "Offline",
    "battery": -1,
    "flight_state": "landed",
    "last_error": "",
}

tello_state = {
    "battery": -1,
    "height": 0.0,
    "pitch": 0.0,
    "roll": 0.0,
    "yaw": 0.0,
    "tof": 0.0,
    "vx_cmd": 0.0,
    "vy_cmd": 0.0,
    "vz_cmd": 0.0,
    "vyaw_cmd": 0.0,
    "last_command": "",
}

tello_target_vel = {
    "vx": 0.0,
    "vy": 0.0,
    "vz": 0.0,
    "vyaw": 0.0,
}

tello_node_intent = {
    "vx": 0.0,
    "vy": 0.0,
    "vz": 0.0,
    "vyaw": 0.0,
    "takeoff": False,
    "land": False,
    "trigger_time": time.monotonic(),
}

_tello: Tello | None = None
_tello_online = False
_tello_keepalive_thread_started = False
_tello_stop_event = threading.Event()


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _get_tello() -> Tello:
    global _tello
    if _tello is None:
        _tello = Tello()
    return _tello


def _sync_state():
    drone = _get_tello()
    try:
        bat = drone.get_battery()
        tello_state["battery"] = bat
        tello_dashboard["battery"] = bat
        tello_state["height"] = float(drone.get_height())
        tello_state["pitch"] = float(drone.get_pitch())
        tello_state["roll"] = float(drone.get_roll())
        tello_state["yaw"] = float(drone.get_yaw())
        tello_state["tof"] = float(drone.get_distance_tof())
    except Exception:
        pass


def init_tello_connection() -> bool:
    global _tello_online
    drone = _get_tello()
    try:
        drone.connect()
        _tello_online = True
        tello_dashboard["hw_link"] = "Online"
        tello_dashboard["last_error"] = ""
        return True
    except Exception as exc:
        _tello_online = False
        tello_dashboard["hw_link"] = "Offline"
        tello_dashboard["last_error"] = str(exc)
        return False


def tello_keepalive_thread():
    global _tello_keepalive_thread_started
    if _tello_keepalive_thread_started:
        return
    _tello_keepalive_thread_started = True
    while not _tello_stop_event.is_set():
        if _tello_online:
            _sync_state()
        else:
            init_tello_connection()
        time.sleep(5.0)


def shutdown_tello():
    global _tello_online
    _tello_stop_event.set()
    _tello_online = False
    try:
        if _tello is not None:
            _tello.end()
    except Exception:
        pass


class TelloRobotDriver(BaseRobotDriver):
    def __init__(self):
        self.last_rc_command = ""
        self.last_rc_time = 0.0
        self.last_takeoff = False
        self.last_land = False

    def get_ui_schema(self):
        return [
            ("vx", "Vx(m/s)", 0.0),
            ("vy", "Vy(m/s)", 0.0),
            ("vz", "Vz(m/s)", 0.0),
            ("vyaw", "Yaw(deg/s)", 0.0),
            ("takeoff", "Takeoff", 0.0),
            ("land", "Land", 0.0),
        ]

    def get_settings_schema(self):
        return [
            ("max_vx", "Max Vx", TELLO_NETWORK_CONFIG["max_vx"]),
            ("max_vy", "Max Vy", TELLO_NETWORK_CONFIG["max_vy"]),
            ("max_vz", "Max Vz", TELLO_NETWORK_CONFIG["max_vz"]),
            ("max_vyaw", "Max Yaw", TELLO_NETWORK_CONFIG["max_vyaw"]),
            ("rc_interval_sec", "RC Int", TELLO_NETWORK_CONFIG["rc_interval_sec"]),
        ]

    def _ensure_online(self):
        if _tello_online:
            return True
        return init_tello_connection()

    def _send_rc(self, lr, fb, ud, yaw, settings):
        interval_sec = float(settings.get("rc_interval_sec", TELLO_NETWORK_CONFIG["rc_interval_sec"]))
        now = time.monotonic()
        command = f"rc {lr} {fb} {ud} {yaw}"
        if command == self.last_rc_command and (now - self.last_rc_time) < interval_sec:
            return
        try:
            _get_tello().send_rc_control(lr, fb, ud, yaw)
            tello_state["last_command"] = command
        except Exception as exc:
            tello_dashboard["last_error"] = str(exc)
        self.last_rc_command = command
        self.last_rc_time = now

    def execute_command(self, inputs, settings):
        vx_in = inputs.get("vx")
        vy_in = inputs.get("vy")
        vz_in = inputs.get("vz")
        vyaw_in = inputs.get("vyaw")

        if vx_in is not None:
            tello_node_intent["vx"] = float(vx_in)
        if vy_in is not None:
            tello_node_intent["vy"] = float(vy_in)
        if vz_in is not None:
            tello_node_intent["vz"] = float(vz_in)
        if vyaw_in is not None:
            tello_node_intent["vyaw"] = float(vyaw_in)

        takeoff_active = bool(inputs.get("takeoff"))
        land_active = bool(inputs.get("land"))

        if takeoff_active and not self.last_takeoff:
            self._ensure_online()
            try:
                _get_tello().takeoff()
                tello_dashboard["flight_state"] = "flying"
                tello_state["last_command"] = "takeoff"
            except Exception as exc:
                tello_dashboard["last_error"] = str(exc)
        elif land_active and not self.last_land:
            self._ensure_online()
            try:
                _get_tello().land()
                tello_dashboard["flight_state"] = "landed"
                tello_state["last_command"] = "land"
            except Exception as exc:
                tello_dashboard["last_error"] = str(exc)

        self.last_takeoff = takeoff_active
        self.last_land = land_active

        if not self._ensure_online():
            return {
                "vx": 0.0,
                "vy": 0.0,
                "vz": 0.0,
                "vyaw": 0.0,
                "takeoff": 0.0,
                "land": 0.0,
            }

        max_vx = float(settings.get("max_vx", TELLO_NETWORK_CONFIG["max_vx"]))
        max_vy = float(settings.get("max_vy", TELLO_NETWORK_CONFIG["max_vy"]))
        max_vz = float(settings.get("max_vz", TELLO_NETWORK_CONFIG["max_vz"]))
        max_vyaw = float(settings.get("max_vyaw", TELLO_NETWORK_CONFIG["max_vyaw"]))

        lr = int(round(_clamp(tello_node_intent["vy"] / max_vy if max_vy else 0.0, -1.0, 1.0) * 100))
        fb = int(round(_clamp(tello_node_intent["vx"] / max_vx if max_vx else 0.0, -1.0, 1.0) * 100))
        ud = int(round(_clamp(tello_node_intent["vz"] / max_vz if max_vz else 0.0, -1.0, 1.0) * 100))
        yaw = int(round(_clamp(tello_node_intent["vyaw"] / max_vyaw if max_vyaw else 0.0, -1.0, 1.0) * 100))

        if takeoff_active or land_active:
            lr = fb = ud = yaw = 0

        self._send_rc(lr, fb, ud, yaw, settings)

        tello_target_vel["vx"] = tello_node_intent["vx"]
        tello_target_vel["vy"] = tello_node_intent["vy"]
        tello_target_vel["vz"] = tello_node_intent["vz"]
        tello_target_vel["vyaw"] = tello_node_intent["vyaw"]

        tello_state["vx_cmd"] = float(tello_target_vel["vx"])
        tello_state["vy_cmd"] = float(tello_target_vel["vy"])
        tello_state["vz_cmd"] = float(tello_target_vel["vz"])
        tello_state["vyaw_cmd"] = float(tello_target_vel["vyaw"])

        return {
            "vx": tello_target_vel["vx"],
            "vy": tello_target_vel["vy"],
            "vz": tello_target_vel["vz"],
            "vyaw": tello_target_vel["vyaw"],
            "takeoff": 1.0 if takeoff_active else 0.0,
            "land": 1.0 if land_active else 0.0,
        }


class UniversalRobotNode(BaseNode):
    def __init__(self, node_id, driver, node_label="Tello Driver", node_type="TELLO_DRIVER"):
        super().__init__(node_id, node_label, node_type)
        self.driver = driver
        self.in_pins = {}
        self.setting_pins = {}

        for key, _, default_value in self.driver.get_ui_schema():
            attr_id = generate_uuid()
            self.inputs[attr_id] = PortType.DATA
            self.in_pins[key] = attr_id
            self.state[key] = default_value

        for key, _, default_value in self.driver.get_settings_schema():
            attr_id = generate_uuid()
            self.inputs[attr_id] = PortType.DATA
            self.setting_pins[key] = attr_id
            self.state[key] = default_value

        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

    def execute(self):
        inputs = {key: self.fetch_input_data(attr_id) for key, attr_id in self.in_pins.items()}
        settings = {key: self.fetch_input_data(attr_id) for key, attr_id in self.setting_pins.items()}

        for key in settings:
            if settings[key] is None:
                settings[key] = self.state.get(key, 0.0)

        new_state = self.driver.execute_command(inputs, settings)
        if new_state:
            for key, value in new_state.items():
                self.state[key] = value

        return self.out_flow


class TelloKeyboardNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (Tello)", "TELLO_KEYBOARD")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_vx = generate_uuid()
        self.outputs[self.out_vx] = PortType.DATA
        self.out_vy = generate_uuid()
        self.outputs[self.out_vy] = PortType.DATA
        self.out_vz = generate_uuid()
        self.outputs[self.out_vz] = PortType.DATA
        self.out_vyaw = generate_uuid()
        self.outputs[self.out_vyaw] = PortType.DATA
        self.out_takeoff = generate_uuid()
        self.outputs[self.out_takeoff] = PortType.DATA
        self.out_land = generate_uuid()
        self.outputs[self.out_land] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self.step_v = 0.3
        self.step_z = 0.3
        self.step_yaw = 60.0
        self.prev_keys = {}

    def _just_pressed(self, key):
        current = bool(self.state.get(key))
        previous = self.prev_keys.get(key, False)
        self.prev_keys[key] = current
        return current and not previous

    def execute(self):
        if self.state.get("is_focused", False):
            return self.out_flow

        vx = vy = vz = vyaw = takeoff = land = 0.0

        key_mode = self.state.get("keys", "WASD")
        if key_mode == "WASD":
            if self.state.get("W"):
                vx = self.step_v
            if self.state.get("S"):
                vx = -self.step_v
            if self.state.get("A"):
                vy = -self.step_v
            if self.state.get("D"):
                vy = self.step_v
        else:
            if self.state.get("UP"):
                vx = self.step_v
            if self.state.get("DOWN"):
                vx = -self.step_v
            if self.state.get("LEFT"):
                vy = -self.step_v
            if self.state.get("RIGHT"):
                vy = self.step_v

        if self.state.get("R"):
            vz = self.step_z
        if self.state.get("F"):
            vz = -self.step_z
        if self.state.get("Q"):
            vyaw = -self.step_yaw
        if self.state.get("E"):
            vyaw = self.step_yaw
        if self._just_pressed("T"):
            takeoff = 1.0
        if self._just_pressed("L"):
            land = 1.0

        self.output_data[self.out_vx] = vx
        self.output_data[self.out_vy] = vy
        self.output_data[self.out_vz] = vz
        self.output_data[self.out_vyaw] = vyaw
        self.output_data[self.out_takeoff] = takeoff
        self.output_data[self.out_land] = land
        return self.out_flow


class TelloActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Tello Action", "TELLO_ACTION")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_action = generate_uuid()
        self.inputs[self.in_action] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self.state["action"] = "Takeoff"

    _ACTION_MAP = {
        "Takeoff": ("takeoff", lambda d: d.takeoff()),
        "Land": ("land", lambda d: d.land()),
        "Flip Left": ("flip l", lambda d: d.flip_left()),
        "Flip Right": ("flip r", lambda d: d.flip_right()),
        "Flip Forward": ("flip f", lambda d: d.flip_forward()),
        "Flip Back": ("flip b", lambda d: d.flip_back()),
        "Emergency Stop": ("emergency", lambda d: d.emergency()),
    }

    def execute(self):
        action = self.fetch_input_data(self.in_action)
        if not action:
            action = self.state.get("action", "Takeoff")
        action = str(action).strip()

        entry = self._ACTION_MAP.get(action)
        if entry:
            cmd_name, fn = entry
            init_tello_connection()
            try:
                fn(_get_tello())
                tello_state["last_command"] = cmd_name
                if cmd_name == "takeoff":
                    tello_dashboard["flight_state"] = "flying"
                elif cmd_name == "land":
                    tello_dashboard["flight_state"] = "landed"
                elif cmd_name == "emergency":
                    tello_dashboard["flight_state"] = "stopped"
                write_log(f"Tello Action: {action}")
            except Exception as exc:
                tello_dashboard["last_error"] = str(exc)

        return self.out_flow
