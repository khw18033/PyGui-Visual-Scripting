# Go1.py 기반 Depth Anything V2 통합 분석 보고서

작성일: 2026-04-27
대상 파일: [nodes/robots/go1.py](nodes/robots/go1.py)

## 1. 현재 go1.py 구조 분석

### 1.1 제어 상태와 데이터 허브
- 주행 의도 입력은 [nodes/robots/go1.py](nodes/robots/go1.py#L109)의 go1_node_intent에서 관리됨
- 실제 제어 상태 출력은 [nodes/robots/go1.py](nodes/robots/go1.py#L122)의 go1_state에서 관리됨
- 대시보드 표시는 [nodes/robots/go1.py](nodes/robots/go1.py#L144)의 go1_dashboard로 집계됨

의미:
- 새로운 비전 인지 결과를 제어에 연결하려면 go1_node_intent에 반영하거나
- 상태 가시화 목적이면 go1_state 또는 go1_dashboard에 별도 필드로 반영하는 구조가 적합함

### 1.2 주 제어 루프
- Go1 제어 루프는 [nodes/robots/go1.py](nodes/robots/go1.py#L689)에서 상시 동작
- 그래프 실행 주기는 [ui/dpg_manager.py](ui/dpg_manager.py#L1137)에서 LOGIC_RATE 0.02(약 50Hz)
- 실제 틱 호출은 [ui/dpg_manager.py](ui/dpg_manager.py#L1245)

의미:
- Depth 추론을 노드 execute 내부에서 동기 처리하면 그래프 틱 지연 위험이 큼
- 비동기 추론 스레드 또는 프레임 캐시 방식이 필수

### 1.3 현재 비전 파이프라인
- 프레임 수신: [VideoSourceNode](nodes/robots/go1.py#L1336)
- 어안 보정: [FisheyeUndistortNode](nodes/robots/go1.py#L1414)
- ArUco 검출/JSON: [ArUcoDetectNode](nodes/robots/go1.py#L1462)
- Flask 스트리밍: [FlaskStreamNode](nodes/robots/go1.py#L1605)
- 프레임 저장: [VideoFrameSaveNode](nodes/robots/go1.py#L1651)

의미:
- Depth Anything V2는 VIS_FISHEYE 이후, VIS_ARUCO 이전 또는 병렬로 배치하는 것이 자연스러움

### 1.4 노드 생성/UI 연결 지점
- 팩토리 등록 위치: [core/factory.py](core/factory.py#L56)
- GO1 비전 노드 등록 구간: [core/factory.py](core/factory.py#L56), [core/factory.py](core/factory.py#L57), [core/factory.py](core/factory.py#L58), [core/factory.py](core/factory.py#L59), [core/factory.py](core/factory.py#L60)
- 팔레트 버튼 구간: [ui/dpg_manager.py](ui/dpg_manager.py#L1109), [ui/dpg_manager.py](ui/dpg_manager.py#L1110), [ui/dpg_manager.py](ui/dpg_manager.py#L1111), [ui/dpg_manager.py](ui/dpg_manager.py#L1112), [ui/dpg_manager.py](ui/dpg_manager.py#L1113)

의미:
- 실제 통합 시 go1.py 외에 factory, dpg_manager 동시 확장이 필요함

## 2. Depth Anything V2를 어떻게 추가할지

## 2.1 추천 통합 방식
- 방식: Go1 비전 체인에 신규 노드 VIS_DEPTH_DA2 추가
- 권장 체인:
  Video Source -> Fisheye Undistort -> Depth Anything V2 -> (ArUco, Flask, Save 또는 제어 분기)

이유:
- 원본 수신/보정 노드를 재사용 가능
- 기존 노드 그래프 철학(BaseNode 데이터 포트 기반)에 맞음
- 결과 프레임과 수치 결과를 동시에 제공 가능

## 2.2 신규 노드 최소 I O 설계

입력:
- in_frame: 보정 완료 BGR 프레임

출력:
- out_depth_raw: HxW float depth map
- out_depth_vis: 컬러 또는 grayscale depth 시각화 프레임
- out_risk_json: ROI 기반 최소값, 분위수, 위험 플래그 JSON 문자열
- out_flow: 기존 실행 체인 호환용

state 예시:
- model_variant: vits, vitb, vitl
- device: auto, cuda, cpu
- input_size: 기본 518
- roi_mode: center_bottom
- roi_x0, roi_y0, roi_x1, roi_y1
- min_valid_depth, max_valid_depth
- risk_threshold
- consecutive_frames_for_stop
- output_json_path

## 2.3 모델 로딩/추론 구조

권장:
- 초기화 시 1회 모델 로드
- execute에서는 최신 프레임만 비동기 큐에 전달
- 추론 스레드는 마지막 프레임만 처리하고 결과 캐시 갱신
- execute는 캐시된 최신 결과를 즉시 반환

이유:
- 50Hz 그래프 틱을 막지 않음
- 추론 지연이 있어도 제어 루프 안정성 유지

## 2.4 제어 연결 방식

직접 정지 신호 연계는 단계적으로 적용:
1단계
- go1_state, go1_dashboard에 위험도만 표시
- 자동 정지는 비활성

2단계
- 위험이 연속 N프레임 검출될 때 go1_node_intent stop 세팅
- 히스테리시스 적용(정지 임계값과 해제 임계값 분리)

비고:
- 기존 즉시 정지 경로는 [nodes/robots/go1.py](nodes/robots/go1.py#L338)에 존재하므로, 우선순위 충돌 없이 합류 설계 필요

## 3. 추가 후 어떻게 작동하는지

런타임 동작 시퀀스:
1. VideoSourceNode가 최신 안정 프레임을 로드
2. FisheyeUndistortNode가 왜곡 보정/크롭 수행
3. Depth 노드가 프레임을 비동기 추론 큐에 전달
4. 이전 추론 결과 캐시를 즉시 out_depth_raw, out_depth_vis, out_risk_json으로 출력
5. 위험 플래그가 연속 조건을 만족하면 stop 의도 반영
6. go1_keepalive_thread가 최종 mode와 cmd를 로봇으로 전송

핵심:
- 추론은 느려도 제어 루프는 지속
- 판단은 최신 완료 추론 결과 기반으로 이루어짐

## 4. 결과값이 어떻게 출력되는지

## 4.1 노드 출력 데이터 형태
- out_depth_raw
  - 타입: numpy float32 HxW
  - 의미: 상대 깊이 또는 설정에 따른 metric depth

- out_depth_vis
  - 타입: BGR uint8 HxW x3
  - 용도: FlaskStreamNode 입력으로 실시간 시각화

- out_risk_json
  - 타입: string(JSON)
  - 예시 필드:
    - ts
    - roi
    - min_depth
    - p10_depth
    - valid_ratio
    - risk
    - stop_recommended
    - infer_latency_ms

## 4.2 파일/네트워크 출력 경로
- 파일 저장: VideoFrameSaveNode를 통해 시각화 프레임 저장 가능
  - 기준 노드: [nodes/robots/go1.py](nodes/robots/go1.py#L1651)
- 스트리밍: FlaskStreamNode로 depth 시각화 웹 송출 가능
  - 기준 노드: [nodes/robots/go1.py](nodes/robots/go1.py#L1605)
- JSON 저장/전송: ArUco 패턴처럼 depth JSON도 동일 스타일로 확장 가능
  - 유사 참고: [nodes/robots/go1.py](nodes/robots/go1.py#L1462)

## 4.3 대시보드 표출 권장 항목
- Depth Status: Ready, Running, Disabled
- Risk: SAFE, WARN, STOP
- ROI Min Depth
- Inference Latency ms
- Effective FPS

## 5. 정확도와 안전성 관점 결론

- Depth Anything V2는 기본적으로 상대 깊이 추정에 강점이 있음
- 즉시 정지 계층에서는 절대거리 정밀도보다 일관된 위험 분류와 저지연이 더 중요
- 따라서 초기 목표는 거리 계측보다 근접 위험 감지 신뢰도 확보가 타당

운영 권장:
1. 첫 단계는 경고 전용으로 운영
2. 로그로 임계값 탐색 후 자동 정지 활성화
3. 연속 프레임 조건과 히스테리시스 없이 단일 프레임 정지는 금지

## 6. 구현 체크리스트

1. go1.py에 VIS_DEPTH_DA2 노드 클래스 추가
2. core factory에 노드 타입 등록
3. dpg_manager에 UI 렌더, 상태 동기, 팔레트 버튼 추가
4. 비동기 추론 스레드 및 결과 캐시 설계
5. depth JSON 스키마 확정 및 로그 저장
6. 정지 정책 N프레임, 히스테리시스, 실패 시 fail-safe 정의
7. 성능 기준 캡처부터 명령까지 end to end latency 계측

## 7. 부록: Depth Anything V2 적용 메모

- 공식 README 기준 infer_image 사용 가능
- 모델 크기와 지연의 균형상 Go1 실시간 제어에는 Small부터 시작 권장
- 상업 사용 가능성 검토 시 모델 라이선스 구분 확인 필요
