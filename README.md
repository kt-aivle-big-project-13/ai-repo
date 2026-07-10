# ai-repo
신용 평가 AI 규제준수 자동감사 플랫폼​ (AI 레포)

## FastAPI 개발 환경 설정

### 1. 프로젝트 클론

```bash
git clone <repository-url>
cd ai-repo
```

원하는 브랜치로 이동합니다.

```bash
git switch <브랜치>
```

---

### 2. Python 버전 확인

본 프로젝트는 **Python 3.13.x** 환경을 기준으로 개발합니다.

```bash
python3.13 --version
```

정상적으로 설치되어 있다면 다음과 같이 출력됩니다.

```text
Python 3.13.x
```

Python 3.13이 설치되어 있지 않은 경우 macOS에서는 Homebrew를 이용해 설치할 수 있습니다.

```bash
brew install python@3.13
```

설치 후 다시 버전을 확인합니다.

```bash
python3.13 --version
```

---

### 3. 가상환경 생성

프로젝트 루트 디렉터리에서 Python 3.13 기반 가상환경을 생성합니다.

```bash
python3.13 -m venv .venv
```

가상환경은 프로젝트별로 Python 패키지와 버전을 분리하여 다른 프로젝트와의 충돌을 방지하기 위해 사용합니다.

---

### 4. 가상환경 활성화

#### macOS / Linux

```bash
source .venv/bin/activate
```

#### Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
```

#### Windows CMD

```cmd
.venv\Scripts\activate
```

가상환경이 정상적으로 활성화되면 터미널 앞에 `(.venv)`가 표시됩니다.

```text
(.venv) user@computer ai-repo %
```

가상환경의 Python 버전을 확인합니다.

```bash
python3 --version
```

다음과 같이 Python 3.13.x가 출력되어야 합니다.

```text
Python 3.13.x
```

---

### 5. 의존성 설치

가상환경이 활성화된 상태에서 pip를 업데이트합니다.

```bash
python3 -m pip install --upgrade pip
```

`requirements.txt`에 정의된 패키지를 설치합니다.

```bash
python3 -m pip install -r requirements.txt
```

FastAPI 설치 여부를 확인합니다.

```bash
python3 -m pip show fastapi
```

Uvicorn 설치 여부도 확인할 수 있습니다.

```bash
python3 -m pip show uvicorn
```

---

### 6. FastAPI 서버 실행

프로젝트 루트 디렉터리에서 다음 명령어를 실행합니다.

```bash
python3 -m uvicorn app.main:app --reload
```

각 항목의 의미는 다음과 같습니다.

```text
app.main
└── app/main.py 파일

app
└── main.py에 선언된 FastAPI 객체

--reload
└── 코드 변경 시 개발 서버 자동 재시작
```

서버가 정상적으로 실행되면 다음 주소로 접속할 수 있습니다.

```text
API 서버
http://127.0.0.1:8000

Swagger UI : API 직접 실행 및 테스트
http://127.0.0.1:8000/docs
```

---

### 7. 가상환경 종료

개발을 종료할 때 다음 명령어를 실행합니다.

```bash
deactivate
```

다음에 프로젝트를 다시 실행할 때는 가상환경을 새로 만들 필요 없이 활성화만 하면 됩니다.

#### macOS / Linux

```bash
source .venv/bin/activate
```

#### Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
```

활성화 후 서버를 다시 실행합니다.

```bash
python3 -m uvicorn app.main:app --reload
```

---

## 패키지 추가 방법

새로운 Python 패키지가 필요한 경우 가상환경이 활성화된 상태에서 설치합니다.

```bash
python3 -m pip install <package-name>
```

패키지를 추가하거나 버전을 변경한 경우 `requirements.txt`를 갱신합니다.

```bash
python3 -m pip freeze > requirements.txt
```

변경된 `requirements.txt`는 Git에 함께 커밋합니다.

```bash
git add requirements.txt
git commit -m "chore: update Python dependencies"
```

다른 팀원은 변경 사항을 받은 뒤 의존성을 다시 설치합니다.

```bash
python3 -m pip install -r requirements.txt
```

---

## Git 제외 파일

가상환경과 환경변수 파일은 GitHub에 업로드하지 않습니다.

`.gitignore` 파일에 다음 내용을 추가합니다.

```gitignore
.venv/
__pycache__/
*.py[cod]
.DS_Store
```

* `.venv/`: 로컬 Python 가상환경
* `__pycache__/`: Python 실행 시 생성되는 캐시
* `*.py[cod]`: Python 실행 시 생성되는 컴파일 및 캐시 파일
* `.DS_Store`: macOS에서 자동 생성되는 파일

가상환경 폴더 자체는 공유하지 않고, `requirements.txt`를 통해 동일한 패키지 환경을 구성합니다.
