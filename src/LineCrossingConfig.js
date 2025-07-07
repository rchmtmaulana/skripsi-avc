import React, { useState } from 'react';

function LineCrossingConfig({ socket }) {
    const [linePosition, setLinePosition] = useState(300);
    const [isConfigOpen, setIsConfigOpen] = useState(false);

    const handleLinePositionChange = (newPosition) => {
        setLinePosition(newPosition);
        if (socket) {
            socket.emit('set_detection_line', { line_y: newPosition });
        }
    };

    return (
        <div className="bg-gray-800 rounded-lg shadow-lg p-6">
            <div className="flex justify-between items-center mb-4">
                <h3 className="text-xl font-bold text-cyan-300">Konfigurasi Line Crossing</h3>
                <button
                    onClick={() => setIsConfigOpen(!isConfigOpen)}
                    className="bg-gray-600 hover:bg-gray-700 text-white px-3 py-1 rounded text-sm transition-colors"
                >
                    {isConfigOpen ? 'Tutup' : 'Buka'}
                </button>
            </div>

            {isConfigOpen && (
                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-300 mb-2">
                            Posisi Garis Deteksi (Y-coordinate)
                        </label>
                        <div className="flex items-center space-x-4">
                            <input
                                type="range"
                                min="100"
                                max="400"
                                value={linePosition}
                                onChange={(e) => handleLinePositionChange(parseInt(e.target.value))}
                                className="flex-1 h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                            />
                            <span className="text-cyan-400 font-mono text-sm min-w-[3rem]">
                                {linePosition}px
                            </span>
                        </div>
                    </div>

                    <div className="bg-gray-700 p-4 rounded-lg">
                        <h4 className="text-sm font-semibold text-cyan-300 mb-2">Cara Kerja Line Crossing:</h4>
                        <ul className="text-xs text-gray-400 space-y-1">
                            <li>• Garis hijau horizontal adalah area deteksi</li>
                            <li>• Sistem melacak pergerakan setiap axle yang terdeteksi</li>
                            <li>• Axle yang melintas garis akan dihitung sebagai "crossed"</li>
                            <li>• Tracking menggunakan algoritma centroid untuk akurasi</li>
                            <li>• Sistem otomatis reset setelah kendaraan lewat</li>
                        </ul>
                    </div>

                    <div className="bg-gray-700 p-4 rounded-lg">
                        <h4 className="text-sm font-semibold text-yellow-300 mb-2">Tips Konfigurasi:</h4>
                        <ul className="text-xs text-gray-400 space-y-1">
                            <li>• Posisikan garis di area yang sering dilewati axle</li>
                            <li>• Hindari area dengan bayangan atau refleksi</li>
                            <li>• Sesuaikan posisi berdasarkan sudut kamera</li>
                            <li>• Test dengan berbagai jenis kendaraan</li>
                        </ul>
                    </div>
                </div>
            )}
        </div>
    );
}

export default LineCrossingConfig;