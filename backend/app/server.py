import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from .add_langgraph_route import add_langgraph_route
from .langgraph.agent import build_graph

load_dotenv()

checkpointer_cm = None
checkpointer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global checkpointer_cm, checkpointer

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        checkpointer_cm = AsyncPostgresSaver.from_conn_string(database_url)
        checkpointer = await checkpointer_cm.__aenter__()
        await checkpointer.setup()
        graph = build_graph(checkpointer=checkpointer)
    else:
        graph = build_graph()

    add_langgraph_route(app, graph, "/api/chat")

    try:
        yield
    finally:
        if checkpointer_cm is not None:
            await checkpointer_cm.__aexit__(None, None, None)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
