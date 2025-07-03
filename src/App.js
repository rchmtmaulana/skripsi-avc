import React, { useState, useEffect } from 'react';
import { io } from 'socket.io-client';

function App() {
  const [overheadFrame, setOverheadFrame] = useState('');
  const [frontalFrame, setFrontalFrame] = useState(''); // State ini sekarang akan kita gunakan

  useEffect(() => {
    const socket = io('http://127.0.0.1:5000');
    socket.on('connect', () => console.log('Terhubung ke server backend!'));

    // --- DENGARKAN KEDUA EVENT ---
    // Listener untuk stream kamera overhead
    socket.on('overhead_stream', data => {
      setOverheadFrame(data.image_data);
    });

    // Listener untuk stream kamera frontal
    socket.on('frontal_stream', data => {
      setFrontalFrame(data.image_data);
    });

    return () => {
      console.log('Memutuskan koneksi dari server backend.');
      socket.disconnect();
    };
  }, []); // Array kosong '[]' memastikan useEffect hanya berjalan sekali saat komponen dimuat

  return (
    <div className="bg-gray-900 text-white min-h-screen p-4 sm:p-6 md:p-8">
      <div className="max-w-7xl mx-auto">
        {/* --- JUDUL APLIKASI --- */}
        <header className="mb-8 text-center">
          <h1 className="text-3xl sm:text-4xl font-bold text-cyan-400">
            Sistem Automatic Vehicle Classification (AVC)
          </h1>
          <p className="text-gray-400 mt-2">
            Analisis Gardan dan Tipe Kendaraan Berbasis YOLO & ByteTrack
          </p>
        </header>

        {/* --- KONTEN UTAMA: VIDEO & DATA --- */}
        <main className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          {/* --- KOLOM UNTUK STREAMING VIDEO --- */}
          <div className="lg:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-6">
            
            {/* Player Video 2: Kamera Frontal */}
            <div className="bg-gray-800 rounded-lg shadow-lg p-4">
              <h2 className="text-xl font-semibold mb-3 text-cyan-300">Kamera Frontal (Deteksi Golongan I/II)</h2>
              <div className="aspect-video bg-black rounded-md flex items-center justify-center overflow-hidden">
                {/* --- TAMPILKAN STREAM FRONTAL DI SINI --- */}
                {frontalFrame ? (
                  <img src={`data:image/jpeg;base64,${frontalFrame}`} alt="Frontal Cam" />
                ) : (
                  <p className="text-gray-500">Menunggu stream...</p>
                )}
              </div>
            </div>
            
            {/* Player Video 1: Kamera Overhead */}
            <div className="bg-gray-800 rounded-lg shadow-lg p-4">
              <h2 className="text-xl font-semibold mb-3 text-cyan-300">Kamera Overhead (Deteksi Gardan)</h2>
              <div className="aspect-video bg-black rounded-md flex items-center justify-center">
                {/* Tampilkan gambar jika ada data, jika tidak tampilkan pesan */}
                {overheadFrame ? (
                  <img src={`data:image/jpeg;base64,${overheadFrame}`} alt="Overhead Cam" />
                ) : (
                  <p className="text-gray-500">Menunggu stream dari kamera overhead...</p>
                )}
              </div>
            </div>

          </div>

          {/* --- KOLOM UNTUK DATA & HASIL KLASIFIKASI --- */}
          <div className="lg:col-span-1 bg-gray-800 rounded-lg shadow-lg p-6">
            <h2 className="text-2xl font-bold mb-4 text-cyan-300">Hasil Analisis</h2>
            <div className="space-y-5">
              
              {/* Data Kendaraan yang Terdeteksi */}
              <div className="bg-gray-700 p-4 rounded-lg">
                <h3 className="font-semibold text-lg mb-2">Kendaraan Terakhir</h3>
                <div className="text-gray-300 space-y-1">
                  <p><strong>ID Kendaraan:</strong> <span className="font-mono bg-gray-600 px-2 py-1 rounded">TRK-128</span></p>
                  <p><strong>Jumlah Gardan:</strong> <span className="font-mono bg-gray-600 px-2 py-1 rounded">2</span></p>
                  <p><strong>Klasifikasi Golongan:</strong> <span className="font-mono bg-cyan-500 text-black px-2 py-1 rounded">GOLONGAN II</span></p>
                  <p><strong>Waktu Deteksi:</strong> <span className="font-mono bg-gray-600 px-2 py-1 rounded">20:15:30</span></p>
                </div>
              </div>

              {/* Log atau Histori */}
              <div className="bg-gray-700 p-4 rounded-lg">
                <h3 className="font-semibold text-lg mb-2">Log Sistem</h3>
                <ul className="text-sm text-gray-400 space-y-1 h-48 overflow-y-auto">
                  {/* Contoh log, nanti akan diisi data dinamis */}
                  <li><span className="text-green-400">[INFO]</span> Backend terhubung.</li>
                  <li><span className="text-green-400">[INFO]</span> Model `overhead.pt` berhasil dimuat.</li>
                  <li><span className="text-yellow-400">[DETECT]</span> Kendaraan TRK-128 terdeteksi di kamera overhead.</li>
                   <li><span className="text-yellow-400">[DETECT]</span> Kendaraan TRK-128 terdeteksi di kamera frontal.</li>
                   <li><span className="text-cyan-400">[CLASSIFY]</span> TRK-128 diklasifikasikan sebagai GOLONGAN II.</li>
                </ul>
              </div>

            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

export default App;