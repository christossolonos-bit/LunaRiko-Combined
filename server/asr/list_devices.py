"""Diagnostic: print all sounddevice input/output devices.

Useful for picking the right `device=` value for `record_on_speech` when
the default mic isn't what you want.

Run: `python -m server.asr.list_devices`
"""

import sounddevice as sd


if __name__ == "__main__":
    print(sd.query_devices())
