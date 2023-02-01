import torch


def get_device(device):
    """Select a backend from CPU, CUDA, or MPS."""
    if isinstance(device, torch.device):
        return device
    elif device == "cpu":
        return torch.device("cpu")
    elif "cuda" in device:
        if torch.cuda.is_available():
            return torch.device(device)
        else:
            raise ValueError("cuda is not available")
    elif device == "mps":
        if torch.backends.mps.is_built() and torch.backends.mps.is_available():
            return torch.device("mps")
        else:
            raise ValueError("mps is not available")
    else:
        devices = ["cpu", "cuda", "mps"]
        raise ValueError(f"device must be one of {devices}")
