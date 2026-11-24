"""Counts the bytes of the patches a Flet session sends, encoded as the server encodes them."""

from __future__ import annotations

from typing import Any

import msgpack  # type: ignore[import-untyped]
from flet.controls.base_control import BaseControl
from flet.messaging.protocol import configure_encode_object_for_msgpack


class PatchMeter:
    """Wraps a host app's fake connection; counts what is sent since the last `reset`."""

    def __init__(self, app: Any) -> None:
        self.bytes = 0
        self.messages = 0
        encode = configure_encode_object_for_msgpack(BaseControl)  # type: ignore[no-untyped-call]

        def send(message: Any) -> None:
            body = msgpack.packb([message.action, message.body], default=encode)
            self.bytes += len(body) + 5
            self.messages += 1

        app.conn.send_message = send

    def reset(self) -> None:
        self.bytes = self.messages = 0
