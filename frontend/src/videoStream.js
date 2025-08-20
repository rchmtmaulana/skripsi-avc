import React from 'react';

const StatusIndicator = ({ status }) => {
    let bgColor, textColor, text;

    switch (status) {
        case 'connected':
            bgColor = 'bg-green-500';
            textColor = 'text-green-300';
            text = 'Terhubung';
            break;
        case 'disconnected':
            bgColor = 'bg-red-500';
            textColor = 'text-red-300';
            text = 'Terputus';
            break;
        default:
            bgColor = 'bg-yellow-500';
            textColor = 'text-yellow-300';
            text = 'Menunggu...';
            break;
    }

    return (
        <div className="flex items-center gap-2">
            <span className={`w-3 h-3 rounded-full ${bgColor}`}></span>
            <span className={`text-sm font-medium ${textColor}`}>{text}</span>
        </div>
    );
};


function VideoStream({ title, frameData, subtitle, status }) {
	return (
		<div className="bg-gray-800 rounded-lg shadow-lg overflow-hidden flex flex-col">
		<div className="p-4">
			<div className="flex justify-between items-center">
				<h2 className="text-lg font-semibold text-cyan-400">{title}</h2>
				<StatusIndicator status={status} />
			</div>
			{subtitle && <p className="text-sm text-gray-400 mt-1">{subtitle}</p>}
		</div>
		<div className="relative w-full bg-black aspect-video">
			{frameData ? (
			<img
				src={`data:image/jpeg;base64,${frameData}`}
				alt="Video Stream"
				className="w-full h-full object-cover"
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