"""감사 입력 파일 검증 결과 스키마."""

from enum import Enum

from pydantic import BaseModel, Field


class IssueLevel(str, Enum):
    """검증 항목의 심각도.

    BLOCK 이 하나라도 있으면 감사를 진행하지 않는다.
    """

    BLOCK = "BLOCK"
    WARN = "WARN"
    INFO = "INFO"


class ValidationIssue(BaseModel):
    """검증 게이트가 발견한 항목 하나."""

    level: IssueLevel
    item: str = Field(description="검증 대상 (파일명·컬럼명 등)")
    message: str


class ModelSchema(BaseModel):
    """`credit_model.json` 에서 읽은 모델 입력 스키마.

    위험점수 산출 단계가 이 순서 그대로 감사 데이터 컬럼을 정렬한다.
    """

    feature_names: list[str] = Field(description="모델이 학습한 피처 순서")
    categorical_features: list[str] = Field(description="범주형(feature_type == 'c') 피처")
    n_features: int
    best_iteration: int | None = Field(
        default=None, description="early stopping 이 있으면 예측 시 iteration_range 상한으로 사용"
    )
    min_category_counts: dict[str, int] = Field(
        default_factory=dict,
        description="범주형 피처별 학습 당시 카테고리 개수의 하한 (모델 트리에서 역산)",
    )


class AuditDatasetInfo(BaseModel):
    """검증을 통과한 감사 데이터의 요약."""

    n_rows: int
    n_columns: int
    target_column: str
    protected_columns: list[str] = Field(description="감사 데이터에서 확인된 보호속성 컬럼")


class ValidationResult(BaseModel):
    """감사 입력 검증 결과.

    `passed` 가 False 면 BLOCK 이슈가 있다는 뜻이며, 이후 단계를 실행하지 않는다.
    """

    passed: bool
    issues: list[ValidationIssue]
    model_schema_info: ModelSchema | None = None
    audit_dataset: AuditDatasetInfo | None = None
    valid_dataset_usable: bool = Field(
        default=False,
        description="valid_processed.csv 를 확률 보정·임계값 산출에 쓸 수 있는지",
    )
    protected_in_model: dict[str, bool] = Field(
        default_factory=dict,
        description="보호속성별 모델 입력 포함 여부. True 면 직접 사용이므로 검토 대상",
    )
