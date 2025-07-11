import React, { useState, useEffect } from 'react';
import { io } from 'socket.io-client';
import VideoStream from './videoStream';
import AnalysisPanel from './analysisPanel';

function App() {
    const [overheadFrame, setOverheadFrame] = useState('');
    const [frontalFrame, setFrontalFrame] = useState('');
    const [axleCount, setAxleCount] = useState(0);
    const [vehicleId, setVehicleId] = useState('---');
    const [classification, setClassification] = useState('--');
    const [detectionTime, setDetectionTime] = useState('--:--:--');
    const [tireConfig, setTireConfig] = useState(null);
    const [detectedAxles, setDetectedAxles] = useState(0);
    const [socket, setSocket] = useState(null);

    useEffect(() => {
        const newSocket = io('http://127.0.0.1:5000');
        setSocket(newSocket);

        newSocket.on('connect', () => {
            console.log('Terhubung ke server backend!');
        });

        newSocket.on('overhead_stream', data => {
            setOverheadFrame(data.image_data);
            if (data.axle_count !== undefined) {
                setAxleCount(data.axle_count);
            }
            if (data.detected_axles !== undefined) {
                setDetectedAxles(data.detected_axles);
            }
            if (data.vehicle_id) {
                setVehicleId(data.vehicle_id);
            }
            if (data.classification) {
                setClassification(data.classification);
            }
            if (data.detection_time) {
                setDetectionTime(data.detection_time);
            }
        });

        newSocket.on('frontal_stream', data => {
            setFrontalFrame(data.image_data);
            if (data.tire_config !== undefined) {
                setTireConfig(data.tire_config);
            }
            if (data.vehicle_id) {
                setVehicleId(data.vehicle_id);
            }
            if (data.classification) {
                setClassification(data.classification);
            }
            if (data.detection_time) {
                setDetectionTime(data.detection_time);
            }
        });

        return () => {
            console.log('Memutuskan koneksi dari server backend.');
            newSocket.disconnect();
        };
    }, []);

    const handleResetClassification = () => {
        if (socket) {
            socket.emit('reset_classification');
            // Reset state lokal
            setAxleCount(0);
            setVehicleId('---');
            setClassification('--');
            setDetectionTime('--:--:--');
            setDetectedAxles(0);
            setTireConfig(null);
        }
    };

    const handleHardReset = () => {
        if (socket) {
            // Kirim event baru ke server
            socket.emit('hard_reset_system');
            
            // Lakukan reset state lokal yang sama seperti reset biasa
            setAxleCount(0);
            setVehicleId('---');
            setClassification('--');
            setDetectionTime('--:--:--');
            setDetectedAxles(0);
            setTireConfig(null);
            
            console.log("HARD RESET: Sinyal reset total dikirim ke server.");
        }
    };

    return (
        <div className="bg-gray-900 text-white min-h-screen p-4 sm:p-6 md:p-8">
            <div className="max-w-7xl mx-auto">
                <header className="mb-8 text-center">
                    <h1 className="text-3xl sm:text-4xl font-bold text-cyan-400">
                        Automatic Vehicle Classification
                    </h1>
                    <p className="text-gray-400 mt-2">
                        SISTEM KLASIFIKASI GOLONGAN KENDARAAN PADA GARDU TOL HYBRID BERBASIS VISI KOMPUTER
                    </p>
                </header>

                <main className="flex flex-col gap-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <VideoStream 
                            title="Kamera Frontal (Deteksi Konfigurasi Ban)" 
                            frameData={frontalFrame}
                            subtitle={tireConfig ? `Konfigurasi: ${tireConfig}` : 'Menunggu deteksi...'}
                        />
                        <VideoStream 
                            title="Kamera Overhead (Line Crossing Detection)" 
                            frameData={overheadFrame}
                            subtitle={`Gandar Terdeteksi: ${detectedAxles} | Gandar Melintas: ${axleCount}`}
                        />
                    </div>

                    <div className="w-full">
                        <AnalysisPanel 
                            axleCount={axleCount}
                            vehicleId={vehicleId}
                            classification={classification}
                            detectionTime={detectionTime}
                        />
                    </div>
                    <div className="bg-gray-800 rounded-lg shadow-lg p-6">
                        <div className="flex flex-col sm:flex-row gap-4 w-full">
                            <button 
                                onClick={handleHardReset}
                                className="w-full sm:flex-1 bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-4 rounded transition-colors"
                            >
                                Reset Semua ID
                            </button>
                            <button 
                                onClick={handleResetClassification}
                                className="w-full sm:flex-1 bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded transition-colors"
                            >
                                Reset Klasifikasi
                            </button>
                        </div>
                    </div>
                </main>
            </div>
        </div>
    );
}

export default App;