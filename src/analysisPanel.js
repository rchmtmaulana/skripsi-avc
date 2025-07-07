import React from 'react';

function AnalysisPanel({ axleCount = 0, vehicleId = "---", classification = "--", detectionTime = "--:--:--" }) {
    const getClassificationColor = (classification) => {
        if (classification === "--") return "text-gray-400";
        if (classification.includes("1")) return "text-green-400";
        if (classification.includes("2")) return "text-blue-400";
        if (classification.includes("3")) return "text-yellow-400";
        if (classification.includes("4")) return "text-orange-400";
        if (classification.includes("5")) return "text-red-400";
        return "text-cyan-400";
    };

    const getStatusIndicator = (classification) => {
        if (classification === "--") {
            return (
                <div className="flex items-center gap-2 mt-2">
                    <div className="w-2 h-2 bg-yellow-400 rounded-full animate-pulse"></div>
                    <span className="text-sm text-yellow-400">Menunggu Klasifikasi</span>
                </div>
            );
        } else {
            return (
                <div className="flex items-center gap-2 mt-2">
                    <div className="w-2 h-2 bg-green-400 rounded-full"></div>
                    <span className="text-sm text-green-400">Terklasifikasi</span>
                </div>
            );
        }
    };

    return (
        <div className="bg-gray-800 rounded-lg shadow-lg p-6">
            <div className="flex justify-between items-center mb-4">
                <h2 className="text-2xl font-bold text-cyan-300">Hasil Analisis</h2>
                {getStatusIndicator(classification)}
            </div>
            
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
                    <p className={`text-xl font-bold font-mono ${getClassificationColor(classification)}`}>
                        {classification}
                    </p>
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