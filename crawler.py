import os, re, json, time, requests
from pathlib import Path
from datetime import datetime
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()  # ✅ FIX: .env 파일에서 환경변수 로드

# ─── 1. API 키 및 DB 설정 (하드코딩 제거, 환경변수에서 로드) ─────────────
NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY")
SUPABASE_URL        = os.environ.get("SUPABASE_URL")
# ✅ FIX: anon(publishable) key 대신 service_role key 사용.
# RLS로 products/store_links 등에 대한 anon 직접 쓰기를 막았기 때문에,
# 크롤러(신뢰된 백엔드 프로세스)는 RLS를 우회하는 service_role key로 써야 정상 동작함.
# service_role key는 절대 프론트엔드/브라우저에 노출하면 안 됨 — 이 .env는 서버(크롤러)에서만 사용.
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not all([NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY]):
    raise RuntimeError("❌ .env 파일에 필요한 키가 없습니다. .env.example을 참고해서 .env를 만들어주세요.")

# ✅ NEW: GitHub Actions 등 클라우드 러너용 스위치.
# upload_to_supabase.py는 image_url만 Supabase에 올리고 local_image는 쓰지 않으므로,
# 로컬 PC가 아닌 곳에서 돌릴 땐 썸네일 다운로드를 건너뛰어 시간/대역폭을 아낄 수 있음.
SKIP_LOCAL_IMAGE_DOWNLOAD = os.environ.get("SKIP_LOCAL_IMAGE_DOWNLOAD", "false").lower() == "true"

genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json", "temperature": 0})
# ✅ FIX: Supabase가 신규 키 체계(sb_secret_...)를 도입 — 이건 JWT가 아니라서
# Authorization: Bearer 헤더에 넣으면 거부됨. apikey 헤더에만 넣어야 함.
# 예전 JWT 기반 service_role 키(eyJ...)는 계속 Authorization 헤더도 필요해서 키 형식으로 자동 분기.
HEADERS = {"apikey": SUPABASE_SERVICE_KEY}
if not SUPABASE_SERVICE_KEY.startswith("sb_"):
    HEADERS["Authorization"] = f"Bearer {SUPABASE_SERVICE_KEY}"

BRANDS_FILE = "brands.txt"
STORES_FILE = "stores.txt"
OUTPUT_DIR  = Path("./label_data")
IMAGE_DIR   = OUTPUT_DIR / "images"
OUTPUT_FILE = OUTPUT_DIR / "products.json"

OUTPUT_DIR.mkdir(exist_ok=True)
IMAGE_DIR.mkdir(exist_ok=True)

IMAGE_PRIORITY = {
    "samtandbyme": 1, "grove": 2, "muniate": 3, "leidu": 4, "staynoah": 5, 
    "bernadette1": 6, "nunedestore": 7, "enough_": 8, "alico": 9, "lanic-u": 10, 
    "beaucla": 11, "pinkholicya": 12, "neulpumdaa": 13, "mou9": 14, "lasibelle": 15, 
    "carinowm": 16, "ttoyuni": 17, "occupe": 18, "nowadays_": 19, "themellow": 20, 
    "bei-an": 21, "sundaymorningmaket": 22, "sweet_i": 23, "lunadeel": 24, "butterpezl": 25
}

STORE_MAPPING = {
    "ttoyuni": ["ttoyuni", "또유니", "떠유니"], "occupe": ["오큐페", "occupe"], "leidu": ["레이두", "leidu"], 
    "enough_": ["이너프", "enough", "enough_"], "nowadays_": ["나우어데이즈", "나우 어 데이즈", "nowadays", "nowadays_"],
    "staynoah": ["스테이노아", "staynoah"], "themellow": ["더멜로우", "themellow"],
    "bernadette1": ["버나뎃", "bernadette", "bernadette1"], "muniate": ["무니에트", "muniate"],
    "bei-an": ["바이안", "bei-an"], "sundaymorningmaket": ["선데이모닝마켓", "sundaymorningmaket", "선데이모닝"],
    "sweet_i": ["스윗아이", "스윗 아이", "sweet_i"], "lunadeel": ["루나드엘", "lunadeel"], "butterpezl": ["버터프레즐", "butterpezl"], 
    "nunedestore": ["누네드", "nunedestore", "누네드스토어"], "samtandbyme": ["그로브", "grove", "grove_store", "샘트앤바이미", "samtandbyme"], 
    "alico": ["알리코", "alico"], "lanic-u": ["라니쿠", "lanic-u"], "beaucla": ["보클레", "beaucla"], "pinkholicya": ["핑크홀릭", "pinkholicya"], 
    "neulpumdaa": ["늘품다", "neulpumdaa"], "mou9": ["모구", "mou9"], "lasibelle": ["라시벨", "lasibelle"], "carinowm": ["카리노", "carinowm"]
}
# ✅ NEW: 셀러가 상품명 끝에 대괄호 없이 그냥 붙이는 자체 홍보 태그(예: "...아이보리 떠유니")를
# AI에게 넘기기 전에 원천 제거하기 위한 패턴. 안 지우면 AI가 이걸 상품 고유 식별어로
# 착각해서 "떠유니 떠유니"처럼 상품명 자체가 스토어명으로 오염되는 사고가 남.
_ALL_STORE_ALIASES = sorted({alias for aliases in STORE_MAPPING.values() for alias in aliases if len(alias) >= 2}, key=len, reverse=True)
_STORE_ALIAS_PATTERN = re.compile("|".join(re.escape(a) for a in _ALL_STORE_ALIASES), re.IGNORECASE)
# ✅ FIX: CATEGORIES(카테고리 3개 제한) 제거됨 — 네이버 자체 분류에 없는 카테고리 상품이
# 통째로 누락되는 원인이었음. 이제 1단계 검색은 카테고리 제한 없이 페이지만 늘려서 수행함.

def load_list(filename):
    if not os.path.exists(filename): return []
    with open(filename, "r", encoding="utf-8") as f: return [l.strip() for l in f if l.strip()]

def get_split_rules():
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/split_rules?select=product_url,correct_title", headers=HEADERS)
        return {item['product_url']: item['correct_title'] for item in res.json()} if res.status_code == 200 else {}
    except: return {}

def get_rename_rules():
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/rename_rules?select=original_title,correct_title", headers=HEADERS)
        return {item['original_title']: item['correct_title'] for item in res.json()} if res.status_code == 200 else {}
    except: return {}

# ✅ FIX: 어드민 패널의 '합치기(🔗)' 기능이 실제로 반영되도록 merge_rules를 읽어오는 함수 추가
def get_merge_rules():
    """{원본 링크 URL: 합쳐질 대상 상품의 '브랜드|상품명'} 형태로 반환"""
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/merge_rules?select=product_url,target_clean_title", headers=HEADERS)
        return {item['product_url']: item['target_clean_title'] for item in res.json()} if res.status_code == 200 else {}
    except: return {}

# ✅ FIX: 어드민 패널의 '삭제(🗑️)' 기능이 실제로 반영되도록 blacklist를 읽어오는 함수 추가
def get_blacklist():
    """다시 크롤링에 포함하지 않을 URL 집합"""
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/blacklist?select=product_url", headers=HEADERS)
        return {item['product_url'] for item in res.json()} if res.status_code == 200 else set()
    except: return set()

# ✅ NEW: 같은 리스팅(링크)이 크롤링마다 AI 정제 결과가 달라져서 서로 다른 상품군으로
# 갈라지는 것을 막기 위해, 이미 예전 크롤링에서 특정 product_id에 배정된 적 있는
# 링크 -> product_id 매핑을 가져온다. 이번 AI 결과가 다르게 나와도 이 매핑이 있으면
# 무조건 기존 상품에 그대로 고정 배정한다 (스토어 상품번호=링크가 곧 정답이므로).
def get_existing_links():
    """
    링크(product_url) -> DB에 저장된 마지막 store_link 정보 전체(product_id 포함).
    ✅ FIX: 예전엔 product_id만 가져와서, 이번 크롤링 검색에서 재발견되지 않은 링크는
    (특히 admin이 병합/분리로 확정해놓은 링크도) 그냥 통째로 유실됐음.
    store_name/store_id/price/store_title/store_image까지 같이 가져와서,
    검색으로 못 찾은 링크도 DB에 남아있던 값 그대로 복원할 수 있게 한다.
    """
    try:
        all_rows, page_size, offset = [], 1000, 0
        while True:
            res = requests.get(
                f"{SUPABASE_URL}/rest/v1/store_links?select=product_url,product_id,store_name,store_id,price,store_title,store_image&limit={page_size}&offset={offset}",
                headers=HEADERS
            )
            if res.status_code != 200: break
            batch = res.json()
            all_rows.extend(batch)
            if len(batch) < page_size: break
            offset += page_size
        return {row['product_url']: row for row in all_rows}
    except: return {}

def is_target_store(mall_name, link, store_ids):
    mall_name_clean = mall_name.replace(" ", "").lower()
    if "enoughroom" in mall_name_clean: return None
    extended_store_ids = set(store_ids + ["grove", "samtandbyme"])

    for sid in extended_store_ids:
        if f"smartstore.naver.com/{sid}/" in link.lower() or f"smartstore.naver.com/{sid}?" in link.lower(): return sid
        for alias in STORE_MAPPING.get(sid, [sid]):
            alias_clean = alias.replace(" ", "").lower()
            if alias_clean == mall_name_clean: return sid
            if any("\uac00" <= c <= "\ud7a3" for c in alias_clean) and alias_clean in mall_name_clean: return sid
    return None

def search_naver(keyword, cat=None, display=100, sort="date", start=1):
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    params = {"query": keyword, "display": display, "sort": sort, "start": start}
    if cat: params["category"] = cat
    try: return requests.get("https://openapi.naver.com/v1/search/shop.json", headers=headers, params=params).json().get("items", [])
    except: return []

def download_image(url, product_id):
    try:
        folder = IMAGE_DIR / str(product_id).replace("|", "_"); folder.mkdir(exist_ok=True)
        path = folder / "main.jpg"
        if path.exists(): return str(path)
        path.write_bytes(requests.get(url, timeout=10).content)
        return str(path)
    except: return None

def get_reviews(product_id, count=5):
    # ✅ FIX: product_id가 없으면(=진짜 네이버 상품ID를 못 구했으면) API를 호출하지 않고 바로 빈 리스트 반환
    if not product_id:
        return []
    try:
        data = requests.get(f"https://api.review.naver.com/v0.1/reviews/headlines?productId={product_id}&page=1&pageSize={count}", timeout=5).json()
        return [{"score": r.get("score"), "content": r.get("content", "")[:100], "date": r.get("createDate", "")} for r in data.get("reviews", [])]
    except: return []

def clean_titles_with_ai(titles_by_brand):
    total_titles = sum(len(v) for v in titles_by_brand.values())
    print(f"\n🤖 제미나이 AI가 {len(titles_by_brand)}개 브랜드, 총 {total_titles}개의 상품명을 분석합니다...")
    cleaned_dict = {}
    all_store_aliases = set()
    for alias_list in STORE_MAPPING.values():
        for alias in alias_list:
            all_store_aliases.add(alias.replace(" ", "").lower())
    batch_size = 40
    # ✅ FIX: 브랜드를 섞지 않고 브랜드별로 묶어서 배치 처리.
    # 같은 브랜드 상품명을 여러 개 같이 보여줘야, 여러 상품에 반복 등장하는
    # 컬렉션/라인명(필러)과 그 상품에만 있는 진짜 고유 식별어를 AI가 구분할 수 있음.
    for brand, titles in titles_by_brand.items():
        unique_titles = list(set(titles))
        for i in range(0, len(unique_titles), batch_size):
            batch = unique_titles[i:i+batch_size]
            prompt = f"""너는 동대문 도매 쇼핑몰 상품명을 정리하는 패션 MD야.
아래는 전부 같은 도매택 "{brand}" 상품들의 원본 상품명이야.

각 원본 상품명에서 "상품명키워드(1단어)" + "카테고리키워드(1단어)"만 남기고 나머지는 전부 삭제해.

[상품명키워드 판별 기준 - 가장 중요]
여러 상품명에 걸쳐 공통으로 반복되는 단어(시즌 컬렉션명/라인명처럼 홍보용으로 계속 붙는 단어)는
진짜 상품명이 아니라 필러야. 삭제해.
그 상품에서만 고유하게 나타나는 단어가 진짜 상품명키워드야. 그것만 남겨.

[제거 대상 - 전부 삭제, 예외 없음]
- 도매택/브랜드명 (한글·영문 표기, 중복 표기 모두)
- 브랜드 바로 뒤에 붙는 의미없는 코드/이니셜/영문 약어 (예: pyt, ss, fw 같은 2~3글자 토큰)
- 여러 상품에 반복 등장하는 컬렉션/라인명 (위 판별 기준 참고)
- 소재어: 린넨, 코튼, 텐셀, 울, 쉬폰, 새틴, 골지 등
- 시즌/계절어: 봄, 여름, 가을, 겨울, 썸머, 당일, 신상, 재진행 등
- 핏/실루엣 디테일어: 오버핏, 루즈핏, 크롭, 슬림, 슬리브리스, 반팔, 긴팔, 오프숄더, 버튼, 스트라이프 등
- 옵션/수량 표기: (3col), nt 같은 괄호·약어 표기
- 소매상 이름, 클릭수 표기 (예: "나우 어 데이즈클릭 0")
- 홍보 문구: 무료배송, 당일출고, SALE, 하객 등
- 대괄호[ ] 안 내용 전체, HTML 태그

[카테고리키워드 판별 기준]
카테고리는 옷의 "형태/종류"를 나타내는 단어여야 해. 아래 목록 중 하나만 골라:
나시, 블라우스, 셔츠, 니트, 가디건, 원피스, 팬츠, 스커트, 자켓, 티셔츠

형태 단어(나시/블라우스/원피스 등)와 소재 단어(니트/린넨 등)가 같이 있으면
형태 단어를 카테고리로 써. (예: "나시 니트" → 카테고리는 "나시", 니트는 소재라서 삭제)
형태 단어 없이 소재 단어만 있으면 그 소재 단어를 카테고리로 써.

동의어는 반드시 목록에 있는 단어로 통일해. 같은 옷을 다른 단어로 부른 것뿐이면 절대 원본 단어를 그대로 쓰지 마:
- 탑, 슬리브리스탑, 나시탑, 민소매, 나시티, 나시 티셔츠 → 전부 "나시"로 통일
- 니트탑, 니트웨어 → "니트"로 통일

[출력 형식]
반드시 "상품명키워드 카테고리키워드" 순서로, 공백 하나로 구분된 두 단어.
단, 브랜드명과 홍보문구 등을 다 지우고 나서 정말로 카테고리 단어 외엔 아무 식별 단어도 안 남으면
(예: 원본이 "브랜드 + 카테고리"뿐이고 별도 스타일명이 없는 경우) 카테고리 단어를 두 번 쓰지 말고 한 단어만 출력해.

[예시]
원본: 프리티영띵 듀이 린넨 나시 니트 슬리브리스 여름 버튼 골지 뷔스티에 nt
(같은 브랜드의 다른 상품명에도 "프리티영띵"이 반복 등장한다면 → 브랜드명이니까 삭제, "듀이"만 이 상품 고유 식별어)
정답: 듀이 나시

원본: riette 리에뜨 로이 니트 썸머 린넨 반팔 오프숄더 크롭 nt (3col) 나우 어 데이즈클릭 0
정답: 로이 니트

원본: 파운더스 파르마 슬리브리스 탑
(다른 상품명에선 같은 상품이 "파르마 나시"로도 불림 → "탑"은 "나시"의 동의어일 뿐, 다른 카테고리가 아님)
정답: 파르마 나시

원본: 파운더스 이네스 유넥 슬리브리스 코튼 나시 티셔츠
(유넥/슬리브리스/코튼은 디테일·소재라 삭제, "나시"와 "티셔츠"가 동시에 있으면 더 구체적인 "나시"를 카테고리로)
정답: 이네스 나시

원본: 누즈 블라우스
(브랜드 지우고 나면 "블라우스"라는 카테고리 단어 외엔 진짜 아무것도 안 남음 → 두 번 쓰지 말고 한 단어만)
정답: 블라우스

입력: {json.dumps(batch, ensure_ascii=False)}
출력: [ {{"original": "원본", "clean_title": "정답"}} ]"""
            success = False
            for attempt in range(3):
                try:
                    res = ai_model.generate_content(prompt)
                    parsed = json.loads(res.text)
                    batch_set = set(batch)
                    for p in parsed:
                        original = p.get("original", "")
                        clean_title = p.get("clean_title", "").replace("[", "").replace("]", "").strip()
                        if original not in batch_set:
                            continue
                        if not clean_title or clean_title.isdigit():
                            clean_title = original
                        first_word = clean_title.split()[0].replace(" ", "").lower() if clean_title.split() else ""
                        if first_word in all_store_aliases:
                            clean_title = original
                        cleaned_dict[original] = clean_title
                    success = True
                    break
                except Exception as e:
                    if attempt < 2:
                        print(f"  ⚠️ [{brand}] 배치 실패({e}), 재시도 {attempt+1}/2...")
                        time.sleep(5 * (attempt + 1))
            if not success:
                print(f"  ❌ [{brand}] 3회 재시도 실패 — 이 배치는 원본 제목이 그대로 저장됩니다.")
                for t in batch: cleaned_dict[t] = t
            print(f"  [{brand}] {min(i+batch_size, len(unique_titles))}/{len(unique_titles)} 분석 완료...")
            time.sleep(3)
    return cleaned_dict

def run():
    brands = load_list(BRANDS_FILE)
    store_ids = load_list(STORES_FILE)
    if not brands or not store_ids: return
    
    split_rules = get_split_rules()
    rename_rules = get_rename_rules()
    merge_rules = get_merge_rules()   # ✅ FIX
    blacklist = get_blacklist()       # ✅ FIX
    existing_links = get_existing_links()  # ✅ NEW: 링크별 기존 배정 상품+스토어 정보 고정/복원용
    print(f"🚀 LABEL V2 가동 (도매택 {len(brands)}개 / 소매상 {len(store_ids)}개 / 휴먼분리 {len(split_rules)}건 / 병합 {len(merge_rules)}건 / 블랙리스트 {len(blacklist)}건 적용)\n")
    
    brand_lower_list = [b.replace(" ", "").lower() for b in brands]
    titles_by_brand = {}
    raw_title_sources = {}  # ✅ NEW: raw_title(trimmed) -> 이 제목으로 발견된 모든 스토어의 실제 아이템 정보 리스트

    extended_store_ids = list(set(store_ids + ["samtandbyme"]))

    # ✅ FIX: 예전엔 CATEGORIES(50000167/50000190/50000174) 3개 코드로만 검색을 좁혔는데,
    # 네이버 자체 분류(예: "티셔츠")가 이 3개 안에 없으면 그 카테고리의 상품은
    # 해당 스토어가 아무리 많이 팔아도 1단계에서 원천적으로 아예 검색조차 안 됐음
    # (실제로 미엘 베이비나시 = "패션의류>여성의류>티셔츠"라서 통째로 누락된 게 확인됨).
    # 카테고리로 좁히는 대신, 페이지(start)를 늘려서 카테고리 무관하게 최근 300개를 모두 훑는다.
    for sid in extended_store_ids:
        search_kw = STORE_MAPPING.get(sid, [sid])[0]
        for start in (1, 101, 201):
            items = search_naver(search_kw, cat=None, display=100, sort="date", start=start)
            for item in items:
                mall_name, link = item.get("mallName", ""), item.get("link", "")
                if not is_target_store(mall_name, link, store_ids) == sid: continue
                
                clean_link = link.split("?")[0].strip()
                if clean_link in blacklist: continue  # ✅ FIX: 삭제된 링크는 재수집하지 않음
                if clean_link in split_rules or clean_link in merge_rules: continue  # ✅ FIX: 병합 대상도 원본 제목 재정제 스킵

                raw_title = re.sub(r"<[^>]+>", "", item.get("title", ""))
                # ✅ FIX: 소매상이 앞에 붙이는 자체 태그는 항상 대괄호 안에 있음
                # (예: "[MUNIATE/무니에트]") — 브랜드 매칭 전에 통째로 제거해서 애초에 후보에서 배제.
                raw_title = re.sub(r"\[[^\]]*\]", "", raw_title).strip()
                # ✅ NEW: 대괄호 없이 그냥 텍스트 끝에 붙는 스토어 자체 홍보 태그도 제거
                # (예: "...프릴 아이보리 떠유니" 처럼 대괄호 없이 스토어명이 그냥 붙는 경우).
                # 이걸 안 지우면 AI가 스토어명을 상품 고유 식별어로 착각해서
                # "떠유니 떠유니"처럼 상품명 자체가 스토어명으로 오염됨.
                raw_title = _STORE_ALIAS_PATTERN.sub("", raw_title).strip()
                raw_title_lower = raw_title.replace(" ", "").lower()
                # ✅ FIX: 대괄호 태그를 이미 제거했으므로, 남은 텍스트에서 여러 브랜드가 동시에
                # 매칭되더라도 가장 먼저(앞에) 나오는 브랜드가 진짜 도매택이다.
                # ("코드/영문표기 + 진짜브랜드 + 상품명 + 카테고리" 순서가 일관된 패턴이라
                # 뒤에 나오는 매칭은 다른 브랜드명과 우연히 겹치는 상품명/디테일어인 경우가 많음)
                matched = [(raw_title_lower.find(b_lower), b) for b, b_lower in zip(brands, brand_lower_list) if b_lower in raw_title_lower]
                if matched:
                    brand_pos, b = min(matched, key=lambda x: x[0])
                    trimmed_title = raw_title[brand_pos:] if brand_pos > 0 else raw_title

                    titles_by_brand.setdefault(b, set()).add(trimmed_title)
                    # ✅ NEW: 예전엔 seen_raw_titles로 "이미 나온 제목"이면 통째로 버려서,
                    # 같은 제목을 쓰는 두 번째 스토어의 실제 링크/가격/썸네일이 아예 기록조차 안 됐음.
                    # 이제 제목이 같아도 스토어별 실제 아이템 정보는 전부 보관한다.
                    src_list = raw_title_sources.setdefault(trimmed_title, [])
                    if clean_link not in {s["clean_link"] for s in src_list}:
                        src_list.append({
                            "mall_name": mall_name, "link": link, "clean_link": clean_link,
                            "price": int(item.get("lprice", "0")), "image": item.get("image", ""),
                            "naver_product_id": item.get("productId", ""),
                            "store_title": raw_title
                        })
        time.sleep(0.3)

    cleaned_map = clean_titles_with_ai(titles_by_brand) if titles_by_brand else {}

    # ✅ FIX: split_rules에 이미 같은 (브랜드, 상품명키워드)로 서로 다른 카테고리가
    # 2개 이상 등록되어 있으면 -> "이 상품명은 실제로 여러 카테고리로 존재한다"고 어드민이
    # 확인해준 것으로 보고, 그 상품명은 앞으로 다수결 강제 통합 대상에서 아예 제외한다.
    # (링크 단위로만 기억하면, 나중에 새로 발견되는 다른 판매처 링크가 계속 다수결에
    # 다시 휩쓸려서 같은 실수가 반복됨 — 상품명 단위로 기억해야 한 번의 판단이 영구 반영됨)
    no_merge_keys = set()
    split_categories_by_key = {}
    for correct_title in split_rules.values():
        if "|" not in correct_title: continue
        b_name, c_title = correct_title.split("|", 1)
        parts = c_title.strip().split()
        if not parts: continue
        key = (b_name.strip(), parts[0])
        split_categories_by_key.setdefault(key, set()).add(parts[-1] if len(parts) > 1 else parts[0])
    for key, cats in split_categories_by_key.items():
        if len(cats) > 1:
            no_merge_keys.add(key)

    # 같은 (브랜드, 상품명키워드) 안에서 AI가 카테고리 단어를 다르게 뽑는 경우
    # (예: "나시" vs "가디건") no_merge_keys에 없으면 기본은 다수결로 하나로 합친다 —
    # 도매명+상품명 조합 자체가 대체로 고유해서 진짜 같은 상품일 확률이 높음.
    category_votes = {}
    for brand_b, titles in titles_by_brand.items():
        for raw_t in titles:
            clean_t = cleaned_map.get(raw_t, "")
            parts = clean_t.split()
            # ✅ FIX: AI가 단어를 하나만 반환한 경우(카테고리를 못 뽑고 상품명만 준 경우) 투표 대상에서 제외.
            # 예전엔 이럴 때 product_name 자기 자신을 카테고리로 셀프 투표해서, 그게 다수결로 이기면
            # "히로 히로"처럼 같은 단어가 중복 출력되는 사고가 났음.
            if len(parts) < 2: continue
            product_name = parts[0]
            category = parts[-1]
            key = (brand_b, product_name)
            category_votes.setdefault(key, {})
            category_votes[key][category] = category_votes[key].get(category, 0) + 1

    final_category = {}
    for key, votes in category_votes.items():
        final_category[key] = "나시" if "나시" in votes else max(votes.items(), key=lambda x: x[1])[0]

    print("\n🔍 정제 및 어드민 수정본 매칭 기반 전체 탐색 시작...")
    grouped_products = {}
    unique_items = set()

    raw_title_resolved = {}  # ✅ NEW: raw_title -> (brand, 최종 clean_title). 1단계 아이템을 재검색 없이 바로 그룹에 편입시키기 위한 역매핑.
    for brand_b, titles in titles_by_brand.items():
        for raw_t in titles:
            clean_t = cleaned_map.get(raw_t, "")
            parts = clean_t.split()
            if not parts: continue
            product_name = parts[0]
            key = (brand_b, product_name)
            if key in no_merge_keys:
                # 어드민이 이미 여러 카테고리로 나눠놓은 상품명 -> 강제 통합 없이 개별 판단 그대로 사용
                final_clean_title = clean_t
            elif key in final_category:
                final_clean_title = f"{product_name} {final_category[key]}"
            else:
                # ✅ FIX: 이 상품명키워드에 대해 카테고리 정보를 준 항목이 하나도 없으면(전부 한 단어 응답)
                # product_name을 억지로 카테고리 자리에도 채우지 말고 AI 원본 응답(clean_t) 그대로 사용
                final_clean_title = clean_t
            unique_items.add((brand_b, final_clean_title))
            raw_title_resolved[raw_t] = (brand_b, final_clean_title)

    for correct_title in split_rules.values():
        if "|" in correct_title:
            b_name, c_title = correct_title.split("|", 1)
            unique_items.add((b_name.strip(), c_title.strip()))

    assigned_links = set()  # 이번 크롤링에서 이미 어떤 상품군에 배정된 링크(URL) 추적 — 같은 리스팅이 여러 그룹에 중복 편입되는 것 방지

    def try_assign_item(brand, clean_title, mall_name, link, price, image, naver_product_id, store_title_text, validate_title=True):
        clean_link = link.split("?")[0].strip()
        if clean_link in blacklist: return
        store_id = is_target_store(mall_name, link, store_ids)
        if not store_id: return
        if clean_link in assigned_links: return  # 이미 다른 (brand, clean_title)에서 배정된 링크면 건너뜀

        forced_merge = merge_rules.get(clean_link)
        if forced_merge and "|" in forced_merge:
            dedup_key = forced_merge
        else:
            forced_title = split_rules.get(clean_link)
            if forced_title:
                forced_brand, forced_clean_title = forced_title.split("|", 1)
                if brand != forced_brand.strip() or clean_title != forced_clean_title.strip(): return
                dedup_key = forced_title
            elif clean_link in existing_links:
                # 이 링크는 예전 크롤링에서 이미 어떤 상품에 배정된 적이 있음. 이번 AI 정제 결과가
                # 그때와 다르게 나왔어도 무시하고 기존 product_id를 그대로 사용.
                dedup_key = existing_links[clean_link]['product_id']
            else:
                if validate_title:
                    # ✅ 2차(광역 sim검색) 결과만 검증. 1차(스토어별 검색) 결과는 raw_title 자체에서
                    # clean_title이 파생됐으므로 이 검증이 불필요 — 오히려 더 정확함.
                    raw_title_nospace = store_title_text.replace(" ", "").lower()
                    clean_words = clean_title.split()
                    if not clean_words: return
                    main_keyword = clean_words[0].lower()
                    category_keyword = clean_words[-1].lower()
                    brand_nospace = brand.replace(" ", "").lower()
                    if (brand_nospace + main_keyword) not in raw_title_nospace: return
                    if category_keyword not in raw_title_nospace: return
                dedup_key = f"{brand}|{clean_title}"

        if dedup_key not in grouped_products:
            grouped_products[dedup_key] = {
                "brand_name": dedup_key.split("|")[0], "title": dedup_key.split("|")[-1], "clean_title": dedup_key.split("|")[-1],
                "image_url": image,
                "product_id": dedup_key,  # 🔥 핵심: 네이버 ID 대신 불변의 '도매택|상품명'을 고유 ID로 콱 박아버립니다!
                "crawled_at": datetime.now().isoformat(), "store_links": [],
                "_best_prio": IMAGE_PRIORITY.get(store_id, 99)
            }

        existing_stores = [l['store_id'] for l in grouped_products[dedup_key]["store_links"]]
        if store_id not in existing_stores:
            grouped_products[dedup_key]["store_links"].append({
                "store_name": mall_name, "store_id": store_id,
                "price": price, "product_url": link,
                "store_title": store_title_text,
                "store_image": image,
                "naver_product_id": naver_product_id  # ✅ FIX: 리뷰 조회용 진짜 네이버 상품ID 보관
            })
            new_prio = IMAGE_PRIORITY.get(store_id, 99)
            if new_prio < grouped_products[dedup_key]["_best_prio"]:
                grouped_products[dedup_key]["image_url"] = image
                grouped_products[dedup_key]["_best_prio"] = new_prio

        assigned_links.add(clean_link)

    # ── 1차: 1단계(스토어별 직접 검색)에서 이미 is_target_store로 검증된 아이템을
    # 재검색 없이 바로 편입. 예전엔 이 정보를 버리고 2단계 sim검색 재발견에만 의존했는데,
    # 인기 상품일수록 다른 판매처에 밀려 sim 200위 안에 못 들어서 멀쩡히 취급 중인
    # 타겟 스토어가 크롤링마다 랜덤하게 누락되는 원인이었음.
    phase1_added = 0
    for raw_t, sources in raw_title_sources.items():
        resolved = raw_title_resolved.get(raw_t)
        if not resolved: continue
        brand, clean_title = resolved
        for src in sources:
            before = len(assigned_links)
            try_assign_item(brand, clean_title, src["mall_name"], src["link"], src["price"], src["image"],
                             src["naver_product_id"], src["store_title"], validate_title=False)
            if len(assigned_links) > before: phase1_added += 1
    print(f"  📌 1차(스토어별 직접 검색)에서 바로 편입된 링크: {phase1_added}개")

    # ── 2차: 브랜드+상품명 광역 검색으로, 1차가 놓친(예: 카테고리당 100개 제한 밖) 추가 스토어 보충 ──
    for brand, clean_title in unique_items:
        search_query = f"{brand} {clean_title}"
        items = search_naver(search_query, display=100, sort="sim")
        items = items + search_naver(search_query, display=100, sort="sim", start=101)  # expand to 200 results (2 pages) to catch listings ranked below top 100

        for item in items:
            mall_name, link = item.get("mallName", ""), item.get("link", "")
            try_assign_item(
                brand, clean_title, mall_name, link,
                int(item.get("lprice", "0")), item.get("image", ""), item.get("productId", ""),
                re.sub(r"<[^>]+>", "", item.get("title", "")), validate_title=True
            )

    # ✅ NEW: admin이 병합(merge_rules)/분리(split_rules)로 확정한 링크는 이번 크롤링의
    # Naver 재검색에서 우연히 재발견되지 못해도(랭킹 변동, is_target_store 실패 등) 사라지면 안 됨.
    # 검색 결과 재등장 여부에 의존하지 않고, DB에 남아있던 마지막 값으로 그대로 복원한다.
    # (단, 이 링크가 DB에서도 이미 지워진 상태였다면 복원할 데이터가 없어 못 살림 —
    #  그 경우엔 이번 패치 이후 다시 발견될 때 admin에서 한 번 더 병합해줘야 함)
    forced_targets = {}
    for url, target in merge_rules.items():
        if "|" in target: forced_targets[url] = target.strip()
    for url, target in split_rules.items():
        if "|" in target: forced_targets.setdefault(url, target.strip())

    restored = 0
    for clean_url, target in forced_targets.items():
        if clean_url in assigned_links: continue      # 이번에 정상적으로 재발견됨 -> 스킵
        if clean_url in blacklist: continue            # 이후 admin이 삭제 처리했으면 복원 안 함
        if target not in grouped_products: continue    # 합쳐질 대상 상품 자체가 이번에 아예 안 잡혔으면 스킵(더 큰 별개 문제)
        cached = existing_links.get(clean_url)
        if not cached or not cached.get('store_id'): continue  # DB에도 예전 기록이 없으면 복원 불가

        existing_stores = [l['store_id'] for l in grouped_products[target]["store_links"]]
        if cached['store_id'] not in existing_stores:
            grouped_products[target]["store_links"].append({
                "store_name": cached.get('store_name', ''), "store_id": cached['store_id'],
                "price": cached.get('price', 0), "product_url": clean_url,
                "store_title": cached.get('store_title', ''),
                "store_image": cached.get('store_image', ''),
                "naver_product_id": ""
            })
            restored += 1
        assigned_links.add(clean_url)

    if restored:
        print(f"  🔗 이번 검색에서 재발견 안 됐지만 병합/분리 확정 기록으로 복원된 링크: {restored}개")

    final_data = []
    print("\n📸 썸네일 다운 및 리뷰 수집 중...")
    for dedup_key, p in grouped_products.items():
        if dedup_key in rename_rules:
            p["title"] = rename_rules[dedup_key].split("|")[-1]
            p["clean_title"] = p["title"]
            
        # ✅ NEW: 클라우드 러너(SKIP_LOCAL_IMAGE_DOWNLOAD=true)에서는 다운로드 생략, 로컬에서는 기존대로 동작
        p["local_image"] = None if SKIP_LOCAL_IMAGE_DOWNLOAD else download_image(p["image_url"], p["product_id"])

        # ✅ FIX: 예전엔 p["product_id"](="브랜드|상품명" 문자열)를 그대로 네이버 리뷰 API에 넘겨서
        # 항상 실패(빈 배열)했음. 이제 store_links 중 우선순위가 가장 높은 곳의 진짜 네이버 productId를 사용.
        review_pid = None
        if p["store_links"]:
            sorted_by_prio = sorted(p["store_links"], key=lambda l: IMAGE_PRIORITY.get(l["store_id"], 99))
            for l in sorted_by_prio:
                if l.get("naver_product_id"):
                    review_pid = l["naver_product_id"]
                    break
        p["reviews"] = get_reviews(review_pid)

        del p["_best_prio"]
        final_data.append(p)

    OUTPUT_FILE.write_text(json.dumps(final_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n🎉 갓벽 정제 완료! 총 {len(final_data)}개의 유일 상품 데이터 완성 -> {OUTPUT_FILE}")

if __name__ == "__main__":
    run()
