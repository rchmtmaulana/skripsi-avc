import React, { useState, useEffect } from 'react';
import { io } from 'socket.io-client';
import VideoStream from './videoStream';
import AnalysisPanel from './analysisPanel';

function App() {
    const [overheadFrame, setOverheadFrame] = useState('');
    const [frontalFrame, setFrontalFrame] = useState('');
    const [axleCount, setAxleCount] = useState(0); 

    useEffect(() => {
        const socket = io('http://127.0.0.1:5000');
        socket.on('connect', () => console.log('Terhubung ke server backend!'));

        // Listener untuk stream overhead, sekarang juga menerima data axle_count
        socket.on('overhead_stream', data => {
        setOverheadFrame(data.image_data);
        if (data.axle_count !== undefined) {
            setAxleCount(data.axle_count);
        }
        });

        // Listener untuk stream frontal
        socket.on('frontal_stream', data => {
        setFrontalFrame(data.image_data);
        });

        return () => {
        console.log('Memutuskan koneksi dari server backend.');
        socket.disconnect();
        };
    }, []);

    return (
        <div className="bg-gray-900 text-white min-h-screen p-4 sm:p-6 md:p-8">
        <div className="max-w-7xl mx-auto">
            <header className="mb-8 text-center">
            <h1 className="text-3xl sm:text-4xl font-bold text-cyan-400">
                Automatic Vehicle Classification
            </h1>
            <p className="text-gray-400 mt-2">
                Analisis Gandar dan Tipe Kendaraan Berbasis Visi Komputer
            </p>
            </header>

            <main className="flex flex-col gap-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <VideoStream title="Kamera Frontal (Deteksi Golongan I/II)" frameData={frontalFrame} />
                <VideoStream title="Kamera Overhead (Deteksi Gandar)" frameData={overheadFrame} />
            </div>

            <div className="w-full">
                <AnalysisPanel axleCount={axleCount} />
            </div>

            </main>
        </div>
        </div>
    );
}

export default App;