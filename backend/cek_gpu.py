import torch

try:
    is_available = torch.cuda.is_available()
    device_count = torch.cuda.device_count()
    current_device = torch.cuda.current_device()
    device_name = torch.cuda.get_device_name(current_device)

    print("--- Pengecekan GPU PyTorch ---")
    print(f"Apakah CUDA tersedia?    : {is_available}")
    if is_available:
        print(f"Jumlah GPU yang terdeteksi : {device_count}")
        print(f"Device yang aktif saat ini : GPU {current_device}")
        print(f"Nama GPU                 : {device_name}")
    else:
        print("PyTorch tidak dapat menemukan GPU yang kompatibel dengan CUDA.")
        print("Ini berarti Anda menginstal versi CPU-only.")

except Exception as e:
    print(f"Terjadi error saat memeriksa CUDA: {e}")