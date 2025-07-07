import cv2, base64, os, threading, time
import torch
import numpy as np
from collections import deque
from flask import Flask
from flask_socketio import SocketIO, emit
from ultralytics import YOLO

os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!key' 
socketio = SocketIO(app, cors_allowed_origins="*")

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"==========================================")
print(f"Menggunakan device: {device}")
print(f"==========================================")

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

class LineCrossingDetector:
    def __init__(self, line_position_y=300, frame_width=640, frame_height=480):
        # Posisi garis horizontal (Y coordinate)
        self.line_y = line_position_y
        self.frame_width = frame_width
        self.frame_height = frame_height
        
        # Tracking data untuk setiap axle yang terdeteksi
        self.tracked_axles = {}
        self.axle_id_counter = 0
        self.crossed_axles = []
        
        # Buffer untuk menyimpan posisi axle dalam beberapa frame
        self.history_frames = 5
        self.crossing_threshold = 10  # Minimum pixel movement untuk dianggap crossing
        
        # Reset timer
        self.last_vehicle_time = time.time()
        self.vehicle_timeout = 5  # Reset jika tidak ada deteksi selama 5 detik
        
    def get_axle_center(self, box):
        """Mendapatkan titik tengah axle dari bounding box"""
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        return center_x, center_y
    
    def update_axle_tracking(self, detections):
        """Update tracking untuk setiap axle yang terdeteksi"""
        current_time = time.time()
        
        # Reset jika sudah lama tidak ada deteksi
        if current_time - self.last_vehicle_time > self.vehicle_timeout:
            self.reset_tracking()
        
        self.last_vehicle_time = current_time
        
        # List untuk menyimpan axle yang terdeteksi di frame ini
        current_axles = []
        
        # Proses setiap deteksi axle
        for detection in detections:
            center_x, center_y = self.get_axle_center(detection)
            current_axles.append((center_x, center_y))
            
            # Cari axle yang paling dekat dari tracking yang sudah ada
            matched_id = self.find_closest_axle(center_x, center_y)
            
            if matched_id is not None:
                # Update posisi axle yang sudah ada
                self.tracked_axles[matched_id]['positions'].append((center_x, center_y))
                self.tracked_axles[matched_id]['last_seen'] = current_time
                
                # Batas history positions
                if len(self.tracked_axles[matched_id]['positions']) > self.history_frames:
                    self.tracked_axles[matched_id]['positions'].pop(0)
                    
                # Cek apakah axle ini melintasi garis
                self.check_line_crossing(matched_id)
            else:
                # Axle baru terdeteksi
                self.axle_id_counter += 1
                self.tracked_axles[self.axle_id_counter] = {
                    'positions': [(center_x, center_y)],
                    'crossed': False,
                    'last_seen': current_time,
                    'direction': None  # 'up_to_down' atau 'down_to_up'
                }
        
        # Bersihkan axle yang sudah lama tidak terdeteksi
        self.cleanup_old_axles(current_time)
        
        return len(current_axles)
    
    def find_closest_axle(self, center_x, center_y, max_distance=50):
        """Mencari axle yang paling dekat untuk tracking"""
        min_distance = float('inf')
        closest_id = None
        
        for axle_id, axle_data in self.tracked_axles.items():
            if axle_data['positions']:
                last_x, last_y = axle_data['positions'][-1]
                distance = np.sqrt((center_x - last_x)**2 + (center_y - last_y)**2)
                
                if distance < min_distance and distance < max_distance:
                    min_distance = distance
                    closest_id = axle_id
        
        return closest_id
    
    def check_line_crossing(self, axle_id):
        """Cek apakah axle melintasi garis"""
        positions = self.tracked_axles[axle_id]['positions']
        
        if len(positions) < 2 or self.tracked_axles[axle_id]['crossed']:
            return
        
        # Ambil posisi pertama dan terakhir
        first_y = positions[0][1]
        last_y = positions[-1][1]
        
        # Cek apakah melintasi garis horizontal
        line_crossed = False
        direction = None
        
        if first_y < self.line_y and last_y > self.line_y:
            # Bergerak dari atas ke bawah
            line_crossed = True
            direction = 'up_to_down'
        elif first_y > self.line_y and last_y < self.line_y:
            # Bergerak dari bawah ke atas
            line_crossed = True
            direction = 'down_to_up'
        
        if line_crossed:
            self.tracked_axles[axle_id]['crossed'] = True
            self.tracked_axles[axle_id]['direction'] = direction
            self.crossed_axles.append({
                'id': axle_id,
                'direction': direction,
                'time': time.time()
            })
            print(f"Axle {axle_id} melintasi garis! Direction: {direction}")
    
    def cleanup_old_axles(self, current_time, timeout=2):
        """Bersihkan axle yang sudah lama tidak terdeteksi"""
        to_remove = []
        for axle_id, axle_data in self.tracked_axles.items():
            if current_time - axle_data['last_seen'] > timeout:
                to_remove.append(axle_id)
        
        for axle_id in to_remove:
            del self.tracked_axles[axle_id]
    
    def get_crossed_axles_count(self):
        """Mendapatkan jumlah axle yang sudah melintasi garis"""
        return len(self.crossed_axles)
    
    def reset_tracking(self):
        """Reset semua tracking data"""
        self.tracked_axles = {}
        self.crossed_axles = []
        self.axle_id_counter = 0
        print("Line crossing detection direset")
    
    def draw_line_and_info(self, frame):
        """Menggambar garis deteksi dan informasi pada frame"""
        # Gambar garis horizontal
        cv2.line(frame, (0, self.line_y), (self.frame_width, self.line_y), (0, 255, 0), 3)
        
        # # Tambahkan label garis
        # cv2.putText(frame, 'DETECTION LINE', (10, self.line_y - 10), 
        #            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        # Gambar tracking points untuk setiap axle
        for axle_id, axle_data in self.tracked_axles.items():
            positions = axle_data['positions']
            if positions:
                # Gambar jalur pergerakan
                if len(positions) > 1:
                    for i in range(1, len(positions)):
                        cv2.line(frame, 
                               (int(positions[i-1][0]), int(positions[i-1][1])),
                               (int(positions[i][0]), int(positions[i][1])),
                               (255, 0, 0), 2)
                
                # Gambar titik terakhir
                last_x, last_y = positions[-1]
                color = (0, 255, 255) if axle_data['crossed'] else (255, 255, 0)
                cv2.circle(frame, (int(last_x), int(last_y)), 8, color, -1)
                
                # Label ID axle
                cv2.putText(frame, f'ID:{axle_id}', 
                           (int(last_x) + 10, int(last_y) - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        # Tampilkan informasi crossing
        crossed_count = self.get_crossed_axles_count()
        cv2.putText(frame, f'Crossed Axles: {crossed_count}', 
                   (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        
        return frame

class VehicleClassifier:
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.axle_count = 0
        self.tire_config = None
        self.classification = "--"
        self.vehicle_id = "---"
        self.detection_time = "--:--:--"
        self.is_classified = False  # Flag untuk menandai apakah sudah diklasifikasi
        self.last_classification_time = 0  # Waktu terakhir klasifikasi
        self.classification_timeout = 10  # Timeout dalam detik untuk klasifikasi yang sama
    
    def update_axle_count(self, axles):
        """Update jumlah gandar dan klasifikasi jika belum pernah diklasifikasi"""
        # Update axle count
        self.axle_count = axles
        
        # Hanya klasifikasi jika belum pernah diklasifikasi dan ada gandar yang terdeteksi
        if not self.is_classified and axles > 0:
            if axles >= 3:
                # Langsung klasifikasi berdasarkan jumlah gandar
                if axles == 3:
                    self.classification = "Golongan 3"
                elif axles == 4:
                    self.classification = "Golongan 4"
                elif axles >= 5:
                    self.classification = "Golongan 5"
                self.finalize_classification()
            elif axles <= 2 and self.tire_config is not None:
                # Klasifikasi berdasarkan konfigurasi ban untuk 1-2 gandar
                if self.tire_config == "single_tire":
                    self.classification = "Golongan 1"
                elif self.tire_config == "double_tire":
                    self.classification = "Golongan 2"
                self.finalize_classification()
    
    def update_tire_config(self, tire_type):
        """Update konfigurasi ban dan klasifikasi jika belum pernah diklasifikasi"""        
        # Update tire config
        self.tire_config = tire_type
        
        # Hanya klasifikasi jika belum pernah diklasifikasi dan gandar <= 2
        if not self.is_classified and self.axle_count <= 2 and tire_type is not None:
            if tire_type == "single_tire":
                self.classification = "Golongan 1"
            elif tire_type == "double_tire":
                self.classification = "Golongan 2"
            self.finalize_classification()
    
    def finalize_classification(self):
        """Finalisasi klasifikasi dengan membuat ID kendaraan jika belum ada"""
        if self.classification != "--" and not self.is_classified:
            # Buat ID kendaraan baru hanya jika belum pernah diklasifikasi
            if self.vehicle_id == "---":
                self.vehicle_id = f"V{int(time.time()) % 10000:04d}"
                self.detection_time = time.strftime("%H:%M:%S")
            
            # Tandai sebagai sudah diklasifikasi
            self.is_classified = True
            self.last_classification_time = time.time()
            
            print(f"Kendaraan terklasifikasi: {self.classification} (ID: {self.vehicle_id})")
    
    def force_reset(self):
        """Reset paksa untuk kendaraan baru (dipanggil manual)"""
        print(f"Manual reset: Kendaraan {self.vehicle_id} selesai diproses")
        self.reset()

# Instance global
line_detector = LineCrossingDetector(line_position_y=200, frame_width=640, frame_height=480)
classifier = VehicleClassifier()

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

def detect_axles_from_results(results):
    """Mengekstrak deteksi axle dari hasil YOLO"""
    axle_detections = []
    if results and len(results) > 0:
        for result in results:
            if result.boxes is not None:
                for box in result.boxes:
                    # Asumsi class 0 adalah 'axle' atau 'gandar'
                    if box.cls == 0:
                        axle_detections.append(box)
    return axle_detections

def detect_tire_config_from_detections(results):
    """Mendeteksi konfigurasi ban dari hasil deteksi YOLO"""
    if not results or len(results) == 0:
        return None
    
    tire_config = None
    for result in results:
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls)
                # Perbaikan: Sesuaikan dengan data.yaml
                # class_id 2 = double_tire, class_id 3 = single_tire
                if class_id == 3:
                    tire_config = "single_tire"
                elif class_id == 2:
                    tire_config = "double_tire"
    
    return tire_config

def generate_overhead_stream():
    vs = VideoStream(src=RTSP_URL_OVERHEAD).start()
    print(f"Stream overhead dengan line crossing detection dimulai...")
    
    frame_count = 0 
    while True:
        frame = vs.read()
        if frame is None:
            continue
            
        frame_count += 1
        if frame_count % 3 != 0:
            continue

        try:
            small_frame = cv2.resize(frame, (640, 480))
        except cv2.error:
            continue 

        current_time = time.time()
        if classifier.is_classified and (current_time - classifier.last_classification_time > classifier.classification_timeout):
            print("Reset otomatis karena timeout...")
            classifier.reset()

        # Deteksi menggunakan YOLO
        results = model_overhead(small_frame, stream=False, verbose=False)
        
        # Ekstrak deteksi axle
        axle_detections = detect_axles_from_results(results)
        
        # Update line crossing detection
        detected_axles_count = line_detector.update_axle_tracking(axle_detections)
        crossed_axles_count = line_detector.get_crossed_axles_count()
        
        # Update classifier dengan jumlah axle yang sudah melintasi
        classifier.update_axle_count(crossed_axles_count)
        
        # Render hasil deteksi YOLO
        rendered_frame = results[0].plot()
        
        # Tambahkan garis deteksi dan informasi
        rendered_frame = line_detector.draw_line_and_info(rendered_frame)

        ret, buffer = cv2.imencode('.jpg', rendered_frame)
        if not ret: continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        
        # Emit data dengan informasi klasifikasi
        socketio.emit('overhead_stream', {
            'image_data': frame_base64,
            'axle_count': crossed_axles_count,
            'detected_axles': detected_axles_count,
            'vehicle_id': classifier.vehicle_id,
            'classification': classifier.classification,
            'detection_time': classifier.detection_time,
            'is_classified': classifier.is_classified
        })
        socketio.sleep(0.01)

def generate_frontal_stream():
    vs = VideoStream(src=RTSP_URL_FRONTAL).start()
    print(f"Stream frontal dimulai dengan threading...")

    frame_count = 0
    while True:
        frame = vs.read()
        if frame is None:
            continue
            
        frame_count += 1
        if frame_count % 3 != 0:
            continue

        try:
            small_frame = cv2.resize(frame, (640, 480))
        except cv2.error:
            continue

        results = model_frontal(small_frame, stream=False, verbose=False)
        rendered_frame = results[0].plot()
        
        # Deteksi konfigurasi ban
        tire_config = detect_tire_config_from_detections(results)
        
        # Update classifier dengan konfigurasi ban
        if tire_config:
            classifier.update_tire_config(tire_config)

        ret, buffer = cv2.imencode('.jpg', rendered_frame)
        if not ret: continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        
        # Emit data dengan informasi klasifikasi
        socketio.emit('frontal_stream', {
            'image_data': frame_base64,
            'tire_config': tire_config,
            'vehicle_id': classifier.vehicle_id,
            'classification': classifier.classification,
            'detection_time': classifier.detection_time,
            'is_classified': classifier.is_classified
        })
        socketio.sleep(0.01)

@socketio.on('connect')
def handle_connect():
    print('Client terhubung! Memulai semua stream video.')
    socketio.start_background_task(target=generate_overhead_stream)
    socketio.start_background_task(target=generate_frontal_stream)

@socketio.on('reset_classification')
def handle_reset():
    """Reset klasifikasi untuk kendaraan baru"""
    global classifier, line_detector
    classifier.force_reset()
    line_detector.reset_tracking()
    print("Klasifikasi dan line crossing detection direset untuk kendaraan baru")

if __name__ == '__main__':
    print("Menjalankan server di http://127.0.0.1:5000")
    socketio.run(app, debug=True, host='127.0.0.1', port=5000, allow_unsafe_werkzeug=True)