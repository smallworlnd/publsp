from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from publsp.api.session import session_manager
from publsp.api.routes import session, ads, orders, channels

app = FastAPI(
    title="publsp API",
    description="API for purchasing liquidity from an LSP "
    "coordinated over Nostr"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(session.router)
app.include_router(ads.router)
app.include_router(orders.router)
app.include_router(channels.router)


# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    # Start the session maintenance task
    await session_manager.start_maintenance()


@app.on_event("shutdown")
async def shutdown_event():
    # Properly clean up all sessions
    await session_manager.shutdown()
