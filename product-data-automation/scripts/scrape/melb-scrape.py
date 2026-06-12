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
MAIN_PAGE_URL = os.getenv("SOURCE_URL", "https://example-source.com") + "/all"

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
    # 디버깅 완료 후 아래 줄의 주석을 해제하세요.
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

# --- 상품 상세 페이지 URL 탐색 (모든 페이지에서 /product-page/ 링크 수집) ---
def discover_product_urls(driver, existing_urls):
    print(f"\n[INFO] 메인 페이지({MAIN_PAGE_URL})에서 새로운 상품 상세 페이지 URL을 탐색합니다...")
    
    all_discovered_urls = set()
    current_page = 1
    has_new_links = True
    
    product_links_xpath = '//a[contains(@href, "/product-page/")]'

    while has_new_links:
        page_url = f"{MAIN_PAGE_URL}?page={current_page}"
        print(f"[INFO] 페이지를 로드합니다: {page_url}")
        
        try:
            driver.get(page_url)
            # 페이지 로드 완료 대기 (기본 DOM)
            WebDriverWait(driver, 20).until(lambda d: d.execute_script('return document.readyState') == 'complete')
            time.sleep(1) # 추가적인 짧은 대기

            # 페이지의 첫 번째 상품 링크가 로드될 때까지 기다림 (더 견고한 대기)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, product_links_xpath))
            )
            print(f"[INFO] 페이지 {current_page}의 상품 목록 요소가 나타났습니다. 추가 로딩 대기.")
            
            # 페이지를 끝까지 스크롤하여 혹시 모를 동적 로딩 유도
            last_height = driver.execute_script("return document.body.scrollHeight")
            scroll_attempts = 0
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2) # 스크롤 후 콘텐츠 로드를 위한 대기
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
                scroll_attempts += 1
                if scroll_attempts > 5: # 무한 스크롤이 아닌 페이지네이션 방식이라면 너무 오래 스크롤할 필요 없음
                    break
            print(f"[INFO] 페이지 {current_page} 스크롤 완료.")


            # 링크 수집 시도 (여러 번 시도하여 가장 많은 링크가 발견될 때까지)
            max_links_found = set()
            for _ in range(5): # 5번까지 링크 수집 시도
                link_elements = driver.find_elements(By.XPATH, product_links_xpath)
                current_attempt_urls = {link.get_attribute('href').split('?')[0].split('#')[0] for link in link_elements if link.get_attribute('href')}
                if len(current_attempt_urls) > len(max_links_found):
                    max_links_found = current_attempt_urls
                time.sleep(0.5) # 짧은 대기 후 다시 시도

            current_page_urls = max_links_found
            
            # 새로 발견된 링크 확인
            new_links_on_this_page = current_page_urls - all_discovered_urls
            
            # 현재 페이지에서 발견된 링크가 없거나, 이전에 발견된 총 링크와 변화가 없으면 종료
            if not new_links_on_this_page and current_page_urls.issubset(all_discovered_urls):
                has_new_links = False 
                print(f"[INFO] 페이지 {current_page}에서 새로운 상품 링크를 찾지 못했습니다. 탐색을 종료합니다.")
            else:
                all_discovered_urls.update(current_page_urls)
                print(f"[INFO] 페이지 {current_page}에서 {len(current_page_urls)}개의 상품 링크 발견. 현재까지 총 {len(all_discovered_urls)}개.")
                current_page += 1 # 다음 페이지로 이동

        except Exception as e:
            print(f"[ERROR] 페이지 {current_page} 로드 또는 링크 수집 중 오류: {e}")
            has_new_links = False # 오류 발생 시 탐색 종료
    
    new_urls_for_processing = all_discovered_urls - existing_urls
    print(f"[INFO] 최종적으로 스크래핑할 {len(new_urls_for_processing)}개의 새로운 상품 상세 페이지 URL을 발견했습니다.")
    print(f"[INFO] 총 발견된 상품 URL: {len(all_discovered_urls)}개 (중복 포함 이전 발견된 URL 제외 전)")
    return new_urls_for_processing

# --- 개별 상품 상세 페이지에서 'Product Info' 섹션 스크래핑 ---
def scrape_single_product_info(driver, product_url):
    print(f"\n{'='*30}\n[SCRAPING] 상품 정보 시작: {product_url}\n{'='*30}")
    try:
        driver.get(product_url)
        WebDriverWait(driver, 20).until(lambda d: d.execute_script('return document.readyState') == 'complete')
        time.sleep(3)

        print("[INFO] 상품 상세 페이지에서 PageDown으로 콘텐츠 로드를 유도합니다 (0.5초 간격으로 15회)...")
        for _ in range(15):
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)
            time.sleep(0.5)
        
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)

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

        product_info_parent_container = None
        target_xpath_id = '//*[@id="comp-ku3wpngu"]/div/div'
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
                print(f"[ERROR] '{product_url}'에서 'Product Info' 부모 컨테이너를 20초(총 시도 시간) 내에 찾지 못했습니다.")
                print(f"[ERROR_DETAIL] 에러 메시지: {e}")
                append_url_to_csv(INVALID_URLS_CSV, product_url)
                # 컨테이너를 찾지 못하면 여기서 함수 종료 (폴더 생성 건너뛰기)
                print(f"[INFO] 상품 정보 컨테이너를 찾지 못하여 '{product_url}'에 대한 폴더를 생성하지 않습니다.")
                return 

        print("[INFO] 'Product Info' 부모 컨테이너에서 필요한 요소들을 추출하고 정제합니다...")
        
        # 상품 정보 컨테이너를 찾은 후에만 폴더를 생성합니다.
        product_folder = os.path.join(BASE_DIR, product_title)
        ensure_directory_exists(product_folder)
        print(f"[INFO] 저장 폴더: {product_folder}")

        # JavaScript 클리닝 로직 (텍스트는 모두 <h5>으로 통일, 이미지는 원본에 가깝게, 중복 텍스트 제거 강화, <br> 제거)
        JS_EXTRACT_AND_CLEAN = r"""
        const container = arguments[0];
        let cleanedHtmlParts = [];
        let lastAddedTrimmedText = ''; 
        
        // Product Info 또는 제품 정보 텍스트를 가진 span을 먼저 제거
        container.querySelectorAll('span').forEach(el => {
            const textContent = el.textContent.trim().replace(/\u200B/g, ''); // \u200B는 유니코드 제로 너비 공백 문자
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
                textContent = textContent.replace(/\u200B/g, ''); // 제로 너비 공백 제거
                textContent = textContent.replace(/\s\s+/g, ' '); // 연속된 공백을 단일 공백으로
                
                // 빈 텍스트는 스킵 (br 태그 추가 로직 제거)
                if (textContent === '' || textContent.match(/^[\s\u200B]*$/)) {
                    lastAddedTrimmedText = ''; 
                    return;
                }

                // 강화된 텍스트 중복 제거 로직
                const currentTrimmedText = textContent.replace(/\s/g, ''); 
                const prevTrimmedText = lastAddedTrimmedText.replace(/\s/g, '');

                if (currentTrimmedText === prevTrimmedText ||
                    (prevTrimmedText !== '' && currentTrimmedText.includes(prevTrimmedText) && currentTrimmedText.length > prevTrimmedText.length && prevTrimmedText.length / currentTrimmedText.length > 0.5) ||
                    (currentTrimmedText !== '' && prevTrimmedText.includes(currentTrimmedText) && prevTrimmedText.length > currentTrimmedText.length && currentTrimmedText.length / prevTrimmedText.length > 0.5)
                ) {
                    return; 
                }

                // 텍스트 요소를 <h5> 태그로 통일
                let tempH5 = document.createElement('h5'); // <--- h5로 변경
                tempH5.textContent = textContent;
                currentProcessedHtml = tempH5.outerHTML; // <--- h5로 변경
                lastAddedTrimmedText = textContent; 

            } else if (tagName === 'img') { // 이미지 태그
                let clonedImg = el.cloneNode(true);
                let originalSrc = clonedImg.getAttribute('src');
                const originalAlt = clonedImg.getAttribute('alt') || '';

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
                lastAddedTrimmedText = ''; 

            }
            if (currentProcessedHtml.trim() !== '') {
                cleanedHtmlParts.push(currentProcessedHtml);
            }
        });

        let finalHtml = cleanedHtmlParts.join('\n'); // JavaScript 내부에서 '\n'은 줄 바꿈 문자

        // <br> 태그를 모두 제거 (새로운 요구사항)
        finalHtml = finalHtml.replace(/<br>/g, '');
        // 여러 개의 연속된 줄바꿈 문자를 하나의 줄바꿈 문자로 줄임
        finalHtml = finalHtml.replace(/\n{2,}/g, '\n'); 
        // 텍스트 사이의 불필요한 공백/줄바꿈도 하나로 줄임
        finalHtml = finalHtml.replace(/\s\s+/g, ' '); 

        return finalHtml;
        """

        cleaned_html_parts = driver.execute_script(JS_EXTRACT_AND_CLEAN, product_info_parent_container)

        html_file_path = os.path.join(product_folder, "product_info.html")
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write("<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"UTF-8\">\n<title>상품 정보</title>\n<style>\nbody { max-width: 800px; margin: 0 auto; padding: 20px; font-family: sans-serif; font-size: 16px; line-height: 1.6; }\n"
                    "h5 { font-size: 1.1em; margin-top: 0.8em; margin-bottom: 0.4em; }\n" # h5 스타일 추가
                    "img { max-width: 100%; height: auto; display: block; margin: 0 auto; object-fit: contain; }\n"
                    "</style>\n</head>\n<body>\n")
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
        urls_to_process = discovered_urls_from_main

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