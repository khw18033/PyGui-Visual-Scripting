# Server Sender 노드 작동 가이드

## 📋 목차
1. [Server Sender 노드 개요](#1-server-sender-노드-개요)
2. [작동 원리](#2-작동-원리)
3. [아키텍처 다이어그램](#3-아키텍처-다이어그램)
4. [카메라 노드와의 연결](#4-카메라-노드와의-연결)
5. [실행 흐름](#5-실행-흐름)
6. [사용 예시](#6-사용-예시)

---

## 1. Server Sender 노드 개요

### 노드 정보
- **노드 이름**: Server Sender (Go1)
- **노드 타입**: `MULTI_SENDER`
- **파일**: `Go1_DS.py`, `visual_scripting_Int_v11.py`
- **역할**: 라즈베리파이/Nano 카메라에서 수집한 이미지를 원격 서버로 HTTP 업로드

### 주요 기능
- ✅ 원격 서버 URL 설정
- ✅ 송신 시작/중지 제어
- ✅ 비동기 이미지 업로드 (asyncio + aiohttp)
- ✅ Flow 제어 (Flow In/Out 포트)

### UI 구성
```
┌─────────────────────────────────┐
│  Server Sender (Go1)            │
├─────────────────────────────────┤
│ [Flow In]                       │
│                                 │
│ Action: [Start Sender ▼]        │
│                                 │
│ Server URL:                     │
│ [http://210.110.250.33:5001/...] │
│                                 │
│ [Flow Out]                      │
└─────────────────────────────────┘
```

---

## 2. 작동 원리

### 전체 구성 요소

#### A. MultiSenderNode (사용자 UI)
**위치**: `Go1_DS.py` 라인 773-791

```python
class MultiSenderNode(BaseNode):
    def execute(self):
        action = dpg.get_value(self.combo_action)  # "Start Sender" 또는 "Stop Sender"
        url = dpg.get_value(self.field_url)         # 원격 서버 URL
        
        if action == "Start Sender" and sender_state['status'] == 'Stopped':
            sender_state['status'] = 'Starting...'
            sender_command_queue.append(('START', url))  # 시작 명령 큐에 추가
        elif action == "Stop Sender" and sender_state['status'] == 'Running':
            sender_state['status'] = 'Stopping...'
            sender_command_queue.append(('STOP', url))   # 중지 명령 큐에 추가
        
        return self.out_flow
```

**역할**:
- UI 입력값 수집 (액션, 서버 URL)
- 명령을 `sender_command_queue`에 큐잉
- Flow 전달

---

#### B. sender_manager_thread() (송신 관리 스레드)
**위치**: `Go1_DS.py` 라인 554-565

```python
def sender_manager_thread():
    global multi_sender_active, sender_state
    sender_threads = []
    
    while True:
        if sender_command_queue:
            cmd, url = sender_command_queue.popleft()  # 큐에서 명령 추출
            
            if cmd == 'START' and not multi_sender_active:
                # 1. 송신 활성화
                multi_sender_active = True
                sender_state['status'] = 'Running'
                write_log(f"Sender: Connect to {url}")
                
                # 2. CAMERA_CONFIG의 각 카메라마다 비동기 워커 스레드 생성
                for config in CAMERA_CONFIG:
                    s_thread = threading.Thread(
                        target=start_async_loop,
                        args=(config, url)
                    )
                    s_thread.daemon = True
                    s_thread.start()
                    sender_threads.extend([s_thread])
            
            elif cmd == 'STOP' and multi_sender_active:
                # 송신 종료
                multi_sender_active = False
                sender_state['status'] = 'Stopped'
                write_log("Sender: Disconnected")
                sender_threads.clear()
        
        time.sleep(0.1)
```

**역할**:
- 송신 명령 처리
- 카메라별 비동기 워커 스레드 생성/관리
- 송신 상태 관리

---

#### C. camera_async_worker() (카메라 이미지 수집 및 송신)
**위치**: `Go1_DS.py` 라인 527-541

```python
async def camera_async_worker(config, server_url):
    global multi_sender_active
    
    folder = config["folder"]           # 예: "Go1_Images/test1"
    camera_id = config["id"]            # 예: "go1_front"
    last_processed_file = None
    
    os.makedirs(folder, exist_ok=True)
    
    async with aiohttp.ClientSession() as session:
        while multi_sender_active:
            cycle_start = time.time()
            
            # 1. 폴더에서 최신 이미지 파일 찾기
            files = glob.glob(os.path.join(folder, "*.jpg"))
            
            if files:
                valid_files = []
                for f in files:
                    try:
                        valid_files.append((os.path.getctime(f), f))
                    except OSError:
                        pass
                
                if valid_files:
                    # 2. 가장 최신 파일 선택
                    _, latest_file = max(valid_files)
                    
                    # 3. 이전에 처리하지 않은 새로운 파일이면 업로드
                    if latest_file != last_processed_file:
                        last_processed_file = latest_file
                        await send_image_async(session, latest_file, camera_id, server_url)
            
            # 4. 타이밍 조정 (TARGET_FPS 기반)
            await asyncio.sleep(max(0, INTERVAL - (time.time() - cycle_start)))

def start_async_loop(config, server_url):
    # asyncio 이벤트루프 생성 및 실행
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(camera_async_worker(config, server_url))
```

**역할**:
- 폴더 모니터링 (`CAMERA_CONFIG`의 `folder`)
- 최신 이미지 파일 감지
- 중복 업로드 방지
- 비동기 업로드 스케줄링

---

#### D. send_image_async() (HTTP 업로드)
**위치**: `Go1_DS.py` 라인 519-526

```python
async def send_image_async(session, filepath, camera_id, server_url):
    try:
        global latest_processed_frames
        
        # 1. 이미지 데이터 로드
        file_data = latest_processed_frames.get(camera_id)
        
        if file_data is None:
            if not os.path.exists(filepath):
                return
            with open(filepath, 'rb') as f:
                file_data = f.read()
        
        # 2. multipart/form-data 형식으로 데이터 구성
        form = aiohttp.FormData()
        form.add_field('camera_id', camera_id)
        form.add_field(
            'file',
            file_data,
            filename=f"{camera_id}_calib.jpg",
            content_type='image/jpeg'
        )
        
        # 3. HTTP POST 요청 (타임아웃 2초)
        async with session.post(
            server_url,
            data=form,
            timeout=2.0
        ) as response:
            pass  # 응답 처리
    
    except:
        pass  # 에러 무시
```

**역할**:
- 이미지 파일 읽기
- HTTP multipart/form-data 포맷 구성
- 원격 서버로 비동기 POST 전송

---

## 3. 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Visual Scripting UI Layer                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────────────┐                                       │
│  │  MultiSenderNode         │  (사용자 제어)                         │
│  │  ├─ Action Combo         │  "Start Sender" / "Stop Sender"      │
│  │  ├─ Server URL Field     │  "http://server:5001/upload"         │
│  │  └─ Flow Ports           │  Flow In/Out                          │
│  └────────────┬─────────────┘                                       │
│               │                                                       │
│               │ sender_command_queue.append(('START'/'STOP', url))  │
│               ▼                                                       │
├─────────────────────────────────────────────────────────────────────┤
│                      Background Thread Layer                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────────────┐                                       │
│  │ sender_manager_thread()  │  (메인 송신 제어 스레드)               │
│  │                          │                                        │
│  │ send_command_queue 처리  │                                        │
│  │  ├─ 'START': 워커 스레드 생성                                   │
│  │  └─ 'STOP': 워커 스레드 정리                                    │
│  └────────────┬─────────────┘                                       │
│               │                                                       │
│      ┌────────┴────────┬──────────────┬──────────────┐              │
│      │                 │              │              │              │
│      ▼                 ▼              ▼              ▼              │
│  ┌─────────┐      ┌─────────┐   ┌─────────┐   ┌─────────┐         │
│  │ AsyncIO │      │ AsyncIO │   │ AsyncIO │   │ AsyncIO │         │
│  │ Worker  │      │ Worker  │   │ Worker  │   │ Worker  │ (카메라 수만큼)
│  │ Thread  │      │ Thread  │   │ Thread  │   │ Thread  │         │
│  │  (cam1) │      │  (cam2) │   │  (cam3) │   │  (cam4) │         │
│  └────┬────┘      └────┬────┘   └────┬────┘   └────┬────┘         │
│       │                │              │              │              │
│       │ camera_async_worker()                       │              │
│       │  - 폴더 모니터링                            │              │
│       │  - 최신 이미지 감지                         │              │
│       │  - send_image_async() 호출                  │              │
│       │                                              │              │
└───────┼──────────────────────────────────────────────┼───────────────┘
        │                                              │
        │ async aiohttp.ClientSession.post()         │
        │                                              │
        ▼                                              ▼
   ┌────────────────────────────────────────────────────────┐
   │         HTTP Multipart/Form-Data Upload               │
   └────────────────────┬─────────────────────────────────┘
                        │
  ┌─────────────────────┼─────────────────────┐
  │                     │                     │
  ▼                     ▼                     ▼
[camera_id]     [filename]              [image data]
"go1_front"   "go1_front_calib.jpg"   (JPEG bytes)
                        │
                        ▼
        ┌──────────────────────────────┐
        │    Remote Server             │
        │  http://server:5001/upload   │
        └──────────────────────────────┘
```

---

## 4. 카메라 노드와의 연결

### CAMERA_CONFIG 설정
**위치**: `Go1_DS.py` 라인 186-189

```python
CAMERA_CONFIG = [
    {"folder": "Go1_Images/test1", "id": "go1_front"}
]
```

### 카메라 노드 체인

#### 구조:
```
Camera Input
    ↓
[Fisheye Undistort] (선택사항)
    ↓
[ArUco Process] (선택사항)
    ↓
[Video Save]  ← 이미지가 CAMERA_CONFIG["folder"]에 저장됨
    ↓
[Server Sender]  ← 저장된 이미지를 서버로 업로드
```

#### 상세 연결:

1. **VideoSourceNode**: 카메라 스트림 수신
   ```python
   # go1.py의 VideoSourceNode
   class VideoSourceNode(BaseNode):
       def execute(self):
           # 카메라에서 프레임 읽기
           frame = self._load_latest_frame()
           self.output_data[self.out_frame] = frame
           return None
   ```

2. **VideoFrameSaveNode**: 프레임 저장
   ```python
   # go1.py의 VideoFrameSaveNode
   class VideoFrameSaveNode(BaseNode):
       def __init__(self, node_id):
           self.state['folder'] = 'Captured_Images/go1_front'  # 또는 CAMERA_CONFIG["folder"]
       
       def execute(self):
           frame = self.fetch_input_data(self.in_frame)
           if frame is not None:
               # 폴더에 이미지 저장
               filename = os.path.join(folder, f"front_{self._frame_index:06d}.jpg")
               cv2.imwrite(filename, frame)
   ```

3. **Server Sender**: 저장된 이미지 업로드
   ```python
   # CAMERA_CONFIG["folder"] 폴더를 모니터링
   # 최신 *.jpg 파일을 감지하면 자동으로 서버로 업로드
   ```

### 중요: 폴더 동기화
Server Sender가 모니터링할 폴더를 설정해야 함:

**방법 1: CAMERA_CONFIG 수정**
```python
CAMERA_CONFIG = [
    {"folder": "Captured_Images/go1_front", "id": "go1_front"}
]
```

**방법 2: VideoFrameSaveNode와 폴더 맞추기**
```
VideoFrameSaveNode의 folder 설정과 
CAMERA_CONFIG의 folder가 동일해야 함
```

---

## 5. 실행 흐름

### 시작 흐름

```
1. 사용자가 Server Sender 노드의 Action을 "Start Sender"로 선택
   ↓
2. MultiSenderNode.execute() 호출
   - sender_state['status'] = 'Starting...'
   - sender_command_queue.append(('START', 'http://server:5001/upload'))
   - Flow 출력
   ↓
3. sender_manager_thread() 감지
   - cmd = 'START' 처리
   - multi_sender_active = True
   - sender_state['status'] = 'Running'
   ↓
4. CAMERA_CONFIG의 각 카메라마다:
   - threading.Thread(target=start_async_loop, args=(config, url))
   - daemon 스레드로 시작
   ↓
5. start_async_loop()로 asyncio 이벤트루프 생성
   - asyncio.run(camera_async_worker(config, url))
   ↓
6. camera_async_worker() 루프 시작:
   while multi_sender_active:
       - 폴더({config["folder"]})에서 *.jpg 파일 찾기
       - 최신 파일 감지
       - send_image_async()로 업로드
       - INTERVAL(0.1초@10FPS) 대기
       - 반복
   ↓
7. send_image_async():
   - 이미지 파일 읽기
   - multipart/form-data 구성
   - aiohttp로 HTTP POST
   - 서버에서 업로드 처리
```

### 중지 흐름

```
1. 사용자가 Server Sender 노드의 Action을 "Stop Sender"로 선택
   ↓
2. MultiSenderNode.execute() 호출
   - sender_state['status'] = 'Stopping...'
   - sender_command_queue.append(('STOP', url))
   ↓
3. sender_manager_thread() 감지
   - cmd = 'STOP' 처리
   - multi_sender_active = False  ← 핵심: 모든 camera_async_worker() 루프 종료
   - sender_state['status'] = 'Stopped'
   - sender_threads.clear()
   ↓
4. 모든 AsyncIO 워커 스레드 종료
```

---

## 6. 사용 예시

### 예시 1: 기본 카메라 송신 파이프라인

```
[Video Source]
    ↓ (Frame 출력)
[Video Save]  ← folder="Go1_Images/test1" 저장
    ↓
[Server Sender] ← 같은 폴더에서 이미지 감지 & 업로드
    ↓ (Flow)
[결과 처리]

CAMERA_CONFIG 설정:
{
    "folder": "Go1_Images/test1",
    "id": "go1_front"
}

Server Sender 설정:
- Action: "Start Sender"
- URL: "http://192.168.1.100:5001/upload"
```

### 예시 2: 마이크로처리 후 송신

```
[Video Source]
    ↓
[Fisheye Undistort]
    ↓
[ArUco Detect]
    ↓
[Video Save] ← 처리된 이미지 저장
    ↓
[Server Sender] ← 자동 감지 & 업로드
```

### 예시 3: 조건부 송신

```
[Video Source]
    ↓
[Video Save]
    ↓
[조건 검사] (예: 프레임 개수 확인)
    ↓ YES
[Server Sender] ← Start Sender
    ↓
[타이머] (예: 10초 후)
    ↓ 종료
[Server Sender] ← Stop Sender
```

---

## 추가 정보

### 성능 설정

| 파라미터 | 값 | 설명 |
|---------|-----|------|
| TARGET_FPS | 10 | 초당 업로드 시도 횟수 |
| INTERVAL | 0.1s | 프레임 간 대기 시간 (1/TARGET_FPS) |
| timeout | 2.0s | HTTP 요청 타임아웃 |

### 에러 처리

```python
# send_image_async()는 모든 예외를 무시 (pass)
# - 네트워크 오류
# - 파일 읽기 오류
# - HTTP 오류

# 서버 응답도 확인하지 않음
async with session.post(...) as response:
    pass  # 응답 상태 무시
```

### 로그 출력

```
[HH:MM:SS] Sender: Connect to http://server:5001/upload
[HH:MM:SS] Sender: Disconnected
```

---

## 개선 사항 (Optional)

### 제안 1: 오류 로깅 추가
```python
async def send_image_async(session, filepath, camera_id, server_url):
    try:
        # ... 기존 코드 ...
        async with session.post(...) as response:
            if response.status != 200:
                write_log(f"[Sender] Upload failed: {response.status}")
    except asyncio.TimeoutError:
        write_log(f"[Sender] Timeout uploading {camera_id}")
    except Exception as e:
        write_log(f"[Sender] Error: {e}")
```

### 제안 2: 카메라별 상태 트래킹
```python
camera_upload_stats = {
    "go1_front": {"uploaded": 0, "failed": 0, "last_upload": 0}
}
```

### 제안 3: 사용자 피드백 UI
- 업로드 통계 표시 (업로드 수, 실패 수, 최근 업로드 시간)
- 현재 네트워크 상태 표시
- 서버 연결 상태 표시
