#!/usr/bin/env python
"""
Run the PublSP API server
"""
import os
import uvicorn
from publsp.cli.logger import LoggerSetup
from publsp.settings import LogLevel


def main():
    # Configure logging
    log_level = LogLevel.INFO
    LoggerSetup(log_level).setup_logging()

    # Get port from environment or use default
    port = int(os.environ.get("PORT", "8000"))

    # Run FastAPI with Uvicorn
    uvicorn.run(
        "publsp.api.app:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level=log_level.value.lower()
    )


if __name__ == "__main__":
    main()
