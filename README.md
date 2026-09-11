# News Tracker

매일 자동으로 Naver News와 Google News에서 회사/키워드 관련 기사를 수집해,
정적 HTML 사이트로 만들어주는 개인용 CLI 도구입니다.

두 가지 방식으로 쓸 수 있습니다:

1. **로컬 전용** (원래 설계): 내 맥에서 cron으로 매일 실행, `output/index.html`을
   브라우저로 직접 열어봄. 외부 배포 없음.
2. **Cloudflare Pages 배포**: GitHub Actions가 매일 자동으로 수집한 뒤 결과를
   Cloudflare Pages로 배포해서, 노트북이 꺼져 있어도 휴대폰/다른 기기에서 URL로
   바로 접속해서 볼 수 있음. 별도 로그인/비밀번호 없이 누구나 URL만 알면 볼 수
   있는 완전 공개 상태입니다 (수집 대상 자체가 이미 공개된 뉴스라 비공개로 막을
   필요가 없다는 판단).

둘 중 하나만 골라도 되고, 로컬 실행으로 먼저 동작을 확인한 뒤 Cloudflare Pages를
추가해도 됩니다.

현재 실제 배포된 사이트: `https://news-tracker-7gg.pages.dev` (인증 없음, 바로 접속 가능)

## 구조

```
news-tracker/
├── config.yaml          # (직접 생성, git에 커밋 안 됨) 로컬용: 키워드+Naver 키+보관기간
├── config.yaml.example  # config.yaml 작성 예시
├── config.ci.yaml        # (커밋됨) GitHub Actions용: 키워드+보관기간만, 키 없음
├── .github/workflows/daily.yml  # GitHub Actions: 매일 수집 + Cloudflare Pages 배포
├── news_tracker/
│   ├── collect.py       # Naver API + Google News RSS 수집
│   ├── dedupe.py        # 제목 정규화 기반 중복 제거
│   ├── render.py        # Jinja2 HTML 렌더링
│   └── main.py          # collect -> dedupe -> save -> render 오케스트레이션
├── data/YYYY-MM-DD.json # 해당 날짜의 원본 수집 결과
├── output/
│   ├── index.html       # 오늘 기사 + 최근 보관 기사 링크
│   └── archive/YYYY-MM-DD.html
├── templates/page.html
└── tests/                # 단위 테스트 (표준 라이브러리 unittest 사용, 추가 설치 불필요)
```

## 1. 설치

```bash
cd /Users/nakta/projects/news-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Naver API 키 발급

**2026년 7월 31일부로 뉴스 검색 API 신규 발급이 개발자센터(developers.naver.com)에서
NAVER Cloud Platform의 "NAVER API HUB"로 이전되었습니다.** 기존 개발자센터에서 발급한
키는 이 API HUB에서 사용할 수 없고, 별도로 새로 발급받아야 합니다.

1. https://www.ncloud.com (NAVER Cloud Platform) 에 접속해 NCP 계정으로 로그인
   (일반 네이버 계정과는 별도 — 없으면 회원가입 필요, 이 API는 무료 티어로 충분함:
   일 25,000건까지 무료)
2. 콘솔 메뉴 → **전체 서비스 → Application Service → NAVER API HUB** 이동
3. **신청하기** → 서비스 이용 신청 (약관 동의)
4. **Application** 메뉴 → **Application 등록** → 사용할 API로 **뉴스 검색(Search - News)**
   선택 → 이름 입력 후 등록
5. 등록된 Application 선택 → **인증 정보** 에서 Client ID / Client Secret 확인

발급받은 값을 아래 설정에 입력하세요.

(참고: 요청 형식·응답 형식 자체는 예전 API와 동일하지만, 엔드포인트와 인증 헤더가
바뀌었습니다 — `collect.py`에 이미 새 엔드포인트/헤더로 반영되어 있으니 신경 쓰지
않으셔도 됩니다.)

## 3. 로컬 설정 (`config.yaml`)

`config.yaml.example`을 복사해 `config.yaml`로 만들고 값을 채웁니다.

```bash
cp config.yaml.example config.yaml
```

```yaml
keywords:
  - "두두원"
naver:
  client_id: "발급받은 CLIENT_ID"
  client_secret: "발급받은 CLIENT_SECRET"
retention_days: 90
max_article_age_days: 3
```

- `keywords`: 검색어 목록 (여러 개 지정 시 결과를 합쳐서 보여줍니다)
- `naver.client_id` / `client_secret`: 위에서 발급받은 값. `NAVER_CLIENT_ID` /
  `NAVER_CLIENT_SECRET` 환경변수가 설정되어 있으면 그 값이 이 파일보다
  우선합니다 (GitHub Actions에서 이 방식을 씁니다 — 아래 6번 참고).
- `retention_days`: 보관/링크할 과거 기록 일수 (기본 90일). 이보다 오래된
  `data/*.json`, `output/archive/*.html` 파일은 실행할 때마다 자동 삭제됩니다.
- `max_article_age_days`: 실제 기사 발행일 기준으로, 오늘로부터 이 일수보다
  오래된 기사는 결과에서 제외합니다 (기본 3일). Google 뉴스는 검색어와의
  관련도로 결과를 주기 때문에 필터링이 없으면 "두두" 같은 단어가 우연히
  들어간 몇 년 전 기사까지 매일 섞여 나올 수 있어, 이를 걸러내기 위한
  값입니다.

`config.yaml`은 `.gitignore`에 포함되어 있어 git에 커밋되지 않습니다 (실제 API
키가 들어있기 때문). GitHub Actions용 설정은 별도의 `config.ci.yaml`을 씁니다
(6번 참고).

## 4. 수동 실행 (동작 확인)

```bash
source venv/bin/activate
python3 -m news_tracker.main
```

정상 실행되면 `output/index.html`이 갱신됩니다. 브라우저로 열어 확인하세요:

```bash
open output/index.html
```

`config.yaml`이 없거나 필수 항목이 빠져 있으면 즉시 에러 메시지를 출력하고
종료합니다 (exit code 1) — 잘못된 설정으로 빈 페이지가 조용히 만들어지는 것을
막기 위함입니다. 그날 두 소스 중 하나만 실패하면(네트워크 오류, API 키 오류 등)
나머지 소스 결과만으로 페이지가 만들어지고, 실패는 로그로만 남습니다. 기사가
0건이면 "기사 없음"이 표시되는 정상 상태입니다.

## 5. 로컬에서만 매일 자동 실행 (crontab)

Cloudflare Pages 배포(6번) 없이 로컬에서만 쓰려면 이 방법으로 충분합니다. 먼저
가상환경의 python 경로를 확인합니다:

```bash
cd /Users/nakta/projects/news-tracker
source venv/bin/activate
which python3
```

`crontab -e`로 편집기를 열고, 아래 줄을 추가합니다 (매일 오전 8시 실행 예시 —
시간은 원하는 대로 변경, 위에서 확인한 python3 경로로 교체):

```cron
0 8 * * * cd /Users/nakta/projects/news-tracker && venv/bin/python3 -m news_tracker.main >> cron.log 2>&1
```

- `cd`로 프로젝트 디렉토리로 이동 후 실행해야 `config.yaml`, `data/`, `output/`
  등을 상대 경로가 아닌 프로젝트 루트 기준으로 정확히 찾습니다.
- `>> cron.log 2>&1`은 실행 로그를 프로젝트 폴더의 `cron.log`에 남겨,
  cron이 조용히 실패했을 때 원인을 확인할 수 있게 해줍니다.
- macOS는 노트북이 잠자기 상태거나 꺼져 있으면 cron이 그 시각에 실행되지 않고
  건너뜁니다. 매일 반드시 갱신되길 원하면 아래 6번(Cloudflare Pages)을 쓰세요.

## 6. Cloudflare Pages로 배포하기 (다른 기기에서 접속)

노트북이 꺼져 있어도 매일 자동으로 갱신되고, URL로 어디서든 볼 수 있게 하려면
GitHub Actions가 매일 수집을 실행한 뒤 결과를 Cloudflare Pages로 올리는 방식을
씁니다. 아래는 한 번만 하면 되는 설정입니다 (이미 이 저장소에는 설정이 끝나
있고, 새로 처음부터 설정할 때 참고용입니다).

**참고**: 저장소는 Public으로 되어 있습니다. 수집되는 검색 키워드와 기사
제목·링크는 어차피 공개된 뉴스 정보라 민감하지 않다고 판단해, 별도 비공개
처리나 사이트 접속 인증 없이 편의성 위주로 운영하기로 했습니다. Naver API
키는 저장소에 들어가지 않고 GitHub Secrets에만 저장됩니다.

### 6-1. Cloudflare 계정 및 API 토큰 준비

1. https://dash.cloudflare.com 에서 무료 계정 생성 후 로그인 (이메일 인증 필요)
2. 대시보드 우측 상단 프로필 → **API Tokens** → **Create Token**
3. **Edit Cloudflare Workers** 템플릿 선택 (Pages 편집 권한 포함) → 필요 없는
   "Workers Routes" 관련 정책은 제거해도 무방 → 토큰 생성
4. 대시보드 우측 사이드바 또는 URL(`dash.cloudflare.com/<account-id>/...`)에서
   **계정 ID(Account ID)** 확인

Cloudflare Pages는 별도 도메인이 없어도 프로젝트마다 무료로
`<project-name>.pages.dev` 서브도메인을 자동으로 줍니다 — 도메인 구매는
필수가 아닙니다.

### 6-2. GitHub Secrets 등록

저장소 페이지 → **Settings → Secrets and variables → Actions → New repository
secret** 에서 아래를 등록합니다:

| Name | 값 |
|---|---|
| `NAVER_CLIENT_ID` | Naver에서 발급받은 Client ID |
| `NAVER_CLIENT_SECRET` | Naver에서 발급받은 Client Secret |
| `CLOUDFLARE_API_TOKEN` | 위에서 만든 Cloudflare API 토큰 |
| `CLOUDFLARE_ACCOUNT_ID` | 위에서 확인한 Cloudflare 계정 ID |

### 6-3. Workflow 권한을 "읽기/쓰기"로 설정

매일 실행 결과(`data/`, `output/`)를 저장소에 커밋해서 기록을 남기기 때문에,
Actions에 쓰기 권한이 필요합니다. **Settings → Actions → General → Workflow
permissions** 에서 **"Read and write permissions"** 를 선택하고 저장하세요.

### 6-4. 첫 실행 확인

저장소의 **Actions** 탭 → **Daily news update** 워크플로 → **Run workflow**
버튼으로 수동 실행해 보세요. 성공하면:

- `data/`, `output/`에 오늘 날짜 파일이 추가된 커밋이 저장소에 생깁니다.
- 워크플로 로그의 "Deploy to Cloudflare Pages" 단계에 배포된 실제 URL이
  출력됩니다 (`<project-name>.pages.dev` 또는 이름이 이미 다른 계정에서
  쓰이고 있으면 `<project-name>-xxxx.pages.dev`처럼 임의 접미사가 붙을 수
  있음 — 반드시 로그에서 실제 URL을 확인하세요).
- Cloudflare 대시보드 **Workers 및 Pages** 목록에서도 프로젝트와 URL을
  확인할 수 있습니다.

이후에는 `.github/workflows/daily.yml`에 설정된 시각(기본 매일 08:00 KST)에
자동으로 실행됩니다. 시각을 바꾸려면 그 파일의 `cron:` 줄을 수정하세요 (UTC
기준이므로 KST에서 9시간을 빼서 넣습니다).

### 로컬 cron과 같이 쓸 때 주의

GitHub Actions를 쓰기 시작하면 `data/`, `output/`이 git에 커밋되는 대상이
됩니다. 로컬 cron(5번)도 동시에 계속 돌리면 두 프로세스가 같은 파일을 각자
건드리게 되어 git 충돌이 날 수 있습니다 — 매일 자동 갱신은 둘 중 하나만
쓰는 것을 권장합니다 (동작 확인용 수동 실행은 언제든 해도 무방).

## 7. 테스트

추가 패키지 설치 없이 표준 라이브러리 `unittest`만으로 실행됩니다:

```bash
python3 -m unittest discover -s tests -v
```

`collect.py`/`dedupe.py`는 `tests/fixtures/`의 샘플 Naver/Google 응답으로,
`render.py`는 샘플 기사 데이터로 검증합니다. 실제 API 키를 이용한 전체
파이프라인 동작 확인은 위 4번의 수동 실행이 acceptance check 역할을 합니다
(개인용 도구이므로 별도의 자동 E2E 테스트는 두지 않았습니다).
