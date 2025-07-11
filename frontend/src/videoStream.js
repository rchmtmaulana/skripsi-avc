import React from 'react';

function VideoStream({ title, frameData, subtitle }) {
  return (
    <div className="bg-gray-800 rounded-lg shadow-lg overflow-hidden flex flex-col">
      <div className="p-4">
        <h2 className="text-lg font-semibold text-cyan-400">{title}</h2>
        {subtitle && <p className="text-sm text-gray-400 mt-1">{subtitle}</p>}
      </div>
      {/* --- PERUBAHAN DI SINI --- */}
      {/* Hapus 'h-full' dan 'flex-grow', tambahkan 'aspect-video' */}
      <div className="relative w-full bg-black aspect-video">
        {frameData ? (
          <img
            src={`data:image/jpeg;base64,${frameData}`}
            alt="Video Stream"
            className="w-full h-full object-contain"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <p className="text-gray-500">Menunggu stream video...</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default VideoStream;