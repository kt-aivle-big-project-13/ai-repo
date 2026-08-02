"""리포트 섹션별 서술 스키마.

리포트 생성 시 이미 만드는 LLM 서술을 응답에 함께 담아, 백엔드가 저장해 두고 감사 질의
챗봇의 근거로 쓸 수 있게 한다. 파일을 다시 내려받아 파싱하지 않기 위한 것이라 추가 LLM
호출은 없다.

목차 제목까지 함께 반환한다. 키만 주면 백엔드가 `metric_results` → "5. 공정성 지표 결과"
같은 매핑을 따로 들고 있어야 하는데, 목차는 이 저장소가 소유하는 정보다.

기획은 model-repo `docs/04-chatbot-plan.md` 참고.
"""

from pydantic import BaseModel, Field


class ReportNarrative(BaseModel):
    """리포트 한 섹션의 서술."""

    section_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str


def build_narratives(
    narratives: dict[str, str],
    titles: dict[str, str],
) -> list[ReportNarrative]:
    """서술 dict 를 목차 순서대로 정리한다.

    `titles` 에 정의된 순서를 따르고, 생성되지 않은 섹션은 건너뛴다.
    """

    return [
        ReportNarrative(
            section_key=key,
            title=title,
            content=narratives[key],
        )
        for key, title in titles.items()
        if narratives.get(key)
    ]
