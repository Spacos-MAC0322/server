from fastapi import FastAPI

from .routers import template

app = FastAPI()

app.include_router(template.router)
