import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .add_langgraph_route import add_langgraph_route
from .chats import store as chat_store
from .chats.routes import make_chats_router
from .documents import store as document_store
from .documents.routes import router as documents_router
from .langgraph.agent import build_graph
from .models.routes import router as models_router
from .tools.mcp.loader import connect_mcp_servers

load_dotenv()

checkpointer_pool = None
checkpointer = None


def make_checkpointer_pool(database_url: str) -> AsyncConnectionPool:
    """Connections for the checkpointer, checked before each use.

    Not AsyncPostgresSaver.from_conn_string: that holds one connection for the
    life of the process and never reopens it. Seen live when unattended-upgrades
    restarted PostgreSQL - the connection was terminated, and every chat turn
    after that failed with "the connection is closed" until the backend was
    restarted. The pool's check replaces a dead connection with a new one.

    The connection settings are the ones from_conn_string uses, which the saver
    relies on: autocommit for its writes, no prepared statements, dict rows.
    """
    return AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=int(os.getenv("CHECKPOINTER_POOL_SIZE", "5")),
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
        open=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global checkpointer_pool, checkpointer

    # Register tools from configured MCP servers before the graph is built.
    await connect_mcp_servers()

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        checkpointer_pool = make_checkpointer_pool(database_url)
        await checkpointer_pool.open(wait=True)
        checkpointer = AsyncPostgresSaver(conn=checkpointer_pool)
        await checkpointer.setup()
        graph = build_graph(checkpointer=checkpointer)
    else:
        graph = build_graph()

    # Document and chat-registry tables, alongside the checkpointer's. Both
    # are no-ops without DATABASE_URL, so startup degrades rather than failing.
    await document_store.setup()
    await chat_store.setup()

    add_langgraph_route(app, graph, "/api/chat")
    # Registered here rather than at import: the history endpoint reads the
    # compiled graph's state, and the graph does not exist until now.
    app.include_router(make_chats_router(graph, checkpointer))

    try:
        yield
    finally:
        if checkpointer_pool is not None:
            await checkpointer_pool.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registered at import rather than in the lifespan: unlike /api/chat, these
# routes do not depend on the graph.
app.include_router(documents_router)
app.include_router(models_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
