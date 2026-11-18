"""Load the only audio dependency when a microphone command needs it."""

from types import ModuleType


class AudioError(RuntimeError):
    """An actionable device, permission, or audio-backend error."""


def get_backend() -> ModuleType:
    """Import PortAudio bindings lazily so help and pure tests need no audio."""
    try:
        import sounddevice
    except ImportError as exc:
        raise AudioError(
            "Audio dependencies are missing. Install Aqua with "
            r".\.venv\Scripts\python.exe -m pip install -e ."
        ) from exc
    except OSError as exc:
        raise AudioError(f"The PortAudio library could not load: {exc}") from exc
    except Exception as exc:
        # PortAudio initializes during import and can raise its own exception
        # before the module is available. Keep this broad catch at the import
        # boundary; cancellation and process-exit exceptions still propagate.
        raise AudioError(f"The audio backend could not initialize: {exc}") from exc
    return sounddevice
