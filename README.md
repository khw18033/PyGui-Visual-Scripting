# Go1_DS

이 프로젝트는 Dear PyGui 기반의 비주얼 스크립팅 툴로, Unitree Go1 제어, 카메라 수집, ArUco 추적, Unity 연동을 한 화면에서 다루기 위한 통합 실행 스크립트입니다.

핵심 실행 파일은 Go1_DS.py이며, 노드 기반 그래프를 구성해 로봇 동작과 데이터 흐름을 제어합니다.

## 주요 기능

1. Go1 제어
- Go1 SDK 통신 스레드를 통해 상태 수신과 명령 전송을 수행합니다.
- 키보드, 액션 노드, 유니티 Teleop 입력으로 vx, vy, wz 명령을 생성합니다.
- E-Stop, Yaw Reset, Yaw Align, 모드 전환(Stand/Walk) 로직을 포함합니다.

2. 카메라 수집 및 저장
- 원격 Nano에서 영상 송출 스크립트를 실행하고, 로컬에서 GStreamer로 JPEG 프레임을 파일 저장합니다.
- 카메라 타이머 기반 자동 종료를 지원합니다.
- 저장 경로는 사용자 입력으로 변경 가능하며, 경로가 없으면 자동 생성됩니다.

3. ArUco 추적 및 보정
- OpenCV ArUco Detector 기반 마커 검출, Pose 추정, 축 시각화를 수행합니다.
- 캘리브레이션 파일(Calib_data)을 사용한 왜곡 보정을 지원합니다.
- 검출 결과를 Unity UDP 전송 및 JSON 파일로 기록할 수 있습니다.

4. Unity 연동
- Unity에서 수신한 Teleop 명령을 로봇 제어 입력으로 사용합니다.
- 로봇 상태, 명령 상태를 Unity로 UDP 송신합니다.

5. 비주얼 노드 시스템
- START, IF, LOOP, 상수, 키 조건, 로그, 출력 노드 등의 기본 로직 노드 제공
- Go1 전용 노드(Driver, Action, Timed, Keyboard, Unity, Camera, Sender, State) 제공
- 그래프 저장/로드(JSON) 및 링크 편집을 지원합니다.

## 내부 구조 요약

1. 백그라운드 스레드
- Go1 통신 스레드
- 카메라 시작/종료 관리 스레드
- 비전 처리 스레드(프레임 후처리, ArUco)
- 이미지 서버 전송 스레드
- 네트워크 상태 모니터 스레드
- Flask 스트리밍 스레드

2. UI 실행 루프
- 대시보드 상태 텍스트(링크, 배터리, 명령, 오도메트리) 갱신
- 스크립트 RUN 상태일 때 그래프 실행 엔진 주기 수행

3. 그래프 실행 엔진
- Start 노드부터 Flow 링크를 따라 노드 execute를 순차 수행
- 데이터 핀은 링크된 출력 데이터를 입력으로 fetch하여 사용

## 실행 전 준비

1. Python 패키지 설치
- requirements.txt 기준으로 필요한 패키지를 설치합니다.
- 환경에 따라 cv2, dearpygui, flask, aiohttp, pyserial 설치가 필요할 수 있습니다.

2. SDK 준비
- unitree_legged_sdk 경로 아래 Python 바인딩이 현재 CPU 아키텍처에 맞게 배치되어야 합니다.

3. 네트워크 준비
- Go1, Relay(Pi), Nano, Unity PC 간 IP 라우팅과 SSH 키 인증이 올바르게 설정되어야 합니다.

## 실행 방법

1. 스크립트 실행
- python Go1_DS.py

2. 기본 사용 흐름
- RUN SCRIPT 버튼으로 그래프 실행 시작
- Camera 노드에서 Start Stream 설정 후 타이머/폴더 지정
- 필요 시 Unity 노드에서 Teleop 수신 및 ArUco 송신 활성화
- Files 탭에서 그래프 저장/로드

## 주의사항

1. SSH/네트워크 지연
- 카메라 시작 단계는 원격 명령 실행이 포함되어 시작 시점에 지연이 발생할 수 있습니다.

2. 저장 경로
- 상대 경로를 사용하면 스크립트 실행 작업 디렉터리 기준으로 폴더가 생성됩니다.

3. 성능
- 카메라, ArUco, Flask, Unity 통신을 동시에 사용할 경우 CPU 사용량이 증가할 수 있습니다.

## 파일 개요

1. Go1_DS.py
- 전체 UI, 노드 시스템, 스레드, 로봇/카메라/유니티 통신의 메인 통합 스크립트

2. Pre_Go1_DS.py
- 이전 버전 또는 실험 버전 스크립트

3. Calib_data
- 카메라 보정 파라미터 numpy 파일 모음

4. Node_File_Integrated
- 그래프 저장 JSON 파일 저장 폴더(실행 중 자동 생성)
