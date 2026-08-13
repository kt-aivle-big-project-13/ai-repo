"""감사 입력 파일 검증 (이슈 #4).

업로드된 XGBoost 모델과 감사 데이터가 Fairlearn 공정성 감사에 쓸 수 있는 형태인지
확인한다. 문제를 만나도 바로 멈추지 않고 BLOCK/WARN/INFO 로 모아서 돌려주므로,
사용자가 여러 문제를 한 번에 파악할 수 있다. 단 모델·감사 데이터를 아예 읽지
못하면 이후 검사가 의미 없으므로 그 시점에 결과를 반환한다.
"""

import json
from pathlib import Path

import pandas as pd
import xgboost as xgb

from app.schemas.validation import (
    AuditDatasetInfo,
    IssueLevel,
    ModelSchema,
    ValidationIssue,
    ValidationResult,
)
from app.services.model_loading import load_booster

TARGET_COLUMN = "TARGET"
GENDER_COLUMN = "CODE_GENDER"
AGE_GROUP_COLUMN = "AGE_GROUP"

# 보호속성이 모델 입력에 직접 들어갔는지 확인할 때 참조하는 원본 컬럼. 키는 보호속성
# 컬럼명(감사 시작 시 고른 sensitive_features 값) 그대로라, 목록에 없는 속성(예: 인종 등
# 새 모델에 맞춰 추가된 민감변수)은 자기 자신을 유일한 후보로 보고 그대로 검사한다.
# 연령만 예외 — 나이 자체가 아니라 생년월일 기반 파생값으로도 모델에 들어갈 수 있어
# 후보를 넓게 둔다.
PROTECTED_SOURCE_COLUMNS: dict[str, list[str]] = {
    GENDER_COLUMN: [GENDER_COLUMN],
    AGE_GROUP_COLUMN: ["DAYS_BIRTH", "AGE", AGE_GROUP_COLUMN],
}

# 집단별 지표를 계산하기에 표본이 너무 적은 집단을 경고할 기준.
MIN_GROUP_SIZE = 300


class _IssueCollector:
    """검증 항목을 모으고 BLOCK 발생 여부를 추적한다."""

    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    def add(self, level: IssueLevel, item: str, message: str) -> None:
        self.issues.append(ValidationIssue(level=level, item=item, message=message))

    @property
    def blocked(self) -> bool:
        return any(issue.level is IssueLevel.BLOCK for issue in self.issues)


def _min_category_counts(learner: dict, feature_names: list[str]) -> dict[str, int]:
    """모델 트리에 쓰인 범주형 코드로 학습 당시 카테고리 개수의 하한을 역산한다.

    XGBoost 는 범주형을 정수 코드로 학습하지만 원래 문자열 라벨은 저장하지
    않는다. 트리 분기에 등장한 최대 코드가 k 면 학습 때 카테고리가 최소 k+1
    개는 있었다는 뜻이다. 감사 데이터의 고유값 개수가 이보다 적으면 pandas 가
    코드를 다르게 매겨 모델이 값을 잘못 알아듣는다.
    """
    trees = learner["gradient_booster"]["model"]["trees"]
    max_code: dict[str, int] = {}
    for tree in trees:
        split_indices = tree["split_indices"]
        categories = tree["categories"]
        for node, segment, size in zip(
            tree["categories_nodes"],
            tree["categories_segments"],
            tree["categories_sizes"],
        ):
            feature = feature_names[split_indices[node]]
            chunk = categories[segment : segment + size]
            if chunk:
                max_code[feature] = max(max_code.get(feature, -1), max(chunk))
    return {feature: code + 1 for feature, code in max_code.items()}


def load_model_schema(model_path: Path) -> ModelSchema:
    """`credit_model.json` 을 로드하고 피처 스키마를 읽는다.

    피처 이름·범주형 목록은 모델 파일의 `learner` 에 들어 있으므로 별도 스키마
    파일이 필요 없다. 로드에 실패하면 예외를 그대로 올린다.
    """
    booster = load_booster(model_path)

    with open(model_path, encoding="utf-8") as f:
        learner = json.load(f)["learner"]

    feature_names = list(learner["feature_names"])
    feature_types = list(learner["feature_types"])
    categorical = [name for name, ftype in zip(feature_names, feature_types) if ftype == "c"]

    attributes = booster.attributes()
    best_iteration = attributes.get("best_iteration")

    return ModelSchema(
        feature_names=feature_names,
        categorical_features=categorical,
        n_features=len(feature_names),
        best_iteration=int(best_iteration) if best_iteration is not None else None,
        min_category_counts=_min_category_counts(learner, feature_names),
    )


def _check_target(frame: pd.DataFrame, collector: _IssueCollector) -> None:
    """실제 연체 여부가 0/1 로만 이루어졌는지 확인한다."""
    if TARGET_COLUMN not in frame.columns:
        collector.add(IssueLevel.BLOCK, TARGET_COLUMN, "실제 연체 여부 컬럼이 없음")
        return

    values = set(frame[TARGET_COLUMN].dropna().unique())
    if not values:
        collector.add(IssueLevel.BLOCK, TARGET_COLUMN, "실제 연체 여부가 모두 결측")
    elif not values.issubset({0, 1}):
        collector.add(
            IssueLevel.BLOCK,
            TARGET_COLUMN,
            f"0/1 외의 값이 있음: {sorted(values, key=str)[:5]}",
        )


def _check_protected_column(
    frame: pd.DataFrame, column: str, collector: _IssueCollector
) -> None:
    """보호속성 컬럼이 집단 비교가 가능한 상태인지 확인한다."""
    if column not in frame.columns:
        collector.add(IssueLevel.BLOCK, column, "보호속성 컬럼이 없어 집단 비교 불가")
        return

    series = frame[column].dropna()
    if series.empty:
        collector.add(IssueLevel.BLOCK, column, "값이 모두 결측")
        return

    if series.nunique() < 2:
        collector.add(IssueLevel.BLOCK, column, "집단이 하나뿐이라 집단 간 비교 불가")
        return

    small_groups = [str(group) for group, n in series.value_counts().items() if n < MIN_GROUP_SIZE]
    if small_groups:
        collector.add(
            IssueLevel.WARN,
            column,
            f"표본 {MIN_GROUP_SIZE}건 미만 집단: {small_groups[:6]}",
        )


def _check_feature_coverage(
    frame: pd.DataFrame, schema: ModelSchema, collector: _IssueCollector, item: str
) -> bool:
    """감사 데이터가 모델 입력 피처를 모두 갖고 있는지 확인한다."""
    missing = [name for name in schema.feature_names if name not in frame.columns]
    if missing:
        collector.add(
            IssueLevel.BLOCK,
            item,
            f"모델 피처 {len(missing)}개가 데이터에 없음: {missing[:10]}",
        )
        return False

    empty_features = [name for name in schema.feature_names if frame[name].isna().all()]
    if empty_features:
        collector.add(
            IssueLevel.WARN,
            item,
            f"값이 전부 결측인 피처: {empty_features[:10]}",
        )
    return True


def _check_categorical_cardinality(
    frame: pd.DataFrame, schema: ModelSchema, collector: _IssueCollector, item: str
) -> None:
    """범주형 피처의 고유값 개수가 학습 당시 하한을 채우는지 확인한다.

    감사 데이터에 학습 때 있던 범주값이 빠져 있으면 pandas 가 코드를 다르게
    매겨 예측이 조용히 틀어진다. 개수가 부족하면 그런 위험을 알리는 BLOCK 이다.
    (개수가 같아도 값 구성이 다르면 못 잡지만, 라벨이 모델에 없어 그 이상은
    확인할 수 없다.)
    """
    for feature in schema.categorical_features:
        expected = schema.min_category_counts.get(feature)
        if expected is None or feature not in frame.columns:
            continue
        actual = frame[feature].dropna().nunique()
        if actual < expected:
            collector.add(
                IssueLevel.BLOCK,
                item,
                f"범주형 '{feature}' 고유값 {actual}개 < 학습 당시 최소 {expected}개 "
                "— 코드 정합이 깨져 예측이 틀어질 수 있음",
            )


def _check_protected_in_model(
    schema: ModelSchema, protected_attributes: list[str], collector: _IssueCollector
) -> dict[str, bool]:
    """보호속성이 모델 입력에 직접 포함됐는지 확인한다.

    포함되어 있어도 감사는 진행할 수 있으나, 보호속성을 직접 학습에 쓴 것이므로
    검토가 필요하다는 뜻에서 WARN 으로 남긴다.
    """
    feature_set = set(schema.feature_names)
    flags: dict[str, bool] = {}

    for attribute in protected_attributes:
        candidates = PROTECTED_SOURCE_COLUMNS.get(attribute, [attribute])
        used = [column for column in candidates if column in feature_set]
        flags[attribute] = bool(used)
        if used:
            collector.add(
                IssueLevel.WARN,
                attribute,
                f"보호속성이 모델 입력에 직접 포함됨: {used}",
            )
        else:
            collector.add(IssueLevel.INFO, attribute, "보호속성이 모델 입력에서 제외됨")

    return flags


def _validate_optional_valid_dataset(
    valid_path: Path, schema: ModelSchema, collector: _IssueCollector
) -> bool:
    """`valid_processed.csv` 를 검증한다.

    선택 파일이므로 문제가 있어도 감사를 막지 않고, 확률 보정·임계값 산출에만
    쓸 수 없다고 표시한다.
    """
    item = valid_path.name
    try:
        frame = pd.read_csv(valid_path)
    except (OSError, ValueError) as exc:
        collector.add(IssueLevel.WARN, item, f"로딩 실패 — 확률 보정에 사용하지 않음: {exc}")
        return False

    if frame.empty:
        collector.add(IssueLevel.WARN, item, "데이터가 비어 있어 확률 보정에 사용하지 않음")
        return False

    missing = [name for name in schema.feature_names if name not in frame.columns]
    if missing:
        collector.add(
            IssueLevel.WARN,
            item,
            f"모델 피처 {len(missing)}개 누락 — 확률 보정에 사용하지 않음: {missing[:10]}",
        )
        return False

    if TARGET_COLUMN not in frame.columns:
        collector.add(
            IssueLevel.WARN,
            item,
            f"{TARGET_COLUMN} 컬럼이 없어 확률 보정에 사용하지 않음",
        )
        return False

    collector.add(IssueLevel.INFO, item, f"검증 통과 — {len(frame):,}행, 확률 보정에 사용 가능")
    return True


def validate_audit_inputs(
    model_path: Path,
    audit_path: Path,
    valid_path: Path | None = None,
    protected_attributes: list[str] | None = None,
) -> ValidationResult:
    """감사 입력 파일을 검증한다.

    `model_path` 와 `audit_path` 는 필수이고, `valid_path` 는 전달된 경우에만
    검증한다. BLOCK 이슈가 하나도 없으면 `passed` 가 True 이며, 이때 반환된
    모델 스키마를 이후 위험점수·공정성 지표 단계가 그대로 사용한다.

    `protected_attributes` 는 이번 감사에서 실제로 고른 민감변수 목록이다(성별·연령대뿐
    아니라 인종 등 새 모델에 맞춰 추가된 것도 포함) — 안 주면 기존 기본값(성별·연령대)만 본다.
    """
    protected_attributes = protected_attributes or [GENDER_COLUMN, AGE_GROUP_COLUMN]
    collector = _IssueCollector()

    if not model_path.exists():
        collector.add(IssueLevel.BLOCK, model_path.name, "모델 파일을 찾을 수 없음")
        return ValidationResult(passed=False, issues=collector.issues)

    try:
        schema = load_model_schema(model_path)
    except (xgb.core.XGBoostError, json.JSONDecodeError, KeyError, OSError) as exc:
        collector.add(IssueLevel.BLOCK, model_path.name, f"XGBoost 모델을 로드할 수 없음: {exc}")
        return ValidationResult(passed=False, issues=collector.issues)

    if not schema.feature_names:
        collector.add(IssueLevel.BLOCK, model_path.name, "모델에서 입력 피처 목록을 읽을 수 없음")
        return ValidationResult(passed=False, issues=collector.issues)

    collector.add(
        IssueLevel.INFO,
        model_path.name,
        f"모델 로드 완료 — 피처 {schema.n_features}개 (범주형 {len(schema.categorical_features)}개)",
    )

    if not audit_path.exists():
        collector.add(IssueLevel.BLOCK, audit_path.name, "감사 데이터 파일을 찾을 수 없음")
        return ValidationResult(passed=False, issues=collector.issues, model_schema_info=schema)

    try:
        audit_frame = pd.read_csv(audit_path)
    except (OSError, ValueError) as exc:
        collector.add(IssueLevel.BLOCK, audit_path.name, f"감사 데이터를 읽을 수 없음: {exc}")
        return ValidationResult(passed=False, issues=collector.issues, model_schema_info=schema)

    if audit_frame.empty:
        collector.add(IssueLevel.BLOCK, audit_path.name, "감사 데이터가 비어 있음")
        return ValidationResult(passed=False, issues=collector.issues, model_schema_info=schema)

    features_present = _check_feature_coverage(audit_frame, schema, collector, audit_path.name)
    if features_present:
        _check_categorical_cardinality(audit_frame, schema, collector, audit_path.name)
    _check_target(audit_frame, collector)
    for column in protected_attributes:
        _check_protected_column(audit_frame, column, collector)

    protected_in_model = _check_protected_in_model(schema, protected_attributes, collector)

    valid_usable = False
    if valid_path is not None:
        if valid_path.exists():
            valid_usable = _validate_optional_valid_dataset(valid_path, schema, collector)
        else:
            collector.add(
                IssueLevel.WARN, valid_path.name, "선택 파일을 찾을 수 없어 확률 보정을 건너뜀"
            )

    dataset_info = AuditDatasetInfo(
        n_rows=len(audit_frame),
        n_columns=len(audit_frame.columns),
        target_column=TARGET_COLUMN,
        protected_columns=[
            column
            for column in protected_attributes
            if column in audit_frame.columns
        ],
    )

    return ValidationResult(
        passed=not collector.blocked,
        issues=collector.issues,
        model_schema_info=schema,
        audit_dataset=dataset_info,
        valid_dataset_usable=valid_usable,
        protected_in_model=protected_in_model,
    )
