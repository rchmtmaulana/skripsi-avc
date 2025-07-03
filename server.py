import cv2
import base64
import os # <-- Impor library 'os'
from flask import Flask
from flask_socketio import SocketIO, emit
from ultralytics import YOLO

# ===================================================================
# MEMAKSA OPENCV MENGGUNAKAN TRANSPORT TCP UNTUK RTSP
# Ini adalah perbaikan untuk skenario "Bisa di VLC, Gagal di OpenCV"
os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
# ===================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!key' 
socketio = SocketIO(app, cors_allowed_origins="*")

# Muat Model
try:
    model_overhead = YOLO('models/overhead.pt')
    model_frontal = YOLO('models/frontal.pt')
    print("Model 'overhead.pt' dan 'frontal.pt' berhasil dimuat.")
except Exception as e:
    print(f"Gagal memuat model: {e}")
    exit()

# URL RTSP
RTSP_URL_OVERHEAD = "rtsp://root:w4h!D.xXx@157.119.222.50:5540/media2/stream.sdp?profile=Profile200"
RTSP_URL_FRONTAL = "rtsp://root:w4h!D.xXx@157.119.222.50:5541/media2/stream.sdp?profile=Profile200"

def generate_overhead_stream():
    """Proses dan stream untuk kamera overhead dari stream RTSP."""
    # Timeout ditambahkan untuk koneksi awal yang lebih sabar (dalam milidetik)
    video_capture = cv2.VideoCapture(RTSP_URL_OVERHEAD, cv2.CAP_FFMPEG) 
    print(f"Mencoba terhubung ke stream overhead: {RTSP_URL_OVERHEAD}")
    while True:
        success, frame = video_capture.read()
        if not success:
            print("Gagal membaca frame dari stream overhead. Mencoba menghubungkan kembali...")
            socketio.sleep(2)
            video_capture.release()
            video_capture = cv2.VideoCapture(RTSP_URL_OVERHEAD, cv2.CAP_FFMPEG)
            continue
        
        results = model_overhead(frame, stream=False, verbose=False)
        rendered_frame = results[0].plot()
        
        ret, buffer = cv2.imencode('.jpg', rendered_frame)
        if not ret: continue
            
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        socketio.emit('overhead_stream', {'image_data': frame_base64})
        socketio.sleep(0.05)

def generate_frontal_stream():
    """Proses dan stream untuk kamera frontal dari stream RTSP."""
    video_capture = cv2.VideoCapture(RTSP_URL_FRONTAL, cv2.CAP_FFMPEG)
    print(f"Mencoba terhubung ke stream frontal: {RTSP_URL_FRONTAL}")
    while True:
        success, frame = video_capture.read()
        if not success:
            print("Gagal membaca frame dari stream frontal. Mencoba menghubungkan kembali...")
            socketio.sleep(2)
            video_capture.release()
            video_capture = cv2.VideoCapture(RTSP_URL_FRONTAL, cv2.CAP_FFMPEG)
            continue
            
        results = model_frontal(frame, stream=False, verbose=False)
        rendered_frame = results[0].plot()

        ret, buffer = cv2.imencode('.jpg', rendered_frame)
        if not ret: continue
            
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        socketio.emit('frontal_stream', {'image_data': frame_base64})
        socketio.sleep(0.05)

@socketio.on('connect')
def handle_connect():
    print('Client terhubung! Memulai semua stream video.')
    socketio.start_background_task(target=generate_overhead_stream)
    socketio.start_background_task(target=generate_frontal_stream)

if __name__ == '__main__':
    print("Menjalankan server di http://127.0.0.1:5000")
    socketio.run(app, debug=True, host='127.0.0.1', port=5000, allow_unsafe_werkzeug=True)