#!/usr/bin/env python3
"""Stands in for the `claude` CLI in tests: echoes stream-json events like the real one."""
import json
import sys

prompt = sys.stdin.read()
args = sys.argv[1:]


def emit(obj):
    print(json.dumps(obj), flush=True)


emit({"type": "system", "subtype": "init"})
emit({"type": "stream_event", "event": {"type": "message_start"}})
for piece in ["You said: ", prompt.splitlines()[-1], " | tools=", args[args.index("--tools") + 1]]:
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": piece}}})
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Read", "input": {"file_path": "Skippy/Todos.md"}}]}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": "ignored"})
