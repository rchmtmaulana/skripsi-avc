import cv2, base64, os, threading, time
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

os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!key' 
socketio = SocketIO(app, cors_allowed_origins="*")

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f"==========================================")
print(f"Menggunakan device: {device}")
print(f"==========================================")

# Muat Model dengan optimasi
try:
    model_overhead = YOLO('models/overhead.pt').to(device)
    model_frontal = YOLO('models/frontal.pt').to(device)
    
    # Optimasi model untuk inferensi yang lebih cepat
    model_overhead.fuse()
    model_frontal.fuse()
    
    print("Model 'overhead.pt' dan 'frontal.pt' berhasil dimuat ke device.")
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
    firestore_manager = FirestoreManager('./serviceAccountKey.json')
    print("✅ Firestore berhasil diinisialisasi")
except Exception as e:
    print(f"❌ Gagal inisialisasi Firestore: {e}")
    firestore_manager = None

# URL RTSP
RTSP_URL_OVERHEAD = "rtsp://root:w4h!D.xXx@157.119.222.50:5540/media2/stream.sdp?profile=Profile200"
RTSP_URL_FRONTAL = "rtsp://root:w4h!D.xXx@157.119.222.50:5541/media2/stream.sdp?profile=Profile200"

# ==============================================================================
# AREA TRANSAKSI
TRANSACTION_AREA = {'x1': 0, 'y1': 0, 'x2': 160, 'y2': 480}
# ==============================================================================

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
        self.status = "detected"
        self.config_locked = False
        self.has_entered_transaction_zone = False
        self.transaction_start_time = None
        self.max_transaction_time = 15
        self.timeout_extended = False
        self.processing_attempts = 0

class VehicleQueue:
    def __init__(self):
        self.vehicles = {}
        self.vehicle_counter = 0
        self.current_processing_vehicle = None
        self.processing_start_time = None
        self.LEARNING_WINDOW_SECONDS = 4
        self.lock = Lock()
        self.timeout_vehicles = set()
        self.indonesia_tz = pytz.timezone('Asia/Makassar')

    def finalize_vehicle_from_overhead(self, vehicle_id):
        with self.lock:
            if vehicle_id in self.vehicles:
                # Status diubah menjadi "menunggu kamera frontal"
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
                # --- AWAL LOGIKA PENAMBAHAN AXLE ---
                vehicle = self.vehicles[vehicle_id]
                if vehicle.axle_count == 1:
                    print(f"KOREKSI OTOMATIS: Gandar untuk {vehicle_id} hanya 1, diubah menjadi 2 saat memasuki area frontal.")
                    vehicle.axle_count = 2
                    # Panggil ulang klasifikasi dengan data yang sudah dikoreksi
                    self.classify_vehicle(vehicle_id)
                # --- AKHIR LOGIKA PENAMBAHAN AXLE ---
                self.current_processing_vehicle = vehicle_id
                self.processing_start_time = time.time()
                # Status awal saat diambil alih oleh kamera frontal
                self.vehicles[vehicle_id].status = "approaching" 
                print(f"Kendaraan {vehicle_id} mulai diproses di area frontal (approaching).")
    
    def complete_current_vehicle(self):
        if self.current_processing_vehicle:
            vehicle_id_completed = self.current_processing_vehicle
            vehicle_data = self.vehicles[vehicle_id_completed]
            
            # PERBAIKAN: Tandai sebagai timeout jika dipaksa selesai
            if (vehicle_data.transaction_start_time and 
                time.time() - vehicle_data.transaction_start_time > vehicle_data.max_transaction_time):
                self.timeout_vehicles.add(vehicle_id_completed)
                print(f"⚠️ {vehicle_id_completed} ditandai sebagai TIMEOUT")
            
            # Hitung durasi pemrosesan
            processing_duration = None
            if self.processing_start_time:
                processing_duration = time.time() - self.processing_start_time
            
            # Simpan ke Firestore dengan flag timeout
            if firestore_manager:
                entry_time_aware = None
                if vehicle_data.transaction_start_time:
                    entry_time_aware = datetime.fromtimestamp(
                        vehicle_data.transaction_start_time, 
                        tz=self.indonesia_tz
                    )
                exit_time_aware = datetime.now(self.indonesia_tz)
                
                firestore_manager.save_vehicle_transaction(
                    vehicle_data=vehicle_data,
                    processing_duration=processing_duration,
                    entry_time=entry_time_aware,
                    exit_time=exit_time_aware,
                    is_timeout=vehicle_id_completed in self.timeout_vehicles
                )

            vehicle_data.status = "completed"
            print(f"✅ Transaksi {vehicle_id_completed} SELESAI")
            
            line_detector.finalize_vehicle(vehicle_id_completed)
            self.current_processing_vehicle = None
            self.processing_start_time = None
            socketio.emit('clear_analysis_panel')
            return True
        return False
    
    def get_next_vehicle_for_processing(self):
        with self.lock:
            if self.current_processing_vehicle: 
                return None
            
            # PERBAIKAN: Prioritas kendaraan yang belum timeout
            candidates = []
            for vehicle_id, vehicle_data in sorted(self.vehicles.items()):
                if vehicle_data.status == "counted_and_waiting":
                    # Prioritaskan yang belum timeout
                    if vehicle_id not in self.timeout_vehicles:
                        candidates.append((vehicle_id, 0))  # Prioritas tinggi
                    else:
                        candidates.append((vehicle_id, 1))  # Prioritas rendah
            
            if candidates:
                # Urutkan berdasarkan prioritas
                candidates.sort(key=lambda x: x[1])
                return candidates[0][0]
            
            return None
    
    def classify_vehicle(self, vehicle_id):
        if vehicle_id not in self.vehicles: 
            return
        vehicle = self.vehicles[vehicle_id]
        
        classification_made = False
        if vehicle.axle_count >= 3:
            if vehicle.axle_count == 3: 
                vehicle.classification = "Golongan 3"
            elif vehicle.axle_count == 4: 
                vehicle.classification = "Golongan 4"
            elif vehicle.axle_count >= 5: 
                vehicle.classification = "Golongan 5"
            classification_made = True
        elif vehicle.axle_count == 2 and vehicle.tire_config:
            if vehicle.tire_config == "single_tire": 
                vehicle.classification = "Golongan 1"
            elif vehicle.tire_config == "double_tire": 
                vehicle.classification = "Golongan 2"
            classification_made = True
        
        if classification_made:
            vehicle.is_classified = True
            print(f"Kendaraan {vehicle_id} TERKLASIFIKASI: {vehicle.classification}")
            
            if vehicle.axle_count == 2:    
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
                return self.vehicles[self.current_processing_vehicle]
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
        
        # --- KOORDINAT GARIS ---

        # Titik 1 (Ujung Kiri Bawah Garis)
        x1 = 200
        y1 = 260

        # Titik 2 (Ujung Kanan Atas Garis)
        x2 = 350
        y2 = 210
        # ------------------------------------

        self.line_x1 = x1
        self.line_y1 = y1
        self.line_x2 = x2
        self.line_y2 = y2

        self.tracked_axles = {}
        self.axle_id_counter = 0
        self.current_vehicle_axles = {}
        self.current_vehicle_id = None
        self.history_frames = 5
        self.last_vehicle_time = time.time()
        self.vehicle_timeout = 1.0
        self.lock = Lock()
        
        # TAMBAHAN: Untuk tracking body kendaraan
        self.vehicle_body_touching_line = False
        self.last_body_detection_time = time.time()
        self.body_timeout = 0.5  # Timeout untuk body detection

    def point_to_line_distance(self, px, py):
        """Menghitung jarak dari titik ke garis diagonal"""
        x1, y1, x2, y2 = self.line_x1, self.line_y1, self.line_x2, self.line_y2
        
        # Rumus jarak titik ke garis: |ax + by + c| / sqrt(a² + b²)
        a = y2 - y1
        b = x1 - x2
        c = x2 * y1 - x1 * y2
        
        distance = abs(a * px + b * py + c) / np.sqrt(a * a + b * b)
        return distance

    def is_point_crossing_line(self, px1, py1, px2, py2):
        """Cek apakah titik melintasi garis diagonal"""
        x1, y1, x2, y2 = self.line_x1, self.line_y1, self.line_x2, self.line_y2
        
        # Hitung posisi relatif titik terhadap garis
        def side_of_line(px, py):
            return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        
        side1 = side_of_line(px1, py1)
        side2 = side_of_line(px2, py2)
        
        # Jika tanda berbeda, berarti melintasi garis
        return (side1 > 0) != (side2 > 0)

    def is_box_touching_line(self, box, tolerance=15):
        """Cek apakah bounding box menyentuh garis diagonal dengan toleransi"""
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        
        # Cek jarak dari keempat sudut bounding box ke garis
        corners = [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
        
        for corner_x, corner_y in corners:
            if self.point_to_line_distance(corner_x, corner_y) <= tolerance:
                return True
        
        # Cek juga apakah garis memotong bounding box
        # Implementasi sederhana: cek apakah garis melewati area bounding box
        line_x_min = min(self.line_x1, self.line_x2)
        line_x_max = max(self.line_x1, self.line_x2)
        line_y_min = min(self.line_y1, self.line_y2)
        line_y_max = max(self.line_y1, self.line_y2)
        
        # Cek overlap area
        if (x1 <= line_x_max and x2 >= line_x_min and 
            y1 <= line_y_max and y2 >= line_y_min):
            return True
        
        return False

    def finalize_vehicle(self, vehicle_id):
        with self.lock:
            if self.current_vehicle_id == vehicle_id:
                print(f"--- Kendaraan {vehicle_id} difinalisasi. Siap untuk ID baru. ---")
                self.current_vehicle_id = None
                self.reset_tracking_system()

    def reset_tracking_system(self):
        """Reset sistem tracking untuk kendaraan baru"""
        print("🔄 RESET SISTEM TRACKING - Siap untuk kendaraan baru")
        self.tracked_axles.clear()
        self.current_vehicle_axles.clear()
        self.vehicle_body_touching_line = False
        self.last_body_detection_time = time.time()

    def get_axle_center(self, box):
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        return (x1 + x2) / 2, (y1 + y2) / 2

    def detect_vehicle_bodies_and_axles(self, results):
        """Pisahkan deteksi body kendaraan dan axle dari hasil YOLO"""
        vehicle_bodies = []
        axles = []
        
        if not results or len(results) == 0:
            return vehicle_bodies, axles
        
        result = results[0]
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls)
                if class_id in [2, 3, 1]:  # car, truck, bus (sesuaikan dengan class ID model Anda)
                    vehicle_bodies.append(box)
                elif class_id == 0:  # axle
                    axles.append(box)
        
        return vehicle_bodies, axles

    def update_vehicle_body_status(self, vehicle_bodies):
        """Update status body kendaraan yang menyentuh garis diagonal"""
        current_time = time.time()
        body_touching_now = False
        
        for body_box in vehicle_bodies:
            if self.is_box_touching_line(body_box):
                body_touching_now = True
                self.last_body_detection_time = current_time
                break
        
        # Update status
        if body_touching_now:
            if not self.vehicle_body_touching_line:
                print("🚗 BODY KENDARAAN MULAI MENYENTUH GARIS - Sistem aktif")
            self.vehicle_body_touching_line = True
        else:
            # Cek apakah sudah timeout
            if self.vehicle_body_touching_line and (current_time - self.last_body_detection_time > self.body_timeout):
                print("🚗 BODY KENDARAAN SUDAH TIDAK MENYENTUH GARIS - Sistem akan reset")
                self.vehicle_body_touching_line = False
                return True  # Signal untuk reset
        
        return False

    def update_axle_tracking(self, results, vehicle_queue):
        with self.lock:
            current_time = time.time()
            
            # Pisahkan deteksi body dan axle
            vehicle_bodies, axle_detections = self.detect_vehicle_bodies_and_axles(results)
            
            # Update status body kendaraan
            should_reset = self.update_vehicle_body_status(vehicle_bodies)
            
            # Jika body sudah tidak menyentuh garis, reset sistem
            if should_reset and self.current_vehicle_id:
                print(f"🔄 AUTO RESET: Kendaraan {self.current_vehicle_id} selesai (body tidak menyentuh garis)")
                vehicle_queue.finalize_vehicle_from_overhead(self.current_vehicle_id)
                self.current_vehicle_id = None
                self.reset_tracking_system()
                return
            
            # Hanya proses axle jika ada body yang menyentuh garis
            if not self.vehicle_body_touching_line:
                return
            
            # Update tracking axle seperti biasa
            if axle_detections:
                self.last_vehicle_time = current_time
            
            # Timeout berdasarkan axle (backup jika body detection gagal)
            if self.current_vehicle_id and (current_time - self.last_vehicle_time > self.vehicle_timeout):
                print(f"--- TIMEOUT AXLE: {self.current_vehicle_id}. Diserahkan ke antrean. ---")
                vehicle_queue.finalize_vehicle_from_overhead(self.current_vehicle_id)
                self.current_vehicle_id = None
                self.reset_tracking_system()
                return

            # Proses axle detection
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
                    # Buat kendaraan baru jika belum ada dan body menyentuh garis
                    if self.current_vehicle_id is None and self.vehicle_body_touching_line:
                        self.start_new_vehicle(vehicle_queue)

                    if self.current_vehicle_id:
                        self.axle_id_counter += 1
                        new_axle_id = self.axle_id_counter
                        self.tracked_axles[new_axle_id] = {
                            'positions': [(center_x, center_y)], 
                            'crossed': False,
                            'last_seen': current_time, 
                            'vehicle_id': self.current_vehicle_id,
                        }
                        self.current_vehicle_axles[self.current_vehicle_id].append(new_axle_id)
                        self.check_line_crossing(new_axle_id, center_x, center_y, vehicle_queue)
            
            self.cleanup_old_axles(current_time)

    def start_new_vehicle(self, vehicle_queue):
        new_vehicle_id = vehicle_queue.create_new_vehicle()
        self.current_vehicle_id = new_vehicle_id
        self.current_vehicle_axles[self.current_vehicle_id] = []
        print(f"--- Memulai tracking untuk kendaraan baru: {self.current_vehicle_id} ---")

    def find_closest_axle(self, center_x, center_y, max_distance=80):
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

    def check_line_crossing(self, axle_id, new_x, new_y, vehicle_queue):
        if axle_id not in self.tracked_axles:
            return
        axle_data = self.tracked_axles[axle_id]
        if axle_data.get('crossed', False):
            return
        
        if len(axle_data['positions']) < 2:
            return
            
        prev_x, prev_y = axle_data['positions'][-2]
        
        # Cek apakah axle melintasi garis diagonal
        if self.is_point_crossing_line(prev_x, prev_y, new_x, new_y):
            axle_data['crossed'] = True
            vehicle_id = axle_data['vehicle_id']
            print(f"✅ Axle {axle_id} (Kendaraan {vehicle_id}) MELINTASI GARIS DIAGONAL!")
            if vehicle_id:
                current_count = self.get_crossed_axles_count_for_vehicle(vehicle_id)
                vehicle_queue.update_vehicle_axle_count(vehicle_id, current_count)

    def get_crossed_axles_count_for_vehicle(self, vehicle_id):
        count = 0
        if vehicle_id not in self.current_vehicle_axles: 
            return 0
        for axle_id in self.current_vehicle_axles[vehicle_id]:
            if axle_id in self.tracked_axles and self.tracked_axles[axle_id].get('crossed', False):
                count += 1
        return count

    def cleanup_old_axles(self, current_time, timeout=5):
        to_remove = [axle_id for axle_id, data in self.tracked_axles.items() 
                    if current_time - data['last_seen'] > timeout]
        for axle_id in to_remove:
            if axle_id in self.tracked_axles:
                del self.tracked_axles[axle_id]
    
    def draw_line_and_info(self, frame):
        with self.lock:
            # Gambar garis diagonal dengan warna berbeda berdasarkan status
            line_color = (0, 255, 0) if self.vehicle_body_touching_line else (0, 0, 255)
            line_thickness = 4 if self.vehicle_body_touching_line else 3
            
            # Gambar garis diagonal dari (x1, y1) ke (x2, y2)
            cv2.line(frame, (self.line_x1, self.line_y1), (self.line_x2, self.line_y2), line_color, line_thickness)
            
            # Gambar titik-titik di ujung garis untuk lebih jelas
            cv2.circle(frame, (self.line_x1, self.line_y1), 5, line_color, -1)
            cv2.circle(frame, (self.line_x2, self.line_y2), 5, line_color, -1)
            
            # Status indicator
            status_text = "AKTIF" if self.vehicle_body_touching_line else "STANDBY"
            status_color = (0, 255, 0) if self.vehicle_body_touching_line else (0, 0, 255)
            cv2.putText(frame, f'Status: {status_text}', 
                       (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)
            
            # Gambar tracking axle
            active_axles = [(axle_id, data) for axle_id, data in self.tracked_axles.items() 
                           if data['positions'] and time.time() - data['last_seen'] < 2]
            
            for axle_id, axle_data in active_axles:
                positions = axle_data['positions']
                if len(positions) > 1:
                    recent_positions = positions[-3:]
                    for i in range(1, len(recent_positions)):
                        cv2.line(frame, 
                                (int(recent_positions[i-1][0]), int(recent_positions[i-1][1])),
                                (int(recent_positions[i][0]), int(recent_positions[i][1])), 
                                (255, 0, 0), 2)
                
                last_x, last_y = positions[-1]
                color = (0, 255, 255) if axle_data.get('crossed', False) else (255, 255, 0)
                cv2.circle(frame, (int(last_x), int(last_y)), 8, color, -1)
                vehicle_id = axle_data.get('vehicle_id', "N/A")
                cv2.putText(frame, f'{vehicle_id}:A{axle_id}', 
                           (int(last_x) + 10, int(last_y) - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
            
            if self.current_vehicle_id:
                cv2.putText(frame, f'Current Overhead Vehicle: {self.current_vehicle_id}', 
                           (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            return frame

# ==============================================================================
# PERUBAHAN 3: REFAKTOR TOTAL KELAS FRONTAL MANAGER
# Kelas ini sekarang bertanggung jawab penuh untuk mengelola logika zona transaksi.
class FrontalVehicleManager:
    def __init__(self, vehicle_queue, transaction_area):
        self.vehicle_queue = vehicle_queue
        self.transaction_area = transaction_area
        self.lock = Lock()
        self.zone_occupied = False
        self.zone_occupation_start_time = None
        self.zone_clear_confirmation_time = None
        self.zone_clear_delay = 0.5  # Delay 0.5 detik untuk konfirmasi zona kosong

    def is_box_in_area(self, box, area):
        """Mengecek apakah bounding box overlap dengan area transaksi."""
        x1, y1, x2, y2 = box
        return not (x2 < area['x1'] or x1 > area['x2'] or 
                    y2 < area['y1'] or y1 > area['y2'])

    def update_status_based_on_zone(self, detections):
        with self.lock:
            current_time = time.time()
            vehicle_is_in_transaction_zone = False
            any_vehicle_detected = False

            # Cek apakah ada kendaraan di dalam zona transaksi
            if detections:
                for box in detections[0].boxes:
                    any_vehicle_detected = True
                    if self.is_box_in_area(box.xyxy[0], self.transaction_area):
                        vehicle_is_in_transaction_zone = True
                        break

            # === PERBAIKAN 1: TRACKING ZONA LEBIH KETAT ===
            # Update status okupasi zona
            if vehicle_is_in_transaction_zone:
                if not self.zone_occupied:
                    self.zone_occupied = True
                    self.zone_occupation_start_time = current_time
                    print(f"🏁 ZONA TRANSAKSI TERISI (waktu: {current_time:.2f})")
                self.zone_clear_confirmation_time = None  # Reset timer clear
            else:
                if self.zone_occupied:
                    if self.zone_clear_confirmation_time is None:
                        self.zone_clear_confirmation_time = current_time
                        print(f"⏳ ZONA MULAI KOSONG - Menunggu konfirmasi...")
                    elif current_time - self.zone_clear_confirmation_time > self.zone_clear_delay:
                        self.zone_occupied = False
                        self.zone_occupation_start_time = None
                        self.zone_clear_confirmation_time = None
                        print(f"✅ ZONA TRANSAKSI BENAR-BENAR KOSONG")

            current_vehicle_id = self.vehicle_queue.current_processing_vehicle
            
            # === PERBAIKAN 2: LOGIKA TIMEOUT YANG LEBIH AMAN ===
            if current_vehicle_id:
                vehicle = self.vehicle_queue.get_vehicle(current_vehicle_id)
                if not vehicle: 
                    return

                # TIMEOUT HANDLING - Hanya jika zona benar-benar kosong
                if (vehicle.status == "in_transaction" and 
                    vehicle.transaction_start_time and 
                    current_time - vehicle.transaction_start_time > vehicle.max_transaction_time):
                    
                    # PERBAIKAN: Timeout hanya dipicu jika zona kosong
                    if not self.zone_occupied:
                        print(f"⚠️ TIMEOUT + ZONA KOSONG: {current_vehicle_id} dipaksa selesai")
                        self.vehicle_queue.complete_current_vehicle()
                        return
                    else:
                        print(f"⏰ TIMEOUT tapi zona masih terisi - menunggu {current_vehicle_id}")
                        # Perpanjang timeout jika zona masih terisi
                        vehicle.max_transaction_time = 60  # Perpanjang jadi 60 detik
                
                # KASUS 1: Kendaraan masuk zona transaksi
                if vehicle_is_in_transaction_zone and vehicle.status != "in_transaction":
                    print(f"🚗 {current_vehicle_id} MASUK ZONA TRANSAKSI")
                    vehicle.status = "in_transaction"
                    vehicle.transaction_start_time = current_time
                    vehicle.has_entered_transaction_zone = True

                    if vehicle.is_classified:
                        print(f"Mengirim analisis tertunda untuk {current_vehicle_id} ({vehicle.classification})")
                        analysis_data = {
                            'vehicle_id': vehicle.vehicle_id,
                            'classification': vehicle.classification,
                            'axle_count': vehicle.axle_count,
                            'detection_time': datetime.now(self.vehicle_queue.indonesia_tz).strftime("%H:%M:%S")
                        }
                        socketio.emit('update_analysis_panel', analysis_data)

                # KASUS 2: Kendaraan keluar zona (dengan konfirmasi delay)
                elif (not self.zone_occupied and 
                      vehicle.has_entered_transaction_zone and 
                      vehicle.status == "in_transaction"):
                    print(f"🏁 {current_vehicle_id} KELUAR ZONA TRANSAKSI (konfirmasi)")
                    self.vehicle_queue.complete_current_vehicle()
            
            # === PERBAIKAN 3: DETEKSI KENDARAAN BARU LEBIH SELEKTIF ===
            # Hanya ambil kendaraan baru jika zona benar-benar kosong
            elif not current_vehicle_id and any_vehicle_detected and not self.zone_occupied:
                next_vehicle_id = self.vehicle_queue.get_next_vehicle_for_processing()
                if next_vehicle_id:
                    print(f"🆕 Zona kosong - mengambil {next_vehicle_id} dari antrean")
                    self.vehicle_queue.set_current_processing_vehicle(next_vehicle_id)
# ==============================================================================

# Instance global
line_detector = LineCrossingDetector(frame_width=640, frame_height=480)
vehicle_queue = VehicleQueue()
frontal_manager = FrontalVehicleManager(vehicle_queue, TRANSACTION_AREA)


class OptimizedVideoStream:
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        self.stream.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        self.stream.set(cv2.CAP_PROP_FPS, 25)
        
        (self.grabbed, self.frame) = self.stream.read()
        self.stopped = False
        self.lock = Lock()

    def start(self):
        threading.Thread(target=self.update, daemon=True).start()
        return self

    def update(self):
        while not self.stopped:
            grabbed, frame = self.stream.read()
            if grabbed:
                with self.lock:
                    self.frame = frame
            time.sleep(0.02)

    def read(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        self.stopped = True
        self.stream.release()

def detect_axles_from_results(results):
    if not results or len(results) == 0:
        return []
    
    axle_detections = []
    result = results[0]
    if result.boxes is not None:
        for box in result.boxes:
            if box.cls == 0:
                axle_detections.append(box)
    return axle_detections

def detect_tire_config_from_detections(results):
    if not results or len(results) == 0:
        return None
    
    result = results[0]
    if result.boxes is not None:
        for box in result.boxes:
            class_id = int(box.cls)
            if class_id == 3: 
                return "single_tire"
            elif class_id == 2: 
                return "double_tire"
    return None

def generate_overhead_stream():
    vs = OptimizedVideoStream(src=RTSP_URL_OVERHEAD).start()
    print(f"Stream overhead dimulai...")
    
    frame_counter = 0
    target_fps = 30
    
    while True:
        frame = vs.read()
        if frame is None: 
            time.sleep(0.01)
            continue

        frame_counter += 1
        try:
            small_frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)
        except cv2.error:
            continue 

        # Dapatkan hasil deteksi lengkap (body + axle)
        results = list(model_overhead(small_frame, stream=True, verbose=False, conf=0.5))
        
        # Update tracking dengan hasil lengkap
        line_detector.update_axle_tracking(results, vehicle_queue)
        
        if frame_counter % 30 == 0:
            vehicle_queue.cleanup_old_vehicles()
        
        rendered_frame = results[0].plot() if results else small_frame
        rendered_frame = line_detector.draw_line_and_info(rendered_frame)

        ret, buffer = cv2.imencode('.jpg', rendered_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ret: 
            continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        
        current_vehicle = vehicle_queue.get_current_vehicle_data()
        data_to_emit = {}
        if current_vehicle:
            data_to_emit = {
                'vehicle_id': current_vehicle.vehicle_id, 
                'axle_count': current_vehicle.axle_count,
                'classification': current_vehicle.classification, 
                'detection_time': current_vehicle.detection_time
            }
        else:
            last_overhead_id = line_detector.current_vehicle_id
            last_vehicle = vehicle_queue.get_vehicle(last_overhead_id) if last_overhead_id else None
            if last_vehicle:
                data_to_emit = {
                    'vehicle_id': last_vehicle.vehicle_id, 
                    'axle_count': last_vehicle.axle_count,
                    'classification': last_vehicle.classification, 
                    'detection_time': last_vehicle.detection_time
                }
            else:
                data_to_emit = {
                    'vehicle_id': "---", 
                    'axle_count': 0, 
                    'classification': "--", 
                    'detection_time': "--:--:--"
                }

        data_to_emit['image_data'] = frame_base64
        
        # Hitung jumlah axle yang terdeteksi dari hasil detection
        axle_count = 0
        if results and results[0].boxes is not None:
            for box in results[0].boxes:
                if int(box.cls) == 0:  # axle class
                    axle_count += 1
        
        data_to_emit['detected_axles'] = axle_count
        data_to_emit['system_status'] = 'AKTIF' if line_detector.vehicle_body_touching_line else 'STANDBY'
        
        socketio.emit('overhead_stream', data_to_emit)
        
        time.sleep(1.0 / target_fps)

def generate_frontal_stream():
    vs = OptimizedVideoStream(src=RTSP_URL_FRONTAL).start()
    print(f"Stream frontal dimulai...")

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

        results = list(model_frontal(small_frame, stream=True, verbose=False, conf=0.5))
        
        # ==============================================================================
        # PERUBAHAN 4: MENGGUNAKAN LOGIKA MANAGER BARU
        # Panggil metode update baru dari frontal_manager
        frontal_manager.update_status_based_on_zone(results)
        
        # Update konfigurasi ban untuk kendaraan yang sedang diproses
        tire_config = detect_tire_config_from_detections(results)
        if tire_config and vehicle_queue.current_processing_vehicle:
            proc_id = vehicle_queue.current_processing_vehicle
            vehicle_queue.update_vehicle_tire_config(proc_id, tire_config)
        # ==============================================================================
        
        rendered_frame = results[0].plot() if results else small_frame
        
        # ==============================================================================
        # PERUBAHAN 5: GAMBAR VISUALISASI AREA TRANSAKSI
        # Gambar persegi panjang hijau transparan untuk menandai zona transaksi.
        overlay = rendered_frame.copy()
        cv2.rectangle(overlay, 
                      (TRANSACTION_AREA['x1'], TRANSACTION_AREA['y1']), 
                      (TRANSACTION_AREA['x2'], TRANSACTION_AREA['y2']), 
                      (0, 255, 0), -1)
        alpha = 0.2  # Transparansi
        rendered_frame = cv2.addWeighted(overlay, alpha, rendered_frame, 1 - alpha, 0)
        # Tambahkan teks label untuk area tersebut
        cv2.putText(rendered_frame, 'ZONA TRANSAKSI', 
                    (TRANSACTION_AREA['x1'] + 10, TRANSACTION_AREA['y1'] + 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        # ==============================================================================

        ret, buffer = cv2.imencode('.jpg', rendered_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ret: 
            continue
        
        frame_base64 = base64.b64encode(buffer.tobytes()).decode('utf-8')
        
        data_to_emit = {}
        current_vehicle = vehicle_queue.get_current_vehicle_data()
        if current_vehicle:
            data_to_emit = {
                'vehicle_id': current_vehicle.vehicle_id, 
                'classification': current_vehicle.classification,
                'detection_time': current_vehicle.detection_time, 
                'status': current_vehicle.status # Kirim status yang sudah diupdate
            }
        else:
            data_to_emit = {'vehicle_id': "---", 'classification': "--", 'detection_time': "--:--:--", 'status': 'idle'}
        
        data_to_emit['image_data'] = frame_base64
        data_to_emit['tire_config'] = tire_config
        
        socketio.emit('frontal_stream', data_to_emit)
        
        time.sleep(1.0 / target_fps)

@socketio.on('connect')
def handle_connect():
    print('Client terhubung! Memulai semua stream video.')
    # Memastikan task tidak dijalankan berulang kali jika ada re-connect
    if not hasattr(handle_connect, 'tasks_started'):
        socketio.start_background_task(target=generate_overhead_stream)
        socketio.start_background_task(target=generate_frontal_stream)
        handle_connect.tasks_started = True

@socketio.on('reset_classification')
def handle_reset():
    """Reset manual untuk sistem (soft reset)."""
    global frontal_manager, line_detector, vehicle_queue
    print("Sistem direset secara manual (Soft Reset)")
    
    # Menyelesaikan kendaraan yang sedang diproses
    if vehicle_queue.current_processing_vehicle:
        with frontal_manager.lock:
            vehicle_queue.complete_current_vehicle()
    
    # Mereset antrian dan state overhead
    with vehicle_queue.lock:
        vehicle_queue.vehicles.clear()
        vehicle_queue.current_processing_vehicle = None
        # Counter ID tidak direset di sini
        
    with line_detector.lock:
        line_detector.reset_tracking_system()
        line_detector.current_vehicle_id = None

@socketio.on('hard_reset_system')
def handle_hard_reset():
    """Reset total (Hard Reset) yang mengembalikan semua counter ke 0."""
    global vehicle_queue, line_detector
    print("🚨 HARD RESET DARI CLIENT! Mereset semua ID dan state.")

    # 1. Reset total antrian kendaraan, termasuk counter utama
    with vehicle_queue.lock:
        vehicle_queue.vehicles.clear()
        vehicle_queue.current_processing_vehicle = None
        vehicle_queue.vehicle_counter = 0  # <- Paling penting: reset counter ID
        print("Antrian kendaraan dan counter ID direset ke 0.")

    # 2. Reset total sistem deteksi garis
    with line_detector.lock:
        line_detector.reset_tracking_system()
        line_detector.current_vehicle_id = None
        line_detector.axle_id_counter = 0 # Reset counter axle juga
        print("Sistem deteksi garis dan counter axle direset.")

@socketio.on('set_detection_line')
def handle_set_line(data):
    global line_detector
    if 'line_y' in data and isinstance(data['line_y'], int):
        with line_detector.lock:
            line_detector.line_y = data['line_y']
        print(f"Garis deteksi dipindah ke Y: {data['line_y']}")

if __name__ == '__main__':
    print("Menjalankan server dengan sistem antrian kendaraan di http://127.0.0.1:5000")
    socketio.run(app, debug=True, host='127.0.0.1', port=5000, allow_unsafe_werkzeug=True)