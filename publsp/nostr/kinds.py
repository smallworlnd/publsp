from enum import IntEnum
from nostr_sdk import Kind


class PublspKind(IntEnum):
    """
    define the ad kind for now, others may come later for whatever purpose
    """
    AD = 39735

    def __str__(self):
        return self.name

    @property
    def as_kind_obj(self) -> Kind:
        return Kind(self.value)
