"""
FastAPI backend: exposes chat endpoint and data loader trigger.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.db import db
from app.llm_service import chat
from app.config import API_HOST, API_PORT


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    db.connect()
    yield
    db.close()


app = FastAPI(
    title="PA-Web Chatbot API",
    description="Natural language interface to Neo4j graph database",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    cypher: str | None = None
    data: list = []
    error: str | None = None


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    """Convert natural language to Cypher, execute, and return results."""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    result = chat(request.question)
    return ChatResponse(**result)


@app.get("/schema")
def get_schema():
    """Return current Neo4j graph schema."""
    try:
        schema = db.get_schema_info()
        return {"schema": schema}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/load-data")
def load_data():
    """Trigger data loading from JSON files into Neo4j."""
    from app.data_loader import run_loader
    try:
        db.close()
        run_loader()
        db.connect()
        return {"status": "Data loaded successfully"}
    except Exception as e:
        db.connect()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.api:app", host=API_HOST, port=API_PORT, reload=True)
