import json, requests, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # ✅ .env 파일에서 환경변수 로드

SUPABASE_URL = os.environ.get("SUPABASE_URL")
# ✅ FIX: anon key 대신 service_role(legacy) 또는 sb_secret_...(신규) key — RLS로 anon 직접 쓰기를 막았기 때문에 필요
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("❌ .env 파일에 SUPABASE_URL / SUPABASE_SERVICE_KEY가 없습니다. .env.example을 참고해서 .env를 만들어주세요.")

# ✅ FIX: Supabase가 신규 키 체계(sb_secret_...)를 도입 — 이건 JWT가 아니라서
# Authorization: Bearer 헤더에 넣으면 "Invalid JWT"로 거부됨. apikey 헤더에만 넣어야 함.
# 반면 예전 JWT 기반 service_role 키(eyJ...로 시작)는 계속 Authorization 헤더도 필요.
# 키 형식으로 자동 분기해서 둘 다 지원.
_IS_NEW_KEY = SUPABASE_SERVICE_KEY.startswith("sb_")
_BASE_HEADERS = {"apikey": SUPABASE_SERVICE_KEY}
if not _IS_NEW_KEY:
    _BASE_HEADERS["Authorization"] = f"Bearer {SUPABASE_SERVICE_KEY}"

# upsert(merge-duplicates)용 헤더 — product_id 충돌 시 payload에 없는 컬럼(click_count 등)은 건드리지 않고 유지됨
UPSERT_HEADERS = {
    **_BASE_HEADERS,
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=representation"
}
# store_links 삭제/삽입용 헤더 (단순 delete/insert라 merge-duplicates 불필요)
WRITE_HEADERS = {
    **_BASE_HEADERS,
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}
DELETE_HEADERS = dict(_BASE_HEADERS)

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
            # ✅ FIX: product_id 기준으로만 지우면, 예전 크롤링에서 이 URL이 다른(옛날) product_id
            # 밑에 들어가 있었을 경우 그 유령 행이 안 지워지고 남아서 같은 URL이 두 상품에 동시에
            # 존재하게 됨 — 다음 크롤링의 "기존 링크 고정 배정" 로직이 어느 쪽이 맞는지 못 정함.
            # URL 자체를 기준으로 지우면 항상 딱 1곳에만 존재하는 게 보장됨.
            requests.delete(
                f"{SUPABASE_URL}/rest/v1/store_links",
                headers=DELETE_HEADERS,
                params={"product_url": f"eq.{link['product_url']}"}
            )
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
