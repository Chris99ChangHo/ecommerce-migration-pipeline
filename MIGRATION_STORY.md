# 🚀 Wix to Shopify Data Migration Pipeline: Automated & Robust Architecture

> **"AI Co-Piloting을 통한 수작업 제로화 및 완벽한 데이터 독립성 확보"**
>
> 본 프로젝트는 기존 Wix 쇼핑몰의 복잡한 상품 데이터(텍스트, 이미지, HTML 구조)를 Shopify 플랫폼으로 무중단, 무결점으로 이관하기 위해 설계된 대규모 자동화 데이터 파이프라인입니다.

## 1. Project Overview (프로젝트 개요)

- **프로젝트 목적**: 기존 수작업 이관 방식에서 발생하는 극심한 비효율성(시간 소요)과 휴먼 에러를 100% 제거하기 위한 전면 자동화.
- **기술 스택**:
  <br>
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/Selenium-43B02A?style=for-the-badge&logo=selenium&logoColor=white"/>
  <img src="https://img.shields.io/badge/Shopify-96B753?style=for-the-badge&logo=shopify&logoColor=white"/>
  <img src="https://img.shields.io/badge/GraphQL-E10098?style=for-the-badge&logo=graphql&logoColor=white"/>
  <img src="https://img.shields.io/badge/Tenacity-002244?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/ChatGPT-74AA9C?style=for-the-badge&logo=openai&logoColor=white"/>
  <img src="https://img.shields.io/badge/Claude-D97757?style=for-the-badge&logo=anthropic&logoColor=white"/>

단순한 데이터 스크래핑을 넘어, 대상 플랫폼(Shopify)의 CDN 생태계에 완벽히 동화되도록 **이미지 에셋의 원천적 이전(Staged Upload)**을 수행하여 장기적 시스템 안정성과 데이터 독립성을 확보했습니다.

## 2. Architecture & Workflow (시스템 구조)

전체 파이프라인은 데이터의 무결성을 보장하기 위해 논리적으로 분리된 3단계 모듈로 작동합니다.

- **Phase 1: 데이터 추출 (Data Extraction)**
  Selenium을 활용해 Wix 동적 페이지(SPA)의 DOM을 강제 렌더링하고, JS 주입을 통해 불필요한 태그를 1차 정제합니다.

- **Phase 2: 텍스트 및 메타데이터 업데이트 (Text & Metadata Update)**
  Shopify REST API를 통해 기존 상품의 메타데이터 및 Description HTML을 Shopify 규격(h5 통일 등)으로 매핑하여 업데이트합니다.

- **Phase 3: 미디어 호스팅 전환 (Media Hosting Transfer)**
  Wix 서버에 의존하던 이미지 원본 URL을 모두 추출, 다운로드 후 Shopify CDN(Files 백엔드)으로 GraphQL Staged Upload 방식을 통해 이관합니다. 이후 본문 HTML의 src 속성을 Shopify CDN 링크로 완벽히 치환합니다.

## 3. Critical Troubleshooting & Core Logic (핵심 문제 해결 및 코드)

데이터 마이그레이션 중 마주한 3가지 크리티컬 이슈와 이를 해결한 핵심 엔지니어링 접근법입니다.

### A. 동적 페이지 한계 극복 및 DOM 클리닝 파이프라인 구축 (HTML 필터링)

Wix와 같은 SPA 기반 사이트는 일반적인 HTTP Request(BS4)로 본문 로딩이 불가능하며, 레거시 플랫폼 특유의 인라인 스타일과 스크립트가 타겟 플랫폼에 그대로 이관되면 Shopify 테마와 심각한 CSS 충돌 및 레이아웃 붕괴를 일으킬 수 있습니다.
이를 해결하기 위해 Selenium의 PAGE_DOWN 키 이벤트로 Lazy Loading을 강제 트리거하여 전체 DOM을 렌더링하고, 추출된 데이터를 타겟 환경에 최적화하기 위해 `clean_html_content` 함수를 모듈화하여 도입했습니다. 이 정밀한 필터링 파이프라인을 통해 종속성 문제를 일으키는 불필요한 태그와 속성을 완벽히 제거했습니다.

```python
# [DOM 필터링 및 클리닝 로직 예시 발췌]

def clean_html_content(html_string):
    """
    레거시 플랫폼 종속적인 인라인 스타일 및 스크립트를 제거하여
    Shopify 테마와의 충돌을 방지하는 정밀 클리닝 파이프라인.
    """
    soup = BeautifulSoup(html_string, 'html.parser')

    # 1. 불필요한 스크립트, 스타일 태그 등 제거
    for tag in soup(['script', 'style', 'iframe']):
        tag.decompose()

    # 2. 인라인 스타일 및 클래스 속성 제거로 테마 충돌 원천 차단
    for tag in soup.find_all(True):
        tag.attrs.pop('style', None)
        tag.attrs.pop('class', None)
        tag.attrs.pop('id', None)

    return str(soup)
```

### B. 이미지 데이터 독립성 확보 및 비동기 폴링(Polling) 로직 (Staged Upload)

단순히 이미지 URL을 HTML에 복사해 넣으면 구 서버(Wix) 계약 만료 시 이미지가 일괄 엑스박스 처리되는 치명적 문제가 발생합니다. 이를 방지하고자 Shopify의 GraphQL API를 활용해 이미지를 Shopify 내부 Files CDN으로 직접 이관(Staged Upload)하여 서버 의존성을 완벽히 끊어냈습니다.
특히, `fileCreate` API 호출 직후 Shopify 내부 처리 지연으로 CDN URL이 즉시 반환되지 않는 **비동기 동기화 문제**를 해결하기 위해, 최대 5회에 걸쳐 지수 백오프 방식을 적용한 폴링 로직을 구축했습니다.

```python
# [Shopify 비동기 폴링(Polling) 로직 발췌]

def wait_for_shopify_cdn_url(file_id, max_attempts=5):
    """지수 백오프를 활용하여 CDN URL이 완전히 생성될 때까지 비동기 대기"""
    for attempt in range(max_attempts):
        result = query_file_status(file_id)
        if result and result.get('url'):
            return result['url']

        # 지연 처리 감지: 지수 백오프 기반 대기 로직 (1, 2, 4, 8, 16초...)
        time.sleep(2 ** attempt)

    raise Exception("Shopify 내부 CDN 동기화 시간 초과")
```

### C. API 호출 제한 방어: 페이지네이션 및 백오프 재시도 로직

대규모 트래픽 발생 시 Shopify API의 Rate Limit(호출 제한)으로 인한 파이프라인 붕괴를 막기 위해, 커서 기반 페이지네이션(since_id)과 `tenacity` 라이브러리를 결합하여 견고한 에러 핸들링을 구현했습니다.

```python
# [API 호출 안전망 구축 발췌]

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

# Tenacity를 활용한 지수 백오프 재시도 아키텍처
@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5), retry=retry_if_exception_type(Exception))
def process_shopify_api_request():
    pass # API Call Logic...
```

## 4. AI Adoption & Prompt Engineering (AI 도입 및 업무 자동화 성과)

💡 **"AI를 단순한 코드 생성기가 아닌, 고성능 Co-Pilot이자 페어 프로그래머로 활용했습니다."**

본 프로젝트는 전통적인 개발 방법론에 **다중 LLM(ChatGPT, Claude, Gemini)**을 결합한 AI Adoption의 성공적인 사례입니다.

- **GraphQL 3-Step Staged Upload API의 복잡한 스키마 학습**: 방대하고 난해한 Shopify 공식 문서를 일일이 분석하는 대신, AI와의 핑퐁 토론을 통해 복잡한 스키마 구조와 동작 원리를 신속하게 파악하여 학습 곡선(Learning Curve)을 대폭 단축시켰습니다.
- **Tenacity 기반의 예외 처리 아키텍처 설계**: 네트워크 지연 및 Rate Limit 발생 시 대처 방안에 대해 LLM과 깊이 있는 아키텍처 설계 회의를 진행했고, 그 결과 `tenacity`를 활용한 데코레이터 패턴의 우아하고 견고한 예외 처리 코드를 파이프라인 전반에 매끄럽게 도입할 수 있었습니다.
- **정밀한 DOM 클리닝 필터링 정규화**: 플랫폼 종속성 문제를 일으키는 요소를 정밀하게 분리해내는 정규 표현식 및 파싱 로직을 다중 LLM의 크로스 체킹을 통해 구현, 완벽에 가까운 무결성을 달성했습니다.
