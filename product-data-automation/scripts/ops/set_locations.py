import asyncio
from collections import defaultdict
import json
import aiohttp
import time
import random
from tqdm.asyncio import tqdm_asyncio

# ---------- 설정 ----------
# ⚠️  아래 두 값을 본인의 Shopify 스토어 정보로 교체하세요.
SHOP_URL = "your-store.myshopify.com"         # 예: my-shop.myshopify.com
ACCESS_TOKEN = "shpat_your_token_here"         # Shopify Admin API 액세스 토큰
API_VERSION = "2024-07" # API 버전

# 안정성 우선 설정
CONCURRENCY = 5  # 대폭 축소
MAX_RETRIES = 8  # 증가
DRY_RUN = False # True로 설정하면 실제 API 호출 없이 로그만 출력

# 배치 및 대기 시간 (안정성 우선)
BATCH_SLEEP = 2.0  # 배치간 더 긴 휴식
BATCH_SIZE = 20   # 작은 배치

# 위치별 대기시간 대폭 증가
PER_LOCATION_SLEEP = 0.8
SHORT_SLEEP = 0.8       

# 스로틀링 임계치 더 보수적으로
THROTTLE_USAGE_THRESHOLD = 0.6

# 체크포인트 파일
CHECKPOINT_FILE = "shopify_progress.json"
FAILED_ITEMS_FILE = "shopify_failed_items.json"

def make_headers():
    return {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

# ---------- 체크포인트 관리 ----------
def load_checkpoint():
    """완료된 variant_id들을 로드"""
    try:
        with open(CHECKPOINT_FILE, "r") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_checkpoint(completed_variants):
    """완료된 variant_id들을 저장"""
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(list(completed_variants), f)

def save_failed_items(failed_items):
    """실패한 항목들을 저장"""
    with open(FAILED_ITEMS_FILE, "w") as f:
        json.dump(failed_items, f, indent=2)

# ---------- 지터 유틸리티 ----------
def jittered_wait(base_wait, jitter_ratio=0.3):
    """지터가 적용된 대기 시간"""
    jitter = base_wait * jitter_ratio * (2 * random.random() - 1)
    return max(0.1, base_wait + jitter)

# ---------- locations 가져오기 (재시도 포함) ----------
async def fetch_locations_with_retry(session: aiohttp.ClientSession):
    url = f"https://{SHOP_URL}/admin/api/{API_VERSION}/locations.json"
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=make_headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("locations", [])
                elif resp.status == 500:
                    wait = jittered_wait(5.0 * attempt)
                    print(f"[WARN] locations 500 에러 (attempt {attempt}/{MAX_RETRIES}). {wait:.1f}초 후 재시도...")
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(wait)
                        continue
                    else:
                        print("[ERROR] locations 최종 실패 - 하드코딩 값 사용")
                        return [
                            {"id": 1, "name": "NSW 0-30 & NSW 30-60"},
                            {"id": 2, "name": "NSW 120+ & SA 0-30"},
                            {"id": 3, "name": "NSW 60-120 & VIC 0-30"},
                            {"id": 4, "name": "QLD 0-50 & VIC 30-100"},
                            {"id": 5, "name": "SA 30-100 & SA 100+"}
                        ]
                else:
                    resp.raise_for_status()
        except Exception as e:
            wait = jittered_wait(5.0 * attempt)
            print(f"[ERROR] locations 에러 (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(wait)
                continue
            else:
                raise
    return []

# ---------- products 가져오기 (재시도 포함) ----------
async def fetch_all_products_with_retry(session: aiohttp.ClientSession):
    products = []
    limit = 30  # 더 작게 (500 에러 방지)
    endpoint = f"https://{SHOP_URL}/admin/api/{API_VERSION}/products.json?limit={limit}"
    
    while endpoint:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(endpoint, headers=make_headers()) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        products.extend(payload.get("products", []))
                        break
                    elif resp.status == 500:
                        wait = jittered_wait(10.0 * attempt)
                        print(f"[WARN] products 500 에러 (attempt {attempt}/{MAX_RETRIES}). {wait:.1f}초 후 재시도...")
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(wait)
                            continue
                        else:
                            raise
                    else:
                        resp.raise_for_status()
            except Exception as e:
                wait = jittered_wait(10.0 * attempt)
                print(f"[ERROR] products 에러 (attempt {attempt}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(wait)
                    continue
                else:
                    raise
            break

        # 다음 페이지 처리
        link = resp.headers.get("Link", "")
        next_url = None
        if 'rel="next"' in link:
            parts = link.split(",")
            for p in parts:
                if 'rel="next"' in p:
                    url_part = p.split(";")[0].strip()
                    if url_part.startswith("<") and url_part.endswith(">"):
                        next_url = url_part[1:-1]
                        break
        endpoint = next_url
        
        # 페이지 간 대기 (서버 부담 완화)
        await asyncio.sleep(2.0)

    return products

# ---------- API 사용량 체크 ----------
def parse_call_limit_header(header_value: str):
    if not header_value:
        return None, None
    try:
        parts = header_value.split("/")
        used = int(parts[0])
        limit = int(parts[1])
        return used, limit
    except Exception:
        return None, None

async def check_and_throttle(resp):
    """API 사용량 체크 및 자동 스로틀링"""
    call_limit_header = resp.headers.get("X-Shopify-Shop-Api-Call-Limit", "")
    used, limit = parse_call_limit_header(call_limit_header)
    if used is not None and limit and (used / limit) >= THROTTLE_USAGE_THRESHOLD:
        sleep_for = jittered_wait(2.0)
        print(f"[THROTTLE] API 사용량 {used}/{limit} -> {sleep_for:.1f}초 대기")
        await asyncio.sleep(sleep_for)

# ---------- 안정적인 3단계 프로세스 (제품 락 + 409 처리) ----------
async def stable_set_locations(session: aiohttp.ClientSession, product_id: int, variant_id: int, inventory_item_id: int, locations: list, product_title: str, variant_title: str, semaphore: asyncio.Semaphore, product_locks: dict):
    """
    안정적인 3단계 프로세스
    - 제품 단위 락으로 409 에러 방지
    - 409/500 전용 재시도 처리
    - 지터 적용된 백오프
    """
    variant_url = f"https://{SHOP_URL}/admin/api/{API_VERSION}/products/{product_id}/variants/{variant_id}.json"

    if DRY_RUN:
        print(f"[DRY] {product_title} - {variant_title} -> 안정적 3단계 위치 설정")
        return {"status": "dry_run"}

    # 전역 세마포어 + 제품 단위 락 (409 방지 핵심!)
    async with semaphore:
        async with product_locks[product_id]:
            print(f"[PROCESS] 처리 중: {product_title} - {variant_title}")
            
            # 구성값
            step_max_retries = MAX_RETRIES
            base_backoff = 2.0
            max_backoff = 60.0

            # --- 1단계: 임시로 재고 추적 활성화 ---
            attempt = 0
            enabled = False
            enable_payload = {
                "variant": {
                    "id": variant_id,
                    "inventory_management": "shopify",
                    "inventory_policy": "deny"
                }
            }
            
            while attempt < step_max_retries and not enabled:
                attempt += 1
                try:
                    async with session.put(variant_url, json=enable_payload, headers=make_headers()) as resp:
                        text = await resp.text()
                        if resp.status == 200:
                            enabled = True
                            await check_and_throttle(resp)
                            break
                        elif resp.status == 409:
                            # 409: 제품이 다른 프로세스에 의해 수정 중
                            wait = jittered_wait(3.0 * attempt)
                            print(f"[WARN] 1단계 409 제품 락 충돌. {wait:.1f}초 후 재시도 ({product_title})")
                            await asyncio.sleep(wait)
                            continue
                        elif resp.status == 429:
                            retry_after = resp.headers.get("Retry-After")
                            wait = float(retry_after) if retry_after else jittered_wait(base_backoff * attempt)
                            print(f"[WARN] 1단계 429 rate limit. {wait:.1f}초 대기")
                            await asyncio.sleep(wait)
                            continue
                        elif 500 <= resp.status < 600:
                            wait = jittered_wait(base_backoff * attempt)
                            print(f"[WARN] 1단계 {resp.status} 서버 에러. {wait:.1f}초 후 재시도 (attempt {attempt})")
                            await asyncio.sleep(min(wait, max_backoff))
                            continue
                        else:
                            print(f"[ERROR] 1단계 실패 {resp.status}: {text} -> {product_title} - {variant_title}")
                            return {"status": "fail", "step": "enable_tracking", "error": text}
                except Exception as e:
                    wait = jittered_wait(base_backoff * attempt)
                    print(f"[EXC] 1단계 예외: {e}. {wait:.1f}초 후 재시도")
                    await asyncio.sleep(min(wait, max_backoff))

            if not enabled:
                print(f"[ERROR] 1단계 최종 실패: {product_title} - {variant_title}")
                return {"status": "fail", "step": "enable_tracking", "reason": "max_retries"}

            await asyncio.sleep(SHORT_SLEEP)

            # --- 2단계: 각 위치별 설정 ---
            location_success = 0
            location_failures = []
            
            for loc_idx, location in enumerate(locations):
                loc_attempt = 0
                loc_ok = False
                inventory_url = f"https://{SHOP_URL}/admin/api/{API_VERSION}/inventory_levels/set.json"
                inventory_payload = {
                    "location_id": location["id"],
                    "inventory_item_id": inventory_item_id,
                    "available": 0
                }
                
                while loc_attempt < step_max_retries and not loc_ok:
                    loc_attempt += 1
                    try:
                        async with session.post(inventory_url, json=inventory_payload, headers=make_headers()) as resp:
                            text = await resp.text()
                            if resp.status == 200:
                                loc_ok = True
                                location_success += 1
                                print(f"  ✓ 위치 {loc_idx+1}/{len(locations)} 완료: {location['name']}")
                                break
                            elif resp.status == 409:
                                wait = jittered_wait(2.0 * loc_attempt)
                                print(f"[WARN] 위치 409 충돌 {location['name']}. {wait:.1f}초 후 재시도")
                                await asyncio.sleep(wait)
                                continue
                            elif resp.status == 429:
                                retry_after = resp.headers.get("Retry-After")
                                wait = float(retry_after) if retry_after else jittered_wait(1.0 * loc_attempt)
                                print(f"[WARN] 위치 429 rate limit {location['name']}. {wait:.1f}초 대기")
                                await asyncio.sleep(wait)
                                continue
                            elif 500 <= resp.status < 600:
                                wait = jittered_wait(1.0 * loc_attempt)
                                print(f"[WARN] 위치 {resp.status} 서버 에러 {location['name']}. {wait:.1f}초 후 재시도")
                                await asyncio.sleep(min(wait, max_backoff))
                                continue
                            else:
                                print(f"[WARN] 위치 설정 실패 {resp.status}: {location['name']} - {text}")
                                break
                    except Exception as e:
                        wait = jittered_wait(1.0 * loc_attempt)
                        print(f"[EXC] 위치 설정 예외 {location['name']}: {e}")
                        await asyncio.sleep(min(wait, max_backoff))

                if not loc_ok:
                    location_failures.append({
                        "location": location["name"],
                        "attempts": loc_attempt,
                        "location_id": location["id"]
                    })
                    print(f"  ✗ 위치 실패: {location['name']}")
                
                # 위치별 충분한 간격 (핵심!)
                await asyncio.sleep(PER_LOCATION_SLEEP)

            await asyncio.sleep(SHORT_SLEEP)

            # --- 3단계: 재고 추적 비활성화 ---
            attempt = 0
            disabled = False
            disable_payload = {
                "variant": {
                    "id": variant_id,
                    "inventory_management": None,
                    "inventory_policy": "continue"
                }
            }
            
            while attempt < step_max_retries and not disabled:
                attempt += 1
                try:
                    async with session.put(variant_url, json=disable_payload, headers=make_headers()) as resp:
                        text = await resp.text()
                        if resp.status == 200:
                            disabled = True
                            await check_and_throttle(resp)
                            break
                        elif resp.status == 409:
                            wait = jittered_wait(3.0 * attempt)
                            print(f"[WARN] 3단계 409 제품 락. {wait:.1f}초 후 재시도")
                            await asyncio.sleep(wait)
                            continue
                        elif resp.status == 429:
                            retry_after = resp.headers.get("Retry-After")
                            wait = float(retry_after) if retry_after else jittered_wait(base_backoff * attempt)
                            print(f"[WARN] 3단계 429 rate limit. {wait:.1f}초 대기")
                            await asyncio.sleep(wait)
                            continue
                        elif 500 <= resp.status < 600:
                            wait = jittered_wait(base_backoff * attempt)
                            print(f"[WARN] 3단계 {resp.status} 서버 에러. {wait:.1f}초 후 재시도")
                            await asyncio.sleep(min(wait, max_backoff))
                            continue
                        else:
                            print(f"[ERROR] 3단계 실패 {resp.status}: {text}")
                            break
                except Exception as e:
                    wait = jittered_wait(base_backoff * attempt)
                    print(f"[EXC] 3단계 예외: {e}")
                    await asyncio.sleep(min(wait, max_backoff))

            status = "ok" if disabled else "partial_success"
            result = {
                "status": status,
                "locations_set": location_success,
                "total_locations": len(locations),
                "failures": location_failures,
                "variant_id": variant_id
            }
            
            if status == "ok":
                print(f"[SUCCESS] 완료: {product_title} - {variant_title} ({location_success}/{len(locations)} 위치)")
            else:
                print(f"[PARTIAL] 부분 완료: {product_title} - {variant_title} ({location_success}/{len(locations)} 위치)")
            
            return result

# ---------- 메인 로직 ----------
async def main():
    timeout = aiohttp.ClientTimeout(total=None)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY*2)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    # 제품 단위 락 생성 (409 방지 핵심!)
    product_locks = defaultdict(asyncio.Lock)
    
    # 체크포인트 로드
    completed_variants = load_checkpoint()
    print(f"[INFO] 체크포인트에서 {len(completed_variants)}개 완료된 variant 로드")

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # locations 가져오기
        print("[INFO] locations 가져오는 중...")
        locations = await fetch_locations_with_retry(session)
        print(f"[INFO] {len(locations)}개 위치 발견: {[loc['name'] for loc in locations]}")
        
        print("[INFO] products 가져오는 중...")
        products = await fetch_all_products_with_retry(session)
        print(f"[INFO] {len(products)}개 제품 로드완료")

        # 작업 리스트 생성 (완료된 것 제외)
        tasks = []
        variant_count = 0
        skipped_count = 0
        
        for product in products:
            product_id = product.get("id")
            product_title = product.get("title", "<untitled>")
            for variant in product.get("variants", []):
                variant_count += 1
                variant_id = variant.get("id")
                inventory_item_id = variant.get("inventory_item_id")
                variant_title = variant.get("title", "")
                
                if variant_id is None or inventory_item_id is None:
                    continue
                
                # 이미 완료된 variant는 건너뛰기
                if variant_id in completed_variants:
                    skipped_count += 1
                    continue
                    
                tasks.append((
                    product_id, variant_id, inventory_item_id,
                    product_title, variant_title
                ))

        total_tasks = len(tasks)
        print(f"[INFO] 총 {variant_count}개 변형 중 {skipped_count}개 완료됨, {total_tasks}개 처리 예정")
        
        if total_tasks == 0:
            print("[INFO] 모든 작업이 완료되었습니다!")
            return

        # 배치 실행
        all_results = []
        failed_items = []
        new_completed = set(completed_variants)
        
        for i in range(0, total_tasks, BATCH_SIZE):
            batch = tasks[i:i+BATCH_SIZE]
            batch_num = i//BATCH_SIZE + 1
            total_batches = (total_tasks-1)//BATCH_SIZE + 1
            
            print(f"\n[INFO] 배치 {batch_num}/{total_batches} 실행 중 ({len(batch)}개 항목)...")
            
            coro_list = [
                stable_set_locations(
                    session, prod_id, var_id, inv_item_id, locations, 
                    prod_title, var_title, semaphore, product_locks
                )
                for (prod_id, var_id, inv_item_id, prod_title, var_title) in batch
            ]
            
            batch_results = await tqdm_asyncio.gather(*coro_list, desc=f"배치 {batch_num}")
            all_results.extend(batch_results)
            
            # 배치별 결과 처리
            batch_success = 0
            for idx, result in enumerate(batch_results):
                prod_id, var_id, inv_item_id, prod_title, var_title = batch[idx]
                
                if result.get("status") in ["ok", "dry_run"]:
                    batch_success += 1
                    new_completed.add(var_id)
                elif result.get("status") == "partial_success":
                    batch_success += 1
                    new_completed.add(var_id)  # 부분 성공도 완료로 간주
                else:
                    # 실패한 항목 기록
                    failed_items.append({
                        "product_id": prod_id,
                        "variant_id": var_id,
                        "inventory_item_id": inv_item_id,
                        "product_title": prod_title,
                        "variant_title": var_title,
                        "error": result
                    })
            
            print(f"[배치 {batch_num}] 성공: {batch_success}/{len(batch)}")
            
            # 진행상황 저장 (체크포인트)
            save_checkpoint(new_completed)
            
            # 배치 간 휴식 (서버 부담 완화)
            if batch_num < total_batches:
                print(f"[INFO] {BATCH_SLEEP:.1f}초 휴식 중...")
                await asyncio.sleep(BATCH_SLEEP)

        # 최종 결과
        total_ok = sum(1 for r in all_results if r.get("status") == "ok")
        total_partial = sum(1 for r in all_results if r.get("status") == "partial_success") 
        total_dry = sum(1 for r in all_results if r.get("status") == "dry_run")
        total_fail = total_tasks - total_ok - total_partial - total_dry
        total_locations_set = sum(r.get("locations_set", 0) for r in all_results if r.get("locations_set"))
        
        print(f"\n{'='*60}")
        print(f"[DONE] 모든 작업 완료!")
        print(f"✓ 완전 성공: {total_ok:,}개")
        print(f"⚠ 부분 성공: {total_partial:,}개")
        print(f"🔄 DRY RUN: {total_dry:,}개")
        print(f"✗ 실패: {total_fail:,}개")
        print(f"📊 총 {total_locations_set:,}개 위치 체크박스 활성화")
        print(f"📍 {len(locations)}개 위치에서 판매 가능")
        print(f"{'='*60}")
        
        # 실패한 항목들 저장
        if failed_items:
            save_failed_items(failed_items)
            print(f"[INFO] {len(failed_items)}개 실패 항목을 {FAILED_ITEMS_FILE}에 저장했습니다.")
            print(f"[INFO] 나중에 실패한 항목만 재시도하려면 해당 파일을 확인하세요.")

if __name__ == "__main__":
    print(f"{'='*60}")
    print(f"Shopify 위치 설정 스크립트 - 안정화 버전")
    print(f"{'='*60}")
    
    start = time.time()
    asyncio.run(main())
    elapsed = time.time() - start
    
    print(f"\n총 소요시간: {elapsed/60:.1f}분 ({elapsed:.1f}초)")
    print(f"체크포인트 파일: {CHECKPOINT_FILE}")
    print(f"실패 항목 파일: {FAILED_ITEMS_FILE}")

# 수동 테스트 시 스토어 URL과 API 토큰은 위에 SHOP_URL / ACCESS_TOKEN 설정을 직접 수정하세요.