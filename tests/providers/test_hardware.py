"""Tests for hardware detection module."""

import pytest
from unittest.mock import patch, MagicMock
from providers.hardware import (
    detect_gpu_vram,
    detect_system_ram,
    detect_cpu_count,
    detect_gpu_type,
    get_hardware_profile,
    clear_hardware_cache,
)


@pytest.mark.unit
class TestHardwareDetection:
    """Test cross-platform hardware detection."""

    def teardown_method(self):
        """Clear cache after each test."""
        clear_hardware_cache()

    def test_detect_gpu_vram_nvidia(self):
        """Test NVIDIA GPU VRAM detection."""
        with patch('providers.hardware.subprocess.run') as mock_run:
            # Mock nvidia-smi output
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=b'24000 MiB\n'
            )

            result = detect_gpu_vram()

            assert result == 24000
            mock_run.assert_called_once()

    def test_detect_gpu_vram_no_gpu(self):
        """Test fallback when GPU not available."""
        with patch('providers.hardware.subprocess.run') as mock_run:
            mock_run.side_effect = Exception("Command not found")

            result = detect_gpu_vram()

            assert result is None

    def test_detect_system_ram(self):
        """Test system RAM detection."""
        with patch('providers.hardware.psutil.virtual_memory') as mock_vm:
            mock_vm.return_value = MagicMock(total=68719476736)  # 64 GB

            result = detect_system_ram()

            assert result == 65536  # 64 GB in MB

    def test_detect_cpu_count(self):
        """Test CPU core detection."""
        with patch('providers.hardware.os.cpu_count') as mock_cpu:
            mock_cpu.return_value = 8

            result = detect_cpu_count()

            assert result == 8

    def test_detect_gpu_type_nvidia(self):
        """Test GPU type detection for NVIDIA."""
        with patch('providers.hardware.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            result = detect_gpu_type()

            assert result in ['nvidia', 'amd', 'metal', None]

    def test_get_hardware_profile(self, hardware_profile):
        """Test comprehensive hardware profile."""
        with patch('providers.hardware.subprocess.run') as mock_run, \
             patch('providers.hardware.psutil.virtual_memory') as mock_vm, \
             patch('providers.hardware.os.cpu_count') as mock_cpu:

            # Mock nvidia-smi
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=b'24000 MiB\n'
            )
            mock_vm.return_value = MagicMock(total=68719476736)  # 64 GB
            mock_cpu.return_value = 8

            profile = get_hardware_profile()

            assert profile['gpu_vram_mb'] == 24000
            assert profile['system_ram_mb'] == 65536
            assert profile['cpu_cores'] == 8
            assert 'has_gpu' in profile
            assert 'gpu_type' in profile

    def test_hardware_profile_caching(self):
        """Test that hardware profile is cached."""
        clear_hardware_cache()

        with patch('providers.hardware.detect_gpu_vram') as mock_gpu, \
             patch('providers.hardware.detect_system_ram') as mock_ram, \
             patch('providers.hardware.detect_cpu_count') as mock_cpu, \
             patch('providers.hardware.detect_gpu_type') as mock_type:

            mock_gpu.return_value = 24000
            mock_ram.return_value = 65536
            mock_cpu.return_value = 8
            mock_type.return_value = 'nvidia'

            # First call
            profile1 = get_hardware_profile()
            call_count_1 = mock_gpu.call_count

            # Second call (should use cache)
            profile2 = get_hardware_profile()
            call_count_2 = mock_gpu.call_count

            assert profile1 == profile2
            assert call_count_1 == call_count_2  # No additional calls

    def test_clear_hardware_cache(self):
        """Test cache clearing."""
        with patch('providers.hardware.detect_gpu_vram') as mock_gpu:
            mock_gpu.return_value = 24000

            # First call
            get_hardware_profile()
            first_call_count = mock_gpu.call_count

            # Clear and call again
            clear_hardware_cache()
            get_hardware_profile()

            assert mock_gpu.call_count > first_call_count  # Called again

    def test_hardware_profile_contains_all_fields(self):
        """Test that hardware profile contains all required fields."""
        profile = get_hardware_profile()

        required_fields = ['gpu_vram_mb', 'system_ram_mb', 'cpu_cores', 'has_gpu', 'gpu_type']
        for field in required_fields:
            assert field in profile
