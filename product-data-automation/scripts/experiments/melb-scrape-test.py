import os
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time
import re # 정규표현식 모듈 임포트
import datetime # 타임스탬프 생성을 위해 datetime 모듈 임포트
import urllib.parse # URL 인코딩을 위해 필요 (직접 사용하지 않지만, 모듈 임포트 유지)

# ▶ URL 설정 (테스트를 위한 단일 URL)
URL = ""

# ▶ 셀레니움 옵션 설정
options = Options()
# 디버깅을 위해 headless 모드를 비활성화합니다. (브라우저가 화면에 보임)
# 문제를 해결한 후 다시 options.add_argument('--headless')를 활성화하세요.
# options.add_argument('--headless')
options.add_argument('--disable-gpu')
options.add_argument('--no-sandbox')
options.add_argument('--start-maximized') # 브라우저를 최대화하여 확인하기 편리하게 합니다.
options.add_argument('--log-level=3') # Chrome 불필요한 로그 억제 (INFO, WARNING 등)
options.add_argument('--incognito') # 시크릿 모드 (세션간 영향 최소화)
options.add_argument('disable-infobars') # "자동화된 소프트웨어에 의해 제어됩니다" 메시지 제거

# 사용자 에이전트 설정 (봇으로 감지되지 않도록 일반 브라우저처럼 위장)
# **주의: 이 부분의 Chrome 버전은 반드시 사용자님의 실제 Chrome 버전에 맞춰야 합니다.**
# 예: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.7151.104 Safari/537.36"
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.7151.104 Safari/537.36" 
options.add_argument(f'user-agent={user_agent}')

# ▶ 드라이버 시작
driver = None
try:
    print("[INFO] ChromeDriver 다운로드 및 초기화 시도...")
    service = Service(ChromeDriverManager().install())
    
    print("[INFO] Chrome 브라우저 시작 시도...")
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60) # 페이지 로드 타임아웃 설정 (60초)
    print("[+] Selenium WebDriver 성공적으로 초기화됨.")
except Exception as e_driver_init:
    print(f"[ERROR] Selenium WebDriver 초기화 실패: {e_driver_init}")
    print("[ERROR] Chrome 브라우저와 ChromeDriver 버전 불일치 또는 환경 문제일 가능성이 높습니다.")
    print("--- 해결 시도 ---")
    print("1. Chrome 브라우저를 최신 버전으로 업데이트 (chrome://settings/help)")
    print("2. 터미널에서 'pip install --upgrade --force-reinstall webdriver-manager' 실행")
    print("3. 컴퓨터 재부팅 후 재시도")
    exit() # 드라이버 초기화 실패 시 스크립트 종료

try:
    driver.get(URL)
    print(f"[+] {URL} 페이지로 이동 성공.")

    # 페이지가 완전히 로드될 때까지 잠시 대기 (JavaScript 로딩 확인)
    time.sleep(3) # 필요에 따라 대기 시간을 늘릴 수 있습니다.

    # --- 페이지 끝까지 스크롤하여 모든 이미지 로드 ---
    print("[INFO] 페이지 끝까지 스크롤하여 이미지 로드 중...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2) # 스크롤 후 이미지 로드를 위한 대기
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    print("[INFO] 페이지 스크롤 완료.")
    time.sleep(2) # 스크롤 완료 후 최종 이미지 로드를 위한 추가 대기
    # -----------------------------------------------

    # ▶ 상품명 추출 (제공된 XPath 사용)
    product_title = "no-title" # 초기값
    try:
        title_xpath = '//*[@id="TPAMultiSection_j38fck9a"]/div/div/div/div/article/div[2]/section[2]/div[1]/h1'
        title_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, title_xpath))
        )
        product_title_raw = title_element.text.strip()
        print(f"[DEBUG] 원본 상품명 (raw): '{product_title_raw}'") # 원본 상품명 출력

        # 폴더명으로 사용할 수 없는 문자 제거 및 공백 처리 강화
        # Windows에서 파일명으로 사용할 수 없는 문자: \ / : * ? " < > |
        # re.escape를 사용하여 regex 패턴 내의 특수문자를 이스케이프 처리
        # 직접 허용되지 않는 문자를 정의하여 제거하는 것이 더 명확하고 강력합니다.
        invalid_chars = r'[\\/:*?"<>|]' # 이스케이프된 특수 문자들
        product_title_sanitized = re.sub(invalid_chars, '', product_title_raw).strip()
        
        print(f"[DEBUG] 정리된 상품명 (sanitized): '{product_title_sanitized}'") # 정리된 상품명 출력

        if not product_title_sanitized: # 문자 제거 후 빈 문자열이 되면 오류로 간주
            raise ValueError("상품명이 비어있거나 유효하지 않습니다.")
        
        product_title = product_title_sanitized
        
        print(f"[+] 상품명 추출 완료: '{product_title}'")

    except Exception as e:
        print(f"[!] 상품명 추출 실패: {e}")
        product_title = "no-title" # 실패 시 기본값

    # ▶ 저장 경로 설정 (상품설명사진/상품명_타임스탬프)
    base_dir = "상품설명사진"
    # 현재 시간을 'YYYYMMDD_HHMMSS' 형식으로 포맷팅하여 폴더명에 추가
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # 폴더명: 상품설명사진/정리된상품명_YYYYMMDD_HHMMSS
    save_folder = os.path.join(base_dir, f"{product_title}_{timestamp}")
    
    # **최종 폴더명 확인을 위한 디버그 출력**
    print(f"[DEBUG] 생성될 폴더 경로: '{save_folder}'")

    # 폴더 생성 시도
    try:
        os.makedirs(save_folder, exist_ok=True) 
        print(f"[INFO] 이미지가 저장될 폴더: {save_folder}")
    except OSError as e:
        print(f"[ERROR] 폴더 생성 실패: {save_folder} - {e}")
        print("[ERROR] 상품명 또는 타임스탬프에 여전히 유효하지 않은 문자가 포함되어 있을 수 있습니다. 스크립트를 종료합니다.")
        driver.quit()
        exit()

    # ▶ 이미지가 들어있는 div 로딩 대기 (제공된 XPath 사용)
    image_container_xpath = '//*[@id="comp-ku3wpngu"]/div/div'
    image_container_element = None # 이미지 컨테이너 요소를 저장할 변수
    try:
        image_container_element = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, image_container_xpath))
        )
        print(f"[+] 이미지 컨테이너 div 로딩 완료: {image_container_xpath}")

        # 컨테이너 내의 첫 번째 <img> 태그가 나타날 때까지 추가 대기 (안정성 강화)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, f'{image_container_xpath}//img'))
        )
        print(f"[+] 컨테이너 내 첫 번째 이미지 태그 로딩 완료.")
        time.sleep(1) # 이미지 로드 후 안정화를 위한 짧은 대기

    except Exception as e:
        print(f"[!] 이미지 컨테이너 또는 그 안의 이미지 로딩 실패: {e}")
        print("[!] 해당 XPath가 존재하지 않거나 로딩이 너무 늦습니다. 스크립트를 종료합니다.")
        driver.quit()
        exit()

    # ▶ 이미지 태그들 수집 (StaleElementReferenceException 방지를 위해 다시 find_elements 호출)
    img_elements = driver.find_elements(By.XPATH, f'{image_container_xpath}//img')

    # 디버깅: 찾은 이미지 요소의 개수와 속성을 출력하여 확인
    print(f"\n[DEBUG] 초기 감지된 img_elements 수: {len(img_elements)}")
    print("[DEBUG] 감지된 이미지 요소들의 src 및 data-src 속성:")
    for k, img_elem in enumerate(img_elements):
        try:
            src_attr = img_elem.get_attribute('src')
            data_src_attr = img_elem.get_attribute('data-src')
            print(f"  [{k+1}] src: {src_attr}, data-src: {data_src_attr}")
        except StaleElementReferenceException:
            print(f"  [{k+1}] 요소 정보 가져오기 실패 (StaleElementReferenceException).")
            continue
        except Exception as e_debug_img:
            print(f"  [{k+1}] 요소 정보 가져오기 실패 (기타): {e_debug_img}")

    # ▶ URL만 추출 및 필터링
    img_urls = []
    for idx_img, img in enumerate(img_elements):
        final_url = None
        try: # StaleElementReferenceException을 개별 요소마다 처리
            src = img.get_attribute('src')
            data_src = img.get_attribute('data-src')

            candidates = []
            if src and 'static.wixstatic.com/media' in src:
                candidates.append(src)
            if data_src and 'static.wixstatic.com/media' in data_src:
                candidates.append(data_src)

            for url_candidate in candidates:
                # Wix URL의 /v1/ 부분을 기준으로 원본 URL 추정
                parts = url_candidate.split('/v1/')
                if len(parts) > 1:
                    # 크기 조절 파라미터가 붙지 않은 원본 이미지 경로 (예: .png, .jpg 등으로 끝남)
                    original_path = parts[0]
                    if re.search(r'\.(jpg|jpeg|png|gif|webp)$', original_path, re.IGNORECASE):
                        final_url = original_path
                        break # 원본 찾았으니 다른 후보 볼 필요 없음
                
                # 원본 추정에 실패했거나 원본이 없는 경우, 현재 URL이 너무 작거나 불필요한 이미지인지 확인
                # 'w_' 나 'h_'를 포함하는 URL은 대부분 썸네일이나 작은 미리보기 이미지임
                if not re.search(r'w_\d+|h_\d+', url_candidate): # w_숫자, h_숫자 패턴이 없는 경우
                    final_url = url_candidate
                    break # 유효한 URL로 간주하고 다음 후보 볼 필요 없음
                
                # 최종적으로 적합한 URL을 찾지 못했다면, 일단 현재 후보를 사용 (필요시 더 엄격한 필터링)
                if final_url is None:
                    final_url = url_candidate
            
            if final_url and final_url not in img_urls: # 중복 방지
                img_urls.append(final_url)

        except StaleElementReferenceException:
            print(f"[!] 이미지 요소 처리 중 StaleElementReferenceException 발생 (인덱스: {idx_img+1}). 건너뜁니다.")
            continue
        except Exception as element_error:
            print(f"[!] 이미지 요소 처리 중 오류 발생 (기타): {element_error}")
            print(f"    문제 발생 이미지 요소 인덱스: {idx_img+1}")
            continue # 오류가 발생한 요소는 건너뛰고 다음 요소로 진행

    print(f"[INFO] 총 {len(img_urls)}장의 최종 이미지가 감지되었습니다.")

    # ▶ 이미지 저장
    for idx_save, img_url in enumerate(img_urls, start=1):
        try:
            response = requests.get(img_url, timeout=10) # 10초 타임아웃 추가
            if response.status_code == 200:
                parsed_url = img_url.split('?')[0] # 쿼리 파라미터 제거
                file_extension = os.path.splitext(parsed_url)[-1] # 파일 확장자 추출
                
                if not file_extension or file_extension.lower() not in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
                    if '.png' in parsed_url.lower():
                        file_extension = '.png'
                    elif '.jpg' in parsed_url.lower() or '.jpeg' in parsed_url.lower():
                        file_extension = '.jpg'
                    elif '.gif' in parsed_url.lower():
                        file_extension = '.gif'
                    elif '.webp' in parsed_url.lower():
                        file_extension = '.webp'
                    else:
                        file_extension = '.jpg' # 최종 기본값

                img_path = os.path.join(save_folder, f'image_{idx_save}{file_extension}')
                with open(img_path, 'wb') as f:
                    f.write(response.content)
                print(f"[+] 저장 완료: {img_path}")
            else:
                print(f"[!] 이미지 요청 실패: {img_url}, 상태 코드: {response.status_code}")
        except requests.exceptions.Timeout:
            print(f"[!] 이미지 다운로드 타임아웃 오류: {img_url}")
        except requests.exceptions.RequestException as req_e:
            print(f"[!] 이미지 다운로드 요청 오류: {req_e}, URL: {img_url}")
        except Exception as e_download:
            print(f"[!] 이미지 다운로드 알 수 없는 오류: {e_download}, URL: {img_url}")

except Exception as main_exception:
    print(f"[ERROR] 스크립트 실행 중 예상치 못한 오류 발생: {main_exception}")

finally:
    # ▶ 드라이버 종료
    if driver:
        driver.quit()
        print("\n--- Selenium WebDriver 종료됨 ---")

print("\n--- 스크립트 실행 완료 ---")
