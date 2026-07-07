import json, requests, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # ✅ .env 파일에서 환경변수 로드

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("❌ .env 파일에 SUPABASE_URL / SUPABASE_KEY가 없습니다. .env.example을 참고해서 .env를 만들어주세요.")

# upsert(merge-duplicates)용 헤더 — product_id 충돌 시 payload에 없는 컬럼(click_count 등)은 건드리지 않고 유지됨
UPSERT_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=representation"
}
# store_links 삭제/삽입용 헤더 (단순 delete/insert라 merge-duplicates 불필요)
WRITE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}
DELETE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

DATA_FILE = Path("./label_data/products.json")

def upload():
    if not DATA_FILE.exists():
        print("❌ 데이터 파일이 없습니다. 크롤러를 먼저 실행하세요.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    print(f"🚀 Supabase 업로드 시작 (upsert 방식 — click_count 보존) — 총 {len(products)}개 상품")

    success, fail = 0, 0

    for p in products:
        product_id = p["product_id"]

        # 1. 상품(products) upsert — 전체 delete 대신 product_id 충돌 시 기존 행을 업데이트.
        #    click_count는 payload에 아예 포함하지 않으므로 기존 값 그대로 보존됨.
        prod_payload = {
            "brand_name": p["brand_name"],
            "title": p["title"],
            "clean_title": p["clean_title"],
            "image_url": p["image_url"],
            "product_id": product_id,
            "crawled_at": p["crawled_at"]
        }
        res_p = requests.post(
            f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_id",
            headers=UPSERT_HEADERS, json=prod_payload
        )
        if res_p.status_code not in (200, 201):
            print(f"  ⚠️ products upsert 실패: {product_id} ({res_p.status_code}) {res_p.text[:200]}")
            fail += 1
            continue

        # 2. store_links는 이 상품(product_id) 것만 지우고 재삽입 (테이블 전체 삭제 아님)
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/store_links?product_id=eq.{product_id}",
            headers=DELETE_HEADERS
        )
        for link in p["store_links"]:
            link_payload = {
                "product_id": product_id,
                "store_name": link["store_name"],
                "store_id": link["store_id"],
                "price": link["price"],
                "product_url": link["product_url"],
                "store_title": link.get("store_title", ""),
                "store_image": link.get("store_image", "")
            }
            requests.post(f"{SUPABASE_URL}/rest/v1/store_links", headers=WRITE_HEADERS, json=link_payload)

        success += 1

    print(f"\n🎉 업로드 완료! 성공 {success}개 / 실패 {fail}개 (click_count 보존됨)")

if __name__ == "__main__":
    upload()
