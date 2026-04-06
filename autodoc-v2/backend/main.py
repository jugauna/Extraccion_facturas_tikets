"""Punto de entrada para `uvicorn main:app` desde el directorio `backend/`."""

from app.main import app

__all__ = ["app"]

if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
    )
