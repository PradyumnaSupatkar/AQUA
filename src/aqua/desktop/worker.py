"""Fixed UIA worker: observation plus one narrowly guarded empty-value write."""

from contextlib import redirect_stdout
import json
import sys

MAX_INPUT_BYTES = 8192
MAX_OUTPUT_BYTES = 32768


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Oversized request")
        request = json.loads(raw.decode("utf-8"))
        # Library diagnostic output must not become protocol/UI observation data.
        with redirect_stdout(sys.stderr):
            if type(request) is dict and request.get("operation") == "set_control_text":
                from .interaction import dispatch_interaction
                result = dispatch_interaction(request)
            elif type(request) is dict and request.get("operation") == "inspect_handle":
                from .backend import inspect_handle
                result = inspect_handle(request)
            else:
                from .backend import dispatch
                result = dispatch(request)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise ValueError("Oversized result")
    except Exception:
        encoded = b'{"status":"error"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
