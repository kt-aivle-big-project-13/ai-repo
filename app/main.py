from fastapi import FastAPI

from app.api.bias_report import router as bias_report_router
from app.api.compliance_report import router as compliance_report_router
from app.api.embedding import router as embedding_router
from app.api.fairness import router as fairness_router
from app.api.improvement_guide import router as improvement_guide_router
from app.api.fairness_internal import router as fairness_internal_router
from app.api.report import router as report_router
from app.api.shap import router as shap_router

app = FastAPI(title="신용평가 AI 규제준수 자동감사 — AI 서버")

app.include_router(bias_report_router)
app.include_router(compliance_report_router)
app.include_router(embedding_router)
app.include_router(fairness_router)
app.include_router(improvement_guide_router)
app.include_router(fairness_internal_router)
app.include_router(report_router)
app.include_router(shap_router)


@app.get("/")
def read_root():
    return {"message": "Hello World"}
