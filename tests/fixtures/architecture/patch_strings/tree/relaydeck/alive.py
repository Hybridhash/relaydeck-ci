"""A module with the kinds of top-level names a patch can reach."""

from os import path as os_path

LIMIT = 3


def function() -> int:
    return LIMIT


class Box:
    size = 1

    def method(self) -> int:
        return self.size


if LIMIT:
    CONDITIONAL = os_path
