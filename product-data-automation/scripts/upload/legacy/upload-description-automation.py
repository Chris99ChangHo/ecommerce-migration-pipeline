import os
import csv
import re
import time
import shopify
from tqdm import tqdm

# --- 설정 ---
# ⚠️  아래 세 값을 본인의 Shopify 스토어 정보로 교체하세요.
SHOPIFY_API_KEY = "your_api_key_here"              # Shopify Admin API 키
SHOPIFY_PASSWORD = "shpat_your_token_here"         # Shopify Admin API 토큰
SHOPIFY_SHOP_NAME = "your-store"                   # 예: my-shop (.myshopify.com 제외)
API_VERSION = "2025-07"
SCRAPED_HTML_BASE_DIR = "상품설명사진" # HTML 파일이 저장된 기본 디렉토리

# --- HTML 파일 읽기 ---
def read_html_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"[ERROR] HTML 파일 읽기 실패: {file_path} - {e}")
        return None

# --- 상품 타이틀 정규화 ---
def normalize_title(title):
    # 파일명으로 사용할 수 있도록 특수문자 제거 및 공백 처리
    title = re.sub(r'[^\w\s\uAC00-\uD7A3]', '', title) # \uAC00-\uD7A3: 유니코드 한글 범위
    title = re.sub(r'\s+', ' ', title).strip() # 여러 공백을 하나로, 앞뒤 공백 제거
    return title.lower()

# --- 메인 로직 ---
def main():
    # Shopify API 초기화 (session 방식 유지)
    try:
        shop_url = f"https://{SHOPIFY_SHOP_NAME}.myshopify.com"
        session = shopify.Session(shop_url, API_VERSION, SHOPIFY_PASSWORD)
        shopify.ShopifyResource.activate_session(session)
        print(f"[INFO] Shopify API {API_VERSION} 버전으로 초기화 완료.")
    except Exception as e:
        print(f"[ERROR] Shopify API 초기화 실패: {e}.")
        print("=> Shopify API 키/액세스 토큰 또는 샵 URL을 확인하세요.")
        return

    # 1. 스크래핑된 HTML 파일 경로 및 폴더명 수집
    scraped_products = {}
    print(f"\n[INFO] '{SCRAPED_HTML_BASE_DIR}' 폴더에서 스크래핑된 HTML 파일을 탐색 중...")
    for product_folder_name in os.listdir(SCRAPED_HTML_BASE_DIR):
        folder_path = os.path.join(SCRAPED_HTML_BASE_DIR, product_folder_name)
        html_file_path = os.path.join(folder_path, "product_info.html")
        
        if os.path.isdir(folder_path) and os.path.exists(html_file_path):
            normalized_folder_title = normalize_title(product_folder_name)
            scraped_products[normalized_folder_title] = html_file_path
    
    if not scraped_products:
        print("[WARNING] '상품설명사진' 폴더에서 스크래핑된 상품 정보 HTML 파일이 발견되지 않았습니다. 작업을 종료합니다.")
        return
    print(f"[INFO] 총 {len(scraped_products)}개의 스크래핑된 상품 HTML 파일을 수집했습니다.")

    # 2. Shopify 상품 목록 가져오기 (since_id 기반 페이지네이션 및 재고 필터링)
    print("\n[INFO] Shopify 상품 목록을 페이지별로 가져오는 중 (재고 0 상품 제외 예정)...")
    all_shopify_products = []
    last_id = None
    page_limit = 250 # Shopify API 최대 리밋

    while True:
        try:
            params = {'limit': page_limit, 'order': 'id asc'} # ID 순으로 정렬하여 next_page_url 대신 since_id 사용
            if last_id:
                params['since_id'] = last_id
                
            products = shopify.Product.find(**params)
            
            if not products: # 더 이상 상품이 없으면 루프 종료
                break 

            all_shopify_products.extend(list(products))
            print(f"    -> 현재까지 총 {len(all_shopify_products)}개 상품 가져옴. (현재 페이지 {len(products)}개)")

            last_id = products[-1].id # 마지막 상품의 ID를 다음 요청의 since_id로 사용

            # Shopify API 레이트 리밋을 준수하기 위해 잠시 대기
            time.sleep(0.7) # 0.5초에서 1초 사이 권장

        except Exception as e:
            print(f"[ERROR] Shopify 상품 목록 가져오기 실패: {e}.")
            print("상품 목록 가져오기 중단.")
            break # 오류 발생 시 루프 중단

    print(f"[INFO] 최종적으로 총 {len(all_shopify_products)}개의 Shopify 상품을 가져왔습니다.")

    # 재고가 0이 아닌 상품만 필터링
    shopify_products_to_process = []
    skipped_zero_stock_count = 0
    for product in all_shopify_products:
        has_stock = False
        if product.variants: # 상품에 variants (옵션)이 있는지 확인
            for variant in product.variants:
                # inventory_quantity가 None이거나 (재고 추적 안 함) 0보다 크면 재고가 있는 것으로 간주
                if variant.inventory_quantity is None or variant.inventory_quantity > 0:
                    has_stock = True
                    break # 하나라도 재고가 있으면 해당 상품은 재고 있는 것으로 판단
        else:
            # variants가 없는 상품 (만약을 대비해 이런 경우는 재고가 있는 것으로 처리)
            has_stock = True

        if has_stock:
            shopify_products_to_process.append(product)
        else:
            skipped_zero_stock_count += 1
            # print(f"[INFO] 재고가 0인 상품 건너뛰기: '{product.title}' (ID: {product.id})") # 너무 많은 출력 방지

    print(f"[INFO] 재고가 0인 상품 {skipped_zero_stock_count}개 제외 후, 총 {len(shopify_products_to_process)}개의 상품을 처리할 예정입니다.")


    # 3. 상품 매칭 및 업데이트
    updated_count = 0
    # 기존 Description이 존재하는지 여부와 상관없이 무조건 덮어쓰므로 이 카운터는 필요 없습니다.
    # skipped_existing_desc_count = 0 
    # 스크래핑된 HTML 중 Shopify에 매칭되지 않은 수를 카운트
    not_matched_html_count = 0 
    
    # 매칭되어 처리된 스크래핑된 HTML 타이틀을 추적
    processed_scraped_titles = set() 

    print("\n[INFO] 상품 매칭 및 Description 업데이트 중...")
    
    # tqdm을 사용하여 진행률 표시
    for shopify_product in tqdm(shopify_products_to_process, desc="상품 업데이트 진행", unit="개"):
        shopify_title_normalized = normalize_title(shopify_product.title)
        
        if shopify_title_normalized in scraped_products:
            html_file_path = scraped_products[shopify_title_normalized]
            scraped_html_content = read_html_file(html_file_path)

            if scraped_html_content:
                try:
                    shopify_product.body_html = scraped_html_content
                    shopify_product.save() 
                    print(f"\n[SUCCESS] '{shopify_product.title}' 상품 Description이 성공적으로 업데이트되었습니다.")
                    updated_count += 1
                    processed_scraped_titles.add(shopify_title_normalized) 
                    time.sleep(0.5) # API 레이트 리밋 준수
                except shopify.ShopifyResource.InvalidRequestError as e:
                    print(f"\n[ERROR] '{shopify_product.title}' 상품 업데이트 실패 (잘못된 요청): {e}")
                except Exception as e:
                    print(f"\n[ERROR] '{shopify_product.title}' 상품 업데이트 실패: {e}")
            else:
                print(f"\n[WARNING] '{shopify_product.title}' (HTML 파일: {html_file_path})에 대한 HTML 콘텐츠를 읽을 수 없어 건너킵니다.")
        # else: # 이 부분은 매칭되지 않은 HTML 파일 수를 정확히 세기 위해 주석 처리하는 것이 좋습니다.
        #     pass
            
    # 스크래핑된 HTML 중에서 Shopify 상품과 매칭되지 않은 목록을 찾습니다.
    unmatched_scraped_products_summary = {title: path for title, path in scraped_products.items() if title not in processed_scraped_titles}
    if unmatched_scraped_products_summary:
        print("\n[SUMMARY] 스크래핑되었으나 Shopify에 매칭되지 않은 HTML 목록 (Shopify 상품에 없거나 이름 불일치):")
        for title, path in unmatched_scraped_products_summary.items():
            print(f"- '{title}' → '{path}'")
            not_matched_html_count += 1

    print(f"\n--- 최종 요약 ---")
    print(f"✅ Description이 성공적으로 업데이트된 상품 수: {updated_count}")
    # print(f"⚠️ Description이 이미 존재하여 건너뛴 상품 수: {skipped_existing_desc_count}") # 이 라인 제거
    print(f"⏩ 재고가 0이라서 처리에서 제외된 Shopify 상품 수: {skipped_zero_stock_count}")
    print(f"❌ 스크래핑되었으나 Shopify에 매칭되지 않은 HTML 파일 수: {not_matched_html_count}")
    print(f"📦 Shopify에서 최종적으로 가져온 총 상품 수: {len(all_shopify_products)}")
    print(f"📦 처리 대상으로 필터링된 Shopify 상품 수 (재고 0 제외): {len(shopify_products_to_process)}")
    print(f"📁 총 스크래핑된 HTML 파일 수: {len(scraped_products)}")
    print("\n--- 작업 완료 ---")

if __name__ == "__main__":
    main()