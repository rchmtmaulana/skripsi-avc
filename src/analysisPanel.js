// src/AnalysisPanel.js
import React from 'react';

function AnalysisPanel({ axleCount = 0, vehicleId = "---", classification = "--", detectionTime = "--:--:--" }) {
    return (
        <div className="bg-gray-800 rounded-lg shadow-lg p-6">
        <h2 className="text-2xl font-bold mb-4 text-cyan-300">Hasil Analisis</h2>
        
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
            <div className="bg-gray-700 p-4 rounded-lg">
            <p className="text-sm text-gray-400 mb-1">ID Kendaraan</p>
            <p className="text-xl font-bold font-mono">{vehicleId}</p>
            </div>
            <div className="bg-gray-700 p-4 rounded-lg">
            <p className="text-sm text-gray-400 mb-1">Jumlah Gandar</p>
            <p className="text-xl font-bold font-mono text-cyan-400">{axleCount}</p>
            </div>
            <div className="bg-gray-700 p-4 rounded-lg">
            <p className="text-sm text-gray-400 mb-1">Golongan</p>
            <p className="text-xl font-bold font-mono">{classification}</p>
            </div>
            <div className="bg-gray-700 p-4 rounded-lg">
            <p className="text-sm text-gray-400 mb-1">Waktu Deteksi</p>
            <p className="text-xl font-bold font-mono">{detectionTime}</p>
            </div>
        </div>
        </div>
    );
}

export default AnalysisPanel;