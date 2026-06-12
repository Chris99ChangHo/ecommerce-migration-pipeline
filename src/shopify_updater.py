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
from tenacity import (
    retry,
    wait_fixed,
    stop_after_attempt,
    retry_if_exception_type,
    wait_exponential,
)
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from dotenv import load_dotenv

# --- 환경변수 로드 (.env 보호) ---
load_dotenv()
TARGET_URL = os.getenv("TARGET_URL", "example-target.myshopify.com")
TARGET_API_TOKEN = os.getenv("TARGET_API_TOKEN")

if not TARGET_API_TOKEN:
    raise ValueError(
        "TARGET_API_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요."
    )


# --- 유틸리티 함수 ---
def ensure_directory_exists(dir_path):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)


# --- Shopify API 및 파일 시스템 설정 ---
SHOPIFY_ADMIN_API_REST_URL = f"https://{TARGET_URL}/admin/api/2025-07/"
SHOPIFY_ADMIN_API_GRAPHQL_URL = (
    f"https://{TARGET_URL}/admin/api/2025-07/graphql.json"
)
API_VERSION = "2025-07"

SCRAPED_HTML_BASE_DIR = os.path.join("data", "productdesimg")
UPLOADED_FILES_DIR = os.path.join("data", "logs", "uploaded_files")
UPLOADED_IMAGES_CSV = os.path.join(UPLOADED_FILES_DIR, "uploaded_images.csv")

LOG_DIR = os.path.join("data", "logs")
ensure_directory_exists(LOG_DIR)
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
ERROR_LOG_FILE = os.path.join(LOG_DIR, f"error_log_{TIMESTAMP}.txt")
PRODUCT_UPDATE_REPORT_FILE = os.path.join(
    LOG_DIR, f"product_update_report_{TIMESTAMP}.csv"
)
IMAGE_UPLOAD_REPORT_FILE = os.path.join(LOG_DIR, f"image_upload_report_{TIMESTAMP}.csv")


# --- GraphQL 클라이언트 생성 ---
def get_graphql_client():
    headers = {
        "X-Shopify-Access-Token": TARGET_API_TOKEN,
        "Content-Type": "application/json",
    }
    transport = RequestsHTTPTransport(
        url=SHOPIFY_ADMIN_API_GRAPHQL_URL, headers=headers, use_json=True, timeout=30
    )
    return Client(transport=transport, fetch_schema_from_transport=False)


STAGED_UPLOAD_CREATE_MUTATION = gql(
    """
mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message }
  }
}
"""
)

FILE_CREATE_MUTATION = gql(
    """
mutation fileCreate($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files { ... on MediaImage { id originalSource { url } } }
    userErrors { field message }
  }
}
"""
)

GET_FILE_URL_QUERY = gql(
    """
query node($id: ID!) {
  node(id: $id) { ... on MediaImage { id image { url } originalSource { url } } }
}
"""
)


# --- 각종 로깅 및 파싱 유틸 함수 ---
def append_to_csv(filename, data_row):
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(data_row)


def write_log(message, log_file=ERROR_LOG_FILE):
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def initialize_report_csv(filename, headers):
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(headers)


def load_uploaded_images_map(filename):
    image_map = {}
    if os.path.exists(filename):
        with open(filename, "r", newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 2:
                    image_map[row[0]] = row[1]
    return image_map


def normalize_title(title):
    return (
        re.sub(r"\s+", " ", re.sub(r"[^\w\s\uAC00-\uD7A3]", "", title)).strip().lower()
    )


def read_html_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        write_log(f"HTML 파일 읽기 실패: {file_path} - {e}", log_file=ERROR_LOG_FILE)
        return None


# --- Staged Upload 업로드 래퍼 (Tenacity 적용) ---
@retry(
    wait=wait_fixed(5),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
)
def upload_file_to_signed_url(file_path, upload_url, parameters):
    file_name = os.path.basename(file_path)
    try:
        mime_type = "application/octet-stream"
        if file_name.lower().endswith(".png"):
            mime_type = "image/png"
        elif file_name.lower().endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        elif file_name.lower().endswith(".gif"):
            mime_type = "image/gif"
        elif file_name.lower().endswith(".webp"):
            mime_type = "image/webp"

        for param in parameters:
            if param["name"] == "Content-Type":
                mime_type = param["value"]
                break

        policy_param_found = any(param["name"] == "policy" for param in parameters)

        if policy_param_found:
            files = {"file": (file_name, open(file_path, "rb"), mime_type)}
            data = {param["name"]: param["value"] for param in parameters}

            parsed_upload_url = urlparse(upload_url)
            query_params_from_url = parse_qs(parsed_upload_url.query)

            if "key" not in data and parsed_upload_url.path:
                data["key"] = parsed_upload_url.path.lstrip("/")

            for param_name in [
                "X-Goog-Algorithm",
                "X-Goog-Credential",
                "X-Goog-Date",
                "X-Goog-Expires",
                "X-Goog-SignedHeaders",
                "X-Goog-Signature",
                "x-amz-algorithm",
                "x-amz-credential",
                "x-amz-date",
                "x-amz-expires",
                "x-amz-signedheaders",
                "x-amz-signature",
            ]:
                if param_name.lower() in query_params_from_url:
                    data[param_name] = query_params_from_url[param_name.lower()][0]
                elif param_name in query_params_from_url:
                    data[param_name] = query_params_from_url[param_name][0]

            base_upload_url_for_post = f"{parsed_upload_url.scheme}://{parsed_upload_url.netloc}{parsed_upload_url.path}"
            response = requests.post(
                base_upload_url_for_post, data=data, files=files, timeout=60
            )
        else:
            with open(file_path, "rb") as f:
                file_data = f.read()
            headers = {"Content-Type": mime_type}
            for param in parameters:
                if param["name"] != "Content-Type":
                    headers[param["name"]] = param["value"]
            response = requests.put(
                upload_url, data=file_data, headers=headers, timeout=60
            )

        response.raise_for_status()
        return True
    except Exception as e:
        write_log(f"파일 업로드 실패 (파일: {file_name}): {e}", log_file=ERROR_LOG_FILE)
        raise


def upload_single_image_to_shopify(graphql_client, image_path, original_wix_src):
    if not os.path.exists(image_path):
        return None
    file_name = os.path.basename(image_path)
    file_size = os.path.getsize(image_path)
    mime_type = "image/jpeg"
    if file_name.lower().endswith(".png"):
        mime_type = "image/png"
    elif file_name.lower().endswith(".gif"):
        mime_type = "image/gif"
    elif file_name.lower().endswith(".webp"):
        mime_type = "image/webp"

    staged_input = {
        "filename": file_name,
        "mimeType": mime_type,
        "resource": "IMAGE",
        "fileSize": str(file_size),
    }

    try:
        response = graphql_client.execute(
            document=STAGED_UPLOAD_CREATE_MUTATION,
            variable_values={"input": [staged_input]},
        )
        staged_targets = response.get("stagedUploadsCreate", {}).get("stagedTargets")
        if not staged_targets:
            return None

        target = staged_targets[0]
        if upload_file_to_signed_url(image_path, target["url"], target["parameters"]):
            file_input = {
                "alt": os.path.splitext(file_name)[0],
                "originalSource": target["resourceUrl"],
            }
            file_response = graphql_client.execute(
                document=FILE_CREATE_MUTATION, variable_values={"files": [file_input]}
            )
            time.sleep(2)

            created_files = file_response.get("fileCreate", {}).get("files")
            file_id = (
                created_files[0]["id"] if created_files and created_files[0] else None
            )
            shopify_file_url = (
                created_files[0]["image"]["url"]
                if created_files and created_files[0] and created_files[0].get("image")
                else None
            )

            if shopify_file_url is None and file_id:
                for attempt in range(1, 6):
                    time.sleep(min(2**attempt, 30))
                    try:
                        file_query_response = graphql_client.execute(
                            document=GET_FILE_URL_QUERY, variable_values={"id": file_id}
                        )
                        queried_node = file_query_response.get("node")
                        if queried_node and queried_node.get("image", {}).get("url"):
                            shopify_file_url = queried_node["image"]["url"]
                            break
                    except Exception:
                        pass
            return shopify_file_url
        return None
    except Exception as e:
        write_log(f"이미지 업로드 중 예외 발생: {e}", log_file=ERROR_LOG_FILE)
        return None


def clean_html_and_process_images(
    html_content, product_folder_path, uploaded_images_map, graphql_client
):
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for span in soup.find_all("span"):
        if span.get_text(strip=True).replace("\u200b", "") in [
            "Product Info",
            "제품 정보",
        ]:
            span.extract()

    for tag in soup.find_all(True):
        if tag.name in ["span", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]:
            original_style = tag.get("style")
            tag.attrs.clear()
            if original_style:
                tag["style"] = original_style
        elif tag.name == "img":
            original_src = tag.get("src")
            original_alt = tag.get("alt")
            original_width, original_height = tag.get("width"), tag.get("height")
            tag.attrs.clear()
            shopify_cdn_url = None

            if original_src:
                try:
                    file_name_from_wix = os.path.basename(urlparse(original_src).path)
                    local_image_path = os.path.join(
                        product_folder_path, "images", file_name_from_wix
                    ).replace("\\", "/")
                    shopify_cdn_url = uploaded_images_map.get(local_image_path)

                    if not shopify_cdn_url:
                        shopify_cdn_url = upload_single_image_to_shopify(
                            graphql_client, local_image_path, original_src
                        )
                        if shopify_cdn_url:
                            append_to_csv(
                                UPLOADED_IMAGES_CSV,
                                [local_image_path, shopify_cdn_url, "N/A"],
                            )
                            uploaded_images_map[local_image_path] = shopify_cdn_url
                        else:
                            shopify_cdn_url = original_src
                except Exception:
                    shopify_cdn_url = original_src

            if shopify_cdn_url:
                tag["src"] = shopify_cdn_url
            if original_alt:
                tag["alt"] = original_alt
            if original_width:
                tag["width"] = original_width
            if original_height:
                tag["height"] = original_height
        else:
            tag.attrs.clear()

    for br in soup.find_all("br"):
        br.extract()
    final_html = str(soup)
    final_html = re.sub(r"\n{2,}", "\n", final_html)
    return re.sub(r"\s\s+", " ", final_html)


# --- 모듈화된 실행 엔트리포인트 ---
def run():
    initialize_report_csv(
        PRODUCT_UPDATE_REPORT_FILE, ["상품명", "HTML 파일 경로", "상태", "오류 메시지"]
    )
    initialize_report_csv(
        IMAGE_UPLOAD_REPORT_FILE,
        ["파일 이름 (로컬)", "원본 Wix SRC", "상태", "오류 메시지", "Shopify CDN URL"],
    )
    write_log("스크립트 실행 시작", log_file=ERROR_LOG_FILE)

    try:
        session = shopify.Session(
            f"https://{TARGET_URL}", API_VERSION, TARGET_API_TOKEN
        )
        shopify.ShopifyResource.activate_session(session)
    except Exception as e:
        print(f"[ERROR] Shopify REST API 초기화 실패: {e}")
        return

    graphql_client = get_graphql_client()
    if not graphql_client:
        return

    ensure_directory_exists(UPLOADED_FILES_DIR)
    uploaded_images_map = load_uploaded_images_map(UPLOADED_IMAGES_CSV)
    scraped_products_html_data = {}

    if not os.path.exists(SCRAPED_HTML_BASE_DIR):
        print(f"[WARNING] '{SCRAPED_HTML_BASE_DIR}' 폴더가 없습니다.")
        return

    for product_folder_name in os.listdir(SCRAPED_HTML_BASE_DIR):
        folder_path = os.path.join(SCRAPED_HTML_BASE_DIR, product_folder_name)
        html_file_path = os.path.join(folder_path, "product_info.html")
        if os.path.isdir(folder_path) and os.path.exists(html_file_path):
            scraped_products_html_data[normalize_title(product_folder_name)] = {
                "html_file_path": html_file_path,
                "product_folder_path": folder_path,
                "original_product_title": product_folder_name,
            }

    if not scraped_products_html_data:
        return

    print("\n[INFO] Shopify 상품 목록을 페이지별로 가져오는 중...")
    all_shopify_products, last_id = {}, None

    while True:
        try:
            params = {"limit": 250, "order": "id asc"}
            if last_id:
                params["since_id"] = last_id
            products = shopify.Product.find(**params)
            if not products:
                break
            for product in products:
                all_shopify_products[normalize_title(product.title)] = product
            last_id = products[-1].id
            time.sleep(0.7)
        except Exception:
            break

    updated_count, skipped_already_latest_count = 0, 0
    print("\n[INFO] 상품 Description 클리닝, 이미지 업로드/교체, 업데이트 중...")

    for normalized_folder_title, data in tqdm(
        scraped_products_html_data.items(), desc="상품 Description 업데이트"
    ):
        shopify_product = all_shopify_products.get(normalized_folder_title)
        if shopify_product:
            original_html_content = read_html_file(data["html_file_path"])
            if original_html_content:
                cleaned_html_content = clean_html_and_process_images(
                    original_html_content,
                    data["product_folder_path"],
                    uploaded_images_map,
                    graphql_client,
                )
                try:
                    if (
                        shopify_product.body_html
                        and shopify_product.body_html.strip()
                        == cleaned_html_content.strip()
                    ):
                        skipped_already_latest_count += 1
                    else:
                        shopify_product.body_html = cleaned_html_content
                        shopify_product.save()
                        updated_count += 1
                    time.sleep(0.5)
                except Exception as e:
                    write_log(f"업데이트 실패: {e}", log_file=ERROR_LOG_FILE)

    print(
        f"\n✅ 완료! 업데이트된 상품: {updated_count}개 (스킵: {skipped_already_latest_count}개)"
    )


if __name__ == "__main__":
    run()
