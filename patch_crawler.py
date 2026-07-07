# crawler.py에 category 파라미터 추가 패치
content = open("crawler.py", "r", encoding="utf-8").read()

# 1. search 함수 시그니처 수정
old1 = 'def search(keyword, display=100, sort="date"):'
new1 = 'def search(keyword, display=100, sort="date", category=None):'
content = content.replace(old1, new1)

# 2. params에 category 추가
old2 = '''    params = {
        "query"  : keyword,
        "display": display,
        "sort"   : sort,
    }
    res = requests.get('''
new2 = '''    params = {
        "query"  : keyword,
        "display": display,
        "sort"   : sort,
    }
    if category:
        params["category"] = category
    res = requests.get('''
content = content.replace(old2, new2)

# 3. FASHION_CATEGORY 단수 → FASHION_CATEGORIES 복수로
old3 = 'FASHION_CATEGORY = "50000167"'
new3 = '''FASHION_CATEGORIES = [
    "50000167",  # 여성의류
    "50000190",  # 여성신발
    "50000174",  # 패션잡화/액세서리
]'''
if old3 in content:
    content = content.replace(old3, new3)

# 4. 단일 카테고리 검색 → 다중 카테고리 반복으로
old4 = '''        for kw in kw_list:
            try:
                items = search(kw, display=ITEMS_PER_STORE, sort="date")
            except Exception as e:
                print(f"오류({e})", end=" ")
                continue

            for item in items:'''
new4 = '''        for kw in kw_list:
            for cat in FASHION_CATEGORIES:
                try:
                    items = search(kw, display=ITEMS_PER_STORE, sort="date", category=cat)
                except Exception as e:
                    continue

                for item in items:'''

if old4 in content:
    content = content.replace(old4, new4)
    # 들여쓰기 수정: for item 블록 4칸 추가
    lines = content.split('\n')
    new_lines = []
    in_block = False
    for i, line in enumerate(lines):
        if '                for item in items:' in line:
            new_lines.append(line)
            in_block = True
        elif in_block:
            stripped = line.lstrip(' ')
            spaces = len(line) - len(stripped)
            if stripped == '':
                new_lines.append(line)
            elif spaces >= 16:
                new_lines.append('    ' + line)
            else:
                in_block = False
                new_lines.append(line)
        else:
            new_lines.append(line)
    content = '\n'.join(new_lines)

open("crawler.py", "w", encoding="utf-8").write(content)

# 검증
try:
    compile(content, 'crawler.py', 'exec')
    print("패치 완료! 문법 OK")
    if 'category=None' in content:
        print("category 파라미터 추가됨")
    if 'FASHION_CATEGORIES' in content:
        print("다중 카테고리 추가됨")
except SyntaxError as e:
    print(f"문법 오류: {e}")
