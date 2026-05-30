"""Hardware detection for Ollama host — GPU VRAM, system RAM, CPU count."""

import logging
import os
import subprocess
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Cache hardware profile for 5 minutes to avoid repeated detection calls
_hw_cache = None
_hw_cache_time = None
_HW_CACHE_TTL = 300  # seconds


def detect_gpu_vram() -> Optional[int]:
    """
    Detect available GPU VRAM in MB.
    
    Tries (in order):
    1. nvidia-smi (NVIDIA GPUs)
    2. rocm-smi (AMD GPUs)
    3. Metal (Apple Silicon / macOS)
    
    Returns MB available, or None if no GPU detected.
    """
    # Try NVIDIA
    try:
        output = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
            stderr=subprocess.DEVNULL,
            timeout=2
        ).decode('utf-8').strip()
        
        # nvidia-smi returns a list for multiple GPUs; take the first
        vram_mb = int(float(output.split('\n')[0]))
        logger.info(f"NVIDIA GPU detected: {vram_mb}MB VRAM available")
        return vram_mb
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    
    # Try AMD/ROCm
    try:
        output = subprocess.check_output(
            ['rocm-smi', '--showmeminfo', 'all'],
            stderr=subprocess.DEVNULL,
            timeout=2
        ).decode('utf-8')
        
        # Parse "Free:" line
        for line in output.split('\n'):
            if 'Free:' in line:
                # Format: "Free: 23456 MB"
                parts = line.split()
                if len(parts) >= 2:
                    vram_mb = int(parts[1])
                    logger.info(f"AMD GPU (ROCm) detected: {vram_mb}MB VRAM available")
                    return vram_mb
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    
    # Try Metal (Apple Silicon)
    try:
        # On macOS with Metal, we can query metal performance shaders
        output = subprocess.check_output(
            ['system_profiler', 'SPDisplaysDataType'],
            stderr=subprocess.DEVNULL,
            timeout=2
        ).decode('utf-8')
        
        # Look for "VRAM" in output
        for line in output.split('\n'):
            if 'VRAM' in line and 'MB' in line:
                # Format: "VRAM (Dynamic): 8192 MB"
                parts = line.split()
                try:
                    vram_mb = int(parts[-2])  # Second-to-last is the number
                    logger.info(f"Apple Metal GPU detected: {vram_mb}MB VRAM available")
                    return vram_mb
                except (IndexError, ValueError):
                    pass
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    logger.debug("No GPU detected (nvidia-smi, rocm-smi, system_profiler all unavailable)")
    return None


def detect_system_ram() -> int:
    """
    Detect total system RAM in MB.
    
    Uses psutil.virtual_memory().total (cross-platform).
    Falls back to os methods if psutil unavailable.
    
    Returns MB of total system RAM.
    """
    try:
        import psutil
        total_bytes = psutil.virtual_memory().total
        total_mb = int(total_bytes / (1024 * 1024))
        logger.debug(f"System RAM detected: {total_mb}MB")
        return total_mb
    except ImportError:
        pass
    
    # Fallback to /proc/meminfo on Linux
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    # Format: "MemTotal: 65536 kB"
                    total_kb = int(line.split()[1])
                    total_mb = int(total_kb / 1024)
                    logger.debug(f"System RAM detected (from /proc): {total_mb}MB")
                    return total_mb
    except FileNotFoundError:
        pass
    
    # Fallback to sysctl on macOS
    try:
        output = subprocess.check_output(
            ['sysctl', '-n', 'hw.memsize'],
            stderr=subprocess.DEVNULL,
            timeout=2
        ).decode('utf-8').strip()
        total_bytes = int(output)
        total_mb = int(total_bytes / (1024 * 1024))
        logger.debug(f"System RAM detected (from sysctl): {total_mb}MB")
        return total_mb
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    
    # Ultimate fallback: assume 8GB
    logger.warning("Could not detect system RAM; assuming 8GB (8192MB)")
    return 8192


def detect_cpu_count() -> int:
    """
    Detect number of CPU cores.
    
    Uses os.cpu_count() (cross-platform).
    Falls back to 4 if unavailable.
    """
    count = os.cpu_count()
    if count is None:
        logger.warning("Could not detect CPU count; assuming 4")
        return 4
    logger.debug(f"CPU cores detected: {count}")
    return count


def detect_gpu_type() -> Optional[str]:
    """
    Detect GPU type: 'nvidia', 'amd', 'metal', or None (no GPU).
    """
    # Try in order
    try:
        subprocess.check_output(['nvidia-smi', '--version'], stderr=subprocess.DEVNULL, timeout=2)
        return 'nvidia'
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    try:
        subprocess.check_output(['rocm-smi', '--version'], stderr=subprocess.DEVNULL, timeout=2)
        return 'amd'
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    try:
        subprocess.check_output(['system_profiler', 'SPDisplaysDataType'], stderr=subprocess.DEVNULL, timeout=2)
        # On macOS, if we can run system_profiler, check if it mentions GPU
        return 'metal'
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return None


def get_hardware_profile() -> dict:
    """
    Get complete hardware profile with caching.
    
    Returns:
    {
        "gpu_vram_mb": 24000,          # None if no GPU
        "system_ram_mb": 64000,
        "cpu_cores": 12,
        "has_gpu": True,
        "gpu_type": "nvidia"           # or "amd", "metal", None
    }
    """
    global _hw_cache, _hw_cache_time
    
    # Check cache validity
    if _hw_cache is not None and _hw_cache_time is not None:
        if datetime.utcnow() - _hw_cache_time < timedelta(seconds=_HW_CACHE_TTL):
            logger.debug("Using cached hardware profile")
            return _hw_cache
    
    logger.debug("Detecting hardware profile...")
    
    gpu_vram = detect_gpu_vram()
    system_ram = detect_system_ram()
    cpu_count = detect_cpu_count()
    gpu_type = detect_gpu_type()
    
    profile = {
        "gpu_vram_mb": gpu_vram,
        "system_ram_mb": system_ram,
        "cpu_cores": cpu_count,
        "has_gpu": gpu_vram is not None,
        "gpu_type": gpu_type,
    }
    
    _hw_cache = profile
    _hw_cache_time = datetime.utcnow()
    
    logger.info(
        f"Hardware profile: GPU={gpu_type} ({gpu_vram}MB), "
        f"RAM={system_ram}MB, CPUs={cpu_count}"
    )
    
    return profile


def clear_hardware_cache():
    """Force re-detection on next call (for testing or if hardware changes)."""
    global _hw_cache, _hw_cache_time
    _hw_cache = None
    _hw_cache_time = None
    logger.debug("Hardware cache cleared")
