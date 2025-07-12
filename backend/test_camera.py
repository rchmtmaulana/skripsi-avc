import torch

if not torch.cuda.is_available():
    print("CUDA tidak tersedia. Pastikan driver dan PyTorch versi GPU sudah terinstal.")
else:
    device_count = torch.cuda.device_count()
    print(f"CUDA tersedia! Ditemukan {device_count} GPU.")
    print("-----------------------------------------")
    
    for i in range(device_count):
        gpu_name = torch.cuda.get_device_name(i)
        print(f"GPU {i}: {gpu_name}")
        
    current_device_index = torch.cuda.current_device()
    current_device_name = torch.cuda.get_device_name(current_device_index)
    print("-----------------------------------------")
    print(f"GPU yang sedang aktif secara default adalah GPU {current_device_index}: {current_device_name}")