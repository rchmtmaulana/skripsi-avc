import cv2
import base64
import os
import threading
import time
import json
import torch
import numpy as np
from flask import Flask
from flask_socketio import SocketIO
from ultralytics import YOLO
from threading import Lock

try:
    with open('config.json', 'r') as f:
        config = json.load(f)
    print("✅ Konfigurasi berhasil dimuat dari config.json")
except FileNotFoundError:
    print("❌ ERROR: File 'config.json' tidak ditemukan.")
    exit()
except json.JSONDecodeError:
    print("❌ ERROR: File 'config.json' tidak valid.")
    exit()

os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

# --- Inisialisasi Flask & SocketIO ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret-test-key'
socketio = SocketIO(app, cors_allowed_origins="*")

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"==========================================")
print(f"Menggunakan device: {device}")
print(f"==========================================")

try:
    model_overhead_path = config['model_paths']['overhead']
    model_frontal_path = config['model_paths']['frontal']

    model_overhead = YOLO(model_overhead_path).to(device)
    model_frontal = YOLO(model_frontal_path).to(device)

    model_overhead.fuse()
    model_frontal.fuse()

    print(f"✅ Model '{model_overhead_path}' dan '{model_frontal_path}' berhasil dimuat.")
except Exception as e:
    print(f"❌ Gagal memuat model: {e}")
    exit()

RTSP_URL_OVERHEAD = config['rtsp_urls']['overhead']
RTSP_URL_FRONTAL = config['rtsp_urls']['frontal']

class OptimizedVideoStream:
    """
    Kelas ini bertanggung jawab untuk mengambil frame dari stream RTSP
    di thread terpisah untuk mencegah I/O blocking.
    """
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        self.stream.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        self.grabbed, self.frame = self.stream.read()
        self.stopped = False
        self.lock = Lock()

    def start(self):
        threading.Thread(target=self.update, daemon=True).start()
        return self

    def update(self):
        while not self.stopped:
            grabbed, frame = self.stream.read()
            with self.lock:
                if grabbed:
                    self.frame = frame
            time.sleep(0.01)

    def read(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        self.stopped = True
        self.stream.release()

def generate_stream(camera_name, rtsp_url, model, socket_event_name):
    """Fungsi generik untuk memproses dan mengirim stream video."""
    vs = OptimizedVideoStream(src=rtsp_url).start()
    print(f"✅ Stream '{camera_name}' (mode tes) dimulai...")
    
    target_fps = 30 
    
    while True:
        frame = vs.read()
        if frame is None:
            time.sleep(0.01)
            continue

        try:
            small_frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)
        except cv2.error:
            continue 

        results = list(model(small_frame, stream=True, verbose=False, conf=0.5))

        rendered_frame = results[0].plot() if results and results[0].boxes else small_frame

        ret, buffer = cv2.imencode('.jpg', rendered_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ret:
            continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        
        socketio.emit(socket_event_name, {'image_data': frame_base64})
        
        time.sleep(1.0 / target_fps)


# --- Event Handler SocketIO ---

@socketio.on('connect')
def handle_connect():
    """Dipanggil ketika client terhubung."""
    print('✅ Client terhubung! Memulai semua stream video (mode tes).')
    
    if not hasattr(handle_connect, 'tasks_started'):
        # Memulai thread untuk setiap stream kamera
        socketio.start_background_task(
            target=generate_stream,
            camera_name="Overhead",
            rtsp_url=RTSP_URL_OVERHEAD,
            model=model_overhead,
            socket_event_name='overhead_stream'
        )
        socketio.start_background_task(
            target=generate_stream,
            camera_name="Frontal",
            rtsp_url=RTSP_URL_FRONTAL,
            model=model_frontal,
            socket_event_name='frontal_stream'
        )
        handle_connect.tasks_started = True
        print("✅ Background task untuk kedua stream telah dimulai.")


# --- Menjalankan Server ---
if __name__ == '__main__':
    server_host = config['server']['host']
    server_port = config['server']['port']
    print(f"🚀 Menjalankan server (MODE TES) di http://{server_host}:{server_port}")
    socketio.run(app, debug=False, host=server_host, port=server_port, allow_unsafe_werkzeug=True)