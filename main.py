import os
import sys
from dotenv import load_dotenv


def main():
    print("[INFO] 환경변수를 로드합니다...")
    load_dotenv()

    # 1. 환경 변수 존재 여부 선제적 검증
    missing_vars = []
    if not os.getenv("SOURCE_URL"):
        missing_vars.append("SOURCE_URL")
    if not os.getenv("TARGET_URL"):
        missing_vars.append("TARGET_URL")
    if not os.getenv("TARGET_API_TOKEN"):
        missing_vars.append("TARGET_API_TOKEN")

    if missing_vars:
        print(
            f"[CRITICAL] .env 파일에 다음 변수가 설정되지 않았습니다: {', '.join(missing_vars)}"
        )
        sys.exit(1)

    print("=" * 50)
    print("Wix to Shopify Data Migration Pipeline")
    print("=" * 50)

    print("\n[Phase 1] 데이터 추출 (Scraper) 시작...")
    try:
        from src.scraper import run as run_scraper

        run_scraper()
    except Exception as e:
        print(f"[CRITICAL] Scraper 실행 중 오류 발생: {e}")
        return

    print("\n[Phase 2 & 3] 텍스트 및 미디어 업데이트 시작...")
    try:
        from src.shopify_updater import run as run_updater

        run_updater()
    except Exception as e:
        print(f"[CRITICAL] Shopify Updater 실행 중 오류 발생: {e}")
        return

    print("\n[SUCCESS] 전체 마이그레이션 파이프라인이 완료되었습니다!")


if __name__ == "__main__":
    main()
