import cv2, base64, os, threading, time, json
import torch
import numpy as np
from collections import deque
from flask import Flask
from flask_socketio import SocketIO, emit
from ultralytics import YOLO
import queue
from threading import Lock
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import pytz

try:
    with open('config.json', 'r') as f:
        config = json.load(f)
    print("✅ Konfigurasi berhasil dimuat dari config.json")
except FileNotFoundError:
    print("❌ ERROR: File 'config.json' tidak ditemukan. Pastikan file ada di direktori yang sama.")
    exit()
except json.JSONDecodeError:
    print("❌ ERROR: File 'config.json' tidak valid. Periksa format JSON.")
    exit()

os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!key' 
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
    
    print(f"Model '{model_overhead_path}' dan '{model_frontal_path}' berhasil dimuat ke device.")
except Exception as e:
    print(f"Gagal memuat model: {e}")
    exit()

class FirestoreManager:
    def __init__(self, credentials_path):
        try:
            cred = credentials.Certificate(credentials_path)
            firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            self.indonesia_tz = pytz.timezone('Asia/Makassar')
            print("✅ Firestore berhasil diinisialisasi")
        except Exception as e:
            print(f"❌ Gagal inisialisasi Firestore: {e}")
            self.db = None

    def save_vehicle_transaction(self, vehicle_data, processing_duration, entry_time, exit_time, is_timeout=False):
        if not self.db:
            print("Firestore client not available. Cannot save transaction.")
            return

        try:
            doc_ref = self.db.collection('transactions').document()
            doc_ref.set({
                'vehicle_id': vehicle_data.vehicle_id,
                'classification': vehicle_data.classification,
                'axle_count': vehicle_data.axle_count,
                'tire_config': vehicle_data.tire_config,
                'entry_time': entry_time,
                'exit_time': exit_time,
                'processing_duration_seconds': round(processing_duration, 2) if processing_duration else None,
                'status': 'timeout' if is_timeout else 'completed'
            })
            status_text = "TIMEOUT" if is_timeout else "SELESAI"
            print(f"📝 Transaksi {vehicle_data.vehicle_id} ({status_text}) disimpan ke Firestore")
        except Exception as e:
            print(f"❌ Gagal menyimpan transaksi ke Firestore: {e}")

try:
    firestore_key_path = config['firestore_key_path']
    firestore_manager = FirestoreManager(firestore_key_path)
except Exception as e:
    print(f"❌ Gagal inisialisasi Firestore: {e}")
    firestore_manager = None

RTSP_URL_OVERHEAD = config['rtsp_urls']['overhead']
RTSP_URL_FRONTAL = config['rtsp_urls']['frontal']
TRANSACTION_AREA = config['transaction_area']

class VehicleData:
    def __init__(self, vehicle_id):
        self.vehicle_id = vehicle_id
        self.axle_count = 0
        self.tire_config = None
        self.classification = "--"
        self.detection_time = time.strftime("%H:%M:%S")
        self.is_classified = False
        self.created_time = time.time()
        self.last_seen_frontal = None
        self.status = "detected" # Status awal
        self.config_locked = False
        self.has_entered_transaction_zone = False
        self.transaction_start_time = None
        self.max_transaction_time = config['vehicle_queue']['max_transaction_time']
        self.timeout_extended = False
        self.processing_attempts = 0

class VehicleQueue:
    def __init__(self):
        self.vehicles = {}
        self.vehicle_counter = 0
        self.current_processing_vehicle = None
        self.processing_start_time = None
        self.LEARNING_WINDOW_SECONDS = config['vehicle_queue']['learning_window_seconds']
        self.lock = Lock()
        self.timeout_vehicles = set()
        self.indonesia_tz = pytz.timezone('Asia/Makassar')

    def finalize_vehicle_from_overhead(self, vehicle_id):
        with self.lock:
            if vehicle_id in self.vehicles:
                if self.vehicles[vehicle_id].axle_count == 0:
                    print(f"GHOST DETECTED: {vehicle_id} memiliki 0 gandar. ID akan di-reuse.")
                    del self.vehicles[vehicle_id]
                    self.vehicle_counter -= 1
                    print(f"Counter direset ke: {self.vehicle_counter}. ID berikutnya akan menjadi V{(self.vehicle_counter + 1):04d}.")
                    return

                if self.vehicles[vehicle_id].status == "detected":
                    self.vehicles[vehicle_id].status = "counted_and_waiting"
                    print(f"ANTREAN: {vehicle_id} (gandar: {self.vehicles[vehicle_id].axle_count}) masuk antrean.")
        
    def create_new_vehicle(self):
        with self.lock:
            self.vehicle_counter += 1
            vehicle_id = f"V{self.vehicle_counter:04d}"
            self.vehicles[vehicle_id] = VehicleData(vehicle_id)
            print(f"Kendaraan baru dibuat dengan ID: {vehicle_id}")
            return vehicle_id
    
    def get_vehicle(self, vehicle_id):
        with self.lock:
            return self.vehicles.get(vehicle_id)
    
    def update_vehicle_axle_count(self, vehicle_id, axle_count):
        with self.lock:
            if vehicle_id in self.vehicles:
                vehicle = self.vehicles[vehicle_id]
                if vehicle.axle_count != axle_count:
                    vehicle.axle_count = axle_count
                    print(f"Update axle count untuk {vehicle_id}: {axle_count}")
                self.classify_vehicle(vehicle_id)
    
    def update_vehicle_tire_config(self, vehicle_id, new_tire_config):
        with self.lock:
            if vehicle_id in self.vehicles:
                vehicle = self.vehicles[vehicle_id]
                
                if vehicle.config_locked:
                    return

                if self.processing_start_time and (time.time() - self.processing_start_time > self.LEARNING_WINDOW_SECONDS):
                    print(f"--- Jendela pembelajaran untuk {vehicle_id} selesai. Konfigurasi '{vehicle.tire_config}' dikunci. ---")
                    vehicle.config_locked = True
                    return

                if new_tire_config and new_tire_config != vehicle.tire_config:
                    print(f"KOREKSI Konfigurasi Ban untuk {vehicle_id}: dari '{vehicle.tire_config}' menjadi '{new_tire_config}'")
                    vehicle.tire_config = new_tire_config
                    vehicle.is_classified = False
                    self.classify_vehicle(vehicle_id)
    
    def set_current_processing_vehicle(self, vehicle_id):
        with self.lock:
            if vehicle_id in self.vehicles:
                vehicle = self.vehicles[vehicle_id]
                
                if vehicle.axle_count == 1:
                    print(f"KOREKSI OTOMATIS: Gandar untuk {vehicle_id} hanya 1, diubah menjadi 2.")
                    vehicle.axle_count = 2
                    self.classify_vehicle(vehicle_id)
                
                self.current_processing_vehicle = vehicle_id
                self.processing_start_time = time.time()
                
                vehicle.status = "in_transaction"
                vehicle.transaction_start_time = self.processing_start_time
                vehicle.has_entered_transaction_zone = True
                
                print(f"Kendaraan {vehicle_id} diambil alih oleh frontal dan berstatus 'in_transaction'.")
                
                if vehicle.is_classified:
                    analysis_data = {
                        'vehicle_id': vehicle.vehicle_id,
                        'classification': vehicle.classification,
                        'axle_count': vehicle.axle_count,
                        'detection_time': datetime.now(self.indonesia_tz).strftime("%H:%M:%S")
                    }
                    socketio.emit('update_analysis_panel', analysis_data)

    def complete_current_vehicle(self):
        if self.current_processing_vehicle:
            vehicle_id_completed = self.current_processing_vehicle
            vehicle_data = self.vehicles.get(vehicle_id_completed)
            
            if not vehicle_data:
                self.current_processing_vehicle = None
                self.processing_start_time = None
                return False

            is_timeout = vehicle_id_completed in self.timeout_vehicles
            if (not is_timeout and vehicle_data.transaction_start_time and 
                time.time() - vehicle_data.transaction_start_time > vehicle_data.max_transaction_time):
                is_timeout = True
                self.timeout_vehicles.add(vehicle_id_completed)
                print(f"⚠️ {vehicle_id_completed} ditandai sebagai TIMEOUT saat penyelesaian")
            
            processing_duration = time.time() - self.processing_start_time if self.processing_start_time else None
            
            if firestore_manager:
                entry_time_aware = datetime.fromtimestamp(vehicle_data.transaction_start_time, tz=self.indonesia_tz) if vehicle_data.transaction_start_time else None
                exit_time_aware = datetime.now(self.indonesia_tz)
                firestore_manager.save_vehicle_transaction(
                    vehicle_data=vehicle_data,
                    processing_duration=processing_duration,
                    entry_time=entry_time_aware,
                    exit_time=exit_time_aware,
                    is_timeout=is_timeout
                )

            vehicle_data.status = "completed"
            print(f"✅ Transaksi {vehicle_id_completed} SELESAI")
            
            line_detector.finalize_vehicle(vehicle_id_completed)
            self.current_processing_vehicle = None
            self.processing_start_time = None
            socketio.emit('clear_analysis_panel')
            return True
        return False

    def classify_vehicle(self, vehicle_id):
        if vehicle_id not in self.vehicles: 
            return
        vehicle = self.vehicles[vehicle_id]
        
        classification_made = False
        if vehicle.axle_count >= 3:
            if vehicle.axle_count == 3: vehicle.classification = "Golongan 3"
            elif vehicle.axle_count == 4: vehicle.classification = "Golongan 4"
            elif vehicle.axle_count >= 5: vehicle.classification = "Golongan 5"
            classification_made = True
        elif vehicle.axle_count == 2 and vehicle.tire_config:
            if vehicle.tire_config == "single_tire": vehicle.classification = "Golongan 1"
            elif vehicle.tire_config == "double_tire": vehicle.classification = "Golongan 2"
            classification_made = True
        
        if classification_made:
            vehicle.is_classified = True
            print(f"Kendaraan {vehicle_id} TERKLASIFIKASI: {vehicle.classification}")
            
            if self.current_processing_vehicle == vehicle_id:
                analysis_data = {
                    'vehicle_id': vehicle.vehicle_id,
                    'classification': vehicle.classification,
                    'axle_count': vehicle.axle_count,
                    'detection_time': datetime.now(self.indonesia_tz).strftime("%H:%M:%S")
                }
                socketio.emit('update_analysis_panel', analysis_data)
    
    def get_current_vehicle_data(self):
        with self.lock:
            if self.current_processing_vehicle:
                return self.vehicles.get(self.current_processing_vehicle)
            return None
    
    def cleanup_old_vehicles(self):
        with self.lock:
            current_time = time.time()
            to_remove = [
                vid for vid, vdata in self.vehicles.items() 
                if (vdata.status == "completed" and current_time - vdata.created_time > 60) or \
                   (vdata.status == "detected" and vdata.axle_count == 0 and current_time - vdata.created_time > 20)
            ]
            for vehicle_id in to_remove:
                if vehicle_id in self.vehicles:
                    del self.vehicles[vehicle_id]
                    print(f"Kendaraan {vehicle_id} dihapus dari memori")

class LineCrossingDetector:
    def __init__(self, frame_width=640, frame_height=480):
        self.frame_width = frame_width
        self.frame_height = frame_height
        coords = config['line_crossing_detector']['line_coords']
        self.line_x1, self.line_y1, self.line_x2, self.line_y2 = coords[0], coords[1], coords[2], coords[3]
        self.tracked_axles = {}
        self.axle_id_counter = 0
        self.current_vehicle_axles = {}
        self.current_vehicle_id = None
        self.history_frames = 5
        self.last_vehicle_time = time.time()
        self.vehicle_timeout = 1.0
        self.lock = Lock()
        self.vehicle_body_touching_line = False
        self.last_body_detection_time = time.time()
        self.body_timeout = config['line_crossing_detector']['body_timeout']

    def point_to_line_distance(self, px, py):
        x1, y1, x2, y2 = self.line_x1, self.line_y1, self.line_x2, self.line_y2
        a, b, c = y2 - y1, x1 - x2, x2 * y1 - x1 * y2
        return abs(a * px + b * py + c) / np.sqrt(a * a + b * b)

    def is_point_crossing_line(self, px1, py1, px2, py2):
        x1, y1, x2, y2 = self.line_x1, self.line_y1, self.line_x2, self.line_y2
        side = lambda px, py: (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        return (side(px1, py1) > 0) != (side(px2, py2) > 0)

    def is_box_touching_line(self, box, tolerance=15):
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        corners = [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
        for corner_x, corner_y in corners:
            if self.point_to_line_distance(corner_x, corner_y) <= tolerance:
                return True
        line_x_min, line_x_max = min(self.line_x1, self.line_x2), max(self.line_x1, self.line_x2)
        line_y_min, line_y_max = min(self.line_y1, self.line_y2), max(self.line_y1, self.line_y2)
        if x1 <= line_x_max and x2 >= line_x_min and y1 <= line_y_max and y2 >= line_y_min:
            return True
        return False

    def finalize_vehicle(self, vehicle_id):
        with self.lock:
            if self.current_vehicle_id == vehicle_id:
                print(f"--- Kendaraan {vehicle_id} difinalisasi oleh sistem. Siap untuk ID baru. ---")
                self.current_vehicle_id = None
                self.reset_tracking_system()

    def reset_tracking_system(self):
        print("🔄 RESET SISTEM TRACKING - Siap untuk kendaraan baru")
        self.tracked_axles.clear()
        self.current_vehicle_axles.clear()
        self.vehicle_body_touching_line = False
        self.last_body_detection_time = time.time()

    def get_axle_center(self, box):
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        return (x1 + x2) / 2, (y1 + y2) / 2

    def detect_vehicle_bodies_and_axles(self, results):
        vehicle_bodies, axles = [], []
        if not results or not results[0].boxes: return vehicle_bodies, axles
        for box in results[0].boxes:
            class_id = int(box.cls)
            if class_id in [1, 2, 3]: vehicle_bodies.append(box)
            elif class_id == 0: axles.append(box)
        return vehicle_bodies, axles

    def update_vehicle_body_status(self, vehicle_bodies):
        current_time = time.time()
        body_touching_now = any(self.is_box_touching_line(body_box) for body_box in vehicle_bodies)
        
        if body_touching_now:
            if not self.vehicle_body_touching_line:
                print("🚗 BODY KENDARAAN MULAI MENYENTUH GARIS - Sistem aktif")
            self.vehicle_body_touching_line = True
            self.last_body_detection_time = current_time
        elif self.vehicle_body_touching_line and (current_time - self.last_body_detection_time > self.body_timeout):
            print("🚗 BODY KENDARAAN SUDAH TIDAK MENYENTUH GARIS - Sistem akan reset")
            self.vehicle_body_touching_line = False
            return True
        return False

    def update_axle_tracking(self, results, vehicle_queue):
        with self.lock:
            current_time = time.time()
            vehicle_bodies, axle_detections = self.detect_vehicle_bodies_and_axles(results)
            should_reset = self.update_vehicle_body_status(vehicle_bodies)
            
            if should_reset and self.current_vehicle_id:
                print(f"🔄 AUTO RESET: Kendaraan {self.current_vehicle_id} selesai (body tidak menyentuh garis)")
                vehicle_queue.finalize_vehicle_from_overhead(self.current_vehicle_id)
                self.current_vehicle_id = None
                self.reset_tracking_system()
                return
            
            if not self.vehicle_body_touching_line: return
            if axle_detections: self.last_vehicle_time = current_time
            
            if self.current_vehicle_id and (current_time - self.last_vehicle_time > self.vehicle_timeout):
                print(f"--- TIMEOUT AXLE: {self.current_vehicle_id}. Diserahkan ke antrean. ---")
                vehicle_queue.finalize_vehicle_from_overhead(self.current_vehicle_id)
                self.current_vehicle_id = None
                self.reset_tracking_system()
                return

            for detection in axle_detections:
                center_x, center_y = self.get_axle_center(detection)
                matched_id = self.find_closest_axle(center_x, center_y)
                
                if matched_id is not None:
                    self.check_line_crossing(matched_id, center_x, center_y, vehicle_queue)
                    self.tracked_axles[matched_id]['positions'].append((center_x, center_y))
                    self.tracked_axles[matched_id]['last_seen'] = current_time
                    if len(self.tracked_axles[matched_id]['positions']) > self.history_frames:
                        self.tracked_axles[matched_id]['positions'].pop(0)
                else:
                    if self.current_vehicle_id is None: self.start_new_vehicle(vehicle_queue)
                    if self.current_vehicle_id:
                        self.axle_id_counter += 1
                        new_axle_id = self.axle_id_counter
                        self.tracked_axles[new_axle_id] = {'positions': [(center_x, center_y)], 'crossed': False, 'last_seen': current_time, 'vehicle_id': self.current_vehicle_id}
                        self.current_vehicle_axles[self.current_vehicle_id].append(new_axle_id)
                        self.check_line_crossing(new_axle_id, center_x, center_y, vehicle_queue)
            
            self.cleanup_old_axles(current_time)

    def start_new_vehicle(self, vehicle_queue):
        self.current_vehicle_id = vehicle_queue.create_new_vehicle()
        self.current_vehicle_axles[self.current_vehicle_id] = []
        print(f"--- Memulai tracking untuk kendaraan baru: {self.current_vehicle_id} ---")

    def find_closest_axle(self, center_x, center_y, max_distance=80):
        min_dist = float('inf')
        closest_id = None
        for axle_id, data in self.tracked_axles.items():
            if data['positions']:
                dist = np.linalg.norm(np.array(data['positions'][-1]) - np.array((center_x, center_y)))
                if dist < min_dist and dist < max_distance:
                    min_dist, closest_id = dist, axle_id
        return closest_id

    def check_line_crossing(self, axle_id, new_x, new_y, vehicle_queue):
        axle_data = self.tracked_axles.get(axle_id)
        if not axle_data or axle_data.get('crossed', False) or len(axle_data['positions']) < 2: return
        
        prev_x, prev_y = axle_data['positions'][-2]
        if self.is_point_crossing_line(prev_x, prev_y, new_x, new_y):
            axle_data['crossed'] = True
            vehicle_id = axle_data['vehicle_id']
            print(f"✅ Axle {axle_id} (Kendaraan {vehicle_id}) MELINTASI GARIS DIAGONAL!")
            if vehicle_id:
                count = self.get_crossed_axles_count_for_vehicle(vehicle_id)
                vehicle_queue.update_vehicle_axle_count(vehicle_id, count)

    def get_crossed_axles_count_for_vehicle(self, vehicle_id):
        if vehicle_id not in self.current_vehicle_axles: return 0
        return sum(1 for axle_id in self.current_vehicle_axles[vehicle_id] 
                   if self.tracked_axles.get(axle_id, {}).get('crossed', False))

    def cleanup_old_axles(self, current_time, timeout=5):
        to_remove = [aid for aid, data in self.tracked_axles.items() if current_time - data['last_seen'] > timeout]
        for aid in to_remove:
            if aid in self.tracked_axles: del self.tracked_axles[aid]
    
    def draw_line_and_info(self, frame):
        with self.lock:
            line_color = (0, 255, 0) if self.vehicle_body_touching_line else (0, 0, 255)
            line_thickness = 4 if self.vehicle_body_touching_line else 3
            cv2.line(frame, (self.line_x1, self.line_y1), (self.line_x2, self.line_y2), line_color, line_thickness)
            cv2.circle(frame, (self.line_x1, self.line_y1), 5, line_color, -1)
            cv2.circle(frame, (self.line_x2, self.line_y2), 5, line_color, -1)
            status_text = "AKTIF" if self.vehicle_body_touching_line else "STANDBY"
            cv2.putText(frame, f'Status: {status_text}', (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, line_color, 2)
            if self.current_vehicle_id:
                cv2.putText(frame, f'Current Overhead Vehicle: {self.current_vehicle_id}', (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            return frame

class FrontalVehicleManager:
    def __init__(self, vehicle_queue, transaction_area):
        self.vehicle_queue = vehicle_queue
        self.transaction_area = transaction_area
        self.lock = Lock()
        self.zone_occupied = False
        self.zone_clear_confirmation_time = None
        self.zone_clear_delay = 0.5

    def is_box_in_area(self, box, area):
        x1, y1, x2, y2 = box
        return not (x2 < area['x1'] or x1 > area['x2'] or 
                    y2 < area['y1'] or y1 > area['y2'])

    def get_next_vehicle_for_processing(self):
        with self.vehicle_queue.lock:
            sorted_vehicles = sorted(self.vehicle_queue.vehicles.items(), 
                                     key=lambda item: int(item[0].replace('V', '')))
            
            for vehicle_id, vehicle_data in sorted_vehicles:
                if vehicle_data.status == "counted_and_waiting":
                    return vehicle_id
            
            return None

    def update_status_based_on_zone(self, detections):
        with self.lock:
            current_time = time.time()
            vehicle_is_in_transaction_zone = False

            if detections and detections[0].boxes:
                for box in detections[0].boxes:
                    if self.is_box_in_area(box.xyxy[0], self.transaction_area):
                        vehicle_is_in_transaction_zone = True
                        break

            if vehicle_is_in_transaction_zone:
                if not self.zone_occupied:
                    self.zone_occupied = True
                    print(f"🏁 ZONA TRANSAKSI TERISI")
                self.zone_clear_confirmation_time = None
            else:
                if self.zone_occupied:
                    if self.zone_clear_confirmation_time is None:
                        self.zone_clear_confirmation_time = current_time
                    elif current_time - self.zone_clear_confirmation_time > self.zone_clear_delay:
                        self.zone_occupied = False
                        print(f"✅ ZONA TRANSAKSI KOSONG")

            current_vehicle_id = self.vehicle_queue.current_processing_vehicle
            
            if not current_vehicle_id and self.zone_occupied:
                next_vehicle_id = self.get_next_vehicle_for_processing()
                if next_vehicle_id:
                    print(f"🆕 Zona terisi, mengambil {next_vehicle_id} dari antrean sesuai urutan FIFO.")
                    self.vehicle_queue.set_current_processing_vehicle(next_vehicle_id)

            elif current_vehicle_id:
                vehicle = self.vehicle_queue.get_vehicle(current_vehicle_id)
                if not vehicle: return

                if not self.zone_occupied and vehicle.has_entered_transaction_zone:
                    print(f"🏁 {current_vehicle_id} dianggap telah KELUAR ZONA TRANSAKSI.")
                    self.vehicle_queue.complete_current_vehicle()
                
                elif (vehicle.transaction_start_time and 
                      current_time - vehicle.transaction_start_time > vehicle.max_transaction_time):
                    if not self.zone_occupied:
                        print(f"⚠️ TIMEOUT & ZONA KOSONG: {current_vehicle_id} dipaksa selesai.")
                        self.vehicle_queue.complete_current_vehicle()
                    else:
                        if not vehicle.timeout_extended:
                            print(f"⏰ TIMEOUT untuk {current_vehicle_id} tapi zona masih terisi. Waktu diperpanjang.")
                            vehicle.max_transaction_time = 60
                            vehicle.timeout_extended = True

# Instance global
line_detector = LineCrossingDetector(frame_width=640, frame_height=480)
vehicle_queue = VehicleQueue()
frontal_manager = FrontalVehicleManager(vehicle_queue, TRANSACTION_AREA)

class OptimizedVideoStream:
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        self.stream.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        self.stream.set(cv2.CAP_PROP_FPS, 25)
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
                if grabbed: self.frame = frame
            time.sleep(0.02)

    def read(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        self.stopped = True
        self.stream.release()

def detect_tire_config_from_detections(results):
    if not results or not results[0].boxes: return None
    for box in results[0].boxes:
        class_id = int(box.cls)
        if class_id == 3: return "single_tire"
        elif class_id == 2: return "double_tire"
    return None

def generate_overhead_stream():
    vs = OptimizedVideoStream(src=RTSP_URL_OVERHEAD).start()
    print(f"Stream overhead dimulai...")
    
    while True:
        frame = vs.read()
        if frame is None:
            time.sleep(0.01)
            continue
        try:
            small_frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)
        except cv2.error:
            continue 

        results = list(model_overhead(small_frame, stream=True, verbose=False, conf=0.5))
        line_detector.update_axle_tracking(results, vehicle_queue)
        
        rendered_frame = results[0].plot() if results else small_frame
        rendered_frame = line_detector.draw_line_and_info(rendered_frame)

        ret, buffer = cv2.imencode('.jpg', rendered_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ret: continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        socketio.emit('overhead_stream', {'image_data': frame_base64})
        time.sleep(1.0 / 30)

def generate_frontal_stream():
    vs = OptimizedVideoStream(src=RTSP_URL_FRONTAL).start()
    print(f"Stream frontal dimulai...")
    
    while True:
        frame = vs.read()
        if frame is None:
            time.sleep(0.01)
            continue
        try:
            small_frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)
        except cv2.error:
            continue

        results = list(model_frontal(small_frame, stream=True, verbose=False, conf=0.5))
        
        frontal_manager.update_status_based_on_zone(results)
        
        tire_config = detect_tire_config_from_detections(results)
        if tire_config and vehicle_queue.current_processing_vehicle:
            vehicle_queue.update_vehicle_tire_config(vehicle_queue.current_processing_vehicle, tire_config)
        
        rendered_frame = results[0].plot() if results else small_frame
        
        overlay = rendered_frame.copy()
        cv2.rectangle(overlay, (TRANSACTION_AREA['x1'], TRANSACTION_AREA['y1']), (TRANSACTION_AREA['x2'], TRANSACTION_AREA['y2']), (0, 255, 0), -1)
        rendered_frame = cv2.addWeighted(overlay, 0.2, rendered_frame, 0.8, 0)
        cv2.putText(rendered_frame, 'ZONA TRANSAKSI', (TRANSACTION_AREA['x1'] + 10, TRANSACTION_AREA['y1'] + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

        ret, buffer = cv2.imencode('.jpg', rendered_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ret: continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        
        data_to_emit = {}
        current_vehicle = vehicle_queue.get_current_vehicle_data()
        if current_vehicle:
            data_to_emit = {
                'vehicle_id': current_vehicle.vehicle_id, 
                'classification': current_vehicle.classification,
                'status': current_vehicle.status
            }
        else:
            data_to_emit = {'vehicle_id': "---", 'classification': "--", 'status': 'idle'}
        
        data_to_emit['image_data'] = frame_base64
        socketio.emit('frontal_stream', data_to_emit)
        time.sleep(1.0 / 30)

@socketio.on('connect')
def handle_connect():
    print('Client terhubung! Memulai semua stream video.')
    if not hasattr(handle_connect, 'tasks_started'):
        socketio.start_background_task(target=generate_overhead_stream)
        socketio.start_background_task(target=generate_frontal_stream)
        socketio.start_background_task(target=lambda: vehicle_queue.cleanup_old_vehicles())
        handle_connect.tasks_started = True

@socketio.on('reset_classification')
def handle_reset():
    """Reset manual untuk sistem (soft reset)."""
    global vehicle_queue, line_detector
    print("Sistem direset secara manual (Soft Reset)")
    
    if vehicle_queue.current_processing_vehicle:
        with frontal_manager.lock:
            vehicle_queue.complete_current_vehicle()
    
    with vehicle_queue.lock:
        vehicle_queue.vehicles.clear()
        vehicle_queue.current_processing_vehicle = None
        
    with line_detector.lock:
        line_detector.reset_tracking_system()
        line_detector.current_vehicle_id = None

@socketio.on('hard_reset_system')
def handle_hard_reset():
    """Reset total (Hard Reset) yang mengembalikan semua counter ke 0."""
    global vehicle_queue, line_detector
    print("🚨 HARD RESET DARI CLIENT! Mereset semua ID dan state.")

    with vehicle_queue.lock:
        vehicle_queue.vehicles.clear()
        vehicle_queue.current_processing_vehicle = None
        vehicle_queue.vehicle_counter = 0
        print("Antrian kendaraan dan counter ID direset ke 0.")

    with line_detector.lock:
        line_detector.reset_tracking_system()
        line_detector.current_vehicle_id = None
        line_detector.axle_id_counter = 0
        print("Sistem deteksi garis dan counter axle direset.")

@socketio.on('set_detection_line')
def handle_set_line(data):
    global line_detector
    if 'line_y' in data and isinstance(data['line_y'], int):
        with line_detector.lock:
            line_detector.line_y = data['line_y']
        print(f"Garis deteksi dipindah ke Y: {data['line_y']}")

if __name__ == '__main__':
    server_host = config['server']['host']
    server_port = config['server']['port']
    print(f"Menjalankan server dengan sistem antrian kendaraan di http://{server_host}:{server_port}")
    socketio.run(app, debug=True, host=server_host, port=server_port, allow_unsafe_werkzeug=True)