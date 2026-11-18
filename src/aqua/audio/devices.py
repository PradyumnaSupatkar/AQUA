"""Enumerate PortAudio devices without opening a microphone."""

from dataclasses import dataclass

from aqua.audio.backend import AudioError, get_backend


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    index: int
    name: str
    host_api: str
    input_channels: int
    output_channels: int
    default_sample_rate: float
    is_default_input: bool


def list_devices(*, include_output: bool = False) -> list[DeviceInfo]:
    """List input devices, or all devices if requested; never capture audio."""
    backend = get_backend()
    try:
        devices = backend.query_devices()
        host_apis = backend.query_hostapis()
        default_input = backend.default.device[0]
    except (backend.PortAudioError, ValueError) as exc:
        raise AudioError(f"Could not enumerate audio devices: {exc}") from exc
    return [
        DeviceInfo(
            index=index,
            name=device["name"],
            host_api=host_apis[device["hostapi"]]["name"],
            input_channels=device["max_input_channels"],
            output_channels=device["max_output_channels"],
            default_sample_rate=device["default_samplerate"],
            is_default_input=index == default_input,
        )
        for index, device in enumerate(devices)
        if include_output or device["max_input_channels"] > 0
    ]


def resolve_input_device(index: int | None = None) -> DeviceInfo:
    """Select only the requested or configured default input; never fall back."""
    for device in list_devices(include_output=True):
        if (index is None and device.is_default_input) or index == device.index:
            if device.input_channels < 1:
                raise AudioError(f"Device {device.index} has no microphone input channels.")
            return device
    if index is None:
        raise AudioError(
            "No default input device is available. Connect a microphone, run "
            "'aqua devices', and select one with '--device ID'."
        )
    raise AudioError(f"Input device {index} was not found. Run 'aqua devices' for current IDs.")
