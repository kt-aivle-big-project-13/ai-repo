from dotenv import load_dotenv

# .env를 os.environ에 실제로 채워 넣는다. pydantic-settings(app.core.config.Settings)는
# .env를 자체적으로 읽지만 그건 Settings 객체 안에서만 쓰이고 os.environ은 안 건드린다.
# storage.py 등 여러 서비스가 os.getenv()로 직접 읽는 S3/MinIO 설정은 이 호출 없이는
# 항상 None이라 503(S3ConfigurationError)이 난다 — 다른 라우터를 import하기 전에,
# 요청 처리 시점에 해당 서비스들이 os.getenv를 호출하기 전에 먼저 실행돼야 한다.
load_dotenv()

from fastapi import FastAPI

from app.api.bias.bias_report import router as bias_report_router
from app.api.chat.chat import router as chat_router
from app.api.compliance.compliance_report import router as compliance_report_router
from app.api.chat.embedding import router as embedding_router
from app.api.fairness.fairness import router as fairness_router
from app.api.improvement.improvement_guide import router as improvement_guide_router
from app.api.fairness.fairness_internal import router as fairness_internal_router
from app.api.report import router as report_router
from app.api.explainability.shap import router as shap_router
from app.api.highimpact.high_impact_report import (
    router as high_impact_report_router,
)

app = FastAPI(title="신용평가 AI 규제준수 자동감사 — AI 서버")

app.include_router(bias_report_router)
app.include_router(chat_router)
app.include_router(compliance_report_router)
app.include_router(embedding_router)
app.include_router(fairness_router)
app.include_router(improvement_guide_router)
app.include_router(fairness_internal_router)
app.include_router(high_impact_report_router)
app.include_router(report_router)
app.include_router(shap_router)

@app.get("/")
def read_root():
    return {"message": "Hello World"}
