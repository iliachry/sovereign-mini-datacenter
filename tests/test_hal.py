"""Tests for the Hardware Abstraction Layer (HAL) modules."""

from unittest.mock import MagicMock, patch

from sovereign_dc.hal.gpu import GPUInfo, detect_gpus
from sovereign_dc.hal.power import PowerReading, _parse_prometheus_metrics, read_power
from sovereign_dc.hal.storage import StorageInfo, detect_storage
from sovereign_dc.hal.thermal import ThermalReading, read_thermal


class TestGPUDetection:
    """Test GPU hardware abstraction."""

    def test_gpu_info_creation(self):
        gpu = GPUInfo(name="RTX 4090", memory_mb="24576")
        assert gpu.name == "RTX 4090"
        assert gpu.memory_mb == "24576"
        assert gpu.status == "Ready"

    def test_detect_gpus_simulation_default(self):
        with patch("shutil.which", return_value=None):
            gpus = detect_gpus(simulation=True)
            assert len(gpus) == 1
            assert "DGX Spark" in gpus[0].name
            assert gpus[0].status == "Simulated"

    def test_detect_gpus_no_simulation(self):
        with patch("shutil.which", return_value=None):
            gpus = detect_gpus(simulation=False)
            assert len(gpus) == 0

    def test_detect_gpus_nvidia_smi_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA A100, 40960\nNVIDIA A100, 40960\n"
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch("subprocess.run", return_value=mock_result):
                gpus = detect_gpus(simulation=True)
                assert len(gpus) == 2
                assert gpus[0].name == "NVIDIA A100"
                assert gpus[0].memory_mb == "40960"

    def test_detect_gpus_nvidia_smi_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch("subprocess.run", return_value=mock_result):
                gpus = detect_gpus(simulation=True)
                assert len(gpus) == 1
                assert gpus[0].status == "Simulated"

    def test_detect_gpus_nvidia_smi_exception(self):
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch("subprocess.run", side_effect=TimeoutError("nvidia-smi hung")):
                gpus = detect_gpus(simulation=True)
                assert len(gpus) == 1
                assert gpus[0].status == "Simulated"

    def test_detect_gpus_single_column(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA Jetson Orin\n"
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            with patch("subprocess.run", return_value=mock_result):
                gpus = detect_gpus()
                assert len(gpus) == 1
                assert gpus[0].memory_mb == "Unknown"


class TestPowerReading:
    """Test power telemetry HAL."""

    def test_power_reading_creation(self):
        r = PowerReading(battery_soc=85.0, solar_watts=1200.0)
        assert r.battery_soc == 85.0
        assert r.solar_watts == 1200.0
        assert r.battery_voltage == 52.8

    def test_net_power_positive(self):
        r = PowerReading(battery_soc=85.0, solar_watts=1200.0, system_load_watts=300.0)
        assert r.net_power == 900.0

    def test_net_power_negative(self):
        r = PowerReading(battery_soc=30.0, solar_watts=100.0, system_load_watts=400.0)
        assert r.net_power == -300.0

    def test_read_power_simulation_fallback(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            reading = read_power(simulation=True)
            assert reading.battery_soc == 88.5
            assert reading.solar_watts == 1240.0

    def test_read_power_from_exporter(self):
        metrics = (
            "sovereign_battery_soc_percent 72.5\n"
            "sovereign_solar_pv_power_watts 980.0\n"
            "sovereign_battery_voltage_volts 51.2\n"
            "sovereign_system_power_draw_watts 350.0\n"
            "sovereign_load_shedding_active 0\n"
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = metrics.encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            reading = read_power()
            assert reading.battery_soc == 72.5
            assert reading.solar_watts == 980.0
            assert reading.load_shedding_active is False

    def test_read_power_no_simulation_raises(self):
        import pytest

        with patch("urllib.request.urlopen", side_effect=ConnectionError("Connection refused")):
            with pytest.raises(ConnectionError):
                read_power(simulation=False)

    def test_parse_prometheus_metrics(self):
        content = "# HELP metric\n# TYPE metric gauge\nmy_metric 42.5\nbad_line\ninvalid value\n"
        parsed = _parse_prometheus_metrics(content)
        assert parsed["my_metric"] == 42.5
        assert "bad_line" not in parsed


class TestThermalReading:
    """Test thermal monitoring HAL."""

    def test_thermal_reading_creation(self):
        r = ThermalReading(coolant_celsius=28.0, rack_inlet_celsius=22.0, rack_exhaust_celsius=30.0)
        assert r.coolant_celsius == 28.0

    def test_thermal_delta(self):
        r = ThermalReading(coolant_celsius=28.0, rack_inlet_celsius=22.0, rack_exhaust_celsius=30.0)
        assert r.thermal_delta == 8.0

    def test_is_overtemp_false(self):
        r = ThermalReading(coolant_celsius=28.0, rack_inlet_celsius=22.0, rack_exhaust_celsius=30.0)
        assert r.is_overtemp is False

    def test_is_overtemp_true(self):
        r = ThermalReading(coolant_celsius=60.0, rack_inlet_celsius=40.0, rack_exhaust_celsius=55.0)
        assert r.is_overtemp is True

    def test_read_thermal_simulation_fallback(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            reading = read_thermal(simulation=True)
            assert reading.coolant_celsius == 28.0
            assert reading.rack_inlet_celsius == 22.0

    def test_read_thermal_from_exporter(self):
        metrics = (
            "sovereign_temp_coolant_celsius 35.2\n"
            "sovereign_temp_rack_inlet_celsius 24.5\n"
            "sovereign_temp_rack_exhaust_celsius 33.1\n"
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = metrics.encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            reading = read_thermal()
            assert reading.coolant_celsius == 35.2
            assert reading.rack_inlet_celsius == 24.5

    def test_read_thermal_no_simulation_raises(self):
        import pytest

        with patch("urllib.request.urlopen", side_effect=ConnectionError("Connection refused")):
            with pytest.raises(ConnectionError):
                read_thermal(simulation=False)


class TestStorageDetection:
    """Test storage detection HAL."""

    def test_storage_info_creation(self):
        s = StorageInfo(total_gb=4000.0, used_gb=400.0, free_gb=3600.0)
        assert s.total_gb == 4000.0

    def test_usage_percent(self):
        s = StorageInfo(total_gb=100.0, used_gb=75.0, free_gb=25.0)
        assert s.usage_percent == 75.0

    def test_usage_percent_zero_total(self):
        s = StorageInfo(total_gb=0.0, used_gb=0.0, free_gb=0.0)
        assert s.usage_percent == 0.0

    def test_is_critically_low_false(self):
        s = StorageInfo(total_gb=4000.0, used_gb=2000.0, free_gb=2000.0)
        assert s.is_critically_low is False

    def test_is_critically_low_true(self):
        s = StorageInfo(total_gb=100.0, used_gb=97.0, free_gb=3.0)
        assert s.is_critically_low is True

    def test_detect_storage_real(self):
        info = detect_storage()
        assert info.total_gb > 0
        assert info.free_gb >= 0

    def test_detect_storage_simulation_fallback(self):
        with patch("shutil.disk_usage", side_effect=OSError("Disk error")):
            info = detect_storage(simulation=True)
            assert info.total_gb == 4000.0
            assert info.free_gb == 3600.0

    def test_detect_storage_no_simulation_raises(self):
        with patch("shutil.disk_usage", side_effect=OSError("Disk error")):
            import pytest

            with pytest.raises(OSError):
                detect_storage(simulation=False)

    def test_detect_storage_custom_path(self, tmp_path):
        info = detect_storage(path=str(tmp_path))
        assert info.total_gb > 0
