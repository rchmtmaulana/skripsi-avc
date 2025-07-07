import React from 'react';

function VideoStream({ title, frameData, subtitle }) {
    return (
        <div className="bg-gray-800 rounded-lg shadow-lg p-4">
            <h2 className="text-xl font-semibold mb-3 text-cyan-300">{title}</h2>
            {subtitle && (
                <p className="text-sm text-gray-400 mb-2">{subtitle}</p>
            )}
            <div className="aspect-video bg-black rounded-md flex items-center justify-center overflow-hidden">
                {frameData ? (
                    <img 
                        src={`data:image/jpeg;base64,${frameData}`} 
                        alt={title}
                        className="w-full h-full object-cover"
                    />
                ) : (
                    <p className="text-gray-500">Menunggu stream...</p>
                )}
            </div>
        </div>
    );
}

export default VideoStream;