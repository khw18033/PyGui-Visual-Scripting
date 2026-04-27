import time
import asyncio
import redis.asyncio as redis
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager 
import uvicorn

# [수정 1] 더 이상 하드디스크 저장을 하지 않으므로 SAVE_DIR, CLEANUP 등의 설정이 모두 삭제되었습니다.

redis_client = None
STOP_EVENT = asyncio.Event()

# =========================================================
# Lifespan (수명 주기) 관리
# =========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    
    print("✅ Server Starting... (Connecting to Redis)")
    redis_client = redis.Redis(host='localhost', port=7860, db=0, password='robot_redis_1234')
    STOP_EVENT.clear()
    
    yield # 서버 작동 중...
    
    print("🛑 Server Shutting down... (Signaling streams to stop)")
    STOP_EVENT.set()
    if redis_client:
        await redis_client.aclose()

app = FastAPI(lifespan=lifespan)

# =========================================================
# 1. 이미지 업로드 (Producer 역할)
# =========================================================
@app.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    camera_id: str = Form("default")
):
    try:
        if redis_client is None:
            return {"status": "redis_not_ready"}

        image_data = await file.read()
        
        # [핵심 1] Redis Stream 키 이름 설정 (예: camera_stream:go1_front)
        stream_key = f"camera_stream:{camera_id}"
        
        # [핵심 2] XADD: Stream에 데이터를 추가합니다.
        # maxlen=300 옵션: 30프레임 기준 딱 '10초' 분량의 최신 데이터만 남기고 과거 데이터는 Redis가 알아서 삭제합니다! (램 용량 터짐 방지)
        await redis_client.xadd(
            stream_key, 
            {b'image': image_data, b'timestamp': str(time.time()).encode('utf-8')}, 
            maxlen=300, 
            approximate=True
        )

        # 디스크 저장 로직이 완전히 빠졌습니다. 응답 속도가 극한으로 빨라집니다.
        return {"status": "streamed_to_redis"}

    except Exception as e:
        print(f"[Error] Upload failed: {e}")
        return {"status": "error"}
    finally:
        await file.close()

# =========================================================
# 2. MJPEG 스트리밍 (Consumer 역할 1 - 실시간 렌더링)
# =========================================================
async def image_streamer(camera_id):
    stream_key = f"camera_stream:{camera_id}"

    while not STOP_EVENT.is_set():
        try:
            if redis_client is None:
                await asyncio.sleep(0.1)
                continue

            # [핵심 3] XREVRANGE: Stream의 데이터를 역순(최신순)으로 읽어옵니다. count=1을 주면 '가장 최신 사진 딱 1장'만 가져옵니다.
            messages = await redis_client.xrevrange(stream_key, count=1)
            
            if messages:
                # messages 구조 분해: [(message_id, {b'image': 바이너리, b'timestamp': 시간})]
                message_id, data_dict = messages[0]
                image_data = data_dict[b'image']
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + image_data + b'\r\n')
            
            await asyncio.sleep(0.033)
            
        except Exception as e:
            print(f"[Stream Error] {e}")
            break

@app.get("/stream/{camera_id}")
async def stream_video(camera_id: str):
    return StreamingResponse(
        image_streamer(camera_id), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# =========================================================
# [추가] 3. AI 처리된 영상 스트리밍 (Consumer 역할 2)
# =========================================================
async def ai_streamer(camera_id):
    # [핵심] 원본(camera_stream)이 아니라, AI가 처리한 결과(processed_stream)를 바라봅니다.
    # object_detection.py의 OUT_STREAM 변수와 똑같아야 합니다.
    stream_key = f"processed_stream:{camera_id}"

    while not STOP_EVENT.is_set():
        try:
            if redis_client is None:
                await asyncio.sleep(0.1)
                continue

            # Redis Stream에서 가장 최신 AI 결과 1개를 가져옵니다.
            messages = await redis_client.xrevrange(stream_key, count=1)
            
            if messages:
                message_id, data_dict = messages[0]
                
                # object_detection.py가 저장한 키 이름이 b'image' 입니다.
                image_data = data_dict.get(b'image')
                
                if image_data:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + image_data + b'\r\n')
            
            # AI 처리가 무거울 수 있으니 대기 시간은 유연하게 (약 30fps 시도)
            await asyncio.sleep(0.033)
            
        except Exception as e:
            # 아직 AI 코드가 안 켜져 있으면 데이터가 없을 수 있습니다. 에러 무시하고 대기.
            await asyncio.sleep(1)

# [핵심] 주소를 다르게 팝니다. /stream/ai/{카메라ID}
@app.get("/stream/ai/{camera_id}")
async def stream_ai_video(camera_id: str):
    return StreamingResponse(
        ai_streamer(camera_id), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# =========================================================
# 4. Depth 시각화 스트리밍
# =========================================================
async def depth_streamer(camera_id):
    stream_key = f"depth_stream:{camera_id}"

    while not STOP_EVENT.is_set():
        try:
            if redis_client is None:
                await asyncio.sleep(0.1)
                continue

            messages = await redis_client.xrevrange(stream_key, count=1)

            if messages:
                message_id, data_dict = messages[0]
                image_data = data_dict.get(b'image')

                if image_data:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + image_data + b'\r\n')

            await asyncio.sleep(0.033)

        except Exception as e:
            await asyncio.sleep(1)

@app.get("/stream/depth/{camera_id}")
async def stream_depth_video(camera_id: str):
    return StreamingResponse(
        depth_streamer(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

if __name__ == "__main__":
    uvicorn.run("server_stream_depth_test:app", host="0.0.0.0", port=5001, workers=4)