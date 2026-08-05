# ai-repo

신용평가 AI 규제준수 자동감사 플랫폼 (AI 레포)

FastAPI로 만든 **AI 분석·리포트 서버**입니다. 신용평가 모델의 공정성(Fairness)·설명가능성(Explainability)을 분석하고, 그 결과를 근거로 규제준수 리포트를 자동 생성하며, 감사 결과에 대한 질의응답(RAG 챗봇)을 제공합니다.

> `backend-repo`(Spring)가 유일한 호출 주체이며, `frontend-repo`는 이 서버와 직접 통신하지 않습니다.

<br>

## 목차

1. [기술 스택](#1-기술-스택)
2. [아키텍처 — 다른 서버와의 관계](#2-아키텍처--다른-서버와의-관계)
3. [이 서버의 역할](#3-이-서버의-역할)
4. [패키지 구조](#4-패키지-구조)
5. [주요 기능 / API 엔드포인트](#5-주요-기능--api-엔드포인트)
6. [어필 포인트](#6-어필-포인트)
7. [FastAPI 개발 환경 설정](#7-fastapi-개발-환경-설정)
8. [테스트 방법](#8-테스트-방법)
9. [패키지 추가 방법](#9-패키지-추가-방법)
10. [Git 제외 파일](#10-git-제외-파일)

<br>

---

## 1. 기술 스택

| 구분 | 기술 |
|---|---|
| 언어 / 런타임 | Python 3.13 |
| 웹 프레임워크 | FastAPI 0.139, Uvicorn (ASGI, `--reload`) |
| 데이터 검증 / 설정 | Pydantic v2, pydantic-settings (`.env` 로드) |
| HTTP 클라이언트 | httpx (OpenAI 호환 LLM 호출을 얇게 래핑) |
| LLM 연동 | OpenAI 호환 Chat Completions API — 리포트 서술, 챗봇 답변, 임베딩 생성 |
| 객체 스토리지 | boto3 — 운영은 AWS S3, 로컬 개발은 MinIO (환경변수로 전환) |
| 머신러닝 | XGBoost(위험점수) · Fairlearn(공정성 지표) · SHAP/LIME(설명가능성) · scikit-learn · numpy · pandas · scipy |
| 리포트 생성 | Jinja2(HTML 템플릿) → Playwright/Chromium(HTML→PDF) · python-docx(HTML→DOCX) · matplotlib(그림) |
| 테스트 | pytest, FastAPI `TestClient`, `httpx.MockTransport`(LLM 모킹), `monkeypatch`(S3 모킹) |
| 연동 대상 | `backend-repo` (Spring Boot, 유일한 API 호출 주체) |

<br>

---

## 2. 아키텍처 — 다른 서버와의 관계

<img width="1705" height="922" alt="image" src="https://github.com/user-attachments/assets/4bbd0a96-0287-4e46-90e7-6f1574547fc7" />

**요청 흐름은 한 방향입니다: `프론트엔드 → 백엔드(Spring) → AI 서버(이 레포)`.** 프론트엔드는 이 서버를 알지 못하고, 항상 백엔드를 거쳐서만 간접적으로 결과를 받습니다.

### 백엔드와의 통신

- 백엔드는 기능별 클라이언트(`FastApiChatAnswerClient`, `FastApiShapAnalysisClient`, `FastApiFairnessAnalysisClient`, `FastApiEmbeddingClient`, `FastApi*ReportClient` 등)로 이 서버의 `/internal/v1/...` 엔드포인트만 호출합니다.
- 연결 정보는 `AI_SERVER_BASE_URL`(기본 `http://localhost:8000`)로 설정하고, 커넥션 타임아웃 3초 · 읽기 타임아웃 120초(리포트 생성은 600초)를 둡니다.
- JDK `HttpClient`는 기본이 HTTP/2라서 평문 연결에서 h2c 업그레이드를 시도하는데, Uvicorn이 h2c를 지원하지 않아 요청이 깨집니다. 그래서 백엔드가 **HTTP/1.1로 고정**해서 통신합니다.

### 에러 응답을 백엔드가 해석하는 방식

| 이 서버의 응답 | 백엔드의 처리 |
|---|---|
| non-2xx 전부 | `AiServerErrorException` (`EA001`, 502) |
| 커넥션 / 읽기 타임아웃 | `AiServerTimeoutException` (`EA002`, 504) |

이 서버는 `422`/`500`/`502`/`503`처럼 상태 코드를 세분화해서 응답하지만, 백엔드 입장에서는 "성공(2xx)이냐 아니냐"만 중요합니다. 세분화된 코드는 이 서버 자체의 관측·디버깅용입니다.

### 이 서버가 의존하는 외부 시스템

| 시스템 | 용도 | 설정 |
|---|---|---|
| OpenAI 호환 LLM | 리포트 서술 문단, 챗봇 답변, 법령 조문 임베딩 생성 | `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` — `app/services/llm.py`에서 단일 창구로 통일 |
| S3 호환 객체 스토리지 | 감사 대상 모델(`credit_model.json`)·감사 데이터셋, 생성된 리포트(HTML/PDF/DOCX) 저장 | `MINIO_ENDPOINT` 유무로 로컬(MinIO)·운영(AWS S3) 전환 — `app/services/storage.py` |

<br>

---

## 3. 이 서버의 역할

> **한 문장으로:** 업로드된 신용평가 모델·데이터를 분석해서 공정성/설명가능성 수치를 계산하고, 그 수치를 근거로 규제준수 리포트를 자동 작성해주는 AI 엔진입니다.

| 역할 | 내용 |
|---|---|
| 공정성(Fairness) 감사 | Fairlearn 기반으로 보호속성별 공정성 지표 7종 계산 |
| 설명가능성(Explainability) 분석 | SHAP·LIME으로 모델 판단 근거, 민감변수 기여도, 설명 안정성/충실성 계산 |
| 위험점수 · 승인 임계값 산출 | XGBoost로 고객별 연체 위험확률을 계산하고 승인/거절 기준을 산정 |
| 리포트 자동 생성 (5종) | 편향진단 · 규제준수 판정서 · 개선 권고 가이드 · 고영향 AI 사전진단 · 설명가능성 리포트. 표·수치·그림은 코드가 채우고 서술 문단만 LLM이 생성 |
| 감사 질의응답 (RAG 챗봇) | 백엔드가 조립해 보낸 감사 근거만으로 답변하고, 실제로 인용된 근거만 응답에 포함 |
| 법령 조문 임베딩 생성 | 법령 조문 요약을 벡터로 변환해 백엔드의 유사도 검색에 사용 |

<br>

---

## 4. 패키지 구조

```text
app/
├── main.py                # FastAPI 엔트리포인트 — .env 로드 + 라우터 등록
├── core/
│   └── config.py          # pydantic-settings 기반 설정 (.env / 환경변수 통합)
├── api/                    # 라우터 계층 — 요청 검증 · 서비스 호출 · 예외→HTTP 상태 매핑
│   ├── bias/               # POST /internal/v1/reports/bias
│   ├── chat/                # POST /internal/v1/chat/answers, /internal/v1/embedding/generate
│   ├── compliance/           # POST /internal/v1/reports/compliance
│   ├── explainability/        # POST /internal/v1/shap/analyze
│   ├── fairness/               # POST /api/fairness/audits, /internal/v1/fairness/analyze
│   ├── highimpact/              # POST /internal/v1/reports/high-impact-assessment
│   ├── improvement/              # POST /internal/v1/reports/improvement
│   └── report.py                 # POST /internal/v1/reports/explainability
├── schemas/                # pydantic 요청/응답 모델 — api/ 하위 구조와 1:1 대응
├── services/               # 실제 로직 — 계산 · LLM 서술 · 문서 변환 · 스토리지 입출력
│   ├── llm.py               # OpenAI 호환 Chat Completions 클라이언트 (단일 창구)
│   ├── storage.py            # S3 / MinIO 다운로드·업로드
│   ├── validation.py          # 모델·데이터 검증
│   ├── scoring.py              # XGBoost 위험점수 · 승인 임계값 산출
│   ├── audit.py                 # 검증 → 위험점수 → 공정성 감사 실행 파이프라인
│   ├── docx_common.py            # python-docx 기반 공통 문서 생성 유틸
│   └── (bias / chat / compliance / explainability / fairness / highimpact / improvement / report)/
│                                  # 도메인별 분석 로직 + report_*.py(조판) + *_prompts.py(LLM 프롬프트) + *_docx.py(워드 변환)
└── templates/              # Jinja2 HTML 리포트 템플릿(.html.j2) — Playwright로 PDF 변환

tests/                      # pytest — api / services / schemas 계층별 단위 테스트 (25개 파일)
```

> `api/` → `schemas/` → `services/` 3계층이 기능별로 동일한 하위 폴더 이름(bias, chat, compliance, explainability, fairness, highimpact, improvement, report)을 공유합니다. 기능 하나를 찾을 때는 세 폴더에서 같은 이름을 따라가면 됩니다.

<br>

---

## 5. 주요 기능 / API 엔드포인트

### 분석 API (S3 기반 계산)

| 기능 | 엔드포인트 | 설명 | 백엔드 호출부 |
|---|---|---|---|
| 공정성 분석 | `POST /internal/v1/fairness/analyze` | S3의 모델·감사데이터를 내려받아 Fairlearn 기반 공정성 지표 7종 계산 | `FastApiFairnessAnalysisClient` |
| 설명가능성(SHAP) 분석 | `POST /internal/v1/shap/analyze` | SHAP, 민감변수 기여비율, 전역 설명 안정성/충실성, LIME 계산 | `FastApiShapAnalysisClient` |

<br>

### 리포트 생성 API

| 기능 | 엔드포인트 | 설명 | 백엔드 호출부 |
|---|---|---|---|
| 설명가능성 리포트 | `POST /internal/v1/reports/explainability` | SHAP 분석 결과로 HTML→PDF 리포트 생성 후 S3 업로드 | `FastApiExplainabilityReportClient` |
| 편향진단 리포트 | `POST /internal/v1/reports/bias` | 공정성 감사 결과로 HTML→PDF/DOCX 리포트 생성 (수치·표·그림은 코드, 서술만 LLM) | `FastApiBiasReportClient` |
| 규제준수 판정서 | `POST /internal/v1/reports/compliance` | 백엔드가 이미 내린 조항별 준수/미준수 판정을 집계·서술 (판정 로직 없음) | `FastApiComplianceReportClient` |
| 개선 권고 가이드 | `POST /internal/v1/reports/improvement` | 백엔드가 넘긴 조치 필요 항목의 우선순위를 코드가 매기고 LLM이 서술 | `FastApiImprovementGuideClient` |
| 고영향 AI 사전진단 | `POST /internal/v1/reports/high-impact-assessment` | 고영향 AI 여부 사전진단 보고서 생성 | `FastApiHighImpactReportClient` |

<br>

### 챗봇 · 임베딩 API

| 기능 | 엔드포인트 | 설명 | 백엔드 호출부 |
|---|---|---|---|
| 감사 질의응답 | `POST /internal/v1/chat/answers` | 백엔드가 조립한 감사 근거로 LLM이 답변, 실제 인용된 근거만 반환. `grounding_status`는 코드가 인용 표기·수치 대조로 재검증 | `FastApiChatAnswerClient` |
| 법령 조문 임베딩 | `POST /internal/v1/embedding/generate` | 법령 조문 요약 텍스트 → 벡터 임베딩 | `FastApiEmbeddingClient` (`LawArticleEmbeddingService`) |

<br>

### 기타

| 기능 | 엔드포인트 | 설명 | 백엔드 호출부 |
|---|---|---|---|
| 공정성 감사 (파일 업로드) | `POST /api/fairness/audits` | 모델·데이터 파일을 직접 업로드해 감사 실행. AI 팀 원본 계약 경로였으나 현재 백엔드는 `/internal/v1/fairness/analyze`를 사용 — 로컬 테스트·Swagger UI 확인용 | (백엔드 미사용) |
| 헬스체크 | `GET /` | `{"message": "Hello World"}` | — |

<br>

---

## 6. 어필 포인트

**1) LLM은 "서술"만, "판정"은 항상 코드가 합니다.**
공정성 수치, 승인/거절 임계값, 조항별 준수 여부, 개선 항목 우선순위는 전부 코드가 계산·결정하고 LLM은 이미 정해진 값을 문장으로 풀어쓰는 역할만 맡습니다. 규제준수 감사라는 도메인 특성상 LLM 환각(hallucination)이 판정 결과에 영향을 줄 수 없도록 책임을 구조적으로 분리했습니다.

**2) 챗봇 답변의 근거를 LLM의 자기 진술로 믿지 않습니다.**
`grounding_status`는 LLM이 "근거 있음"이라고 말한 걸 그대로 신뢰하지 않고, 실제 인용 표기와 수치를 코드가 대조해서 판정합니다.

**3) LLM 호출을 한 곳(`app/services/llm.py`)으로 통일했습니다.**
OpenAI Chat Completions 규격을 httpx로 얇게 감싸 OpenAI든 호환 엔드포인트든 `.env` 설정만으로 전환할 수 있고, 테스트 시엔 `httpx.MockTransport`를 주입해 실제 API 호출 없이 검증합니다.

**4) 로컬(MinIO)·운영(AWS S3)을 코드 변경 없이 전환합니다.**
`MINIO_ENDPOINT` 환경변수 유무만으로 같은 `storage.py`가 두 스토리지를 모두 처리합니다.

**5) 신용평가 도메인에 특화된 실제 ML 분석 파이프라인을 갖췄습니다.**
XGBoost 위험점수, Fairlearn 공정성 지표, SHAP+LIME 설명가능성 분석을 실제로 수행하며, "그럴듯한 텍스트"가 아니라 계산된 수치를 기반으로 리포트를 만듭니다.

**6) 5종 리포트를 HTML → PDF / DOCX로 자동 산출합니다.**
Jinja2로 조판한 HTML을 Playwright로 PDF, python-docx로 DOCX까지 변환해 S3에 업로드하고 Key만 반환하므로, 백엔드·프론트는 파일을 직접 다루지 않아도 됩니다.

**7) 백엔드와의 계약을 컨벤션으로 통일했습니다.**
내부 전용 엔드포인트는 `/internal/v1/{feature}/{action}` 패턴으로 통일하고, 상태 코드(422/500/502/503)를 의미별로 분리해 백엔드가 재시도·타임아웃 처리를 명확히 할 수 있게 설계했습니다. HTTP/1.1 고정처럼 Uvicorn 연동 과정에서 실제로 부딪힌 이슈까지 문서화되어 있습니다.

**8) 외부 의존성 없이 테스트를 격리했습니다.**
LLM은 `httpx.MockTransport`, S3는 `monkeypatch`로 페이크 클라이언트를 주입해 외부 네트워크·비용 없이 25개 테스트 파일 전체를 CI에서 반복 실행할 수 있습니다.

<br>

---

## 7. FastAPI 개발 환경 설정

### 최초 1회

```powershell
cd ai-repo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 매번 새 터미널을 열 때마다

```powershell
cd ai-repo  # 이미 ai-repo 폴더 안에 있으면 생략
.\.venv\Scripts\Activate.ps1

python -m uvicorn app.main:app --reload
```

서버가 정상적으로 실행되면 다음 주소로 접속할 수 있습니다.

```text
API 서버
http://127.0.0.1:8000

Swagger UI
http://127.0.0.1:8000/docs
```

서버를 종료하려면 터미널에서 `Ctrl + C`를 누릅니다.

<details>
<summary>세부 절차 (Python 버전 확인, 가상환경 생성/활성화, 실행 정책 오류 등)</summary>

#### Python 버전 확인

```powershell
python --version
```

만약 다음과 같은 에러 메세지가 뜬다면
"Python was not found; run without arguments to install from the Microsoft Store, or disable this shortcut from Settings > Apps > Advanced app settings > App execution aliases."

아래 내용을 실행해본다.

1. Windows 설정 → 앱 → 고급 앱 설정 → 앱 실행 별칭
2. 목록에서 "App Installer python.exe", "App Installer python3.exe" 항목을 찾아 꺼짐(Off) 으로 전환
3. VS Code 완전히 종료 후 재시작

다음과 같이 Python 3.13.x가 출력되어야 합니다.

```text
Python 3.13.x
```

`python` 명령어를 사용할 수 없고 Python Launcher가 설치된 경우 다음 명령어로 확인합니다.

```powershell
py -3.13 --version
```

#### 가상환경 생성

프로젝트 루트 디렉터리에서 Python 가상환경을 생성합니다.

```powershell
python -m venv .venv
```

`python` 명령어 대신 Python Launcher를 사용하는 경우 다음 명령어를 실행합니다.

```powershell
py -3.13 -m venv .venv
```

가상환경은 프로젝트별로 Python 패키지와 버전을 분리하여 다른 프로젝트와의 충돌을 방지합니다.

#### 가상환경 활성화

```powershell
.\.venv\Scripts\Activate.ps1
```

정상적으로 활성화되면 터미널 앞에 `(.venv)`가 표시됩니다.

가상환경의 Python 버전을 확인합니다.

```powershell
python --version
```

##### PowerShell 실행 정책 오류가 발생하는 경우

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

실행 정책 변경 후 가상환경을 다시 활성화합니다.

```powershell
.\.venv\Scripts\Activate.ps1
```

#### 의존성 설치

가상환경이 활성화된 상태에서 pip를 업데이트합니다.

```powershell
python -m pip install --upgrade pip
```

`requirements.txt`에 정의된 패키지를 설치합니다.

```powershell
python -m pip install -r requirements.txt
```

#### 환경변수(.env) 설정

`.env.example`을 복사해 `.env`를 만들고 값을 채웁니다. (S3/MinIO 접속 정보, `OPENAI_API_KEY` 등이 없으면 관련 엔드포인트가 503으로 응답합니다.)

```powershell
Copy-Item .env.example .env
```

#### 가상환경 종료

```powershell
deactivate
```

다음에 프로젝트를 다시 실행할 때는 가상환경을 새로 생성할 필요가 없습니다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

</details>

<br>

---

## 8. 테스트 방법

가상환경 활성화 후 프로젝트 루트에서 실행합니다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest
```

특정 파일·테스트만 실행:

```powershell
python -m pytest tests/test_chat.py
python -m pytest tests/test_fairness.py -k "threshold"
```

- 외부 API 키·S3 접속 정보 없이도 전체 테스트가 통과합니다. LLM 호출은 `httpx.MockTransport`로, S3 호출은 `monkeypatch`로 페이크 클라이언트를 주입해 검증하기 때문입니다.
- `tests/`는 계층별로 나뉘어 있습니다.
  - `test_*_api.py` — FastAPI `TestClient`로 라우터의 상태 코드·예외 매핑 검증
  - `test_*.py`(서비스명) — 서비스 로직 단위 테스트 (계산 정확성, LLM 프롬프트 조립, 문서 조판 등)
  - `test_*_schema.py`, `test_*_docx.py`, `test_*_pdf.py` — pydantic 스키마 검증, DOCX/PDF 산출물 검증
- Swagger UI(`http://127.0.0.1:8000/docs`)에서 서버를 띄운 채로 각 엔드포인트를 직접 호출해 볼 수도 있습니다. 단, `/internal/v1/*` 계열은 S3에 실제 모델·데이터가 있어야 하고 LLM API 키가 필요하므로, 로컬 수동 확인용으로는 파일 업로드 방식인 `POST /api/fairness/audits`가 가장 간단합니다.
