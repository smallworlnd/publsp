"""
https://github.com/lightning/blips/blob/master/blip-0051.md#4-channel
"""

from datetime import datetime
from pydantic import BaseModel


class Channel(BaseModel):
    """part of order response"""
    funded_at: datetime
    funding_outpoint: str
    expires_at: datetime
