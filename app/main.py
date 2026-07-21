from fastapi import FastAPI

from app.api.fairness import router as fairness_router

app = FastAPI(title="신용평가 AI 규제준수 자동감사 — AI 서버")

app.include_router(fairness_router)


@app.get("/")
def read_root():
    return {"message": "Hello World"}
