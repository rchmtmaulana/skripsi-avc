import cv2, base64, os, threading
import base64
import os
from flask import Flask
from flask_socketio import SocketIO, emit
import torch
from ultralytics import YOLO

os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!key' 
socketio = SocketIO(app, cors_allowed_origins="*")
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# Muat Model
try:
    model_overhead = YOLO('models/overhead.pt').to(device)
    model_frontal = YOLO('models/frontal.pt').to(device) 
    print("Model 'overhead.pt' dan 'frontal.pt' berhasil dimuat ke device.")
except Exception as e:
    print(f"Gagal memuat model: {e}")
    exit()

# URL RTSP
RTSP_URL_OVERHEAD = "rtsp://root:w4h!D.xXx@157.119.222.50:5540/media2/stream.sdp?profile=Profile200"
RTSP_URL_FRONTAL = "rtsp://root:w4h!D.xXx@157.119.222.50:5541/media2/stream.sdp?profile=Profile200"

class VideoStream:
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        (self.grabbed, self.frame) = self.stream.read()
        self.stopped = False

    def start(self):
        threading.Thread(target=self.update, args=()).start()
        return self

    def update(self):
        while True:
            if self.stopped:
                return
            (self.grabbed, self.frame) = self.stream.read()

    def read(self):
        return self.frame

    def stop(self):
        self.stopped = True

def generate_overhead_stream():
    vs = VideoStream(src=RTSP_URL_OVERHEAD).start()
    print(f"Stream overhead dimulai dengan threading...")
    while True:
        frame = vs.read()
        if frame is None:
            continue

        results = model_overhead(frame, stream=False, verbose=False)
        rendered_frame = results[0].plot()

        ret, buffer = cv2.imencode('.jpg', rendered_frame)
        if not ret: continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        socketio.emit('overhead_stream', {'image_data': frame_base64})
        socketio.sleep(0.05)

def generate_frontal_stream():
    vs = VideoStream(src=RTSP_URL_FRONTAL).start()
    print(f"Stream frontal dimulai dengan threading...")
    while True:
        frame = vs.read()
        if frame is None:
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