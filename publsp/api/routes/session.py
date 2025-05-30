from fastapi import APIRouter, Depends
from pydantic import BaseModel

from publsp.api.session import UserSession
from publsp.api.utils import get_user_session


class SessionInfo(BaseModel):
    session_id: str
    nostr_pubkey: str
    created_at: str
    last_accessed: str


router = APIRouter(prefix="/session", tags=["Session"])


@router.get("/", response_model=SessionInfo)
async def get_session(session: UserSession = Depends(get_user_session)):
    """Get information about the current session"""

    # Ensure session is initialized
    if not session.initialized:
        await session.initialize(reuse_keys=False)

    return SessionInfo(
        session_id=session.session_id,
        nostr_pubkey=session.nostr_client.get_npub(),
        created_at=session.created_at.isoformat(),
        last_accessed=session.last_accessed.isoformat()
    )
