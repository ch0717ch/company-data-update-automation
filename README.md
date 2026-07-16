# 하이서울기업 로컬 데이터 관리자

하이서울기업의 기업정보를 검색해 PC의 SQLite 데이터베이스에 저장하고, 재검색 시 달라진 필드를 기록·표시하는 FastAPI 기반 로컬 웹 애플리케이션입니다.

확인된 원본 호출은 다음 두 ASP.NET WebMethod입니다.

- 목록 검색: `POST /Pages/AccountList.aspx/GetData`
- 기업 상세: `POST /Pages/AccountDetail.aspx/GetData`

상세 응답을 함께 사용하므로 업종, 대표자, 설립연도, 직원 수, 전화번호, 주소, 이메일을 모두 저장할 수 있습니다.

## 가장 간단한 실행 방법

1. 프로젝트 폴더의 `run.bat`을 더블클릭합니다.
2. 배치 파일이 Windows Python, 일반 Python, Codex 내장 Python 순서로 사용 가능한 Python 3.10 이상을 자동 탐색합니다.
3. 최초 실행 시 가상환경과 패키지를 자동 설치하고 `.env`를 생성합니다. 인터넷 속도에 따라 몇 분 걸릴 수 있습니다.
4. 브라우저에서 `http://127.0.0.1:8765`가 자동으로 열립니다.
5. 종료하려면 BAT 콘솔 창에서 `Ctrl+C`를 누릅니다.

Codex 내장 Python이 없는 다른 PC에서는 Python 3.10 이상을 먼저 설치하고, 설치 화면에서 `Add Python to PATH`를 선택하세요.

수동 실행이 필요하면 다음 명령을 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app
```

## 제공 기능

- 기업명으로 하이서울기업 원본 사이트 검색
- Excel 기업명 열을 붙여넣는 원클릭 대량추가
- 제공된 1,107개 고유 기업명 목록 바로 불러오기
- 전체 목록 1회 조회 후 `(주)·㈜·주식회사·공백` 정규화 매칭
- 1,000개 이상 상세정보를 백그라운드에서 수집하고 진행률 표시
- 미매칭 기업명 TXT 다운로드
- 목록 검색 후 기업별 상세정보 병렬 조회
- SQLite 저장 및 기존 값과 필드 단위 비교
- 신규/변경/기존 상태 표시와 변경 셀 강조
- 기업별 전체 변경 이력 조회
- 업종, 대표자, 주소, 설립연도, 직원 수, 상태 필터
- 기업명·대표자·전화·이메일·주소 통합검색
- 현재 필터의 전체 결과 Excel 다운로드
- 전체 결과 첫 시트는 기준 파일과 같은 `순번·기업명·업종·대표자·설립연도·직원수·전화번호·주소·이메일·홈페이지·사업영역` 순서로 생성
- 변경 이력이 있는 기업과 변경 내역 Excel 다운로드
- 기업 소개·상세 설명·제품/서비스를 포함한 전체 상세정보 Excel 다운로드
- SQLite DB 파일 다운로드 및 Windows 탐색기에서 DB 폴더 열기
- API 키, Cookie, 사용자 정의 헤더를 `.env`에서 관리

## 데이터 흐름

```text
브라우저 (localhost)
  → FastAPI
    → 하이서울기업 전체 목록 WebMethod (대량추가 시 1회)
      → 붙여넣은 기업명 정규화·매칭
        → 매칭 기업별 상세 WebMethod (동시 요청 제한)
        → SQLite 비교 저장 + 변경 이력
          → 화면 필터 / Excel 다운로드
```

## 주요 환경설정

`.env.example`을 복사한 `.env`는 Git에 포함되지 않습니다.

| 설정 | 기본값 | 설명 |
|---|---:|---|
| `APP_HOST` | `127.0.0.1` | 로컬 PC에서만 접근하도록 제한 |
| `APP_PORT` | `8765` | 웹 서버 포트 |
| `DATABASE_PATH` | `data/hiseoul_companies.db` | SQLite 파일 위치 |
| `SOURCE_COOKIE` | `lang=KOR` | 원본 호출에 전달할 Cookie |
| `SOURCE_API_KEY` | 빈 값 | 향후 API 키가 필요할 경우 입력 |
| `SOURCE_HEADERS_JSON` | `{}` | 추가 헤더 JSON 객체 |
| `SOURCE_MAX_CONCURRENCY` | `4` | 상세정보 동시 요청 수 |
| `SOURCE_MAX_DETAILS` | `100` | 한 번에 상세 조회할 최대 기업 수 |
| `SOURCE_BULK_MAX_DETAILS` | `2000` | 대량추가 한 번에 처리할 최대 기업 수 |
| `ALLOW_EMPTY_SOURCE_SEARCH` | `false` | 빈 검색어로 전체 기업 조회 허용 여부 |
| `SOURCE_VERIFY_TLS` | `true` | TLS 인증서 검증. 보안상 `true` 권장 |

원본 사이트가 비공개 Cookie를 요구하게 되면 브라우저 개발자 도구에서 확인한 값을 `.env`의 `SOURCE_COOKIE`에만 입력하세요. `.env`는 공유하거나 커밋하지 마세요.

## 데이터베이스와 변경 판정

- 기업의 원본 GUID를 기본키로 사용합니다.
- 첫 조회는 `신규`, 기존 값과 다른 필드가 있으면 `변경`, 차이가 없으면 `기존` 상태가 됩니다.
- 공백 종류와 줄바꿈 차이는 비교 시 정규화하여 불필요한 변경 기록을 줄였습니다.
- 상세 조회 일부가 실패하면 목록에 없는 전화번호·이메일·직원 수를 기존 데이터에서 지우지 않습니다.
- 변경 이력은 `company_changes`, 검색 실행 기록은 `search_runs` 테이블에 누적됩니다.

DB를 초기화하려면 서버 종료 후 `data/hiseoul_companies.db`와 같은 이름의 `-wal`, `-shm` 파일을 함께 삭제하세요. 중요한 데이터가 있으면 먼저 DB 파일을 백업하세요.

## API

서버 실행 후 `http://127.0.0.1:8765/docs`에서 전체 API 문서를 볼 수 있습니다.

주요 경로:

- `POST /api/source/search` 원본 검색 및 동기화
- `GET /api/bulk/default-names` 제공 기업명 목록
- `POST /api/bulk/start` 대량추가 작업 시작
- `GET /api/bulk/{job_id}` 대량추가 진행률·결과
- `GET /api/companies` 로컬 검색·필터
- `GET /api/companies/{source_id}` 기업 상세 및 이력
- `GET /api/stats` 통계
- `GET /api/export?scope=all` 전체 결과 Excel
- `GET /api/export?scope=changed` 변경 결과 Excel
- `GET /api/database/download` SQLite DB 다운로드
- `POST /api/database/open-folder` DB 폴더 열기

## 원클릭 대량추가 사용법

1. 화면의 `기업명 대량추가` 영역에서 `제공된 기업 목록 불러오기`를 누르거나 Excel의 기업명 열을 복사해 입력칸에 붙여넣습니다.
2. `원클릭 대량추가`를 누릅니다.
3. 원본 전체 목록을 한 번 불러온 뒤 기업명을 매칭하고 상세정보를 수집합니다. 1,000개 처리에는 네트워크 상태에 따라 수 분 이상 걸릴 수 있습니다.
4. 완료 후 `전체 상세정보 엑셀`을 누릅니다. 업종, 대표자, 설립연도, 직원 수, 전화, 주소, 이메일, 홈페이지, 사업영역, 기업 소개, 상세 설명, 제품·서비스가 포함됩니다.
5. 원본 사이트에 현재 존재하지 않거나 이름이 바뀐 기업은 `미매칭 목록 받기`로 확인할 수 있습니다.

앱은 인증 기능 없이 `127.0.0.1`에서만 실행됩니다. 같은 PC에서 DB 원본이 필요하면 `SQLite DB`, 저장 폴더가 필요하면 `DB 폴더` 버튼을 사용하세요.

## 기존 설치본 업데이트

1. 실행 중인 BAT 콘솔에서 `Ctrl+C`를 눌러 서버를 종료합니다.
2. 기존 폴더의 `data` 폴더를 별도 위치에 한 번 복사해 백업합니다.
3. 새 ZIP의 파일을 기존 프로그램 폴더에 덮어씁니다. 배포 ZIP에는 DB와 `.env`가 들어 있지 않으므로 기존 데이터와 설정은 유지됩니다.
4. `run.bat`을 다시 실행합니다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest
```

테스트는 신규 저장, 필드 변경 기록, 상세 조회 실패 시 기존 값 보존, Excel 시트/헤더/고정 행 생성, Excel 금지 제어문자 정제를 검증합니다.

## 실행 문제 해결

- `Could not create the virtual environment`가 표시되면 최신 `run.bat`을 사용하세요. 최신 배치 파일은 실패한 `.venv`를 다시 만들고 Codex 내장 Python도 자동으로 탐색합니다.
- 콘솔에 `Uvicorn running on http://127.0.0.1:8765`가 표시되면 서버가 정상 실행 중입니다. 브라우저가 자동으로 열리지 않으면 주소창에 `http://127.0.0.1:8765`를 직접 입력하세요.
- 최초 패키지 설치 중에는 창을 닫지 마세요. `[3/3] Starting server`가 나올 때까지 기다려야 합니다.
- 이전 버전에서 `전체 결과 엑셀`이 `Internal Server Error`로 끝나면 v1.2.0 이상으로 업데이트하세요. 원본 상세정보의 세로 탭 등 Excel 금지 문자는 내보낼 때 줄바꿈 또는 빈 문자로 안전하게 변환됩니다.

## 운영 참고

하이서울기업의 WebMethod는 공식 공개 API 문서가 확인되지 않은 웹 화면용 인터페이스이므로 사이트 개편 시 경로나 응답 필드가 바뀔 수 있습니다. 엔드포인트·Cookie·추가 헤더는 `.env`에서 변경할 수 있으며, 호출 제한을 낮게 유지하고 해당 사이트의 이용정책과 데이터 이용 권한을 확인해 사용하세요.
