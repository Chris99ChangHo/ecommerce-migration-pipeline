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
from dotenv import load_dotenv

load_dotenv()

# --- 파일 및 URL 설정 ---
BASE_DIR = os.path.join("data", "productdesimg")
URL_DIR = os.path.join("data", "urls")
VALID_URLS_CSV = os.path.join(URL_DIR, "valid_urls.csv")
INVALID_URLS_CSV = os.path.join(URL_DIR, "invalid_urls.csv")
MAIN_PAGE_URL = os.getenv("SOURCE_URL", "https://example-source.com/all")

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
    options.add_argument('--headless=new') # HEADLESS 모드
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

# --- 상품 상세 페이지 URL 탐색 ---
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
            WebDriverWait(driver, 20).until(lambda d: d.execute_script('return document.readyState') == 'complete')
            time.sleep(1)

            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, product_links_xpath)))
            
            last_height = driver.execute_script("return document.body.scrollHeight")
            scroll_attempts = 0
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height: break
                last_height = new_height
                scroll_attempts += 1
                if scroll_attempts > 5: break
            
            max_links_found = set()
            for _ in range(5):
                link_elements = driver.find_elements(By.XPATH, product_links_xpath)
                current_attempt_urls = {link.get_attribute('href').split('?')[0].split('#')[0] for link in link_elements if link.get_attribute('href')}
                if len(current_attempt_urls) > len(max_links_found):
                    max_links_found = current_attempt_urls
                time.sleep(0.5)

            current_page_urls = max_links_found
            new_links_on_this_page = current_page_urls - all_discovered_urls
            
            if not new_links_on_this_page and current_page_urls.issubset(all_discovered_urls):
                has_new_links = False 
            else:
                all_discovered_urls.update(current_page_urls)
                current_page += 1

        except Exception as e:
            print(f"[ERROR] 페이지 {current_page} 로드 또는 링크 수집 중 오류: {e}")
            has_new_links = False
    
    return all_discovered_urls - existing_urls

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
                title_element = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.XPATH, '//h1')))
                raw_title = title_element.text.strip()
                if raw_title:
                    product_title = re.sub(r'[\/:*?"<>|]', '', raw_title).strip()
                    break
                time.sleep(1.5)
            except Exception:
                time.sleep(1.5)
        else:
            try:
                url_segment = product_url.split('/')[-1]
                decoded_segment = requests.utils.unquote(url_segment)
                product_title = re.sub(r'[\/:*?"<>|]', '', decoded_segment).strip() or f"상품명-없음_{int(time.time())}"
            except Exception: pass

        product_info_parent_container = None
        target_xpath_id = '//*[@id="comp-ku3wpngu"]/div/div'
        target_xpath_text_based = '//section[.//span[contains(text(), "Product Info")]]/div/div | //div[.//span[contains(text(), "Product Info")]]/div/div'

        try:
            product_info_parent_container = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.XPATH, target_xpath_id)))
        except Exception:
            try:
                product_info_parent_container = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.XPATH, target_xpath_text_based)))
            except Exception as e:
                append_url_to_csv(INVALID_URLS_CSV, product_url)
                return 

        product_folder = os.path.join(BASE_DIR, product_title)
        ensure_directory_exists(product_folder)

        JS_EXTRACT_AND_CLEAN = r"""
        const container = arguments[0];
        let cleanedHtmlParts = [];
        let lastAddedTrimmedText = ''; 
        
        container.querySelectorAll('span').forEach(el => {
            const textContent = el.textContent.trim().replace(/\u200B/g, ''); 
            if (textContent === 'Product Info' || textContent === '제품 정보') el.remove();
        });

        const allElements = container.querySelectorAll('span, p, h1, h2, h3, h4, h5, h6, li, img');
        
        allElements.forEach(el => {
            let currentProcessedHtml = '';
            const tagName = el.tagName.toLowerCase();

            if (tagName.match(/^(span|p|h[1-6]|li)$/)) { 
                let textContent = el.textContent.trim().replace(/\u200B/g, '').replace(/\s\s+/g, ' '); 
                if (textContent === '' || textContent.match(/^[\s\u200B]*$/)) {
                    lastAddedTrimmedText = ''; return;
                }

                const currentTrimmedText = textContent.replace(/\s/g, ''); 
                const prevTrimmedText = lastAddedTrimmedText.replace(/\s/g, '');

                if (currentTrimmedText === prevTrimmedText ||
                    (prevTrimmedText !== '' && currentTrimmedText.includes(prevTrimmedText) && currentTrimmedText.length > prevTrimmedText.length && prevTrimmedText.length / currentTrimmedText.length > 0.5) ||
                    (currentTrimmedText !== '' && prevTrimmedText.includes(currentTrimmedText) && prevTrimmedText.length > currentTrimmedText.length && currentTrimmedText.length / prevTrimmedText.length > 0.5)
                ) { return; }

                let tempH5 = document.createElement('h5'); 
                tempH5.textContent = textContent;
                currentProcessedHtml = tempH5.outerHTML; 
                lastAddedTrimmedText = textContent; 
            } else if (tagName === 'img') { 
                let clonedImg = el.cloneNode(true);
                let originalSrc = clonedImg.getAttribute('src');
                const originalAlt = clonedImg.getAttribute('alt') || '';

                if (originalSrc) {
                    let cleanedSrc = originalSrc.split('/v1/fill/')[0];
                    const queryIndex = cleanedSrc.indexOf('?');
                    if (queryIndex !== -1) cleanedSrc = cleanedSrc.substring(0, queryIndex);
                    
                    const finalExtensionMatch = cleanedSrc.match(/\.(png|jpg|jpeg|gif|webp)$/i);
                    if (!finalExtensionMatch) {
                        const fullSrcMatch = originalSrc.match(/\.(png|jpg|jpeg|gif|webp)([~?].*|$)/i);
                        if (fullSrcMatch) cleanedSrc += fullSrcMatch[0].split(/[~?]/)[0];
                    }
                    originalSrc = cleanedSrc;
                }
                
                for (let i = clonedImg.attributes.length - 1; i >= 0; i--) {
                    clonedImg.removeAttribute(clonedImg.attributes[i].name);
                }
                if (originalSrc) clonedImg.setAttribute('src', originalSrc);
                if (originalAlt) clonedImg.setAttribute('alt', originalAlt);
                
                currentProcessedHtml = clonedImg.outerHTML;
                lastAddedTrimmedText = ''; 
            }
            if (currentProcessedHtml.trim() !== '') cleanedHtmlParts.push(currentProcessedHtml);
        });

        let finalHtml = cleanedHtmlParts.join('\n');
        finalHtml = finalHtml.replace(/<br>/g, '');
        finalHtml = finalHtml.replace(/\n{2,}/g, '\n'); 
        finalHtml = finalHtml.replace(/\s\s+/g, ' '); 
        return finalHtml;
        """

        cleaned_html_parts = driver.execute_script(JS_EXTRACT_AND_CLEAN, product_info_parent_container)

        html_file_path = os.path.join(product_folder, "product_info.html")
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write("<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"UTF-8\">\n<title>상품 정보</title>\n<style>\nbody { max-width: 800px; margin: 0 auto; padding: 20px; font-family: sans-serif; font-size: 16px; line-height: 1.6; }\n"
                    "h5 { font-size: 1.1em; margin-top: 0.8em; margin-bottom: 0.4em; }\n" 
                    "img { max-width: 100%; height: auto; display: block; margin: 0 auto; object-fit: contain; }\n"
                    "</style>\n</head>\n<body>\n")
            f.write(cleaned_html_parts)
            f.write("\n</body>\n</html>")
        
        append_url_to_csv(VALID_URLS_CSV, product_url)

    except Exception as e:
        print(f"[FATAL_ERROR] '{product_url}' 처리 중 심각한 오류 발생: {e}. 이 URL은 'INVALID_URLS.CSV'에 기록됩니다.")
        append_url_to_csv(INVALID_URLS_CSV, product_url)

# --- 모듈화된 실행 엔트리포인트 ---
def run():
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
        print("\n--- Scraper 실행 완료 ---")

if __name__ == "__main__":
    run()