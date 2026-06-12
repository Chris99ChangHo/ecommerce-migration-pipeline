# 🌐 Universal E-commerce Data Migration Boilerplate

> **무중단, 무결점 데이터 이관을 위한 재사용 가능한 파이프라인 아키텍처**
> 
> 이 프로젝트는 레거시 이커머스 플랫폼(SPA/CSR 기반 등)에서 최신 플랫폼(Shopify 등)으로 방대한 상품 데이터를 안전하게 마이그레이션하기 위해 설계된 범용 파이프라인 템플릿입니다. 타겟 URL과 API만 교체하면 어떤 환경에서도 동작하도록 완벽하게 모듈화되어 있습니다.

> 📚 **프로젝트의 실제 개발 스토리, 필드 일지, 핵심 로직 세부 설명은 [MIGRATION_STORY.md](./MIGRATION_STORY.md) 에서 확인하세요.**

---

## 🎯 설계 철학 (Design Philosophy)

단순히 한 번 쓰고 버리는 일회성 스크립트가 아닙니다. 엔터프라이즈급 데이터 마이그레이션에서 발생하는 핵심 문제(동적 렌더링, 대용량 미디어 처리, API Rate Limit 등)를 해결하기 위해 다음과 같은 설계 원칙을 적용했습니다.

### 1. 🔄 완벽한 파이프라인 자동화 (End-to-End Pipeline)
- **Phase 1 (Data Extraction)**: Selenium 기반의 강력한 동적 DOM 렌더링 스크래퍼. SPA 구조의 한계를 뚫고 정제된 `outerHTML`만 추출.
- **Phase 2 & 3 (Media Staging & Text Update)**: 추출된 이미지 에셋을 통째로 Shopify CDN(Files 백엔드)으로 Staged Upload 처리 후, 본문 HTML의 `<img src>`를 CDN 링크로 치환. 이후 REST API(`since_id` Pagination 활용)를 통해 상품 Description 최종 업데이트.
- **Unified Entry Point**: `main.py` 하나만 실행하면 두 Phase가 유기적으로 연동되어 원클릭 자동 마이그레이션이 수행됩니다.

### 2. 🧩 환경 비종속적 범용성 (Environment Agnostic)
- **.env 동적 할당**: 코드 내부에 하드코딩된 URL이나 API Key가 전혀 없습니다.
  - `SOURCE_URL`: 추출 대상이 되는 레거시 스토어 URL
  - `TARGET_URL`: 데이터가 마이그레이션 될 타겟 스토어 도메인
  - `TARGET_API_TOKEN`: 타겟 스토어(Shopify) API 액세스 토큰
- 타겟 사이트나 환경이 바뀌더라도 `.env` 파일의 변수만 교체하면 즉시 재사용할 수 있습니다.

### 3. 🛡️ 에러 복원력 및 장기적 안정성 (Resilience)
- **Tenacity 재시도(Retry) 로직**: 대량의 이미지를 Staged Upload하는 과정에서 발생하는 네트워크 타임아웃, Shopify API Rate Limit 초과, 간헐적 HTTP Error 등을 방어하기 위해 `tenacity` 라이브러리의 `wait_exponential` 및 `stop_after_attempt` 로직을 빈틈없이 적용했습니다.
- **데이터 독립성(CDN 이관)**: 기존 레거시 서버의 트래픽을 잡아먹거나 엑스박스(Broken Image)가 뜨는 사태를 방지하기 위해, 원본 이미지를 로컬에 저장(`productdesimg/`)한 뒤 타겟 플랫폼의 공식 CDN으로 완전히 이관하는 방식을 채택했습니다.

---

## 🛠️ 기술 스택 (Tech Stack)
- **Language**: Python 3.10+
- **Extraction (Phase 1)**: `selenium` (강제 스크롤 및 동적 로딩 대응), `beautifulsoup4` (HTML 클리닝)
- **API Communication (Phase 2 & 3)**: `ShopifyAPI` (REST - 텍스트 매핑), `gql` (GraphQL - Staged Upload용 쿼리/뮤테이션 처리)
- **Fault Tolerance**: `tenacity` (API 재시도), `python-dotenv` (환경 변수 은닉)

---

## 🚀 빠른 시작 (Quick Start)

### 1. 설치 (Installation)
```bash
git clone https://github.com/Chris99ChangHo/ecommerce-migration-pipeline.git
cd ecommerce-migration-pipeline
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 환경 변수 설정 (Environment Configuration)
프로젝트 루트에 `.env` 파일을 생성하고 아래 템플릿을 채워주세요. (참고: `.env.example`)
```env
SOURCE_URL="https://legacy-store.example.com/all"
TARGET_URL="new-store.myshopify.com"
TARGET_API_TOKEN="shpat_..."
```

### 3. 실행 (Execution)
```bash
python main.py
```
> 실행 시 스크래핑된 HTML 및 이미지 원본은 `data/productdesimg/`에 안전하게 저장되며, 업로드 실패나 네트워크 에러 등 모든 진행률은 `data/logs/` 내의 CSV 리포트와 텍스트 파일로 자동 기록됩니다.

---

## 📁 디렉토리 구조 (Directory Structure)
```text
📦 ecommerce-migration-pipeline
 ┣ 📂 src                    # 파이프라인 핵심 모듈
 ┃ ┣ 📜 scraper.py           # Phase 1: 동적 웹 페이지 추출 및 DOM 정제
 ┃ ┗ 📜 shopify_updater.py   # Phase 2&3: GraphQL 미디어 업로드 및 REST 텍스트 업데이트
 ┣ 📂 data                   # (Git Ignore) 로컬 데이터 및 로그
 ┃ ┣ 📂 logs                 # 에러 로그 및 CSV 처리 리포트
 ┃ ┣ 📂 urls                 # 추출된/실패한 URL 리스트 캐싱
 ┃ ┗ 📂 productdesimg        # 추출된 HTML 및 로컬 원본 이미지 저장소
 ┣ 📜 main.py                # 엔트리 포인트 (환경 변수 검증 및 파이프라인 순차 실행)
 ┣ 📜 .env.example           # 환경 변수 템플릿
 ┣ 📜 requirements.txt       # 의존성 명세서
 ┗ 📜 README.md
```

---

## 💡 개발 주안점 및 트러블슈팅 (Troubleshooting)

### 1. SPA/CSR 동적 로딩 한계 돌파
가장 큰 난관은 React/Vue 기반의 SPA 페이지에서 DOM이 스크롤 이벤트에 의해 지연 렌더링(Lazy Loading)된다는 점이었습니다. 일반적인 BeautifulSoup/Requests 방식으로는 텅 빈 HTML만 반환되었습니다. 
이를 해결하기 위해 `Selenium`을 도입하여 `Keys.PAGE_DOWN`으로 스크롤을 강제 유도하고, `WebDriverWait`를 통해 특정 식별자(`Product Info` 등)가 DOM에 완전히 마운트될 때까지 대기하는 영속성 있는 추출 알고리즘을 설계했습니다.

### 2. DOM 클리닝 및 HTML 필터링 파이프라인 구축
레거시 플랫폼(Wix) 특유의 인라인 스타일, 클래스, 불필요한 스크립트가 타겟 플랫폼으로 그대로 이관될 경우, Shopify 테마의 CSS와 충돌하여 레이아웃이 깨지거나 플랫폼 종속성 문제가 발생할 위험이 높았습니다.
이를 원천적으로 차단하기 위해 `clean_html_content` 함수를 모듈화하여 파이프라인에 통합했습니다. 이 필터링 모듈은 타겟 플랫폼에 종속성 문제를 일으킬 수 있는 요소를 정밀하게 제거하고, HTML 구조를 깔끔하게 정제하여 Shopify 환경에 최적화된 호환성을 보장합니다.

### 3. 대용량 미디어 이관 및 비동기 폴링(Polling) 동기화
본문 삽입용(Description) 이미지를 백엔드로 업로드하기 위해 Shopify 권장 규격인 **GraphQL 3-Step Staged Upload API**를 도입했습니다. 
하지만 `fileCreate` API 호출 직후, Shopify 내부의 파일 처리 지연으로 인해 영구적인 CDN URL이 즉시 반환되지 않는 **비동기 동기화 문제**가 발생했습니다. 이를 해결하기 위해 CDN URL이 확인될 때까지 최대 5회에 걸쳐 지수 백오프(`time.sleep(2 ** attempt)`) 방식을 적용한 폴링(Polling) 로직을 구현했습니다. 이 견고한 재시도 메커니즘 덕분에 처리 지연에도 불구하고 영구적인 CDN URL을 안전하게 확보하여 로컬 HTML의 `<img src>`를 성공적으로 치환할 수 있었습니다.

---

## 🤖 AI Adoption 및 프롬프트 엔지니어링 성과

단순한 코드 템플릿 생성을 넘어, 다중 LLM을 실질적인 페어 프로그래머(Pair Programmer)로 활용하여 마이그레이션 파이프라인 구축의 러닝 커브를 대폭 단축했습니다.

- **복잡한 GraphQL 스키마의 신속한 학습**: 공식 문서만으로는 직관적인 파악이 어려운 "Shopify GraphQL 3-Step Staged Upload API"의 복잡한 스키마와 인증 구조를 AI와의 심도 있는 아키텍처 토론을 통해 신속하게 학습하고 구현에 성공했습니다.
- **예외 처리 아키텍처 설계**: 네트워크 불안정성이나 API Rate Limit 등에 대비하여, 파이프라인 전반에 걸쳐 `Tenacity` 기반의 안정적이고 우아한 예외 처리(Retry & Exponential Backoff) 아키텍처를 AI의 제안을 바탕으로 성공적으로 설계 및 도입했습니다.
