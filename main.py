from fastapi import FastAPI
import uvicorn
import logging
import asyncio
from argparse import ArgumentParser
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from controller import line 
from controller import order
from controller import product

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await line.init_line_bot()
    print("✅ LINE Bot API initialized")
    yield
    # Shutdown
    print("🛑 Shutting down LINE Bot API")

app = FastAPI(lifespan=lifespan)
app.include_router(line.router)
app.include_router(order.router)
app.include_router(product.router)

origins = [
    "http://localhost:3000",
    "https://food-ordering-frontend-vert.vercel.app",
    "https://food-ordering-frontend-eyl0l78e3-siripats-projects-9975bcbc.vercel.app"
]

# app.add_middleware(
#     CORSMiddleware,
#     # allow_origins=origins,
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

@app.get("/ping")
async def ping():
    return {"message": "pong"}

async def main(port: int):
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", reload=True)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)

    arg_parser = ArgumentParser(
        usage="Usage: python " + __file__ + " [--port <port>] [--help]"
    )
    arg_parser.add_argument("-p", "--port", type=int, default=8000, help="port")
    options = arg_parser.parse_args()

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=options.port,
        log_level="info",
        reload=True,
    )
