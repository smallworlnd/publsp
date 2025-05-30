from fastapi import Header, Request
from typing import Optional

from publsp.api.session import UserSession, session_manager


async def get_user_session(
    request: Request,
    npub: Optional[str] = Header(None),
    session_id: Optional[str] = Header(None)
) -> UserSession:
    """
    Get a user session.

    If session_id is provided, returns an existing session (if found).
    Otherwise, creates a new session.
    """
    # If a specific session ID is provided, try to use it
    if session_id:
        session = session_manager.get_session(session_id)
        if session:
            return session

    user_id = npub
    if not user_id:
        import uuid
        random_id = str(uuid.uuid4())
        user_id = f"ip_{request.client.host}_{random_id}" \
            if request.client \
            else f"anonymous_{random_id}"

    # Always create a new session if not using an explicit session ID
    session = await session_manager.create_new_session(user_id)
    return session
