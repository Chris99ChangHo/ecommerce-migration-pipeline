import os
import csv
import time
import requests
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from tqdm import tqdm
from tenacity import retry, wait_fixed, stop_after_attempt, retry_if_exception_type
from collections import OrderedDict
# from requests_toolbelt.multipart.encoder import MultipartEncoder # 이 모듈은 현재 코드에서 사용되지 않습니다.
from urllib.parse import urlparse, parse_qs # parse_qs를 올바르게 import

# --- 설정 (Shopify 정보) ---
# ⚠️  아래 두 값을 본인의 Shopify 스토어 정보로 교체하세요.
SHOPIFY_STORE_URL = "your-store.myshopify.com"          # 예: my-shop.myshopify.com
SHOPIFY_ADMIN_API_ACCESS_TOKEN = "shpat_your_token_here"  # Shopify Admin API 액세스 토큰

SHOPIFY_ADMIN_API_URL = f"https://{SHOPIFY_STORE_URL}/admin/api/2024-07/graphql.json"

BASE_DIR = "상품설명사진"
UPLOADED_IMAGES_CSV = os.path.join("uploaded_files", "uploaded_images.csv")


# --- GraphQL 클라이언트 생성 ---
def get_graphql_client():
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ADMIN_API_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    transport = RequestsHTTPTransport(
        url=SHOPIFY_ADMIN_API_URL,
        headers=headers,
        use_json=True,
        timeout=30
    )
    client = Client(transport=transport, fetch_schema_from_transport=True)
    return client


# --- 유틸리티 함수 ---
def ensure_directory_exists(dir_path):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

def append_to_csv(filename, data_row):
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(data_row)

def load_uploaded_images(filename):
    uploaded = set()
    if os.path.exists(filename):
        with open(filename, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if row:
                    uploaded.add(row[0])
    return uploaded


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


# --- 실제 업로드 함수 ---
@retry(wait=wait_fixed(5), stop=stop_after_attempt(3),
       retry=retry_if_exception_type(requests.exceptions.RequestException))
def upload_file_to_signed_url(file_path, upload_url, parameters):
    try:
        file_name = os.path.basename(file_path)
        mime_type = "application/octet-stream"
        
        # content_type 파라미터 찾기
        for param in parameters:
            if param["name"] == "content_type":
                mime_type = param["value"]
                break
        
        print(f"    [INFO] 파일 직접 업로드 시도 (PUT/POST): {file_name}")

        # GCS POST 요청에 필요한 'policy' 파라미터가 있는지 확인
        # 이전에 Shopify가 policy를 주지 않는다고 확인되었으므로,
        # parameters에 policy가 없으면 PUT 요청으로 강제 전환합니다.
        policy_param_found = any(param["name"] == "policy" for param in parameters)

        if policy_param_found: # policy 파라미터가 있다면, POST 요청으로 시도
            print(f"    [INFO] 'policy' 파라미터 존재. POST 요청으로 파일 업로드 시도.")
            
            files = {
                'file': (file_name, open(file_path, 'rb'), mime_type)
            }

            data = {}
            for param in parameters:
                data[param["name"]] = param["value"]

            parsed_url = urlparse(upload_url)
            query_params = parse_qs(parsed_url.query)

            base_upload_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            object_key = parsed_url.path.lstrip('/')
            if 'key' not in data:
                data['key'] = object_key

            for param_name in ['X-Goog-Algorithm', 'X-Goog-Credential', 'X-Goog-Date', 
                               'X-Goog-Expires', 'X-Goog-SignedHeaders', 'X-Goog-Signature']:
                if param_name.lower() in query_params:
                    data[param_name] = query_params[param_name.lower()][0]
                elif param_name in query_params:
                     data[param_name] = query_params[param_name][0]
            
            print(f"    [DEBUG] POST URL: {base_upload_url}")
            print(f"    [DEBUG] POST Data Fields: {list(data.keys())}")
            print(f"    [DEBUG] POST Files Field: {list(files.keys())}")

            response = requests.post(base_upload_url, data=data, files=files, timeout=60)

        else: # 'policy' 파라미터가 없다면, PUT 요청으로 시도
            print(f"    [INFO] 'policy' 파라미터 없음. PUT 요청으로 파일 업로드 시도.")
            with open(file_path, "rb") as f:
                file_data = f.read()

            # Shopify가 제공하는 parameters를 PUT 요청의 헤더로 사용합니다.
            # GCS Signed PUT URL은 URL 자체에 모든 서명 정보가 포함되어 있으므로
            # parameters의 'name'을 'value'로 매핑하여 헤더로 보냅니다.
            headers = {'Content-Type': mime_type}
            for param in parameters:
                # 'content_type'은 이미 mime_type으로 설정했으므로 건너뛰거나,
                # 중복이 아니면 추가합니다. acl 같은 필드는 추가해야 합니다.
                if param["name"] != "content_type":
                    headers[param["name"]] = param["value"]
            
            print(f"    [DEBUG] PUT URL: {upload_url}")
            print(f"    [DEBUG] PUT Headers: {headers}")

            response = requests.put(upload_url, data=file_data, headers=headers, timeout=60)

        print(f"    [DEBUG] 응답 코드: {response.status_code}")
        print(f"    [DEBUG] 응답 내용: {response.text}")
        response.raise_for_status()

        print(f"    [SUCCESS] '{file_name}' 파일이 서명된 URL로 성공적으로 업로드되었습니다.")
        return True

    except requests.exceptions.RequestException as e:
        print(f"    [ERROR] 서명된 URL로 파일 업로드 실패 (파일: {file_name}, URL: {upload_url}): {e}")
        raise
    except Exception as e:
        print(f"    [ERROR] 알 수 없는 오류로 파일 업로드 실패 (파일: {file_name}): {e}")
        raise

def upload_images_to_shopify(client, image_paths):
    uploaded_image_details = []

    # 1. 서명된 업로드 URL 요청
    staged_inputs = []
    for img_path in image_paths:
        file_name = os.path.basename(img_path)
        file_size = os.path.getsize(img_path)
        mime_type = "image/jpeg"
        if file_name.lower().endswith(".png"): mime_type = "image/png"
        elif file_name.lower().endswith(".gif"): mime_type = "image/gif"
        elif file_name.lower().endswith(".webp"): mime_type = "image/webp"

        staged_inputs.append({
            "filename": file_name,
            "mimeType": mime_type,
            "resource": "IMAGE",
            "fileSize": str(file_size),
        })

    try:
        print(f"[INFO] {len(staged_inputs)}개의 파일에 대한 서명된 업로드 URL을 요청합니다.")
        response = client.execute(
            document=STAGED_UPLOAD_CREATE_MUTATION,
            variable_values={"input": staged_inputs}
        )

        user_errors = response.get("stagedUploadsCreate", {}).get("userErrors")
        if user_errors:
            for err in user_errors:
                print(f"[ERROR] stagedUploadsCreate 사용자 오류: {err.get('message')} (필드: {err.get('field')})")
            return []

        staged_targets = response.get("stagedUploadsCreate", {}).get("stagedTargets")
        if not staged_targets:
            print("[ERROR] stagedUploadsCreate 응답에서 stagedTargets를 찾을 수 없습니다.")
            return []

        successfully_uploaded_staged_files = [] # 실제로 서명된 URL에 업로드 성공한 파일들

        for i, target in enumerate(staged_targets):
            img_path = image_paths[i]
            print(f"    [INFO] 처리 중인 파일: '{os.path.basename(img_path)}'")
            print(f"    [INFO] Shopify Staged Target URL: {target.get('resourceUrl', 'N/A')}")
            print(f"    [INFO] 직접 업로드할 Signed URL: {target['url']}") # Signed URL은 target['url']

            # 디버깅용: Shopify가 제공하는 모든 파라미터를 출력
            print(f"    [DEBUG] Shopify provided parameters: {target['parameters']}")

            # upload_file_to_signed_url 함수는 이제 parameters 유무에 따라 POST/PUT을 자체적으로 결정
            if upload_file_to_signed_url(img_path, target["url"], target["parameters"]):
                successfully_uploaded_staged_files.append({
                    "filename": os.path.basename(img_path),
                    "original_path": img_path,
                    "staged_target_url": target["resourceUrl"] # Shopify에 등록할 때 사용할 URL
                })
            time.sleep(0.5) # 개별 파일 업로드 후 대기

    except Exception as e:
        print(f"[ERROR] 서명된 URL 요청 또는 직접 업로드 중 예외 발생: {e}")
        return []

    if not successfully_uploaded_staged_files:
        print("[WARNING] 서명된 URL로 업로드된 파일이 없습니다. Shopify Files에 등록할 파일이 없습니다.")
        return []

    # 2. 업로드된 파일 Shopify Files에 등록
    file_inputs = [{
        "alt": os.path.splitext(f["filename"])[0],
        "originalSource": f["staged_target_url"]
    } for f in successfully_uploaded_staged_files]

    try:
        print(f"[INFO] {len(file_inputs)}개의 파일을 Shopify Files에 등록합니다.")
        file_response = client.execute(
            document=FILE_CREATE_MUTATION,
            variable_values={"files": file_inputs}
        )

        user_errors = file_response.get("fileCreate", {}).get("userErrors")
        if user_errors:
            for err in user_errors:
                print(f"[ERROR] fileCreate 사용자 오류: {err.get('message')} (필드: {err.get('field')})")
            return []

        created_files = file_response.get("fileCreate", {}).get("files")
        if not created_files:
            print("[ERROR] fileCreate 응답에서 생성된 파일을 찾을 수 없습니다.")
            return []

        for file_info in created_files:
            if file_info and file_info.get('originalSource', {}).get('url'):
                shopify_file_url = file_info['originalSource']['url']
                shopify_file_id = file_info.get('id')
                uploaded_image_details.append({
                    "shopify_url": shopify_file_url,
                    "shopify_id": shopify_file_id,
                    # staged_target_url과 일치하는 original_path를 찾기
                    "local_path": next((f['original_path'] for f in successfully_uploaded_staged_files if f['staged_target_url'] == shopify_file_url), "N/A")
                })
                print(f"[SUCCESS] Shopify에 파일 등록 완료: {shopify_file_url} (ID: {shopify_file_id})")

        return uploaded_image_details

    except Exception as e:
        print(f"[ERROR] Shopify Files GraphQL 등록 중 예외 발생: {e}")
        return []


def main():
    ensure_directory_exists("uploaded_files")
    client = get_graphql_client()
    if not client:
        print("[FATAL] GraphQL 클라이언트 초기화 실패. 스크립트를 종료합니다.")
        return

    all_images_to_upload = []
    for product_folder_name in os.listdir(BASE_DIR):
        product_folder_path = os.path.join(BASE_DIR, product_folder_name)
        image_dir_path = os.path.join(product_folder_path, "images")

        if os.path.isdir(image_dir_path):
            for img_file in os.listdir(image_dir_path):
                if img_file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                    full_image_path = os.path.join(image_dir_path, img_file)
                    all_images_to_upload.append(full_image_path)
    
    already_uploaded = load_uploaded_images(UPLOADED_IMAGES_CSV)
    
    images_to_process = [img for img in all_images_to_upload if img not in already_uploaded]

    if not images_to_process:
        print("[INFO] 새로 업로드할 이미지가 없습니다.")
        return

    print(f"[INFO] 총 {len(images_to_process)}개의 이미지를 Shopify에 업로드할 예정입니다.")

    batch_size = 5 # 한 번에 처리할 이미지 수 (Shopify API 제한에 따라 조정 가능)
    uploaded_count = 0

    for i in tqdm(range(0, len(images_to_process), batch_size), desc="이미지 업로드 진행", unit="이미지"):
        batch = images_to_process[i:i + batch_size]
        
        print(f"\n[INFO] {i+1}-{min(i+batch_size, len(images_to_process))}번째 이미지 배치 처리 중...")
        
        try:
            uploaded_details = upload_images_to_shopify(client, batch)
            
            for detail in uploaded_details:
                append_to_csv(UPLOADED_IMAGES_CSV, [detail["local_path"], detail["shopify_url"], detail["shopify_id"]])
                uploaded_count += 1
            
            time.sleep(1) # API 레이트 리밋 준수 (배치당 1초 대기)
        except ValueError as e:
            print(f"[FATAL] 배치 처리 중 치명적인 오류 발생: {e}. 스크립트를 중단합니다.")
            break # 치명적인 오류 발생 시 전체 스크립트 중단
        except Exception as e:
            print(f"[ERROR] 배치 처리 중 일반 오류 발생: {e}. 다음 배치로 넘어갑니다.")


    print(f"\n[SUMMARY] 총 {uploaded_count}개의 이미지를 성공적으로 Shopify에 업로드했습니다.")
    print(f"[SUMMARY] 업로드된 이미지 목록은 '{UPLOADED_IMAGES_CSV}'에 저장되었습니다.")


if __name__ == "__main__":
    main()