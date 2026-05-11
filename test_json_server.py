"""
Simple HTTP server for testing Go1ServerJsonRecvNode HTTP mode.
Serves JSON commands at http://127.0.0.1:8001/cmd without external dependencies.

Run: python test_json_server.py
Then set Go1ServerJsonRecvNode mode to "HTTP" and source to "http://127.0.0.1:8001/cmd"

Interactive CLI - Type commands in real-time to control the server:
  front, back, left, right, stop, spin, vx:<value>, vy:<value>, wz:<value>, custom:<json>
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import socket


HOST = '127.0.0.1'
PORT = 8001
SERVER_BIND_HOST = '0.0.0.0'

state = {
    'detections': [],
    'command': 'idle',
    'vx': 0.0,
    'vy': 0.0,
    'wz': 0.0,
    'stop': False,
    'seq': 0,
}

state_lock = threading.Lock()
server_ready = threading.Event()

GO1_POLICY_PRESETS = {
    'AGENT': {'name': 'person', 'rel_depth': 2.0, 'bbox_xyxy': [150, 110, 260, 300]},
    'VEHICLE': {'name': 'car', 'rel_depth': 3.0, 'bbox_xyxy': [145, 110, 285, 305]},
    'HARD_OBSTACLE': {'name': 'box', 'rel_depth': 1.5, 'bbox_xyxy': [165, 90, 295, 290]},
    'SOFT_PUSHABLE': {'name': 'backpack', 'rel_depth': 1.8, 'bbox_xyxy': [170, 120, 260, 280]},
    'LOW_OBSTACLE': {'name': 'laptop', 'rel_depth': 1.4, 'bbox_xyxy': [170, 150, 250, 240]},
    'THIN_OBSTACLE': {'name': 'wire', 'rel_depth': 0.8, 'bbox_xyxy': [150, 140, 310, 185]},
    'GROUND_HAZARD': {'name': 'stairs', 'rel_depth': 1.2, 'bbox_xyxy': [140, 155, 300, 280]},
    'UNKNOWN_OBSTACLE': {'name': 'traffic cone', 'rel_depth': 1.0, 'bbox_xyxy': [175, 120, 255, 260]},
}

GO1_SCENARIO_ALIASES = {
    'agent': 'AGENT',
    'person': 'AGENT',
    'pedestrian': 'AGENT',
    'vehicle': 'VEHICLE',
    'car': 'VEHICLE',
    'hard': 'HARD_OBSTACLE',
    'hard_obstacle': 'HARD_OBSTACLE',
    'soft': 'SOFT_PUSHABLE',
    'soft_pushable': 'SOFT_PUSHABLE',
    'low': 'LOW_OBSTACLE',
    'low_obstacle': 'LOW_OBSTACLE',
    'thin': 'THIN_OBSTACLE',
    'thin_obstacle': 'THIN_OBSTACLE',
    'ground': 'GROUND_HAZARD',
    'ground_hazard': 'GROUND_HAZARD',
    'unknown': 'UNKNOWN_OBSTACLE',
    'unknown_obstacle': 'UNKNOWN_OBSTACLE',
}

GO1_HARD_OBSTACLE_BBOX_PRESETS = {
    'left': [25, 95, 140, 285],
    'center': [165, 95, 300, 285],
    'right': [320, 95, 440, 285],
    'clear': [0, 95, 80, 285],
}

GO1_SOFT_PUSHABLE_BBOX_PRESETS = {
    'left': [50, 120, 150, 280],
    'center': [170, 120, 260, 280],
    'right': [300, 120, 400, 280],
}

GO1_LOW_OBSTACLE_BBOX_PRESETS = {
    'left': [50, 150, 150, 240],
    'center': [170, 150, 250, 240],
    'right': [300, 150, 400, 240],
}

GO1_THIN_OBSTACLE_BBOX_PRESETS = {
    'left': [30, 140, 150, 185],
    'center': [150, 140, 310, 185],
    'right': [310, 140, 430, 185],
}

GO1_GROUND_HAZARD_BBOX_PRESETS = {
    'left': [20, 155, 160, 280],
    'center': [140, 155, 300, 280],
    'right': [310, 155, 450, 280],
}


def _get_lan_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('8.8.8.8', 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return '127.0.0.1'


def _snapshot_state():
    with state_lock:
        data = dict(state)
        data['ts'] = time.time()
        data['has_near_obstacle'] = any(
            str(det.get('risk_level', '')).strip().lower() == 'near'
            for det in data.get('detections', [])
            if isinstance(det, dict)
        )
        return data


def _current_command_text():
    snapshot = _snapshot_state()
    return str(snapshot.get('command', 'idle')).strip().lower() or 'idle'


def _apply_motion(command, vx, vy, wz, stop=False):
    with state_lock:
        state['command'] = command
        state['vx'] = float(vx)
        state['vy'] = float(vy)
        state['wz'] = float(wz)
        state['stop'] = bool(stop)
        state['seq'] += 1
        state['ts'] = time.time()


def _apply_custom(custom_data):
    with state_lock:
        if 'vx' in custom_data:
            state['vx'] = float(custom_data['vx'])
        if 'vy' in custom_data:
            state['vy'] = float(custom_data['vy'])
        if 'wz' in custom_data:
            state['wz'] = float(custom_data['wz'])
        if 'stop' in custom_data:
            state['stop'] = bool(custom_data['stop'])
        if 'command' in custom_data:
            state['command'] = str(custom_data['command'])
        state['seq'] += 1
        state['ts'] = time.time()


def _parse_bbox_value(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, (list, tuple)) and len(raw_value) == 4:
        return [float(raw_value[0]), float(raw_value[1]), float(raw_value[2]), float(raw_value[3])]
    text = str(raw_value).strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(',') if part.strip()]
    if len(parts) != 4:
        raise ValueError('bbox must contain four comma-separated values')
    return [float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])]


def _apply_policy_scenario(policy_name, placement='center'):
    normalized_policy = str(policy_name or '').strip().upper()
    preset = GO1_POLICY_PRESETS.get(normalized_policy)
    if not preset:
        raise KeyError(normalized_policy)

    placement_lower = str(placement or 'center').strip().lower()
    
    # 정책별 bbox 프리셋 선택
    if normalized_policy == 'HARD_OBSTACLE':
        bbox_xyxy = GO1_HARD_OBSTACLE_BBOX_PRESETS.get(placement_lower, GO1_HARD_OBSTACLE_BBOX_PRESETS['center'])
    elif normalized_policy == 'SOFT_PUSHABLE':
        bbox_xyxy = GO1_SOFT_PUSHABLE_BBOX_PRESETS.get(placement_lower, GO1_SOFT_PUSHABLE_BBOX_PRESETS['center'])
    elif normalized_policy == 'LOW_OBSTACLE':
        bbox_xyxy = GO1_LOW_OBSTACLE_BBOX_PRESETS.get(placement_lower, GO1_LOW_OBSTACLE_BBOX_PRESETS['center'])
    elif normalized_policy == 'THIN_OBSTACLE':
        bbox_xyxy = GO1_THIN_OBSTACLE_BBOX_PRESETS.get(placement_lower, GO1_THIN_OBSTACLE_BBOX_PRESETS['center'])
    elif normalized_policy == 'GROUND_HAZARD':
        bbox_xyxy = GO1_GROUND_HAZARD_BBOX_PRESETS.get(placement_lower, GO1_GROUND_HAZARD_BBOX_PRESETS['center'])
    else:
        bbox_xyxy = list(preset['bbox_xyxy'])

    _clear_detections()
    _add_detection(
        1,
        preset['name'],
        preset['rel_depth'],
        group=normalized_policy,
        risk_level='near',
        bbox_xyxy=bbox_xyxy,
    )


def _apply_multi_policy_scenario():
    _clear_detections()
    _add_detection(1, 'person', 2.0, group='AGENT', risk_level='near', bbox_xyxy=[155, 110, 260, 300])
    _add_detection(2, 'box', 1.7, group='HARD_OBSTACLE', risk_level='near', bbox_xyxy=[25, 100, 140, 290])
    _add_detection(3, 'wire', 0.9, group='THIN_OBSTACLE', risk_level='near', bbox_xyxy=[150, 145, 310, 180])


def _apply_named_scenario(scenario_name, placement='center'):
    normalized_name = str(scenario_name or '').strip().lower()
    if normalized_name == 'multi':
        _apply_multi_policy_scenario()
        return 'multi'
    if normalized_name == 'clear':
        _clear_detections()
        return 'clear'

    policy_name = GO1_SCENARIO_ALIASES.get(normalized_name, normalized_name.upper())
    if policy_name in GO1_POLICY_PRESETS:
        _apply_policy_scenario(policy_name, placement=placement)
        return policy_name

    raise KeyError(normalized_name)


def _set_named_command(name):
    if name == 'front':
        _apply_motion('front', 0.2, 0.0, 0.0, False)
    elif name == 'back':
        _apply_motion('back', -0.2, 0.0, 0.0, False)
    elif name == 'left':
        _apply_motion('left', 0.0, 0.2, 0.0, False)
    elif name == 'right':
        _apply_motion('right', 0.0, -0.2, 0.0, False)
    elif name == 'stop':
        _apply_motion('stop', 0.0, 0.0, 0.0, True)
    elif name == 'spin':
        _apply_motion('spin', 0.0, 0.0, 1.0, False)


def _add_detection(det_id, name, rel_depth, group=None, risk_level='near', bbox_xyxy=None):
    """detection 추가"""
    detection = {
        'id': det_id,
        'name': name,
        'rel_depth': float(rel_depth),
    }
    if group:
        detection['group'] = group
    if risk_level:
        detection['risk_level'] = risk_level
    if bbox_xyxy:
        detection['bbox_xyxy'] = bbox_xyxy
    
    with state_lock:
        state['detections'].append(detection)
        state['seq'] += 1
        state['ts'] = time.time()


def _clear_detections():
    """모든 detection 제거"""
    with state_lock:
        state['detections'] = []
        state['seq'] += 1
        state['ts'] = time.time()


class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200):
        body = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'

        if path == '/':
            self._send_text(
                '<h1>Go1 JSON Test Server</h1>'
                '<p>Open <a href="/cmd">/cmd</a> for JSON output.</p>'
                '<p>Use the console or /cmd/scenario/&lt;name&gt; to trigger policy presets.</p>'
            )
            return

        if path == '/cmd':
            # 전체 state를 dict로 반환 (go1.py가 파싱 가능하도록)
            # 1회 송신 보장: 매 폴링마다 seq 증가 + 바로 SAFE 상태로 복귀
            with state_lock:
                state['seq'] += 1
                response_data = dict(state)
                response_data['ts'] = time.time()
                response_data['has_near_obstacle'] = any(
                    str(det.get('risk_level', '')).strip().lower() == 'near'
                    for det in response_data.get('detections', [])
                    if isinstance(det, dict)
                )
                # 한 번 보낸 후 바로 안전 상태로: detections 비움
                state['detections'] = []
            self._send_json(response_data)
            return

        if path == '/cmd/clear':
            _clear_detections()
            with state_lock:
                state['seq'] += 1
            self._send_json(_snapshot_state())
            return

        if path.startswith('/cmd/scenario/'):
            scenario_name = path[len('/cmd/scenario/'):]
            placement = 'center'
            if '/' in scenario_name:
                scenario_name, placement = scenario_name.split('/', 1)
            try:
                applied = _apply_named_scenario(scenario_name, placement=placement)
            except KeyError:
                self._send_json({'status': 'error', 'message': f'unknown scenario: {scenario_name}'}, status=404)
                return
            with state_lock:
                state['seq'] += 1
                response_data = dict(state)
                response_data['ts'] = time.time()
                response_data['has_near_obstacle'] = any(
                    str(det.get('risk_level', '')).strip().lower() == 'near'
                    for det in response_data.get('detections', [])
                    if isinstance(det, dict)
                )
                state['detections'] = []
            self._send_json({
                'status': 'ok',
                'scenario': applied,
                'placement': placement,
                'current_state': response_data,
            })
            return

        if path.startswith('/cmd/policy/'):
            policy_name = path[len('/cmd/policy/'):]
            placement = 'center'
            if '/' in policy_name:
                policy_name, placement = policy_name.split('/', 1)
            try:
                applied = _apply_named_scenario(policy_name, placement=placement)
            except KeyError:
                self._send_json({'status': 'error', 'message': f'unknown policy: {policy_name}'}, status=404)
                return
            with state_lock:
                state['seq'] += 1
                response_data = dict(state)
                response_data['ts'] = time.time()
                response_data['has_near_obstacle'] = any(
                    str(det.get('risk_level', '')).strip().lower() == 'near'
                    for det in response_data.get('detections', [])
                    if isinstance(det, dict)
                )
                state['detections'] = []
            self._send_json({
                'status': 'ok',
                'policy': applied,
                'placement': placement,
                'current_state': response_data,
            })
            return

        if path in ('/cmd/front', '/cmd/back', '/cmd/left', '/cmd/right', '/cmd/stop', '/cmd/spin'):
            _set_named_command(path.rsplit('/', 1)[-1])
            with state_lock:
                state['seq'] += 1
                response_data = dict(state)
                response_data['ts'] = time.time()
                response_data['has_near_obstacle'] = False
                state['detections'] = []
            self._send_json(response_data)
            return

        if path == '/cmd/status':
            self._send_json({
                'status': 'ok',
                'server': 'test_json_server',
                'current_state': _snapshot_state(),
            })
            return

        if path == '/cmd/set':
            query = parse_qs(parsed.query)
            custom = {}
            for key in ('command', 'vx', 'vy', 'wz', 'stop'):
                if key in query and query[key]:
                    custom[key] = query[key][-1]
            if custom:
                _apply_custom(custom)
                with state_lock:
                    state['seq'] += 1
                    response_data = dict(state)
                    response_data['ts'] = time.time()
                    state['detections'] = []
                self._send_json(response_data)
                return
            self._send_json({'status': 'error', 'message': 'missing query parameters'}, status=400)
            return

        self._send_json({'status': 'error', 'message': f'not found: {path}'}, status=404)


def _print_help():
    lan_ip = _get_lan_ip()
    print("\n" + "=" * 70)
    print("Go1 JSON Test Server - 정책표 테스트 모드")
    print("=" * 70)
    print(f"HTTP URL: http://0.0.0.0:{PORT}/cmd")
    print("\nGo1ServerJsonRecvNode 설정:")
    print("  Mode:   HTTP")
    print(f"  Source: http://{lan_ip}:{PORT}/cmd")
    print("\n[Detection 관련 명령어]")
    print("  add <id> <class> <depth> [group] [risk=near|far] [bbox=x0,y0,x1,y1]")
    print("    예: add 1 person 1.5")
    print("    예: add 2 box 2.0 HARD_OBSTACLE")
    print("    예: add 3 box 1.5 HARD_OBSTACLE bbox=25,95,140,285")
    print("    지원 클래스: person, car, box, chair, wire, bottle, staircase, puddle, ...")
    print("  clear - 모든 detection 제거")
    print("  list  - 현재 detections 출력")
    print("\n[테스트 시나리오]")
    print("  scenario <name>[:<placement>]  (placement: left|center|right)")
    print("    agent/person   - AGENT (긴급정지 4초)")
    print("    vehicle/car    - VEHICLE (긴급정지 4초)")
    print("    hard[:left|center|right] - HARD_OBSTACLE (회피, bbox 위치 선택 가능)")
    print("    soft[:left|center|right] - SOFT_PUSHABLE (회피, bbox 위치 선택 가능)")
    print("    low[:left|center|right]  - LOW_OBSTACLE (회피, bbox 위치 선택 가능)")
    print("    thin[:left|center|right] - THIN_OBSTACLE (정지 1초 → 후진, bbox 위치 선택 가능)")
    print("    ground[:left|center|right] - GROUND_HAZARD (정지 2초 → 후진, bbox 위치 선택 가능)")
    print("    unknown        - UNKNOWN_OBSTACLE (정지 2초)")
    print("    multi          - 여러 정책 동시 인식")
    print("  HTTP: /cmd/scenario/<name>, /cmd/policy/<name>, /cmd/clear")
    print("\n[기본 명령어]")
    print("  status - 현재 상태 출력")
    print("  help   - 이 메시지 출력")
    print("  exit   - 종료")
    print("=" * 70 + "\n")


def _handle_cli_command(user_input):
    if user_input in ['front', 'back', 'left', 'right', 'stop', 'spin']:
        _set_named_command(user_input)
        print(f"→ {user_input} applied")
        return

    if user_input == 'clear':
        _clear_detections()
        print("→ 모든 detections 제거됨")
        return

    if user_input == 'list':
        snapshot = _snapshot_state()
        dets = snapshot.get('detections', [])
        if not dets:
            print("현재 detections: 없음")
        else:
            print(f"현재 detections ({len(dets)}개):")
            for det in dets:
                print(f"  - ID:{det['id']} | {det['name']} | depth:{det['rel_depth']}m | group:{det.get('group', 'AUTO')}")
        return

    if user_input.startswith('add '):
        parts = user_input[4:].split()
        if len(parts) < 3:
            print("✗ 사용법: add <id> <class> <depth> [group] [risk=near|far] [bbox=x0,y0,x1,y1]")
            print("  예: add 1 person 1.5")
            print("  예: add 2 box 2.0 HARD_OBSTACLE")
            print("  예: add 3 box 1.5 HARD_OBSTACLE bbox=25,95,140,285")
            return
        try:
            det_id = int(parts[0])
            class_name = parts[1]
            depth = float(parts[2])
            group = None
            risk_level = 'near'
            bbox_xyxy = None
            remaining = parts[3:]
            if remaining and '=' not in remaining[0]:
                group = remaining[0]
                remaining = remaining[1:]
            for token in remaining:
                if '=' not in token:
                    continue
                key, value = token.split('=', 1)
                key = key.strip().lower()
                value = value.strip()
                if key == 'group':
                    group = value
                elif key == 'risk':
                    risk_level = value
                elif key == 'bbox':
                    bbox_xyxy = _parse_bbox_value(value)
            _add_detection(det_id, class_name, depth, group, risk_level=risk_level, bbox_xyxy=bbox_xyxy)
            print(f"→ Detection 추가: id={det_id}, class={class_name}, depth={depth}m, group={group or 'AUTO'}, risk={risk_level}, bbox={bbox_xyxy or 'AUTO'}")
        except ValueError:
            print(f"✗ 잘못된 형식: add {user_input[4:]}")
        return

    if user_input.startswith('scenario '):
        scenario_input = user_input[9:].strip()
        placement = 'center'
        if ':' in scenario_input:
            scenario_name, placement = scenario_input.split(':', 1)
        elif '/' in scenario_input:
            scenario_name, placement = scenario_input.split('/', 1)
        else:
            scenario_name = scenario_input
        scenario_key = scenario_name.strip().lower()
        try:
            applied = _apply_named_scenario(scenario_key, placement=placement)
            snapshot = _snapshot_state()
            print(f"→ 시나리오: {applied} (placement={placement}) | detections={len(snapshot.get('detections', []))} | near={snapshot.get('has_near_obstacle', False)}")
        except KeyError:
            print(f"✗ 알 수 없는 시나리오: {scenario_key}")
            print("  지원: agent, person, vehicle, car, hard[:left|center|right|clear], soft[:left|center|right], low[:left|center|right], thin[:left|center|right], ground[:left|center|right], unknown, multi, clear")
        return

    if user_input == 'status':
        snapshot = _snapshot_state()
        dets = snapshot.get('detections', [])
        print(f"\n현재 상태:")
        print(f"  Detections: {len(dets)}개")
        for det in dets:
            print(f"    - {det['name']} (depth: {det['rel_depth']}m)")
        print(f"  Has near obstacle: {snapshot.get('has_near_obstacle', False)}")
        print(f"  Motion: vx={snapshot['vx']}, vy={snapshot['vy']}, wz={snapshot['wz']}")
        print(f"  Stop: {snapshot['stop']}")
        print(f"  Seq: {snapshot['seq']}\n")
        return

    if user_input == 'help':
        _print_help()
        return

    if user_input.startswith('vx:'):
        try:
            value = float(user_input[3:])
            with state_lock:
                state['vx'] = value
                state['seq'] += 1
                state['ts'] = time.time()
            print(f"→ VX set to {value}")
        except ValueError:
            print(f"✗ Invalid value for vx: {user_input[3:]}")
        return

    if user_input.startswith('vy:'):
        try:
            value = float(user_input[3:])
            with state_lock:
                state['vy'] = value
                state['seq'] += 1
                state['ts'] = time.time()
            print(f"→ VY set to {value}")
        except ValueError:
            print(f"✗ Invalid value for vy: {user_input[3:]}")
        return

    if user_input.startswith('wz:'):
        try:
            value = float(user_input[3:])
            with state_lock:
                state['wz'] = value
                state['seq'] += 1
                state['ts'] = time.time()
            print(f"→ WZ set to {value}")
        except ValueError:
            print(f"✗ Invalid value for wz: {user_input[3:]}")
        return

    if user_input.startswith('custom:'):
        try:
            json_str = user_input[7:].strip()
            custom_data = json.loads(json_str)
            if isinstance(custom_data, dict):
                _apply_custom(custom_data)
                print(f"→ Custom state applied")
            else:
                print("✗ custom: 뒤에는 JSON object가 와야 합니다.")
        except json.JSONDecodeError:
            print(f"✗ Invalid JSON: {user_input[7:]}")
        return

    print(f"✗ Unknown command: {user_input}")
    print("   Type 'help' for available commands")


def _server_thread():
    try:
        httpd = ThreadingHTTPServer((SERVER_BIND_HOST, PORT), RequestHandler)
        server_ready.set()
        httpd.serve_forever()
    except OSError as e:
        print(f"✗ Failed to bind server on {SERVER_BIND_HOST}:{PORT}: {e}")
        server_ready.set()


if __name__ == '__main__':
    lan_ip = _get_lan_ip()
    _print_help()

    thread = threading.Thread(target=_server_thread, daemon=True)
    thread.start()
    server_ready.wait(timeout=5.0)

    print(f"✓ Server running at http://0.0.0.0:{PORT}")
    print(f"  Go1 JSON Recv 설정:")
    print(f"    Mode:   HTTP")
    print(f"    Source: http://{lan_ip}:{PORT}/cmd")
    print("\n자동 회피 정책 테스트 시작...")
    print("명령어를 입력하세요 ('help' 입력하면 도움말 표시):\n")

    try:
        while True:
            try:
                user_input = input(">> ").strip().lower()
            except EOFError:
                break

            if not user_input:
                continue

            if user_input in ['exit', 'quit']:
                print("Shutting down...")
                break

            _handle_cli_command(user_input)
    except KeyboardInterrupt:
        print("\n\nShutting down...")

    sys.exit(0)
