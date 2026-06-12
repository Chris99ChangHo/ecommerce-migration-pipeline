import os
import csv
import re
import time
import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

# --- 파일 및 URL 설정 ---
BASE_DIR = "상품설명사진"
URL_DIR = "url"
VALID_URLS_CSV = os.path.join(URL_DIR, "valid_urls.csv")
INVALID_URLS_CSV = os.path.join(URL_DIR, "invalid_urls.csv")
MAIN_PAGE_URL = os.getenv("SOURCE_URL", "https://example-source.com") + "/"

# --- 유틸리티 함수 ---
def ensure_directory_exists(dir_path):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

def load_urls_from_csv(filename):
    ensure_directory_exists(os.path.dirname(filename))
    if not os.path.exists(filename):
        return set()
    with open(filename, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        return {row[0] for row in reader if row}

def append_url_to_csv(filename, url):
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([url])

# --- 셀레니움 드라이버 설정 ---
def initialize_driver():
    print("[INFO] ChromeDriver를 초기화합니다...")
    options = Options()
    # ====================================================================
    # 디버깅을 위해 headless 모드를 잠시 비활성화하고 실제 브라우저를 확인하는 것을 강력히 권장합니다.
    # 문제가 해결되면 'headless=new' 주석을 해제하세요.
    options.add_argument('--headless=new') # HEADLESS 모드 (주석 처리하면 브라우저가 보임)
    # ====================================================================
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
        driver.set_page_load_timeout(60)
        print("[SUCCESS] WebDriver가 성공적으로 초기화되었습니다.")
        return driver
    except Exception as e:
        print(f"[FATAL] WebDriver 초기화 실패: {e}")
        return None

# --- 상품 상세 페이지 URL 탐색 (메인 페이지에서 /product-page/ 링크 수집) ---
def discover_product_urls(driver, existing_urls):
    print(f"\n[INFO] 메인 페이지({MAIN_PAGE_URL})에서 새로운 상품 상세 페이지 URL을 탐색합니다...")
    driver.get(MAIN_PAGE_URL)
    WebDriverWait(driver, 20).until(lambda d: d.execute_script('return document.readyState') == 'complete')
    time.sleep(3)
    print("[INFO] 페이지를 스크롤하여 모든 상품 링크를 로드합니다...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(10):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height: break
        last_height = new_height

    product_links_xpath = '//a[contains(@href, "/product-page/")]'

    try:
        link_elements = driver.find_elements(By.XPATH, product_links_xpath)
        discovered_urls = {link.get_attribute('href').split('?')[0].split('#')[0] for link in link_elements if link.get_attribute('href')}
        new_urls = discovered_urls - existing_urls
        print(f"[INFO] {len(discovered_urls)}개의 상품 상세 페이지 URL 발견, 그 중 {len(new_urls)}개가 새 URL입니다.")
        return new_urls
    except Exception as e:
        print(f"[ERROR] 상품 상세 페이지 링크 수집 중 오류: {e}")
        return set()

# --- 개별 상품 상세 페이지에서 'Product Info' 섹션 스크래핑 ---
def scrape_single_product_info(driver, product_url):
    print(f"\n{'='*30}\n[SCRAPING] 상품 정보 시작: {product_url}\n{'='*30}")
    try:
        driver.get(product_url)
        # 페이지 로드 완료 대기
        WebDriverWait(driver, 20).until(lambda d: d.execute_script('return document.readyState') == 'complete')
        time.sleep(3) # 초기 렌더링 대기

        # 기존 새로고침 로직 제거됨
        # driver.refresh() 로직은 삭제되었습니다.

        print("[INFO] 상품 상세 페이지에서 PageDown으로 콘텐츠 로드를 유도합니다 (0.5초 간격으로 15회)...")
        # PageDown 15번, 각 0.5초 간격
        for _ in range(15):
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
            time.sleep(0.5) # 0.5초 간격으로 변경
        
        # 다시 최상단으로 스크롤 (상품명 등 상단 요소가 제대로 보이도록)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2) # 스크롤 후 콘텐츠 안정화 대기

        # 1. 상품명 추출 로직 강화: visibility_of_element_located 사용 및 재시도
        product_title = f"상품명-없음_{int(time.time())}"
        for attempt in range(5):
            try:
                print(f"[INFO] 상품명 추출 시도 중... (시도 {attempt + 1}/5)")
                title_element = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.XPATH, '//h1')))
                raw_title = title_element.text.strip()
                if raw_title:
                    product_title = re.sub(r'[\/:*?"<>|]', '', raw_title).strip()
                    print(f"[SUCCESS] 상품명 추출: '{product_title}'")
                    break
                else:
                    print("[WARNING] h1 태그는 찾았으나 텍스트가 비어있습니다. 재시도합니다.")
                    time.sleep(1.5)
            except Exception as e:
                print(f"[WARNING] 상품명 추출 실패 (h1 태그 없음 또는 찾지 못함): {e}. 재시도합니다.")
                time.sleep(1.5)
        else:
            print(f"[WARNING] 모든 상품명 추출 시도 실패. URL 기반으로 폴더명을 생성합니다.")
            try:
                url_segment = product_url.split('/')[-1]
                decoded_segment = requests.utils.unquote(url_segment)
                product_title = re.sub(r'[\/:*?"<>|]', '', decoded_segment).strip() or f"상품명-없음_{int(time.time())}"
                print(f"[INFO] URL 기반 상품명: '{product_title}'")
            except Exception as url_e:
                print(f"[ERROR] URL 기반 상품명 생성 실패: {url_e}")

        product_folder = os.path.join(BASE_DIR, product_title)
        ensure_directory_exists(product_folder)
        print(f"[INFO] 저장 폴더: {product_folder}")

        # 2. 'Product Info' 부모 컨테이너 찾기: XPath 안정성 강화
        product_info_parent_container = None
        # 기존 ID 기반 XPath
        target_xpath_id = '//*[@id="comp-ku3wpngu"]/div/div'
        # 대안 XPath (더 일반적인 패턴, 상품 정보 제목 등 활용)
        target_xpath_text_based = '//section[.//span[contains(text(), "Product Info")]]/div/div | //div[.//span[contains(text(), "Product Info")]]/div/div'

        try:
            print(f"[INFO] 'Product Info' 부모 컨테이너를 찾기 위해 XPath '{target_xpath_id}' 시도 중...")
            product_info_parent_container = WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.XPATH, target_xpath_id))
            )
            print("[SUCCESS] ID 기반 'Product Info' 부모 컨테이너를 찾았습니다.")
        except Exception:
            print(f"[WARNING] ID 기반 XPath '{target_xpath_id}'로 컨테이너를 찾지 못했습니다. 텍스트 기반 XPath 시도 중...")
            try:
                product_info_parent_container = WebDriverWait(driver, 10).until(
                    EC.visibility_of_element_located((By.XPATH, target_xpath_text_based))
                )
                print("[SUCCESS] 텍스트 기반 'Product Info' 부모 컨테이너를 찾았습니다.")
            except Exception as e:
                print(f"[ERROR] '{product_url}'에서 'Product Info' 부모 컨테이너를 {20}초(총 시도 시간) 내에 찾지 못했습니다. 이 URL은 스크래핑할 수 없습니다.")
                print(f"[ERROR_DETAIL] 에러 메시지: {e}")
                append_url_to_csv(INVALID_URLS_CSV, product_url)
                return

        print("[INFO] 'Product Info' 부모 컨테이너에서 필요한 요소들을 추출하고 정제합니다...")

        # 3. JavaScript 클리닝 로직 (텍스트는 모두 <span>으로 통일, 이미지는 원본에 가깝게)
        JS_EXTRACT_AND_CLEAN = """
        const container = arguments[0];
        let cleanedHtmlParts = [];
        let lastAddedTextContent = ''; // 최종 추가된 텍스트 내용 (빈 줄 구분용)
        let previousWasEmptyText = false; // 연속된 빈 텍스트 태그 처리 플래그

        // 우선적으로 제거할 텍스트 (예: 'Product Info', '제품 정보') - <span> 태그만 대상으로
        container.querySelectorAll('span').forEach(el => {
            const textContent = el.textContent.trim().replace(/\\u200B/g, '');
            if (textContent === 'Product Info' || textContent === '제품 정보') {
                el.remove();
            }
        });

        // 컨테이너 내의 모든 텍스트 및 이미지 요소를 가져옴.
        // 중요: 텍스트는 오직 span으로만 통일, 이미지는 원본에 가깝게
        const allElements = container.querySelectorAll('span, p, h1, h2, h3, h4, h5, h6, li, img');
        
        allElements.forEach(el => {
            let currentProcessedHtml = '';
            const tagName = el.tagName.toLowerCase();

            // 텍스트를 포함할 수 있는 태그들 (p, h1-h6, li, span)
            if (tagName.match(/^(span|p|h[1-6]|li)$/)) { 
                let textContent = el.textContent.trim();
                textContent = textContent.replace(/\\u200B/g, ''); // 제로 너비 공백 제거
                textContent = textContent.replace(/\\s\\s+/g, ' '); // 연속된 공백을 단일 공백으로

                // 텍스트 내용이 비어있거나 공백만 있는 경우
                if (textContent === '' || textContent.match(/^[\\s\\u200B]*$/)) {
                    // 연속된 빈 텍스트가 아닌 경우에만 <br> 추가
                    if (!previousWasEmptyText && cleanedHtmlParts.length > 0) {
                        cleanedHtmlParts.push('<br>'); 
                        previousWasEmptyText = true;
                    }
                    return; // 빈 텍스트 요소는 스킵
                } else {
                    previousWasEmptyText = false; // 유효한 텍스트가 나왔으므로 플래그 리셋
                }

                // **핵심: 텍스트 중복 제거 로직**
                // 이전에 추가된 텍스트와 현재 텍스트가 완전히 동일하면 건너뛰기
                if (textContent === lastAddedTextContent) {
                    return;
                }

                // 모든 텍스트 요소를 <span> 태그로 통일
                let tempSpan = document.createElement('span');
                tempSpan.textContent = textContent; // 순수 텍스트만 삽입
                currentProcessedHtml = tempSpan.outerHTML;
                lastAddedTextContent = textContent; // 최종 추가된 텍스트 내용 업데이트

            } else if (tagName === 'img') { // 이미지 태그
                let clonedImg = el.cloneNode(true);
                let originalSrc = clonedImg.getAttribute('src');
                const originalAlt = clonedImg.getAttribute('alt') || '';

                // Wix 이미지 URL 정제 로직 (지난번 완벽하다 하셨던 로직 기반)
                if (originalSrc) {
                    // v1/fill/ 이후의 모든 크기 조절 파라미터 제거
                    let cleanedSrc = originalSrc.split('/v1/fill/')[0];

                    // ~mv2.png? 혹은 ~mv2.jpg? 와 같이 확장자 뒤에 붙는 쿼리스트링 제거
                    const queryIndex = cleanedSrc.indexOf('?');
                    if (queryIndex !== -1) {
                        cleanedSrc = cleanedSrc.substring(0, queryIndex);
                    }
                    
                    // 마지막에 확장자가 없는 경우 원본 src에서 확장자 추측하여 붙이기 (다시 추가)
                    const finalExtensionMatch = cleanedSrc.match(/\\.(png|jpg|jpeg|gif|webp)$/i);
                    if (!finalExtensionMatch) {
                        const fullSrcMatch = originalSrc.match(/\\.(png|jpg|jpeg|gif|webp)([~?].*|$)/i);
                        if (fullSrcMatch) {
                            cleanedSrc += fullSrcMatch[0].split(/[~?]/)[0];
                        }
                    }
                    originalSrc = cleanedSrc;
                }
                
                // 모든 속성 제거 후 src와 alt만 다시 설정 (Shopify 호환성 유지)
                for (let i = clonedImg.attributes.length - 1; i >= 0; i--) {
                    clonedImg.removeAttribute(clonedImg.attributes[i].name);
                }
                if (originalSrc) {
                    clonedImg.setAttribute('src', originalSrc);
                }
                if (originalAlt) {
                    clonedImg.setAttribute('alt', originalAlt);
                }
                
                currentProcessedHtml = clonedImg.outerHTML;

                // 이미지가 나오면 텍스트 중복 검사 상태 리셋
                lastAddedTextContent = '';
                previousWasEmptyText = false;

            }
            // 최종 HTML 파트에 추가하기 전에 비어있지 않은지 다시 확인
            if (currentProcessedHtml.trim() !== '') {
                cleanedHtmlParts.push(currentProcessedHtml);
            }
        });

        return cleanedHtmlParts.join('\\n');
        """

        cleaned_html_parts = driver.execute_script(JS_EXTRACT_AND_CLEAN, product_info_parent_container)

        html_file_path = os.path.join(product_folder, "product_info.html")
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write("<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"UTF-8\">\n<title>상품 정보</title>\n<style>body { max-width: 800px; margin: 0 auto; padding: 20px; font-family: sans-serif; } img { max-width: 100%; height: auto; display: block; margin: 0 auto; }</style>\n</head>\n<body>\n")
            f.write(cleaned_html_parts)
            f.write("\n</body>\n</html>")
        print(f"[SUCCESS] 정리된 'Product Info' HTML을 '{html_file_path}'에 저장했습니다.")

        append_url_to_csv(VALID_URLS_CSV, product_url)
        print(f"[COMPLETE] '{product_title}' 상품 정보 처리가 완료되었습니다.")

    except Exception as e:
        print(f"[FATAL_ERROR] '{product_url}' 처리 중 심각한 오류 발생: {e}. 이 URL은 'INVALID_URLS.CSV'에 기록됩니다.")
        append_url_to_csv(INVALID_URLS_CSV, product_url)

# --- 메인 실행 로직 ---
def main():
    ensure_directory_exists(BASE_DIR)
    ensure_directory_exists(URL_DIR)

    processed_urls = load_urls_from_csv(VALID_URLS_CSV).union(load_urls_from_csv(INVALID_URLS_CSV))
    print(f"[INFO] 시작 전, 총 {len(processed_urls)}개의 URL을 이미 처리했습니다.")

    driver = initialize_driver()
    if not driver: return

    try:
        discovered_urls_from_main = discover_product_urls(driver, processed_urls)
        existing_valid_urls_in_csv = load_urls_from_csv(VALID_URLS_CSV)
        urls_to_process = (discovered_urls_from_main | existing_valid_urls_in_csv) - processed_urls

        if not urls_to_process:
            print("\n[INFO] 스크래핑할 새로운 상품 상세 페이지 URL이 없습니다.")
            return

        print(f"\n{'='*50}\n[INFO] 총 {len(urls_to_process)}개의 새로운 상품 상세 페이지에 대해 스크래핑을 시작합니다.\n{'='*50}\n")

        for url in sorted(list(urls_to_process)):
            scrape_single_product_info(driver, url)
            time.sleep(3)

    except Exception as e:
        print(f"\n[CRITICAL] 메인 로직 실행 중 오류 발생: {e}")
    finally:
        if driver: driver.quit()
        print("\n--- 전체 스크립트 실행 완료 ---")

if __name__ == "__main__":
    main()