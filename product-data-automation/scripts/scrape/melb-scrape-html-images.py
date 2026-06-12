import os
import csv
import re
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup # HTML 파싱을 위해 추가
from urllib.parse import urlparse, urljoin, unquote # URL 파싱 및 조합, 디코딩 추가
from tqdm import tqdm # 진행률 표시를 위해 추가
from dotenv import load_dotenv

load_dotenv()

# --- 파일 및 URL 설정 ---
BASE_DIR = "상품설명사진"
URL_DIR = "url"
VALID_URLS_CSV = os.path.join(URL_DIR, "valid_urls.csv") # 성공적으로 스크래핑된 URL 기록
INVALID_URLS_CSV = os.path.join(URL_DIR, "invalid_urls.csv") # 스크래핑 실패 URL 기록

# --- 재스크래핑할 특정 상품 URL 목록 ---
# 아래 URL들은 .env의 SOURCE_URL 도메인 기준으로 수집된 상품 페이지 목록입니다.
# 실제 사용 시 .env에 SOURCE_URL=https://your-source-site.com 설정 후 사용하세요.
SOURCE_BASE = os.getenv("SOURCE_URL", "https://example-source.com")
TARGET_URLS_TO_RESCRAPE = [
    "https://www.melbmarket.com.au/product-page/%EA%B0%95%EC%9B%90-%EC%9A%B0%EC%9C%A0-%EC%83%9D%ED%81%AC%EB%A6%BC-%EB%B9%B5-%EB%94%B8%EA%B8%B0-%EC%B4%88%EC%BD%94",
    "https://www.melbmarket.com.au/product-page/%EB%A7%9B%EC%9E%88%EB%8A%94-%EA%B5%B0%EC%98%A5%EC%88%98%EC%88%98-2%ED%8C%A9",
    "https://www.melbmarket.com.au/product-page/%EC%9E%90%EC%97%B0%EA%B9%83%EB%93%A0-%EC%84%A4%EB%A0%81%ED%83%95-600g-%EC%83%81%EC%98%A8%EB%B3%B4%EA%B4%80",
    "https://www.melbmarket.com.au/product-page/%EC%9E%90%EC%97%B0%EA%B9%83%EB%93%A0-%EC%9C%A1%EA%B0%9C%EC%9E%A5-600g-%EC%83%81%EC%98%A8%EB%B3%B4%EA%B4%80",
    "https://www.melbmarket.com.au/product-page/%EC%9E%90%EC%97%B0%EA%B9%83%EB%93%A0-%EC%86%8C%EA%B0%88%EB%B9%84%ED%83%95-600g-%EC%83%81%EC%98%A8%EB%B3%B4%EA%B4%80",
    "https://www.melbmarket.com.au/product-page/%EC%9E%90%EC%97%B0%EA%B9%83%EB%93%A0-%EC%9E%A5%ED%84%B0%EA%B5%AD%EB%B0%A5-600g-%EC%83%81%EC%98%A8%EB%B3%B4%EA%B4%80",
    "https://www.melbmarket.com.au/product-page/%EC%B0%B8%EC%B0%B8-%EC%B0%B8%EC%86%8C%EC%8A%A4",
    "https://www.melbmarket.com.au/product-page/%EC%98%A4%EB%9A%9C%EA%B8%B0-%EC%98%9B%EB%82%A0-%EA%B5%AC%EC%88%98%ED%95%9C-%EB%88%84%EB%A3%BD%EC%A7%80-60g",
    "https://www.melbmarket.com.au/product-page/bts-%EC%8A%A4%ED%85%8C%EB%B9%84%EC%95%84-%EC%BB%A4%ED%94%BC%EB%AF%B9%EC%8A%A4-100t",
    "https://www.melbmarket.com.au/product-page/%EB%A7%9B%EC%9E%88%EB%8A%94-%EC%96%91%EB%85%90%EC%B9%98%ED%82%A8%EC%86%8C%EC%8A%A4-%EC%88%9C%ED%95%9C%EB%A7%9B-%EB%A7%A4%EC%9A%B4%EB%A7%9B%EC%9E%88%EB%8A%94-%EC%96%91%EB%85%90%EC%B9%98%ED%82%A8-%EC%86%8C%EC%8A%A4-100g",
    "https://www.melbmarket.com.au/product-page/%EC%82%B6%EC%A7%80-%EC%95%8A%EA%B3%A0-%EB%B0%94%EB%A1%9C-%EB%A8%B9%EC%9D%84%EC%88%98%EC%9E%88%EB%8A%94-%EA%B3%A8%EB%B1%85%EC%9D%B4-%EB%B9%84%EB%B9%94%EB%A9%B4",
    "https://www.melbmarket.com.au/product-page/%EB%8D%B0%EB%AF%B8%EC%86%8C%EB%8B%A4-%EC%B2%AD%ED%8F%AC%EB%8F%84%EC%BA%94",
    "https://www.melbmarket.com.au/product-page/%ED%95%B4%ED%83%9C-%EC%97%90%EC%9D%B4%EC%8A%A4-%EC%A4%91-218g",
    "https://www.melbmarket.com.au/product-page/%EB%86%8D%EC%8B%AC-%EB%A8%B9%ED%83%9C%EA%B9%A1-60g",
    "https://www.melbmarket.com.au/product-page/%EB%86%8D%EC%8B%AC-%EC%9D%B8%EB%94%94%EC%95%88%EB%B0%A5-83g",
    "https://www.melbmarket.com.au/product-page/%ED%95%B4%ED%83%9C-%EB%B2%84%ED%84%B0%EB%A7%81-%EC%86%8C-65g",
    "https://www.melbmarket.com.au/product-page/%ED%81%AC%EB%9D%BC%EC%9A%B4-%EC%82%B0%EB%8F%84-%EB%94%B8%EA%B8%B0-%EC%A4%91-161g",
    "https://www.melbmarket.com.au/product-page/%ED%81%AC%EB%9D%BC%EC%9A%B4-%EC%B9%B4%EB%9D%BC%EB%A9%9C%EC%BD%98-%EB%95%85%EC%BD%A9-%EB%8C%80-125g",
    "https://www.melbmarket.com.au/product-page/%ED%81%AC%EB%9D%BC%EC%9A%B4-%EC%BD%98%EC%B9%A9-%EB%8C%80-148g",
    "https://www.melbmarket.com.au/product-page/%EC%98%A4%EB%A6%AC%EC%98%A8-%EA%B3%A0%EB%9E%98%EB%B0%A5-%EB%B3%B6%EC%9D%8C-%EC%96%91%EB%85%90%EB%A7%9B-46g",
    "https://www.melbmarket.com.au/product-page/%ED%99%88%EB%9F%B0%EB%B3%BC-4%EB%B2%88%EB%93%A4-164g",
    "https://www.melbmarket.com.au/product-page/%EC%BF%A0%ED%82%A4%EC%95%A4-%ED%81%AC%EB%A6%BC-%EC%95%84%EB%AA%AC%EB%93%9C-%EB%8C%80-190g",
    "https://www.melbmarket.com.au/product-page/%ED%97%88%EB%8B%88%EB%B2%84%ED%84%B0-%EC%95%84%EB%AA%AC%EB%93%9C-190g",
    "https://www.melbmarket.com.au/product-page/%EA%B9%8C%EB%A8%B9%EB%8A%94-%EC%A0%A4%EB%A6%AC-%EA%B3%A8%EB%93%9C%ED%82%A4%EC%9C%84-%EC%A0%A4%EB%A6%AC-320g",
    "https://www.melbmarket.com.au/product-page/%EA%B9%8C%EB%A8%B9%EB%8A%94-%EC%A0%A4%EB%A6%AC-%ED%83%91%ED%91%B8%EB%A5%B4%ED%8A%B8-%EB%A7%9D%EA%B3%A0%EC%A0%A4%EB%A6%AC-320g",
]

# --- 유틸리티 함수 ---
def ensure_directory_exists(dir_path):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

def append_url_to_csv(filename, url):
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([url])

def normalize_filename(title):
    # 파일명으로 사용할 수 있도록 특수문자 제거 및 공백 처리
    title = re.sub(r'[^\w\s\uAC00-\uD7A3]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title

# --- 셀레니움 드라이버 설정 ---
def initialize_driver():
    print("[INFO] ChromeDriver를 초기화합니다...")
    options = Options()
    options.add_argument('--headless=new') # HEADLESS 모드 (주석 처리하면 브라우저가 보임)
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--start-maximized')
    options.add_argument('--log-level=3')
    options.add_argument('--incognito')
    options.add_argument('disable-infobars')
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    options.add_argument(f'user-agent={user_agent}')
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(90) # 페이지 로드 타임아웃을 90초로 늘림
        print("[SUCCESS] WebDriver가 성공적으로 초기화되었습니다.")
        return driver
    except Exception as e:
        print(f"[FATAL] WebDriver 초기화 실패: {e}")
        return None

# --- 이미지 다운로드 함수 ---
def download_image_to_local(image_url, save_path, product_title="unknown"):
    """
    주어진 URL에서 이미지를 다운로드하여 지정된 로컬 경로에 저장합니다.
    """
    try:
        response = requests.get(image_url, stream=True, timeout=15)
        response.raise_for_status()

        # 파일명 추출 및 유니크하게 만들기
        parsed_url = urlparse(image_url)
        file_name = os.path.basename(parsed_url.path)
        if not file_name or '.' not in file_name:
            ext = 'jpg' 
            content_type = response.headers.get('content-type', '').lower()
            if 'image/png' in content_type: ext = 'png'
            elif 'image/gif' in content_type: ext = 'gif'
            elif 'image/webp' in content_type: ext = 'webp'
            file_name = f"image_{int(time.time() * 1000)}_{os.path.basename(parsed_url.path.strip('/') or 'no_name')}.{ext}"
        
        file_name = re.sub(r'[<>:"/\\|?*]', '_', file_name) # 파일명에 불가능한 문자 제거

        full_file_path = os.path.join(save_path, file_name)

        if os.path.exists(full_file_path) and os.path.getsize(full_file_path) > 0:
            print(f"    [INFO] 로컬 이미지 파일이 이미 존재: {full_file_path}. 재다운로드 건너뜀.")
            return full_file_path

        with open(full_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"    [SUCCESS] 이미지 다운로드: '{file_name}' to '{save_path}'")
        return full_file_path
    except requests.exceptions.RequestException as e:
        print(f"    [ERROR] 로컬 이미지 다운로드 실패 (상품: '{product_title}', URL: '{image_url}'): {e}")
        return None
    except Exception as e:
        print(f"    [ERROR] 로컬 이미지 저장 중 오류 발생 (상품: '{product_title}', URL: '{image_url}'): {e}")
        return None

# --- 개별 상품 상세 페이지에서 'Product Info' 섹션 스크래핑 (개선된 로딩/요소 대기) ---
def scrape_single_product_info(driver, product_url):
    print(f"\n{'='*30}\n[SCRAPING] 상품 정보 시작: {product_url}\n{'='*30}")
    try:
        driver.get(product_url)
        
        # 페이지 로드 완료 대기
        WebDriverWait(driver, 30).until(lambda d: d.execute_script('return document.readyState') == 'complete')
        print("[INFO] 초기 페이지 로드 완료. 5초 대기 후 PageDown 스크롤 시작.")
        time.sleep(5) # 초기 로딩 후 충분한 대기

        # PageDown 스크롤을 통해 동적 콘텐츠 로드 유도
        print("[INFO] 상품 상세 페이지에서 PageDown으로 콘텐츠 로드를 유도합니다 (0.5초 간격으로 20회)...")
        for _ in range(20): # 기존 15회에서 20회로 늘림
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
            time.sleep(0.5) 
        
        # 스크롤 최하단까지 추가 스크롤하여 모든 콘텐츠 로드 시도
        print("[INFO] 페이지 최하단까지 스크롤하여 모든 동적 콘텐츠를 로드합니다...")
        last_height = driver.execute_script("return document.body.scrollHeight")
        scroll_attempts = 0
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2) # 스크롤 후 콘텐츠 로드 대기
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                scroll_attempts += 1
                if scroll_attempts > 3: # 3번 이상 높이 변화가 없으면 종료
                    break
            else:
                scroll_attempts = 0 # 높이 변화가 있으면 초기화
            last_height = new_height
        print("[INFO] 페이지 스크롤 완료.")
        
        driver.execute_script("window.scrollTo(0, 0);") # 다시 상단으로 스크롤하여 상품명 확인 용이
        time.sleep(2) # 스크롤 후 대기

        product_title = f"상품명-없음_{int(time.time())}"
        
        # 상품명 추출 시도
        # 여러 셀렉터를 순서대로 시도하며 가장 먼저 찾은 것을 사용
        product_title_selectors = [
            (By.XPATH, '//*[@id="TPAMultiSection_j38fck9a"]/div/div/div/div/article/nav/div[1]/div/a[2]'), # 요청하신 XPath 추가 (최우선)
            (By.CSS_SELECTOR, '[data-hook="product-page-title"]'), # Wix 기본 상품명
            (By.XPATH, '//h1[contains(@class, "product-title")]'), # product-title 클래스를 가진 h1 (자주 사용됨)
            (By.XPATH, '//h1[contains(@data-testid, "product-title")]'), # product-title data-testid를 가진 h1 (Wix에서 발견될 수 있음)
            (By.XPATH, '//h1'), # 일반적인 h1 태그 (가장 포괄적)
            (By.XPATH, '//article//h1'), # article 태그 내 h1 (의미론적 구조 고려)
        ]

        found_title = False
        for selector_type, selector_value in product_title_selectors:
            try:
                print(f"[INFO] 상품명 추출 시도 중... (셀렉터 타입: {selector_type}, 값: '{selector_value}')")
                title_element = WebDriverWait(driver, 15).until( # 각 셀렉터에 대해 15초 대기
                    EC.visibility_of_element_located((selector_type, selector_value))
                )
                raw_title = title_element.text.strip()
                if raw_title:
                    product_title = normalize_filename(raw_title) 
                    print(f"[SUCCESS] 상품명 추출: '{product_title}' (셀렉터 타입: {selector_type}, 값: '{selector_value}')")
                    found_title = True
                    break # 상품명을 찾았으니 반복 중단
                else:
                    print(f"[WARNING] '{selector_value}' 태그는 찾았으나 텍스트가 비어있습니다. 다음 셀렉터 시도합니다.")
            except Exception as e:
                print(f"[WARNING] 상품명 추출 실패 (셀렉터: '{selector_value}'): {e}. 다음 셀렉터 시도합니다.")
            time.sleep(1) # 각 셀렉터 시도 후 잠시 대기

        if not found_title:
            print(f"[WARNING] 모든 상품명 추출 시도 실패. URL 기반으로 폴더명을 생성합니다.")
            try:
                url_segment = product_url.split('/')[-1]
                decoded_segment = unquote(url_segment) 
                product_title = normalize_filename(decoded_segment) or f"상품명-없음_{int(time.time())}"
                print(f"[INFO] URL 기반 상품명: '{product_title}'")
            except Exception as url_e:
                print(f"[ERROR] URL 기반 상품명 생성 실패: {url_e}")

        product_info_parent_container = None
        # 상품 상세 정보 섹션 CSS 셀렉터/XPath 시도
        # 가장 안정적인 셀렉터를 먼저 시도합니다.
        target_selectors = [
            (By.XPATH, '//*[@id="bgLayers_comp-ku3webxr"]/div[1]'), # 새로 추가된 XPath (최우선)
            (By.CSS_SELECTOR, 'div[data-hook="product-page-description"]'), # Wix의 상품 설명 영역
            (By.XPATH, '//*[@id="comp-ku3wpngu"]/div/div'), # 기존 ID 기반 XPath (성공했던 XPath)
            (By.XPATH, '//section[.//span[contains(text(), "Product Info")]]/div/div'), # 텍스트 기반 XPath (section 부모)
            (By.XPATH, '//div[.//span[contains(text(), "Product Info")]]/div/div') # 텍스트 기반 XPath (div 부모)
        ]

        found_container = False
        for selector_type, selector_value in target_selectors:
            try:
                print(f"[INFO] 'Product Info' 부모 컨테이너를 찾기 위해 '{selector_type}' '{selector_value}' 시도 중...")
                product_info_parent_container = WebDriverWait(driver, 20).until( # 각 시도에 20초 대기
                    EC.visibility_of_element_located((selector_type, selector_value))
                )
                print(f"[SUCCESS] '{selector_type}' '{selector_value}' 기반 'Product Info' 부모 컨테이너를 찾았습니다.")
                found_container = True
                break
            except Exception:
                print(f"[WARNING] '{selector_type}' '{selector_value}'로 컨테이너를 찾지 못했습니다. 다음 셀렉터 시도 중...")
        
        if not found_container:
            print(f"[ERROR] '{product_url}'에서 'Product Info' 부모 컨테이너를 모든 시도 내에 찾지 못했습니다.")
            append_url_to_csv(INVALID_URLS_CSV, product_url)
            print(f"[INFO] 상품 정보 컨테이너를 찾지 못하여 '{product_url}'에 대한 폴더를 생성하지 않습니다.")
            return False # 실패

        print("[INFO] 'Product Info' 부모 컨테이너에서 필요한 요소들을 추출하고 정제합니다...")
        
        # 상품 정보 컨테이너를 찾은 후에만 폴더를 생성합니다.
        product_folder = os.path.join(BASE_DIR, product_title)
        ensure_directory_exists(product_folder)
        image_save_dir = os.path.join(product_folder, "images") # 이미지 저장 폴더
        ensure_directory_exists(image_save_dir)
        print(f"[INFO] 저장 폴더: {product_folder}, 이미지 폴더: {image_save_dir}")

        # JavaScript 클리닝 로직 (글자 색상 및 원본 태그 유지, 중복 텍스트 제거, <br> 제거)
        JS_EXTRACT_AND_CLEAN = r"""
        const container = arguments[0];
        let cleanedHtmlParts = [];
        let lastAddedTrimmedText = ''; 
        
        // Product Info 또는 제품 정보 텍스트를 가진 span을 먼저 제거
        container.querySelectorAll('span').forEach(el => {
            const textContent = el.textContent.trim().replace(/\u200B/g, ''); 
            if (textContent === 'Product Info' || textContent === '제품 정보') {
                el.remove();
            }
        });

        // 텍스트와 이미지 요소를 모두 가져옴. 텍스트는 순서대로 처리
        const allElements = container.querySelectorAll('span, p, h1, h2, h3, h4, h5, h6, li, img');
        
        allElements.forEach(el => {
            let currentProcessedHtml = '';
            const tagName = el.tagName.toLowerCase();

            if (tagName.match(/^(span|p|h[1-6]|li)$/)) { // 텍스트를 포함할 수 있는 태그들
                let textContent = el.textContent.trim();
                textContent = textContent.replace(/\u200B/g, ''); 
                textContent = textContent.replace(/\s\s+/g, ' '); 
                
                if (textContent === '' || textContent.match(/^[\s\u200B]*$/)) {
                    lastAddedTrimmedText = ''; 
                    return;
                }

                const currentTrimmedText = textContent.replace(/\s/g, ''); 
                const prevTrimmedText = lastAddedTrimmedText.replace(/\s/g, '');

                if (currentTrimmedText === prevTrimmedText ||
                    (prevTrimmedText !== '' && currentTrimmedText.includes(prevTrimmedText) && currentTrimmedText.length > prevTrimmedText.length && prevTrimmedText.length / currentTrimmedText.length > 0.5) ||
                    (currentTrimmedText !== '' && prevTrimmedText.includes(currentTrimmedText) && prevTrimmedText.length > currentTrimmedText.length && currentTrimmedText.length / prevTrimmedText.length > 0.5)
                ) {
                    return; 
                }

                // 원본 태그와 스타일 유지
                let clonedEl = el.cloneNode(true);
                // 불필요한 id/class 제거 (선택 사항, 충돌 방지)
                clonedEl.removeAttribute('id');
                clonedEl.removeAttribute('class');
                // style 속성만은 유지 (글자 색상 등)
                currentProcessedHtml = clonedEl.outerHTML; 
                lastAddedTrimmedText = textContent; 

            } else if (tagName === 'img') { // 이미지 태그
                let clonedImg = el.cloneNode(true);
                let originalSrc = clonedImg.getAttribute('src');
                const originalAlt = clonedImg.getAttribute('alt') || '';

                // Wix의 v1/fill/ 쿼리 파라미터 제거 로직 유지
                if (originalSrc) {
                    let cleanedSrc = originalSrc.split('/v1/fill/')[0];
                    const queryIndex = cleanedSrc.indexOf('?');
                    if (queryIndex !== -1) {
                        cleanedSrc = cleanedSrc.substring(0, queryIndex);
                    }
                    
                    const finalExtensionMatch = cleanedSrc.match(/\.(png|jpg|jpeg|gif|webp)$/i);
                    if (!finalExtensionMatch) {
                        const fullSrcMatch = originalSrc.match(/\.(png|jpg|jpeg|gif|webp)([~?].*|$)/i);
                        if (fullSrcMatch) {
                            cleanedSrc += fullSrcMatch[0].split(/[~?]/)[0];
                        }
                    }
                    originalSrc = cleanedSrc;
                }
                
                // 이미지 태그에서 불필요한 속성 제거
                for (let i = clonedImg.attributes.length - 1; i >= 0; i--) {
                    const attrName = clonedImg.attributes[i].name;
                    // 'src', 'alt', 'style' (만약 인라인 스타일이 있다면) 등은 유지
                    if (attrName !== 'src' && attrName !== 'alt' && attrName !== 'style' && attrName !== 'width' && attrName !== 'height') {
                        clonedImg.removeAttribute(attrName);
                    }
                }
                # 원본 src와 alt 속성은 유지
                if (originalSrc) {
                    clonedImg.setAttribute('src', originalSrc);
                }
                if (originalAlt) {
                    clonedImg.setAttribute('alt', originalAlt);
                }
                
                currentProcessedHtml = clonedImg.outerHTML;
                lastAddedTrimmedText = ''; 

            }
            if (currentProcessedHtml.trim() !== '') {
                cleanedHtmlParts.push(currentProcessedHtml);
            }
        });

        let finalHtml = cleanedHtmlParts.join('\n'); // JavaScript 내부에서 '\n'은 줄 바꿈 문자

        // <br> 태그를 모두 제거 (새로운 요구사항)
        finalHtml = finalHtml.replace(/<br[^>]*>/g, ''); // <br/>, <br > 등 다양한 형태 처리
        // 여러 개의 연속된 줄바꿈 문자를 하나의 줄바꿈 문자로 줄임
        finalHtml = finalHtml.replace(/\n{2,}/g, '\n'); 
        // 텍스트 사이의 불필요한 공백/줄바꿈도 하나로 줄임
        finalHtml = finalHtml.replace(/\s\s+/g, ' '); 

        return finalHtml;
        """

        cleaned_html_parts = driver.execute_script(JS_EXTRACT_AND_CLEAN, product_info_parent_container)

        # BeautifulSoup을 사용하여 HTML 콘텐츠에서 이미지 URL 추출 및 로컬 저장
        soup = BeautifulSoup(cleaned_html_parts, 'html.parser')
        image_tags_in_html = soup.find_all('img')
        
        for img_tag in image_tags_in_html:
            img_src = img_tag.get('src')
            if img_src:
                # 상대 경로 URL을 절대 경로로 변환 (현재 페이지 URL을 기준으로)
                absolute_img_src = urljoin(product_url, img_src)
                download_image_to_local(absolute_img_src, image_save_dir, product_title)
            
        # HTML 파일 저장
        html_file_path = os.path.join(product_folder, "product_info.html")
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write("<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"UTF-8\">\n<title>상품 정보</title>\n<style>\n")
            # body 스타일은 제거합니다. (Shopify 테마의 body 스타일에 맡깁니다)
            
            # img 태그에 대한 스타일 (가장 중요)
            f.write("img { max-width: 100% !important; height: auto !important; display: block !important; margin: 0 auto !important; object-fit: contain !important; }\n")
            
            # Shopify 상품 설명 영역 내의 p, div, span 등 이미지의 부모 요소에 대한 스타일 추가 (매우 중요)
            # 이 클래스/태그 이름은 Shopify 테마에 따라 다를 수 있으므로, 실제 HTML 구조를 확인하고 적절히 조정해야 합니다.
            # 일반적으로 상품 설명은 .rte (rich text editor) 또는 .product-description 클래스 안에 있습니다.
            # 아래 셀렉터들은 Shopify에서 상품 설명 영역에 자주 사용되는 컨테이너들입니다.
            f.write(".rte img, .product-description img, /* Shopify 테마의 img 기본 스타일이 적용되는 경우 */\n")
            f.write(".rte p, .product-description p, .rte div, .product-description div, /* 이미지를 포함하는 p/div 컨테이너 */\n")
            f.write(".wix-style-product-info-container * { /* Wix에서 스크랩한 컨테이너의 자식 요소들에 대한 일반적인 규칙 */\n")
            f.write("    max-width: 100% !important; /* 최대 너비를 100%로 설정하여 부모 컨테이너를 넘지 않도록 */\n")
            f.write("    width: auto !important; /* 너비를 자동으로 조정 */\n")
            f.write("    box-sizing: border-box !important; /* 패딩/보더가 너비에 포함되도록 */\n")
            f.write("    overflow: visible !important; /* 내용이 잘리지 않도록 (핵심) */\n")
            f.write("    padding: 0 !important; /* 불필요한 패딩 제거 */\n")
            f.write("    margin: 0 auto !important; /* 중앙 정렬 */\n")
            f.write("}\n")
            f.write("</style>\n</head>\n<body>\n")
            f.write(cleaned_html_parts)
            f.write("\n</body>\n</html>")
        print(f"[SUCCESS] 정리된 'Product Info' HTML을 '{html_file_path}'에 저장했습니다.")

        append_url_to_csv(VALID_URLS_CSV, product_url)
        print(f"[COMPLETE] '{product_title}' 상품 정보 처리가 완료되었습니다.")
        return True # 성공

    except Exception as e:
        print(f"[FATAL_ERROR] '{product_url}' 처리 중 심각한 오류 발생: {e}. 이 URL은 'INVALID_URLS.CSV'에 기록됩니다.")
        append_url_to_csv(INVALID_URLS_CSV, product_url)
        return False # 실패

# --- 메인 실행 로직 ---
def main():
    ensure_directory_exists(BASE_DIR)
    ensure_directory_exists(URL_DIR)

    driver = initialize_driver()
    if not driver: return

    failed_urls_on_rescrape = [] # 재스크래핑 중 실패한 URL을 저장할 리스트

    try:
        if not TARGET_URLS_TO_RESCRAPE:
            print("\n[INFO] 재스크래핑할 URL 목록이 비어 있습니다. 스크립트를 종료합니다.")
            return

        print(f"\n{'='*50}\n[INFO] 총 {len(TARGET_URLS_TO_RESCRAPE)}개의 특정 상품 상세 페이지에 대해 재스크래핑을 시작합니다.\n{'='*50}\n")

        for url in tqdm(TARGET_URLS_TO_RESCRAPE, desc="URL 재스크래핑 진행", unit="URL"):
            success = scrape_single_product_info(driver, url)
            if not success:
                failed_urls_on_rescrape.append(url)
            time.sleep(3) # 각 상품 스크래핑 간 대기

    except Exception as e:
        print(f"\n[CRITICAL] 메인 로직 실행 중 오류 발생: {e}")
    finally:
        if driver: driver.quit()
        print("\n--- 전체 스크립트 실행 완료 ---")

        if failed_urls_on_rescrape:
            print(f"\n[SUMMARY] 재스크래핑에 실패한 URL {len(failed_urls_on_rescrape)}개:")
            for url in failed_urls_on_rescrape:
                print(f"  - {url}")
            # 이 URL들을 `INVALID_URLS_CSV`에 다시 기록하거나, 새로운 실패 목록 파일에 기록할 수 있습니다.
            # 현재 `scrape_single_product_info` 내에서 실패 시 `INVALID_URLS_CSV`에 기록하므로, 추가적인 기록은 필요 없습니다.
        else:
            print("\n[SUMMARY] 모든 특정 URL을 성공적으로 재스크래핑했습니다!")


if __name__ == "__main__":
    main()