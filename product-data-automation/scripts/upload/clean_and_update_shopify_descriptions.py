import os
import csv
import re
import time
import requests
import shopify
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from tqdm import tqdm
from bs4 import BeautifulSoup
from tenacity import retry, wait_fixed, stop_after_attempt, retry_if_exception_type, wait_exponential
from urllib.parse import urlparse, parse_qs
from datetime import datetime

# --- 유틸리티 함수 (가장 먼저 정의) ---
def ensure_directory_exists(dir_path):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

# --- Shopify API 설정 ---
# ⚠️  아래 두 값을 본인의 Shopify 스토어 정보로 교체하세요.
SHOPIFY_STORE_URL = "your-store.myshopify.com"          # 예: my-shop.myshopify.com
SHOPIFY_ADMIN_API_ACCESS_TOKEN = "shpat_your_token_here"  # Shopify Admin API 액세스 토큰

SHOPIFY_ADMIN_API_REST_URL = f"https://{SHOPIFY_STORE_URL}/admin/api/2025-07/"
SHOPIFY_ADMIN_API_GRAPHQL_URL = f"https://{SHOPIFY_STORE_URL}/admin/api/2025-07/graphql.json"
API_VERSION = "2025-07"

SCRAPED_HTML_BASE_DIR = "상품설명사진"
UPLOADED_FILES_DIR = "uploaded_files" # 업로드 기록용 폴더
UPLOADED_IMAGES_CSV = os.path.join(UPLOADED_FILES_DIR, "uploaded_images.csv") # 업로드 기록용 CSV

# --- 로깅 파일 설정 ---
LOG_DIR = "logs"
ensure_directory_exists(LOG_DIR) 
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
ERROR_LOG_FILE = os.path.join(LOG_DIR, f"error_log_{TIMESTAMP}.txt")
PRODUCT_UPDATE_REPORT_FILE = os.path.join(LOG_DIR, f"product_update_report_{TIMESTAMP}.csv")
IMAGE_UPLOAD_REPORT_FILE = os.path.join(LOG_DIR, f"image_upload_report_{TIMESTAMP}.csv")

# --- GraphQL 클라이언트 생성 ---
def get_graphql_client():
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ADMIN_API_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    transport = RequestsHTTPTransport(
        url=SHOPIFY_ADMIN_API_GRAPHQL_URL,
        headers=headers,
        use_json=True,
        timeout=30
    )
    client = Client(transport=transport, fetch_schema_from_transport=False)
    return client

# --- GraphQL 정의 ---
STAGED_UPLOAD_CREATE_MUTATION = gql("""
mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets {
      url
      resourceUrl
      parameters {
        name
        value
      }
    }
    userErrors {
      field
      message
    }
  }
}
""")

FILE_CREATE_MUTATION = gql("""
mutation fileCreate($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files {
      ... on MediaImage {
        id
        originalSource {
          url
        }
      }
    }
    userErrors {
      field
      message
    }
  }
}
""")

# 새롭게 추가된 GraphQL 쿼리: 파일 ID로 파일 정보 조회
GET_FILE_URL_QUERY = gql("""
query node($id: ID!) {
  node(id: $id) {
    ... on MediaImage {
      id
      image { # <--- 이 부분을 추가하거나 기존 originalSource와 함께 요청
        url
      }
      originalSource { # 이 부분은 유지하거나 필요 없으면 삭제해도 됩니다.
        url
      }
    }
  }
}
""")

# --- 유틸리티 함수 (나머지 유틸리티 함수들도 이어서 정의) ---
def append_to_csv(filename, data_row):
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(data_row)

def write_log(message, log_file=ERROR_LOG_FILE):
    """지정된 로그 파일에 메시지를 기록합니다."""
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

def initialize_report_csv(filename, headers):
    """보고서 CSV 파일의 헤더를 작성합니다."""
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)

def load_uploaded_images_map(filename):
    """uploaded_images.csv에서 로컬 경로 -> Shopify URL 맵을 로드합니다."""
    image_map = {}
    if os.path.exists(filename):
        with open(filename, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    local_path = row[0]
                    shopify_url = row[1]
                    image_map[local_path] = shopify_url
    return image_map

def normalize_title(title):
    title = re.sub(r'[^\w\s\uAC00-\uD7A3]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title.lower()

def read_html_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        write_log(f"HTML 파일 읽기 실패: {file_path} - {e}", log_file=ERROR_LOG_FILE)
        return None

@retry(wait=wait_fixed(5), stop=stop_after_attempt(3),
        retry=retry_if_exception_type(requests.exceptions.RequestException))
def upload_file_to_signed_url(file_path, upload_url, parameters):
    """실제 파일을 서명된 URL로 PUT/POST 요청하여 업로드합니다."""
    file_name = os.path.basename(file_path)
    try:
        mime_type = "application/octet-stream"
        if file_name.lower().endswith(".png"): mime_type = "image/png"
        elif file_name.lower().endswith((".jpg", ".jpeg")): mime_type = "image/jpeg"
        elif file_name.lower().endswith(".gif"): mime_type = "image/gif"
        elif file_name.lower().endswith(".webp"): mime_type = "image/webp"

        for param in parameters:
            if param["name"] == "Content-Type":
                mime_type = param["value"]
                break
        
        policy_param_found = any(param["name"] == "policy" for param in parameters)

        if policy_param_found:
            files = {
                'file': (file_name, open(file_path, 'rb'), mime_type)
            }
            data = {}
            for param in parameters:
                data[param["name"]] = param["value"]
            
            parsed_upload_url = urlparse(upload_url)
            query_params_from_url = parse_qs(parsed_upload_url.query)

            if 'key' not in data and parsed_upload_url.path:
                data['key'] = parsed_upload_url.path.lstrip('/')

            for param_name in ['X-Goog-Algorithm', 'X-Goog-Credential', 'X-Goog-Date', 
                               'X-Goog-Expires', 'X-Goog-SignedHeaders', 'X-Goog-Signature',
                               'x-amz-algorithm', 'x-amz-credential', 'x-amz-date', 'x-amz-expires', 'x-amz-signedheaders', 'x-amz-signature']:
                if param_name.lower() in query_params_from_url:
                    data[param_name] = query_params_from_url[param_name.lower()][0]
                elif param_name in query_params_from_url:
                    data[param_name] = query_params_from_url[param_name][0]
            
            base_upload_url_for_post = f"{parsed_upload_url.scheme}://{parsed_upload_url.netloc}{parsed_upload_url.path}"

            response = requests.post(base_upload_url_for_post, data=data, files=files, timeout=60)
        else:
            with open(file_path, "rb") as f:
                file_data = f.read()

            headers = {'Content-Type': mime_type}
            for param in parameters:
                if param["name"] != "Content-Type":
                    headers[param["name"]] = param["value"]
            
            response = requests.put(upload_url, data=file_data, headers=headers, timeout=60)

        response.raise_for_status() # HTTP 오류가 발생하면 예외를 발생시킴
        return True

    except requests.exceptions.RequestException as e:
        error_details = ""
        response_status_code = "N/A"
        response_text_preview = "N/A"
        if hasattr(e, 'response') and e.response is not None:
            response_status_code = e.response.status_code
            try:
                # 응답 본문을 최대한 많이 가져와서 로그에 기록
                response_text_preview = e.response.text[:500] 
                # print(f"    [DEBUG] 서명된 URL 응답 본문 (500자 제한): {response_text_preview}...") # 콘솔에도 출력
            except Exception as inner_e:
                response_text_preview = f"응답 본문 읽기 실패: {inner_e}"
        
        log_message = f"서명된 URL로 파일 업로드 실패 (파일: {file_name}, URL: {upload_url}). 오류: {e}. HTTP 상태: {response_status_code}. 응답 상세: {response_text_preview}"
        write_log(log_message, log_file=ERROR_LOG_FILE)
        append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, "N/A", "Failed", f"{e} | Status: {response_status_code} | Details: {response_text_preview}", upload_url])
        raise # 재시도를 위해 예외 다시 발생
    except Exception as e:
        log_message = f"알 수 없는 오류로 파일 업로드 실패 (파일: {file_name}): {e}"
        write_log(log_message, log_file=ERROR_LOG_FILE)
        append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, "N/A", "Failed", str(e), upload_url])
        raise

def upload_single_image_to_shopify(graphql_client, image_path, original_wix_src):
    """하나의 이미지를 Shopify Files에 업로드하고 CDN URL을 반환합니다."""
    if not os.path.exists(image_path):
        error_msg = f"이미지 파일이 존재하지 않습니다: {image_path}"
        print(f"    [ERROR] {error_msg}") # 콘솔 출력 추가
        write_log(error_msg, log_file=ERROR_LOG_FILE)
        append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [os.path.basename(image_path), original_wix_src, "Failed", error_msg, "N/A"])
        return None

    file_name = os.path.basename(image_path)
    file_size = os.path.getsize(image_path)
    mime_type = "application/octet-stream"
    if file_name.lower().endswith(".png"): mime_type = "image/png"
    elif file_name.lower().endswith((".jpg", ".jpeg")): mime_type = "image/jpeg"
    elif file_name.lower().endswith(".gif"): mime_type = "image/gif"
    elif file_name.lower().endswith(".webp"): mime_type = "image/webp"

    staged_input = {
        "filename": file_name,
        "mimeType": mime_type,
        "resource": "IMAGE",
        "fileSize": str(file_size),
    }

    try:
        # 1단계: Staged Upload URL 요청
        print(f"    [INFO] Shopify stagedUploadsCreate 요청 중: {file_name}") # 콘솔 출력 추가
        response = graphql_client.execute(
            document=STAGED_UPLOAD_CREATE_MUTATION,
            variable_values={"input": [staged_input]}
        )
        # print(f"    [DEBUG] stagedUploadsCreate 응답: {response}") # 응답 전체를 콘솔에 출력 (디버깅용)

        user_errors = response.get("stagedUploadsCreate", {}).get("userErrors")
        if user_errors:
            error_msgs = [err.get('message') for err in user_errors]
            error_msg = f"stagedUploadsCreate 사용자 오류: {', '.join(error_msgs)}"
            print(f"    [ERROR] {error_msg}") # 콘솔 출력 추가
            write_log(error_msg, log_file=ERROR_LOG_FILE)
            append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, original_wix_src, "Failed", error_msg, "N/A"])
            return None

        staged_targets = response.get("stagedUploadsCreate", {}).get("stagedTargets")
        if not staged_targets:
            error_msg = "stagedUploadsCreate 응답에서 stagedTargets를 찾을 수 없습니다."
            print(f"    [ERROR] {error_msg}") # 콘솔 출력 추가
            write_log(error_msg, log_file=ERROR_LOG_FILE)
            append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, original_wix_src, "Failed", error_msg, "N/A"])
            return None

        target = staged_targets[0]
        # print(f"    [INFO] Staged Upload URL 획득 성공. 실제 파일 업로드 시도: {target['url'][:80]}...") # 콘솔 출력 추가
        
        # 2단계: 서명된 URL로 실제 파일 업로드
        if upload_file_to_signed_url(image_path, target["url"], target["parameters"]):
            print(f"    [INFO] 서명된 URL로 파일 업로드 성공. Shopify Files에 등록 요청 중: {file_name}") # 콘솔 출력 추가
            file_input = {
                "alt": os.path.splitext(file_name)[0],
                "originalSource": target["resourceUrl"]
            }
            file_response = graphql_client.execute(
                document=FILE_CREATE_MUTATION,
                variable_values={"files": [file_input]}
            )
            # print(f"    [DEBUG] fileCreate 응답: {file_response}") # 응답 전체를 콘솔에 출력 (디버깅용)

            # fileCreate 이후 Shopify CDN URL이 준비될 시간을 약간 기다려줍니다.
            time.sleep(2)

            user_errors = file_response.get("fileCreate", {}).get("userErrors")
            if user_errors:
                error_msgs = [err.get('message') for err in user_errors]
                error_msg = f"fileCreate 사용자 오류: {', '.join(error_msgs)}"
                print(f"    [ERROR] {error_msg}") # 콘솔 출력 추가
                write_log(error_msg, log_file=ERROR_LOG_FILE)
                append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, original_wix_src, "Failed", error_msg, "N/A"])
                return None

            created_files = file_response.get("fileCreate", {}).get("files")
            file_id = created_files[0]['id'] if created_files and created_files[0] and created_files[0].get('id') else None
            shopify_file_url = created_files[0]['image']['url'] if created_files and created_files[0] and created_files[0].get('image', {}).get('url') else None

            # originalSource.url이 None인 경우, 재시도 로직 추가
            if shopify_file_url is None and file_id:
                print(f"    [WARNING] fileCreate 응답에서 Shopify CDN URL이 바로 제공되지 않았습니다. (파일 ID: {file_id}). 재시도하여 조회합니다.")
                # 엑스포넨셜 백오프를 사용하여 최대 5번 재시도
                for attempt in range(1, 6):
                    time.sleep(min(2 ** attempt, 30)) # 2, 4, 8, 16, 30초 (최대 30초)
                    print(f"    [INFO] 파일 CDN URL 조회 재시도 중... (시도 {attempt}/5)")
                    try:
                        file_query_response = graphql_client.execute(
                            document=GET_FILE_URL_QUERY,
                            variable_values={"id": file_id}
                        )
                        queried_node = file_query_response.get('node')
                        if queried_node and queried_node.get('image', {}).get('url'):
                            shopify_file_url = queried_node['image']['url']
                            # print(f"    [SUCCESS] 재시도 후 Shopify CDN URL 획득 성공: {shopify_file_url}")
                            print(f"    [SUCCESS] 재시도 후 Shopify CDN URL 획득 성공")
                            break # URL을 찾았으면 루프 종료
                        else:
                            print(f"    [DEBUG] 재시도 {attempt}회: 아직 CDN URL을 찾을 수 없습니다. 응답: {file_query_response}")
                    except Exception as query_e:
                        print(f"    [ERROR] 파일 CDN URL 조회 중 예외 발생 (재시도 {attempt}회): {query_e}")
                        write_log(f"파일 CDN URL 조회 중 예외 발생 (파일 ID: {file_id}, 재시도 {attempt}회): {query_e}", log_file=ERROR_LOG_FILE)

            if shopify_file_url:
                # print(f"    [SUCCESS] Shopify Files에 등록 및 CDN URL 획득 성공: {shopify_file_url}") # 콘솔 출력 추가
                print(f"    [SUCCESS] Shopify Files에 등록 및 CDN URL 획득 성공")
                append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, original_wix_src, "Success", "", shopify_file_url])
                return shopify_file_url
            else:
                error_msg = f"fileCreate 응답 및 재시도 후에도 Shopify CDN URL을 찾을 수 없습니다. (파일: {file_name}, 파일 ID: {file_id}). 응답: {file_response}"
                print(f"    [ERROR] {error_msg}") # 콘솔 출력 추가
                write_log(error_msg, log_file=ERROR_LOG_FILE)
                append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, original_wix_src, "Failed", error_msg, "N/A"])
                return None
        else: # upload_file_to_signed_url에서 False가 반환되면 이 블록 실행 (현재 코드에서는 사실상 실행 안 됨. 예외 발생하거나 True 반환)
            error_msg = f"서명된 URL로 파일 업로드 자체 실패 (Tenacity 재시도 후에도): {image_path}. 추가 로그 확인."
            print(f"    [ERROR] {error_msg}") # 콘솔 출력 추가
            # 상세 오류는 upload_file_to_signed_url 내부에서 이미 로깅했으므로 여기서는 추가 로깅 불필요
            return None

    except Exception as e:
        error_msg = f"이미지 업로드 중 예외 발생 (파일: {file_name}): {e}"
        print(f"    [CRITICAL ERROR] {error_msg}") # 콘솔 출력 추가 (주목할 수 있도록 CRITICAL ERROR로 표시)
        write_log(error_msg, log_file=ERROR_LOG_FILE)
        append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [file_name, original_wix_src, "Failed", error_msg, "N/A"])
        return None

def clean_html_and_process_images(html_content, product_folder_path, uploaded_images_map, graphql_client):
    """
    HTML 콘텐츠에서 Wix 요소를 제거하고, 이미지 경로를 Shopify CDN URL로 교체하며,
    필요시 이미지를 Shopify Files에 업로드합니다.
    """
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, 'html.parser')
    
    for span in soup.find_all('span'):
        text_content = span.get_text(strip=True).replace('\u200B', '')
        if text_content in ['Product Info', '제품 정보']:
            span.extract()

    for tag in soup.find_all(True):
        if tag.name in ['span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']:
            original_style = tag.get('style')
            attrs_to_remove = list(tag.attrs.keys())
            for attr in attrs_to_remove:
                del tag.attrs[attr]
            if original_style:
                tag['style'] = original_style

        elif tag.name == 'img':
            original_src = tag.get('src')
            original_alt = tag.get('alt')
            original_width = tag.get('width')
            original_height = tag.get('height')
            original_style = tag.get('style')

            attrs_to_remove = list(tag.attrs.keys())
            for attr in attrs_to_remove:
                del tag.attrs[attr]
            
            shopify_cdn_url = None

            if original_src:
                try:
                    parsed_wix_url = urlparse(original_src)
                    file_name_from_wix_url = os.path.basename(parsed_wix_url.path)
                    
                    local_image_path = os.path.join(product_folder_path, "images", file_name_from_wix_url).replace("\\", "/")

                    shopify_cdn_url = uploaded_images_map.get(local_image_path)

                    if not shopify_cdn_url:
                        print(f"  [INFO] 이미지 업로드 시도: {local_image_path} (Wix SRC: {original_src[:50]}...)")
                        shopify_cdn_url = upload_single_image_to_shopify(graphql_client, local_image_path, original_src)
                        
                        if shopify_cdn_url:
                            append_to_csv(UPLOADED_IMAGES_CSV, [local_image_path, shopify_cdn_url, "N/A"]) 
                            uploaded_images_map[local_image_path] = shopify_cdn_url 
                            print(f"  [SUCCESS] 이미지 업로드 및 SRC 교체: {os.path.basename(local_image_path)} -> {shopify_cdn_url}")
                        else:
                            print(f"  [ERROR] 이미지 업로드 실패: {local_image_path}. SRC 변경하지 않음. (자세한 내용은 로그 확인)")
                            # 실패 시 원본 Wix src 유지하여, 나중에 수동 확인 가능
                            shopify_cdn_url = original_src 
                except Exception as e:
                    error_msg = f"이미지 SRC 처리 중 예외 발생 (Original SRC: {original_src}): {e}"
                    write_log(error_msg, log_file=ERROR_LOG_FILE)
                    append_to_csv(IMAGE_UPLOAD_REPORT_FILE, [os.path.basename(original_src), original_src, "Failed", error_msg, "N/A"])
                    shopify_cdn_url = original_src # 오류 발생 시 원본 SRC 유지
            
            if shopify_cdn_url:
                tag['src'] = shopify_cdn_url
            if original_alt:
                tag['alt'] = original_alt
            if original_width:
                tag['width'] = original_width
            if original_height:
                tag['height'] = original_height
            if original_style:
                tag['style'] = original_style
        else:
            attrs_to_remove = list(tag.attrs.keys())
            for attr in attrs_to_remove:
                del tag.attrs[attr]

    # 3. 불필요한 <br> 태그 제거
    for br in soup.find_all('br'):
        br.extract()

    # 4. 여러 개의 연속된 줄바꿈 문자를 하나의 줄바꿈 문자로 줄임 및 공백 정규화
    final_html = str(soup)
    final_html = re.sub(r'\n{2,}', '\n', final_html)
    final_html = re.sub(r'\s\s+', ' ', final_html)

    return final_html

# --- 메인 로직 ---
def main():
    initialize_report_csv(PRODUCT_UPDATE_REPORT_FILE, ["상품명", "HTML 파일 경로", "상태", "오류 메시지"])
    initialize_report_csv(IMAGE_UPLOAD_REPORT_FILE, ["파일 이름 (로컬)", "원본 Wix SRC", "상태", "오류 메시지", "Shopify CDN URL"])
    write_log("스크립트 실행 시작", log_file=ERROR_LOG_FILE)

    try:
        session = shopify.Session(f"https://{SHOPIFY_STORE_URL}", API_VERSION, SHOPIFY_ADMIN_API_ACCESS_TOKEN)
        shopify.ShopifyResource.activate_session(session)
        print(f"[INFO] Shopify REST API {API_VERSION} 버전으로 초기화 완료.")
    except Exception as e:
        error_msg = f"Shopify REST API 초기화 실패: {e}. Shopify 액세스 토큰 또는 샵 URL을 확인하세요."
        print(f"[ERROR] {error_msg}")
        write_log(error_msg, log_file=ERROR_LOG_FILE)
        return

    graphql_client = get_graphql_client()
    if not graphql_client:
        error_msg = "GraphQL 클라이언트 초기화 실패. 스크립트를 종료합니다."
        print(f"[FATAL] {error_msg}")
        write_log(error_msg, log_file=ERROR_LOG_FILE)
        return
    
    ensure_directory_exists(UPLOADED_FILES_DIR)
    uploaded_images_map = load_uploaded_images_map(UPLOADED_IMAGES_CSV)
    
    if not uploaded_images_map:
        print("[INFO] 'uploaded_images.csv' 파일이 비어 있거나 새로 생성되었습니다. 모든 이미지를 확인하고 필요시 업로드합니다.")

    scraped_products_html_data = {}
    print(f"\n[INFO] '{SCRAPED_HTML_BASE_DIR}' 폴더에서 스크래핑된 HTML 파일을 탐색 중...")
    for product_folder_name in os.listdir(SCRAPED_HTML_BASE_DIR):
        folder_path = os.path.join(SCRAPED_HTML_BASE_DIR, product_folder_name)
        html_file_path = os.path.join(folder_path, "product_info.html")
        
        if os.path.isdir(folder_path) and os.path.exists(html_file_path):
            normalized_folder_title = normalize_title(product_folder_name)
            scraped_products_html_data[normalized_folder_title] = {
                "html_file_path": html_file_path,
                "product_folder_path": folder_path,
                "original_product_title": product_folder_name
            }
    
    if not scraped_products_html_data:
        print("[WARNING] '상품설명사진' 폴더에서 스크래핑된 상품 정보 HTML 파일이 발견되지 않았습니다. 작업을 종료합니다.")
        write_log("상품설명사진 폴더에서 HTML 파일 미발견.", log_file=ERROR_LOG_FILE)
        return
    
    total_scraped_html_files = len(scraped_products_html_data)
    print(f"[INFO] 총 {total_scraped_html_files}개의 스크래핑된 상품 HTML 파일을 수집했습니다.")

    print("\n[INFO] Shopify 상품 목록을 페이지별로 가져오는 중...")
    all_shopify_products = {}
    last_id = None
    page_limit = 250 
    total_shopify_products_fetched = 0

    while True:
        try:
            params = {'limit': page_limit, 'order': 'id asc'}
            if last_id:
                params['since_id'] = last_id
                
            products = shopify.Product.find(**params)
            
            if not products:
                break 

            for product in products:
                normalized_title = normalize_title(product.title)
                all_shopify_products[normalized_title] = product
                total_shopify_products_fetched += 1

            last_id = products[-1].id
            time.sleep(0.7) 

        except Exception as e:
            error_msg = f"Shopify 상품 목록 가져오기 실패: {e}."
            print(f"[ERROR] {error_msg}")
            write_log(error_msg, log_file=ERROR_LOG_FILE)
            print("상품 목록 가져오기 중단.")
            break 

    print(f"[INFO] 최종적으로 총 {total_shopify_products_fetched}개의 Shopify 상품을 가져왔습니다.")

    updated_count = 0
    skipped_not_found_count = 0
    skipped_already_latest_count = 0
    failed_product_updates_count = 0
    
    print("\n[INFO] 상품 Description 클리닝, 이미지 업로드/교체, 업데이트 중...")
    
    for normalized_folder_title, data in tqdm(
        scraped_products_html_data.items(), 
        desc="상품 Description 처리 진행", 
        unit="개",
        total=total_scraped_html_files
    ):
        html_file_path = data["html_file_path"]
        product_folder_path = data["product_folder_path"]
        original_product_title = data["original_product_title"]
        
        shopify_product = all_shopify_products.get(normalized_folder_title)

        if shopify_product:
            original_html_content = read_html_file(html_file_path)
            if original_html_content:
                print(f"\n[INFO] '{shopify_product.title}' 상품 Description 처리 시작...")
                cleaned_html_content = clean_html_and_process_images(
                    original_html_content, 
                    product_folder_path, 
                    uploaded_images_map, 
                    graphql_client
                )

                try:
                    if shopify_product.body_html.strip() == cleaned_html_content.strip():
                        print(f"  [INFO] '{shopify_product.title}' 상품 Description이 이미 최신 상태입니다. 스킵합니다.")
                        skipped_already_latest_count += 1
                        append_to_csv(PRODUCT_UPDATE_REPORT_FILE, 
                                      [original_product_title, html_file_path, "Skipped (Already Latest)", ""])
                    else:
                        shopify_product.body_html = cleaned_html_content
                        shopify_product.save() 
                        print(f"  [SUCCESS] '{shopify_product.title}' 상품 Description이 성공적으로 업데이트되었습니다.")
                        updated_count += 1
                        append_to_csv(PRODUCT_UPDATE_REPORT_FILE, 
                                      [original_product_title, html_file_path, "Success", ""])
                    time.sleep(0.5)
                except shopify.ShopifyResource.InvalidRequestError as e:
                    error_msg = f"'{shopify_product.title}' 상품 업데이트 실패 (잘못된 요청): {e}"
                    print(f"\n  [ERROR] {error_msg}")
                    write_log(error_msg, log_file=ERROR_LOG_FILE)
                    append_to_csv(PRODUCT_UPDATE_REPORT_FILE, 
                                  [original_product_title, html_file_path, "Failed", error_msg])
                    failed_product_updates_count += 1
                except Exception as e:
                    error_msg = f"'{shopify_product.title}' 상품 업데이트 실패: {e}"
                    print(f"\n  [ERROR] {error_msg}")
                    write_log(error_msg, log_file=ERROR_LOG_FILE)
                    append_to_csv(PRODUCT_UPDATE_REPORT_FILE, 
                                  [original_product_title, html_file_path, "Failed", error_msg])
                    failed_product_updates_count += 1
            else:
                error_msg = f"'{os.path.basename(product_folder_path)}' (HTML 파일: {html_file_path})에 대한 HTML 콘텐츠를 읽을 수 없어 건너뜁니다."
                print(f"\n[WARNING] {error_msg}")
                write_log(error_msg, log_file=ERROR_LOG_FILE)
                append_to_csv(PRODUCT_UPDATE_REPORT_FILE, 
                              [original_product_title, html_file_path, "Skipped (HTML Read Failed)", error_msg])
        else:
            skipped_not_found_count += 1
            warning_msg = f"Shopify에서 '{original_product_title}'에 해당하는 상품을 찾을 수 없어 건너뜁니다."
            append_to_csv(PRODUCT_UPDATE_REPORT_FILE, 
                          [original_product_title, html_file_path, "Skipped (Product Not Found)", warning_msg])

    print(f"\n--- 최종 요약 ---")
    print(f"✅ Description이 성공적으로 클리닝 및 업데이트된 상품 수: {updated_count}")
    print(f"⏩ Description이 이미 최신 상태여서 건너뛴 상품 수: {skipped_already_latest_count}")
    print(f"❌ Shopify에 매칭되지 않아 건너뛴 스크래핑 HTML 수: {skipped_not_found_count}")
    print(f"⚠️ Description 업데이트에 실패한 상품 수: {failed_product_updates_count}")
    print(f"📦 Shopify에서 최종적으로 가져온 총 상품 수: {total_shopify_products_fetched}")
    print(f"📁 총 스크래핑된 HTML 파일 수: {total_scraped_html_files}")
    print(f"\n상세 로그: '{ERROR_LOG_FILE}'")
    print(f"상품 업데이트 보고서: '{PRODUCT_UPDATE_REPORT_FILE}'")
    print(f"이미지 업로드 보고서: '{IMAGE_UPLOAD_REPORT_FILE}'")
    print("\n--- 작업 완료 ---")

if __name__ == "__main__":
    main()