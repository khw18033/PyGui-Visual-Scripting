# [AI 프롬프트용] MT4 노드 시스템 기능 복구 요구사항 정의서

> 너는 파이썬(Python)과 DearPyGui를 활용하여 노드 기반 로봇 제어 소프트웨어를 개발하는 엔지니어입니다. 아래 제시된 6가지 클래스의 비워진 `execute` 또는 `execute_command` 메서드 내부에 들어갈 코드를 요구사항에 맞게 작성해 주세요. 없는 변수나 함수를 지어내지(Hallucination) 말고, 주어진 전역 변수와 클래스 상태만을 활용해야 합니다.

---

## Task 1. `MT4RobotDriver.execute_command(self, inputs, settings)` 복구

* **목적:** 입력받은 목표 좌표와 현재 좌표를 비교하여 부드러운 이동(Smooth) 및 속도 제어를 수행하고 시리얼 통신으로 G-Code를 전송.
* **사용 변수:** 전역 `mt4_current_pos`, `mt4_target_goal`, `mt4_manual_override_until`, `ser` (시리얼 객체)
* **요구사항:**
1. `time.time() < mt4_collision_lock_until` 이면 즉시 `return`.
2. `inputs`에서 들어온 좌표값(x, y, z, roll, gripper)이 기존 `mt4_target_goal`과 **0.5 이상 차이**가 날 때만 입력이 변경된 것으로 간주(inputs_changed 플래그). 변경되었고 강제 제어 시간이 지났다면 `mt4_target_goal` 갱신.
3. XYZ 이동: `settings.get('smooth')` 값을 활용하여 보간법(`현재좌표 + 거리차이 * smooth`) 적용 (오차 0.5 미만이면 목표값으로 스냅).
4. 그리퍼 및 롤 속도 제어: `settings.get('grip_spd') * 0.1`, `settings.get('roll_spd') * 0.1`을 최대 속도로 삼아 `math.copysign`을 활용해 이동량 제한.
5. 생성된 모든 좌표를 `MT4_LIMITS` 및 `MT4_GRIPPER_MIN/MAX`로 Clamping.
6. `self.write_interval` 주기에 맞춰 G-Code(`G0 X... Y... Z... A... \nM3 S...`)를 생성하고 `ser.write`로 전송.
7. 갱신된 `new_state` 딕셔너리를 `mt4_current_pos`에 업데이트하고 반환(`return new_state`).



## Task 2. `StartNode.execute(self)` 복구

* **목적:** 노드 흐름의 시작점 역할.
* **요구사항:** 단순히 `self.out` 변수를 `return` 할 것.

## Task 3. `UniversalRobotNode.execute(self)` 복구

* **목적:** 드라이버에 입력값을 전달하고 결과값을 UI에 동기화.
* **요구사항:**
1. `self.in_pins`와 `self.setting_pins`를 순회하며 `self.fetch_input_data()`를 통해 선으로 연결된 외부 `inputs`와 `settings` 딕셔너리 생성.
2. 데이터가 `None`일 경우 `self.state`의 기본값을 사용하도록 폴백(Fallback) 처리.
3. `self.driver.execute_command(inputs, settings)` 호출.
4. 반환된 `new_state` 값을 `self.state`에 덮어쓰고, `self.ui_fields`에 매핑된 DearPyGui 아이템의 값을 `dpg.set_value()`로 업데이트.
5. `self.outputs`에서 값이 `PortType.FLOW`인 키를 찾아 `return`.



## Task 4. `MT4KeyboardNode.execute(self)` 복구

* **목적:** 키보드 입력을 통해 로봇의 목표 좌표(`mt4_target_goal`) 계산 및 갱신.
* **요구사항:**
1. `self.state.get("is_focused")`가 True면 키 입력을 무시하고 흐름(FLOW) 반환.
2. `time.time() - self.last_input_time > self.cooldown` 일 때만 키 입력 처리.
3. 키 맵핑:
* `WASD` 모드 또는 `Arrow Keys` 모드에 따라 X(`dx`), Y(`dy`) 축 이동값 설정 (1 또는 -1).
* Q/E (Z축 `dz`), J/U (그리퍼 `dg`), Z/X (롤 `dr`) 키 확인.


4. 키 입력이 하나라도 발생하면 `mt4_manual_override_until`을 현재 시간 + 0.5초로 연장하고, `mt4_target_goal`에 각 `step` 크기(`step_size`, `grip_step`, `roll_step`)를 곱하여 누적.
5. 누적된 `mt4_target_goal` 값을 `self.output_data`의 각 포트(`out_x`, `out_y`, `out_z`, `out_r`, `out_g`)에 저장.
6. 마지막으로 흐름(FLOW) 포트 반환.



## Task 5. `MT4UnityNode.execute(self)` 복구

* **목적:** 유니티에서 UDP로 받은 JSON(`raw_json`) 파싱 및 이벤트 분기 처리.
* **사용 변수:** `self.last_processed_json`, `mt4_manual_override_until`, `mt4_mode`
* **요구사항:**
1. `raw_json`이 기존 `self.last_processed_json`과 다를 경우만 새로운 메시지(`is_new_msg`)로 취급.
2. `mt4_manual_override_until` 조작 시간 내이거나 `mt4_mode["playing"]` 중일 경우(강제 제어 상태), JSON을 무시하고 현재 `mt4_target_goal`을 `self.output_data`로 바로 넘김 (Active Echo).
3. 새로운 메시지일 경우 JSON을 파싱(`json.loads` + `try-except`).
4. `"type"`이 `"CMD"`인 경우 `"val"` 문자열 분기:
* `COLLISION`: 락업 타임 갱신, 시리얼 `!` 전송, 충돌 로그 출력.
* `START_REC`, `STOP_REC:파일명`, `PLAY:파일명`: 관련된 글로벌 함수(`toggle_mt4_record`, `play_mt4_path`) 적절히 호출.
* `REQ_FILES`, `LOG_SUCCESS`, `LOG_FAIL`: UI 피드백 전송(`send_unity_ui`).


5. `"type"`이 `"MOVE"`인 경우 축 변환 공식 적용:
* `out_x` = `JSON의 z * 1000.0`
* `out_y` = `-JSON의 x * 1000.0`
* `out_z` = `(JSON의 y * 1000.0) + MT4_Z_OFFSET`
* 롤/그리퍼는 그대로 매핑.


6. `self.outputs` 반환.



## 🛠️ Task 6. `UDPReceiverNode.execute(self)` 복구

* **목적:** 로컬 소켓을 열어 비동기로 UDP 통신 수신.
* **요구사항:**
1. UI에서 설정한 `port`와 `ip`를 읽어와 전역 `MT4_UNITY_IP`를 갱신.
2. `self.is_bound`가 False이거나 포트가 변경되었다면 기존 소켓을 닫고, 새로운 소켓을 생성하여 `('0.0.0.0', port)`에 `bind`. (`setblocking(False)` 필수)
3. `while True:` 루프 안에서 `self.sock.recvfrom(4096)`을 시도. `try-except`로 블로킹 예외를 안전하게 넘길 것.
4. 데이터를 성공적으로 받았다면 디코딩 후 `mt4_dashboard`의 `latency`, `last_pkt_time`, `status` 갱신.
5. 수신한 텍스트를 `self.output_data[self.out_json]`에 저장.
6. 최종적으로 흐름(`self.out_flow`) 반환.