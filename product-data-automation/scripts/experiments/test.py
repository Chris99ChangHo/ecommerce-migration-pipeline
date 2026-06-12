import os
import csv
import time
import requests
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from tqdm import tqdm
from tenacity import retry, wait_fixed, stop_after_attempt, retry_if_exception_type
from urllib.parse import urlparse, parse_qs

# --- 설정 (Shopify 정보) ---
# ⚠️  아래 두 값을 본인의 Shopify 스토어 정보로 교체하세요.
SHOPIFY_STORE_URL = "your-store.myshopify.com"          # 예: my-shop.myshopify.com
SHOPIFY_ADMIN_API_ACCESS_TOKEN = "shpat_your_token_here" # Shopify Admin API 액세스 토큰

SHOPIFY_ADMIN_API_URL = f"https://{SHOPIFY_STORE_URL}/admin/api/2024-07/graphql.json"

BASE_DIR = "상품설명사진"
UPLOADED_IMAGES_CSV = os.path.join("uploaded_files", "uploaded_images_log.csv") # 로그 파일명 변경


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

def write_csv_header(filename):
    ensure_directory_exists(os.path.dirname(filename))
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['local_path', 'shopify_url', 'shopify_id', 'status', 'error_message'])

def append_to_csv(filename, data_row):
    ensure_directory_exists(os.path.dirname(filename))
    if not os.path.exists(filename) or os.stat(filename).st_size == 0:
        write_csv_header(filename)
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(data_row)

def load_uploaded_images(filename):
    uploaded = set()
    if os.path.exists(filename):
        with open(filename, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 'status' 필드가 있고 값이 'SUCCESS'인 경우에만 추가
                if row.get('status') == 'SUCCESS' and 'local_path' in row:
                    uploaded.add(row['local_path'])
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
    """
    서명된 URL을 사용하여 클라우드 스토리지(GCS)에 파일을 업로드합니다.
    policy 파라미터 유무에 따라 POST 또는 PUT 요청을 선택합니다.
    """
    file_name = os.path.basename(file_path)
    mime_type = "application/octet-stream"
    
    for param in parameters:
        if param["name"] == "content_type":
            mime_type = param["value"]
            break
    
    policy_param_found = any(param["name"] == "policy" for param in parameters)

    try:
        if policy_param_found: # policy 파라미터가 있다면, POST 요청으로 시도
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
            
            response = requests.post(base_upload_url, data=data, files=files, timeout=60)

        else: # 'policy' 파라미터가 없다면, PUT 요청으로 시도
            with open(file_path, "rb") as f:
                file_data = f.read()

            headers = {'Content-Type': mime_type}
            for param in parameters:
                if param["name"] != "content_type": # content_type은 이미 설정
                    headers[param["name"]] = param["value"]
            
            response = requests.put(upload_url, data=file_data, headers=headers, timeout=60)

        response.raise_for_status()

        print(f"    ✅ '{file_name}' GCS 업로드 성공.")
        return True, None # 성공 시 True와 에러 메시지 없음 반환

    except requests.exceptions.RequestException as e:
        error_msg = f"HTTP 요청 실패: {e} (응답: {e.response.text if e.response else 'N/A'})"
        print(f"    ❌ '{file_name}' GCS 업로드 실패: {error_msg}")
        return False, error_msg # 실패 시 False와 에러 메시지 반환
    except Exception as e:
        error_msg = f"알 수 없는 오류: {e}"
        print(f"    ❌ '{file_name}' GCS 업로드 중 예상치 못한 오류: {error_msg}")
        return False, error_msg


@retry(wait=wait_fixed(5), stop=stop_after_attempt(3), # 최대 3번 재시도, 5초 대기
       retry=retry_if_exception_type(Exception)) # 모든 예외에 대해 재시도
def register_files_to_shopify(client, file_inputs, successfully_staged_for_shopify_register):
    """
    Shopify Files에 실제로 등록하는 GraphQL 호출을 담당하고 재시도 로직을 포함합니다.
    """
    uploaded_image_details = []
    
    try:
        file_response = client.execute(
            document=FILE_CREATE_MUTATION,
            variable_values={"files": file_inputs}
        )

        user_errors = file_response.get("fileCreate", {}).get("userErrors")
        if user_errors:
            for err in user_errors:
                error_msg = f"Shopify Files 등록 중 오류: {err.get('message')} (필드: {err.get('field')})"
                print(f"    ❌ Shopify Files 등록 실패: {error_msg}")
                # 오류 발생한 파일을 찾아 CSV에 기록 (정확한 매핑 필요)
                for f_input in file_inputs:
                    local_path = next((s['original_path'] for s in successfully_staged_for_shopify_register if s['staged_target_url'] == f_input['originalSource']), f_input['originalSource'])
                    append_to_csv(UPLOADED_IMAGES_CSV, [local_path, f_input['originalSource'], '', 'FAILED_SHOPIFY_REGISTER', error_msg])
            raise Exception("Shopify Files userErrors 발생") # 재시도를 위해 예외 발생

        created_files = file_response.get("fileCreate", {}).get("files")
        if not created_files:
            error_msg = "Shopify Files 응답에 생성된 파일 없음."
            print(f"    ❌ Shopify Files 등록 실패: {error_msg}")
            for f_input in file_inputs:
                local_path = next((s['original_path'] for s in successfully_staged_for_shopify_register if s['staged_target_url'] == f_input['originalSource']), f_input['originalSource'])
                append_to_csv(UPLOADED_IMAGES_CSV, [local_path, f_input['originalSource'], '', 'FAILED_SHOPIFY_REGISTER', error_msg])
            raise Exception("Shopify Files created_files 없음") # 재시도를 위해 예외 발생

        for file_info in created_files:
            if file_info and file_info.get('originalSource', {}).get('url') and file_info.get('id'):
                shopify_file_url = file_info['originalSource']['url']
                shopify_file_id = file_info['id']
                local_path = next((f['original_path'] for f in successfully_staged_for_shopify_register if f['staged_target_url'] == shopify_file_url), "N/A")
                
                uploaded_image_details.append({
                    "shopify_url": shopify_file_url,
                    "shopify_id": shopify_file_id,
                    "local_path": local_path
                })
                # 성공 로그를 CSV에 기록
                append_to_csv(UPLOADED_IMAGES_CSV, [local_path, shopify_file_url, shopify_file_id, 'SUCCESS', ''])
                print(f"    ✅ '{os.path.basename(local_path)}' Shopify 등록 완료. ID: {shopify_file_id}")
            else:
                missing_info = []
                if not file_info: missing_info.append("file_info is None")
                if not file_info.get('originalSource', {}).get('url'): missing_info.append("originalSource.url missing")
                if not file_info.get('id'): missing_info.append("id missing")
                
                error_msg = f"불완전한 Shopify fileCreate 응답 (누락: {', '.join(missing_info)})."
                print(f"    ❌ Shopify Files 등록 실패: {error_msg} (일부 파일).")
                # 실패한 파일에 대해 다시 시도해야 하므로, 해당 파일만 실패 처리하고 예외를 발생시키지 않음
                # 그러나 이 경우 해당 배치는 성공으로 간주되므로, 더 엄격하게 처리하려면 raise Exception을 하는 것이 좋습니다.
                # 여기서는 '불완전한 응답'이 나온다는 것은 전체 배치에 문제가 있을 가능성이 높으므로 예외를 발생시키겠습니다.
                for f_info_staged in successfully_staged_for_shopify_register:
                    # 해당 file_info에 대응하는 staged 정보를 찾기
                    if f_info_staged['staged_target_url'] == file_info.get('originalSource', {}).get('url'):
                        append_to_csv(UPLOADED_IMAGES_CSV, [f_info_staged['original_path'], f_info_staged['staged_target_url'], '', 'FAILED_SHOPIFY_REGISTER', error_msg])
                raise Exception(f"불완전한 Shopify fileCreate 응답 발생: {error_msg}") # 재시도를 위해 예외 발생

        return uploaded_image_details

    except Exception as e:
        error_msg = f"Shopify Files GraphQL 등록 중 예외 발생: {e}"
        print(f"[ERROR] {error_msg}")
        # 이 예외는 tenacity에 의해 재시도될 것입니다.
        # 실패한 파일은 이미 위에서 append_to_csv를 통해 기록되었거나, 재시도 후 최종 실패 시 기록됩니다.
        raise # tenacity가 예외를 catch할 수 있도록 다시 예외 발생


def upload_images_to_shopify(client, image_paths):
    uploaded_image_details = []
    staged_inputs_with_local_path = [] # local_path를 포함한 전체 정보
    
    # 1. 서명된 업로드 URL 요청을 위한 staged_inputs 구성
    for img_path in image_paths:
        file_name = os.path.basename(img_path)
        file_size = os.path.getsize(img_path)
        mime_type = "image/jpeg"
        if file_name.lower().endswith(".png"): mime_type = "image/png"
        elif file_name.lower().endswith(".gif"): mime_type = "image/gif"
        elif file_name.lower().endswith(".webp"): mime_type = "image/webp"

        staged_inputs_with_local_path.append({
            "filename": file_name,
            "mimeType": mime_type,
            "resource": "IMAGE",
            "fileSize": str(file_size),
            "local_path": img_path # 로컬 경로를 임시로 저장하여 매핑에 사용
        })

    graphql_inputs = []
    for s_input in staged_inputs_with_local_path:
        graphql_inputs.append({
            "filename": s_input["filename"],
            "mimeType": s_input["mimeType"],
            "resource": s_input["resource"],
            "fileSize": s_input["fileSize"]
        })

    staged_targets_map = {} # 로컬 경로를 키로 staged target 정보를 저장
    try:
        response = client.execute(
            document=STAGED_UPLOAD_CREATE_MUTATION,
            variable_values={"input": graphql_inputs}
        )

        user_errors = response.get("stagedUploadsCreate", {}).get("userErrors")
        if user_errors:
            for err in user_errors:
                error_msg = f"서명된 URL 요청 중 오류: {err.get('message')} (필드: {err.get('field')})"
                for s_input in staged_inputs_with_local_path:
                    append_to_csv(UPLOADED_IMAGES_CSV, [s_input["local_path"], '', '', 'FAILED_STAGED_URL_REQUEST', error_msg])
            return []

        staged_targets = response.get("stagedUploadsCreate", {}).get("stagedTargets")
        if not staged_targets:
            error_msg = "서명된 URL 응답에 stagedTargets 없음."
            for s_input in staged_inputs_with_local_path:
                append_to_csv(UPLOADED_IMAGES_CSV, [s_input["local_path"], '', '', 'FAILED_STAGED_URL_REQUEST', error_msg])
            return []

        for i, target in enumerate(staged_targets):
            staged_targets_map[staged_inputs_with_local_path[i]["local_path"]] = target

    except Exception as e:
        error_msg = f"서명된 URL 요청 중 예외 발생: {e}"
        print(f"[ERROR] {error_msg}")
        for s_input in staged_inputs_with_local_path:
            append_to_csv(UPLOADED_IMAGES_CSV, [s_input["local_path"], '', '', 'FAILED_STAGED_URL_REQUEST', error_msg])
        return []

    successfully_staged_for_shopify_register = [] # GCS에 성공적으로 업로드된 파일들

    # 2. GCS에 파일 직접 업로드
    for img_path_info in staged_inputs_with_local_path:
        local_path = img_path_info["local_path"]
        target = staged_targets_map.get(local_path)

        if not target:
            print(f"    ❌ '{os.path.basename(local_path)}' staged target 정보 없음. 건너뜁니다.")
            append_to_csv(UPLOADED_IMAGES_CSV, [local_path, '', '', 'FAILED_GCS_UPLOAD', 'No staged target info'])
            continue

        success, error_msg = upload_file_to_signed_url(local_path, target["url"], target["parameters"])
        
        if success:
            successfully_staged_for_shopify_register.append({
                "filename": os.path.basename(local_path),
                "original_path": local_path,
                "staged_target_url": target["resourceUrl"] # Shopify에 등록할 때 사용할 URL
            })
        else:
            append_to_csv(UPLOADED_IMAGES_CSV, [local_path, '', '', 'FAILED_GCS_UPLOAD', error_msg])
        
    if not successfully_staged_for_shopify_register:
        print("[WARNING] 서명된 URL로 GCS에 업로드된 파일이 없습니다. Shopify Files에 등록할 파일이 없습니다.")
        return []

    # 3. 업로드된 파일 Shopify Files에 등록 (재시도 로직 적용)
    file_inputs = [{
        "alt": os.path.splitext(f["filename"])[0],
        "originalSource": f["staged_target_url"]
    } for f in successfully_staged_for_shopify_register]

    try:
        # register_files_to_shopify 함수에 재시도 로직이 적용됨
        uploaded_image_details = register_files_to_shopify(client, file_inputs, successfully_staged_for_shopify_register)
        return uploaded_image_details
    except Exception as e:
        # tenacity에 의해 재시도가 모두 실패했을 경우 최종적으로 이 catch 블록으로 오게 됩니다.
        print(f"[FINAL_ERROR] Shopify Files 등록 최종 실패 (모든 재시도 실패): {e}")
        return []


def main():
    ensure_directory_exists("uploaded_files")
    if not os.path.exists(UPLOADED_IMAGES_CSV) or os.stat(UPLOADED_IMAGES_CSV).st_size == 0:
        write_csv_header(UPLOADED_IMAGES_CSV)

    client = get_graphql_client()
    if not client:
        print("❌ GraphQL 클라이언트 초기화 실패. 스크립트를 종료합니다.")
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
        print("✅ 모든 이미지가 이미 성공적으로 업로드되었습니다. 새로 업로드할 이미지가 없습니다.")
        return

    print(f"✨ 총 {len(all_images_to_upload)}개 이미지 중 {len(already_uploaded)}개는 이미 업로드 완료.")
    print(f"🚀 **{len(images_to_process)}개 이미지**를 Shopify에 업로드할 예정입니다. (남은 이미지)")

    batch_size = 5
    processed_count = 0
    successful_this_run = 0

    pbar = tqdm(total=len(images_to_process), desc="이미지 업로드 진행", unit="개", dynamic_ncols=True)
    
    for i in range(0, len(images_to_process), batch_size):
        batch = images_to_process[i:i + batch_size]
        
        # tqdm.write를 사용하여 진행률 바를 방해하지 않고 메시지 출력
        pbar.write(f"\n--- 배치 #{pbar.n // batch_size + 1} ({i+1}-{min(i+batch_size, len(images_to_process))}번째 이미지) 처리 중 ---")
        
        try:
            uploaded_details = upload_images_to_shopify(client, batch)
            successful_this_run += len(uploaded_details)
            processed_count += len(batch)
            
            pbar.update(len(batch))
            
            time.sleep(1) # API 레이트 리밋 준수 (배치당 1초 대기)
        except Exception as e:
            pbar.write(f"⚠️ 배치 처리 중 일반 오류 발생: {e}. 다음 배치로 넘어갑니다.")
            for img_path in batch:
                 append_to_csv(UPLOADED_IMAGES_CSV, [img_path, '', '', 'FAILED_BATCH_ERROR', str(e)])
            processed_count += len(batch)
            pbar.update(len(batch))

    pbar.close()

    print("\n--- **업로드 요약** ---")
    print(f"✅ 이번 실행에서 총 **{successful_this_run}개** 이미지를 성공적으로 Shopify에 업로드했습니다.")
    print(f"🔄 총 **{processed_count}개** 이미지 처리를 시도했습니다.")
    print(f"📄 업로드 및 처리 로그는 **'{UPLOADED_IMAGES_CSV}'**에 저장되었습니다.")
    print("💡 실패한 이미지가 있다면 CSV 파일을 확인하고, 오류를 수정한 후 스크립트를 다시 실행하여 재시도할 수 있습니다.")


if __name__ == "__main__":
    main()