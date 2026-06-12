import os
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse # URL 인코딩을 위한 모듈 임포트
from dotenv import load_dotenv

load_dotenv()

# ▶ 메인 페이지 URL 설정 (환경변수 SOURCE_URL에서 불러옴, .env 파일 참조)
MAIN_URL = os.getenv("SOURCE_URL", "https://example-source.com")

# ▶ 상품 상세 페이지 URL을 저장할 리스트 초기화
product_detail_urls_raw = [] # 초기 수집된 원본 URL (인코딩 전)
product_detail_urls_encoded = [] # 최종 인코딩된 URL

print("--- BeautifulSoup을 사용하여 상품 상세 페이지 URL 수집 시작 ---")

try:
    # requests 라이브러리를 사용하여 메인 페이지의 HTML 콘텐츠를 가져옵니다.
    print(f"[INFO] requests로 메인 페이지 HTML 가져오는 중: {MAIN_URL}")
    response = requests.get(MAIN_URL, timeout=15)
    
    # HTTP 요청이 성공했는지 확인합니다. (예: 200 OK)
    response.raise_for_status() 
    
    # BeautifulSoup을 사용하여 HTML 콘텐츠를 파싱합니다.
    soup = BeautifulSoup(response.text, 'html.parser')
    print("[+] BeautifulSoup으로 HTML 파싱 완료.")

    # 사용자님께서 제공해주신 XPath //*[@id="comp-kuv75ej3"]/div/div/div/div 에 해당하는
    # HTML 요소를 CSS 선택자 형태로 찾습니다.
    main_container = soup.select_one('#comp-kuv75ej3 > div > div > div > div')

    if main_container:
        print(f"[+] 메인 컨테이너 '{main_container.name}' 찾음.")
        
        # 찾은 메인 컨테이너 내에서 모든 <a> 태그를 찾습니다.
        all_links_in_container = main_container.find_all('a', href=True)
        
        print(f"[INFO] 메인 컨테이너 내에서 총 {len(all_links_in_container)}개의 <a> 태그가 감지되었습니다.")

        # 감지된 <a> 태그들을 순회하며 유효한 상품 상세 페이지 URL만 필터링합니다.
        for link_element in all_links_in_container:
            href = link_element['href'] 
            
            # 'product-page' 포함, http/https 시작, 중복되지 않는 유효한 URL만 추가
            if "product-page" in href and (href.startswith("http://") or href.startswith("https://")) and href not in product_detail_urls_raw:
                product_detail_urls_raw.append(href)
    else:
        print(f"[!] 메인 컨테이너 XPath '//*[@id=\"comp-kuv75ej3\"]/div/div/div/div'에 해당하는 요소를 찾을 수 없습니다.")
        print("[!] 이 웹사이트는 JavaScript로 상품 목록을 동적으로 로드하는 것 같습니다. BeautifulSoup만으로는 링크 수집이 어려울 수 있습니다.")

    print(f"[INFO] 중복을 제외한 총 {len(product_detail_urls_raw)}개의 유효한 상품 상세 페이지 URL을 초기 수집했습니다.")
    
    if not product_detail_urls_raw:
        print("[ERROR] 상품 상세 페이지 URL을 수집하지 못했습니다. 웹사이트 구조 변경 또는 동적 로딩 문제일 수 있습니다.")
        exit() # URL 수집 실패 시 스크립트 종료

except requests.exceptions.RequestException as e:
    print(f"[ERROR] 메인 페이지 접속 중 오류 발생 (requests): {e}")
    print("[ERROR] 네트워크 연결 문제, URL 오류 또는 웹사이트에서 요청을 차단했을 수 있습니다. 스크립트를 종료합니다.")
    exit()
except Exception as e:
    print(f"[ERROR] URL 수집 중 예상치 못한 오류 발생: {e}")
    exit()

print("\n--- 수집된 URL에 포함된 한글 URL 인코딩 시작 ---")

# ▶ 수집된 URL 리스트를 순회하며 한글 부분을 URL 인코딩
for url in product_detail_urls_raw:
    try:
        # URL을 'scheme', 'netloc', 'path', 'params', 'query', 'fragment'로 분리
        parsed_url = urllib.parse.urlparse(url)
        
        # path 부분만 가져와서 한글을 인코딩합니다.
        # safe='/'는 슬래시(/)는 인코딩하지 않고 그대로 두도록 합니다.
        encoded_path = urllib.parse.quote(parsed_url.path, safe='/')
        
        # 인코딩된 path를 사용하여 새로운 URL을 재구성합니다.
        encoded_full_url = urllib.parse.urlunparse(
            (parsed_url.scheme, parsed_url.netloc, encoded_path, 
             parsed_url.params, parsed_url.query, parsed_url.fragment)
        )
        product_detail_urls_encoded.append(encoded_full_url)
        # print(f"[+] 인코딩 완료: {url} -> {encoded_full_url}") # 너무 많으면 주석 처리
    except Exception as e:
        print(f"[!] URL 인코딩 중 오류 발생: {url} - {e}")

print(f"[INFO] 총 {len(product_detail_urls_encoded)}개의 URL이 성공적으로 인코딩되었습니다.")

print("\n--- 최종 수집 및 인코딩된 상품 상세 페이지 URL 리스트 (복사하여 Selenium 스크립트에 붙여넣으세요) ---")
print("product_detail_urls = [")
for url in product_detail_urls_encoded:
    print(f"    \"{url}\",")
print("]")
print("\n--- 모든 과정 완료 ---")