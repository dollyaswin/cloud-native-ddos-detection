# apps/fastapi/main.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {
        "status": "success",
        "message": "Hello World from FastAPI!",
        "architecture": "Traffic routed via Envoy & Ext-Proc"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}
