"""The Mac companion's answer about a newer release: a notice only, never an installer."""

from typing import Literal, TypedDict

from relaydeck.contracts.pairing import document

_VERSION = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
_DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
_MOMENT = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"

UPDATES_SCHEMAS = {
    "relaydeck/gateway/v1/updates": document(
        "updates",
        {
            "schema": {"const": "relaydeck/gateway/v1/updates"},
            "installed": {"type": "string", "minLength": 1, "maxLength": 64},
            "available": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["version", "issued", "notes"],
                "properties": {
                    "version": {"type": "string", "pattern": _VERSION},
                    "issued": {"type": "string", "pattern": _DATE},
                    "notes": {"type": "array", "items": {"type": "string", "maxLength": 200}},
                },
            },
            "check": {"enum": ["on", "off"]},
            "last_success": {"type": ["string", "null"], "pattern": _MOMENT},
            "instructions": {"type": ["array", "null"], "items": {"type": "string"}},
        },
    ),
}


# Typed body for the schema above; tests keep it identical to the JSON Schema.
class UpdatesAvailable(TypedDict):
    version: str
    issued: str
    notes: list[str]


class Updates(TypedDict):
    schema: Literal["relaydeck/gateway/v1/updates"]
    installed: str
    available: UpdatesAvailable | None
    check: Literal["on", "off"]
    last_success: str | None
    instructions: list[str] | None
