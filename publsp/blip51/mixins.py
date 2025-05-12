import json
from enum import Enum
from nostr_sdk import Tag
from typing import Any, List, Optional


class ErrorMessageMixin:
    error_message: Optional[str] = None


class NostrTagsMixin:
    @classmethod
    def model_from_tags(cls, tags: List[Tag]):
        data: dict[str, Any] = {}

        for tag in tags:
            key, raw = tag.as_vec()

            if raw and raw[0] in ("{", "["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw
            elif raw == "null":
                parsed = None
            else:
                parsed = raw

            data[key] = parsed

        return cls(**data)

    def model_dump_tags(self) -> list[Tag]:
        return [
            Tag.parse([
                key,
                # 1) None → "null"
                "null" if val is None
                # 2) Enum → its .value
                else str(val.value) if isinstance(val, Enum)
                # 3) container → JSON
                else json.dumps(val, default=str, separators=(",", ":"))
                    if isinstance(val, (dict, list, tuple))
                # 4) everything else → str()
                else str(val)
            ])
            for key, val in self.model_dump().items()
        ]
