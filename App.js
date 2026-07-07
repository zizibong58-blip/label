import { useEffect, useState } from "react"
import { supabase } from "./supabase"
import "./App.css"

const CATEGORIES = [
  { label: "전체",     keyword: "" },
  { label: "블라우스", keyword: "블라우스" },
  { label: "셔츠",     keyword: "셔츠" },
  { label: "니트",     keyword: "니트" },
  { label: "가디건",   keyword: "가디건" },
  { label: "원피스",   keyword: "원피스" },
  { label: "팬츠",     keyword: "팬츠" },
  { label: "스커트",   keyword: "스커트" },
  { label: "아우터",   keyword: "자켓" },
  { label: "티셔츠",   keyword: "티셔츠" },
  { label: "신발",     keyword: "샌들|슬리퍼|스니커즈|플랫|로퍼|힐|뮬|슈즈" },
  { label: "악세서리", keyword: "귀걸이|목걸이|반지|팔찌|스크런치|헤어" },
]

// 카테고리 단어에서 끊기
const CAT_WORDS = [
  "블라우스","니트","가디건","팬츠","스커트","원피스","자켓","코트",
  "셔츠","티셔츠","탑","슬랙스","데님","점프수트","세트","나시","뷔스티에"
]

function sharpTitle(title) {
  if (!title) return ""
  // 숫자+부 패턴 제거 (7부, 5부 등)
  let t = title.replace(/\d+부/g, "").replace(/\s+/g, " ").trim()
  const words = t.split(" ")
  const result = []
  for (const word of words) {
    if (!word) continue
    result.push(word)
    if (CAT_WORDS.some(cat => word.includes(cat))) break
    if (result.length >= 4) break
  }
  return result.join(" ")
}

export default function App() {
  const [products, setProducts] = useState([])
  const [category, setCategory] = useState(CATEGORIES[0])
  const [loading, setLoading]   = useState(true)
  const [tab, setTab]           = useState("feed")
  const [selected, setSelected] = useState(null)
  const [showSearch, setShowSearch] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")

  useEffect(() => {
    if (!showSearch) fetchProducts()
  }, [category])

  async function fetchProducts() {
    setLoading(true)
    let query = supabase
      .from("products")
      .select("*")
      .order("crawled_at", { ascending: false })
      .limit(60)
    if (category.keyword) {
      if (category.keyword.includes("|")) {
        const keywords = category.keyword.split("|")
        const filter = keywords.map(k => `clean_title.ilike.%${k}%`).join(",")
        query = query.or(filter)
      } else {
        query = query.ilike("clean_title", `%${category.keyword}%`)
      }
    }
    const { data } = await query
    setProducts(data || [])
    setLoading(false)
  }

  async function handleSearch(q) {
    if (!q.trim()) { fetchProducts(); return }
    setLoading(true)
    // 브랜드명 검색
    const [{ data: d1 }, { data: d2 }] = await Promise.all([
      supabase.from("products").select("*").ilike("brand_name", `%${q}%`).limit(40),
      supabase.from("products").select("*").ilike("clean_title", `%${q}%`).limit(40),
    ])
    // 중복 제거 후 합치기
    const seen = new Set()
    const merged = []
    for (const item of [...(d1 || []), ...(d2 || [])]) {
      if (!seen.has(item.id)) { seen.add(item.id); merged.push(item) }
    }
    setProducts(merged)
    setLoading(false)
  }

  async function handleClick(product) {
    await supabase.from("clicks").insert({ product_id: product.product_id })
    await supabase
      .from("products")
      .update({ click_count: (product.click_count || 0) + 1 })
      .eq("product_id", product.product_id)
    setSelected(product)
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div className="header-left">
            <span className="logo">LABEL</span>
            {!showSearch && (
              <nav className="tab-nav">
                <button className={tab === "feed" ? "tab-btn active" : "tab-btn"} onClick={() => setTab("feed")}>피드</button>
                <button className={tab === "ranking" ? "tab-btn active" : "tab-btn"} onClick={() => setTab("ranking")}>🔥 랭킹</button>
                <button className={tab === "sale" ? "tab-btn active" : "tab-btn"} onClick={() => setTab("sale")} style={{color: tab === "sale" ? "" : "#D85A30"}}>🏷️ SALE</button>
              </nav>
            )}
            {showSearch && (
              <input
                className="search-input"
                autoFocus
                placeholder="도매택 또는 상품명 검색"
                value={searchQuery}
                onChange={e => { setSearchQuery(e.target.value); handleSearch(e.target.value) }}
              />
            )}
          </div>
          <div className="header-right">
            <button
              className="icon-btn"
              onClick={() => {
                setShowSearch(!showSearch)
                if (showSearch) { setSearchQuery(""); fetchProducts() }
              }}
            >{showSearch ? "✕" : "🔍"}</button>
            {!showSearch && <>
              <button className="cta-btn brand-btn">BRANDS</button>
              <button className="cta-btn buy-btn">SHOP</button>
            </>}
          </div>
        </div>
        {tab === "feed" && !showSearch && (
          <div className="category-bar">
            {CATEGORIES.map(c => (
              <button
                key={c.label}
                className={category.label === c.label ? "cat-btn active" : "cat-btn"}
                onClick={() => setCategory(c)}
              >{c.label}</button>
            ))}
          </div>
        )}
      </header>

      <main className="main">
        {tab === "feed"    && <Feed products={products} loading={loading} onClickProduct={handleClick} />}
        {tab === "ranking" && !showSearch && <Ranking onClickProduct={handleClick} />}
        {tab === "sale"    && <Sale onClickProduct={handleClick} />}
      </main>

      {selected && <ProductModal product={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

function Feed({ products, loading, onClickProduct }) {
  if (loading) return <div className="loading">불러오는 중...</div>
  if (!products.length) return <div className="empty">상품이 없어요</div>
  return (
    <div className="grid">
      {products.map(p => (
        <div key={p.id} className="card" onClick={() => onClickProduct(p)}>
          <div className="card-img-wrap">
            <img src={p.image_url} alt={p.clean_title} className="card-img" />
          </div>
          <div className="card-body">
            <div className="card-brand">{p.brand_name}</div>
            <div className="card-title">{sharpTitle(p.clean_title)}</div>
            <div className="card-clicks">클릭 {p.click_count || 0}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

function ProductModal({ product, onClose }) {
  const [links, setLinks]     = useState([])
  const [reviews, setReviews] = useState([])

  useEffect(() => {
    supabase.from("store_links").select("*").eq("product_id", product.product_id)
      .then(({ data }) => {
        const all = (data || []).filter(l => l.price > 0)
        if (all.length === 0) { setLinks(data || []); return }
        // 최저가 대비 3배 초과 제외
        const minPrice = Math.min(...all.map(l => l.price))
        const filtered = all.filter(l => l.price <= minPrice * 3)
        const sorted   = filtered.sort((a, b) => a.price - b.price)
        setLinks(sorted)
      })
    supabase.from("reviews").select("*").eq("product_id", product.product_id)
      .then(({ data }) => setReviews(data || []))
  }, [product])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>✕</button>
        <img src={product.image_url} alt={product.clean_title} className="modal-img" />
        <div className="modal-body">
          <div className="modal-brand">{product.brand_name}</div>
          <div className="modal-title">{sharpTitle(product.clean_title)}</div>

          <div className="modal-section">
            <div className="modal-section-title">이 상품을 파는 소매상 ({links.length})</div>
            {links.length === 0 && <div style={{fontSize:13,color:"#bbb"}}>소매상 정보 없음</div>}
            {links.map((lnk, i) => (
              <div key={i} className="store-row">
                <div className="store-info">
                  <div className="store-name">{lnk.store_name}</div>
                  {lnk.price > 0 && <div className="store-price">{lnk.price.toLocaleString()}원</div>}
                </div>
                <button className="goto-btn" onClick={() => window.open(lnk.product_url, "_blank")}>바로가기</button>
              </div>
            ))}
          </div>

          {reviews.length > 0 && (
            <div className="modal-section">
              <div className="modal-section-title">구매자 리뷰</div>
              {reviews.slice(0, 3).map((r, i) => (
                <div key={i} className="review-row">
                  <span className="review-score">{"⭐".repeat(r.score || 5)}</span>
                  <span className="review-content">{r.content}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Sale({ onClickProduct }) {
  const [items, setItems]     = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    supabase
      .from("sale_products")
      .select("*")
      .order("discount_rate", { ascending: false })
      .limit(60)
      .then(({ data }) => {
        setItems(data || [])
        setLoading(false)
      })
  }, [])

  if (loading) return <div className="loading">세일 상품 찾는 중...</div>
  if (!items.length) return <div className="empty">세일 상품이 없어요</div>

  return (
    <div className="grid">
      {items.map(p => (
        <div key={p.id} className="card" onClick={() => onClickProduct(p)}>
          <div className="card-img-wrap">
            <img src={p.image_url} alt={p.clean_title} className="card-img" />
            <div className="sale-badge">-{p.discount_rate}%</div>
          </div>
          <div className="card-body">
            <div className="card-brand">{p.brand_name}</div>
            <div className="card-title">{sharpTitle(p.clean_title)}</div>
            <div className="sale-price-row">
              <span className="sale-min">{Number(p.min_price).toLocaleString()}원~</span>
              <span className="sale-max">{Number(p.max_price).toLocaleString()}원</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function Ranking({ onClickProduct }) {
  const [items, setItems]       = useState([])
  const [period, setPeriod]     = useState("today")
  const [rankCat, setRankCat]   = useState(CATEGORIES[0])
  const [loading, setLoading]   = useState(true)

  const PERIODS = [
    { key: "today",   label: "오늘",  days: 1 },
    { key: "weekly",  label: "주간",  days: 7 },
    { key: "monthly", label: "월간",  days: 30 },
  ]

  useEffect(() => { fetchRanking() }, [period, rankCat])

  async function fetchRanking() {
    setLoading(true)
    const p    = PERIODS.find(x => x.key === period)
    const from = new Date()
    from.setDate(from.getDate() - p.days)

    // 기간 클릭 수집
    const { data: clicks } = await supabase
      .from("clicks").select("product_id")
      .gte("clicked_at", from.toISOString())

    const periodMap = {}
    ;(clicks || []).forEach(c => {
      periodMap[c.product_id] = (periodMap[c.product_id] || 0) + 1
    })

    // 전체 상품 + 누적 클릭수
    let query = supabase.from("products").select("*")
    if (rankCat.keyword) {
      query = query.ilike("clean_title", `%${rankCat.keyword}%`)
    }
    const { data: allProducts } = await query.limit(200)

    // 가중치 계산: 기간클릭 50% + 누적클릭 50%
    const maxPeriod = Math.max(...Object.values(periodMap), 1)
    const maxTotal  = Math.max(...(allProducts || []).map(p => p.click_count || 0), 1)

    const scored = (allProducts || [])
      .map(p => ({
        ...p,
        _score: (periodMap[p.product_id] || 0) / maxPeriod * 0.5
               + (p.click_count || 0) / maxTotal * 0.5
      }))
      .sort((a, b) => b._score - a._score)
      .slice(0, 20)

    setItems(scored)
    setLoading(false)
  }

  if (loading) return <div className="loading">랭킹 집계 중...</div>

  return (
    <div className="ranking-wrap">
      <div className="period-nav">
        {PERIODS.map(p => (
          <button key={p.key}
            className={period === p.key ? "period-btn active" : "period-btn"}
            onClick={() => setPeriod(p.key)}>{p.label}</button>
        ))}
      </div>
      <div className="category-bar" style={{marginBottom: 12}}>
        {CATEGORIES.map(c => (
          <button key={c.label}
            className={rankCat.label === c.label ? "cat-btn active" : "cat-btn"}
            onClick={() => setRankCat(c)}>{c.label}</button>
        ))}
      </div>
      <div className="ranking-list">
        {items.map((item, i) => (
          <div key={item.id} className="rank-row" onClick={() => onClickProduct(item)}>
            <div className="rank-num" style={{ color: i < 3 ? "#D85A30" : "inherit" }}>{i + 1}</div>
            <img src={item.image_url} alt={item.clean_title} className="rank-img" />
            <div className="rank-info">
              <div className="rank-brand">{item.brand_name}</div>
              <div className="rank-title">{sharpTitle(item.clean_title)}</div>
              <div className="rank-clicks">클릭 {item.click_count || 0}</div>
            </div>
          </div>
        ))}
        {!items.length && <div className="empty">해당 카테고리 데이터가 없어요</div>}
      </div>
    </div>
  )
}
