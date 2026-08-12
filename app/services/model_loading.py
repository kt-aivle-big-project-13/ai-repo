"""XGBoost 모델 로딩 공통 처리.

Booster 를 만드는 곳이 검증·채점·SHAP 세 군데인데, 스레드 수를 지정하지 않으면
XGBoost 가 OpenMP 로 가용 코어를 전부 잡으려 한다. 요청이 동시에 여러 개 들어오면
코어 수를 크게 넘는 스레드가 서로 경합해 전체가 느려지므로, 한곳에서 같은 값을
걸어 준다.
"""

from pathlib import Path

import xgboost as xgb

from app.core.config import get_settings


def load_booster(model_path: Path) -> xgb.Booster:
    """모델 파일을 읽어 스레드 수가 고정된 Booster 를 돌려준다."""

    booster = xgb.Booster()
    booster.load_model(model_path)
    booster.set_param({"nthread": get_settings().xgboost_threads})
    return booster
