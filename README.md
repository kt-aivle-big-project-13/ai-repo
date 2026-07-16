# ai-repo

신용평가 AI 규제준수 자동감사 플랫폼 (AI 레포)

## FastAPI 개발 환경 설정

본 문서는 **Windows PowerShell 환경**을 기준으로 작성되었습니다.

---

### 1. 프로젝트 클론

```powershell
git clone <repository-url>
cd ai-repo
```

원하는 브랜치로 이동합니다.

```powershell
git switch <브랜치명>
```

---

### 2. Python 버전 확인

```bash
python --version
```

다음과 같이 Python 3.13.x가 출력되어야 합니다.

```text
Python 3.13.x
```

`python` 명령어를 사용할 수 없고 Python Launcher가 설치된 경우 다음 명령어로 확인합니다.

```powershell
py -3.13 --version
```

---

### 4. 가상환경 생성

프로젝트 루트 디렉터리에서 Python 가상환경을 생성합니다.

```powershell
python -m venv .venv
```

`python` 명령어 대신 Python Launcher를 사용하는 경우 다음 명령어를 실행합니다.

```powershell
py -3.13 -m venv .venv
```

가상환경은 프로젝트별로 Python 패키지와 버전을 분리하여 다른 프로젝트와의 충돌을 방지합니다.

---

### 5. 가상환경 활성화

```powershell
.\.venv\Scripts\Activate.ps1
```

정상적으로 활성화되면 터미널 앞에 `(.venv)`가 표시됩니다.

가상환경의 Python 버전을 확인합니다.

```powershell
python --version
```

#### PowerShell 실행 정책 오류가 발생하는 경우

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

실행 정책 변경 후 가상환경을 다시 활성화합니다.

```powershell
.\.venv\Scripts\Activate.ps1
```

---

### 6. 의존성 설치

가상환경이 활성화된 상태에서 pip를 업데이트합니다.

```powershell
python -m pip install --upgrade pip
```

`requirements.txt`에 정의된 패키지를 설치합니다.

```powershell
python -m pip install -r requirements.txt
```

---

### 7. FastAPI 서버 실행

프로젝트 루트 디렉터리에서 다음 명령어를 실행합니다.

```powershell
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

---

### 8. 가상환경 종료

```powershell
deactivate
```

다음에 프로젝트를 다시 실행할 때는 가상환경을 새로 생성할 필요가 없습니다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

---

## 패키지 추가 방법

새로운 Python 패키지가 필요한 경우 가상환경이 활성화된 상태에서 설치합니다.

```powershell
python -m pip install <package-name>
```

패키지를 추가하거나 버전을 변경한 경우 `requirements.txt`를 갱신합니다.

```powershell
python -m pip freeze > requirements.txt
```

변경된 `requirements.txt`는 Git에 함께 커밋합니다.

```powershell
git add requirements.txt
git commit -m "chore: update Python dependencies"
```

다른 팀원은 변경 사항을 받은 뒤 의존성을 다시 설치합니다.

```powershell
python -m pip install -r requirements.txt
```

---

## Git 제외 파일

가상환경, 환경변수 및 Python 캐시 파일은 GitHub에 업로드하지 않습니다.

`.gitignore` 파일에 다음 내용을 추가합니다.

```gitignore
.venv/
__pycache__/
*.py[cod]
.env
.DS_Store
Thumbs.db
```

- `.venv/`: 로컬 Python 가상환경
- `__pycache__/`: Python 실행 시 생성되는 캐시 폴더
- `*.py[cod]`: Python 컴파일 및 캐시 파일
- `.env`: 환경변수 파일
- `.DS_Store`: macOS 시스템 파일
- `Thumbs.db`: Windows 썸네일 캐시 파일

가상환경 폴더 자체는 공유하지 않고, `requirements.txt`를 통해 동일한 패키지 환경을 구성합니다.