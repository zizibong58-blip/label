import json, requests, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # ✅ FIX: .env 파일에서 환경변수 로드

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

# ✅ NEW: 배치(묶음) 크기. products/store_links 삽입은 요청 바디에 실리므로 넉넉하게,
# in.() 필터를 쓰는 삭제는 쿼리스트링 길이 제한이 있을 수 있어 보수적으로 작게 잡음.
INSERT_BATCH = 300
DELETE_BATCH = 80

def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

def in_list(values):
    # PostgREST in.() 필터 문법 — 공백/한글 등 특수문자가 있는 값은 큰따옴표로 감싸야 안전함
    return "(" + ",".join(f'"{v}"' for v in values) + ")"

def upload():
    if not DATA_FILE.exists():
        print("❌ 데이터 파일이 없습니다. 크롤러를 먼저 실행하세요.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    print(f"🚀 Supabase 업로드 시작 (배치 방식 — click_count 보존) — 총 {len(products)}개 상품")

    # ── 1. products 배치 upsert (product_id 충돌 시 병합 — click_count는 payload에 없으므로 보존됨) ──
    p_success, p_fail = 0, 0
    for batch in chunked(products, INSERT_BATCH):
        payload = [{
            "brand_name": p["brand_name"], "title": p["title"], "clean_title": p["clean_title"],
            "image_url": p["image_url"], "product_id": p["product_id"], "crawled_at": p["crawled_at"]
        } for p in batch]
        res = requests.post(f"{SUPABASE_URL}/rest/v1/products?on_conflict=product_id", headers=UPSERT_HEADERS, json=payload)
        if res.status_code not in (200, 201):
            print(f"  ⚠️ products 배치 upsert 실패: {res.status_code} {res.text[:200]}")
            p_fail += len(batch)
        else:
            p_success += len(batch)
    print(f"  ✅ products upsert: 성공 {p_success} / 실패 {p_fail}")

    # ── 2. 이번 크롤에 포함된 product_id들의 기존 store_links 배치 삭제 (product_id 기준) ──
    all_pids = [p["product_id"] for p in products]
    for batch in chunked(all_pids, DELETE_BATCH):
        requests.delete(f"{SUPABASE_URL}/rest/v1/store_links", headers=DELETE_HEADERS,
                         params={"product_id": f"in.{in_list(batch)}"})

    # ── 3. 이번에 삽입할 모든 URL에 대해서도 배치 삭제 ──
    # ✅ FIX(기존 유지): product_id 기준으로만 지우면, 예전 크롤링에서 이 URL이 다른(옛날) product_id
    # 밑에 들어가 있었을 경우 그 유령 행이 안 지워지고 남아서 같은 URL이 두 상품에 동시에
    # 존재하게 됨 — URL 자체를 기준으로도 지우면 항상 딱 1곳에만 존재하는 게 보장됨.
    all_urls = [link["product_url"] for p in products for link in p["store_links"]]
    for batch in chunked(all_urls, DELETE_BATCH):
        requests.delete(f"{SUPABASE_URL}/rest/v1/store_links", headers=DELETE_HEADERS,
                         params={"product_url": f"in.{in_list(batch)}"})

    # ── 4. store_links 배치 삽입 ──
    all_links_payload = []
    for p in products:
        for link in p["store_links"]:
            all_links_payload.append({
                "product_id": p["product_id"], "store_name": link["store_name"], "store_id": link["store_id"],
                "price": link["price"], "product_url": link["product_url"],
                "store_title": link.get("store_title", ""), "store_image": link.get("store_image", "")
            })

    l_success, l_fail = 0, 0
    for batch in chunked(all_links_payload, INSERT_BATCH):
        res = requests.post(f"{SUPABASE_URL}/rest/v1/store_links", headers=WRITE_HEADERS, json=batch)
        if res.status_code not in (200, 201):
            print(f"  ⚠️ store_links 배치 삽입 실패: {res.status_code} {res.text[:200]}")
            l_fail += len(batch)
        else:
            l_success += len(batch)

    print(f"\n🎉 업로드 완료! products {p_success}/{len(products)} · store_links {l_success}/{len(all_links_payload)} (click_count 보존됨)")

    dedupe_existing_duplicates()


def fetch_all(table, select, extra_params=None):
    """페이지네이션 처리하며 테이블 전체 조회"""
    all_rows, page_size, offset = [], 1000, 0
    while True:
        params = {"select": select, "limit": page_size, "offset": offset}
        if extra_params:
            params.update(extra_params)
        res = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=DELETE_HEADERS, params=params)
        if res.status_code != 200:
            print(f"  ⚠️ {table} 조회 실패: {res.status_code} {res.text[:200]}")
            break
        batch = res.json()
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return all_rows


def dedupe_existing_duplicates():
    """
    업로드 끝날 때마다 자동 실행되는 사후 정리.
    같은 product_url(스토어 링크)이 서로 다른 product_id 밑에 동시에 존재하면
    (AI가 크롤링마다 상품명을 조금씩 다르게 뽑아서 예전에 갈라진 경우 등),
    그건 100% 같은 상품이 갈라진 것이므로 자동으로 하나로 합친다.
    브랜드+최저가 추측(어드민 병합 추천)과 달리 "URL 공유"는 반박 불가능한 사실이라 안전하게 자동 처리 가능.
    승자 선정: store_links 개수가 더 많은 쪽 -> 동률이면 crawled_at이 더 최근인 쪽.
    """
    print("\n🧹 URL 공유 기반 중복 상품군 자동 정리 시작...")
    store_links = fetch_all("store_links", "id,product_id,product_url")
    products = fetch_all("products", "id,product_id,brand_name,title,crawled_at")
    products_by_pid = {p["product_id"]: p for p in products}

    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    url_to_pids = {}
    for row in store_links:
        url_to_pids.setdefault(row["product_url"], set()).add(row["product_id"])
    for pids in url_to_pids.values():
        pids = list(pids)
        for i in range(1, len(pids)):
            union(pids[0], pids[i])

    clusters = {}
    for pid in parent:
        clusters.setdefault(find(pid), set()).add(pid)
    clusters = [c for c in clusters.values() if len(c) > 1]

    if not clusters:
        print("  ✅ 중복 없음")
        return

    links_by_pid = {}
    for row in store_links:
        links_by_pid.setdefault(row["product_id"], []).append(row)

    for cluster in clusters:
        cluster = list(cluster)
        cluster.sort(key=lambda pid: (len(links_by_pid.get(pid, [])), products_by_pid.get(pid, {}).get("crawled_at", "")), reverse=True)
        winner, losers = cluster[0], cluster[1:]
        print(f"  🔗 {winner}  <-  {', '.join(losers)}")

        for loser in losers:
            fav_rows = requests.get(
                f"{SUPABASE_URL}/rest/v1/favorites", headers=DELETE_HEADERS,
                params={"select": "id", "product_id": f"eq.{loser}"}
            ).json()
            for row in fav_rows:
                r = requests.patch(
                    f"{SUPABASE_URL}/rest/v1/favorites", headers=DELETE_HEADERS,
                    params={"id": f"eq.{row['id']}"}, json={"product_id": winner}
                )
                if r.status_code not in (200, 204):
                    requests.delete(f"{SUPABASE_URL}/rest/v1/favorites", headers=DELETE_HEADERS, params={"id": f"eq.{row['id']}"})
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/store_links", headers=DELETE_HEADERS,
                params={"product_id": f"eq.{loser}"}, json={"product_id": winner}
            )
            requests.delete(f"{SUPABASE_URL}/rest/v1/products", headers=DELETE_HEADERS, params={"product_id": f"eq.{loser}"})

    print(f"  🎉 {len(clusters)}개 중복 클러스터 자동 병합 완료")

if __name__ == "__main__":
    upload()
