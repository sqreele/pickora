let products = [];
let activeCategory = "ทั้งหมด";

const grid = document.getElementById("grid");
const filters = document.getElementById("filters");
const search = document.getElementById("search");
const sort = document.getElementById("sort");
const empty = document.getElementById("empty");
const feedStatus = document.getElementById("feedStatus");
const popularGrid = document.getElementById("popularGrid");
const recentGrid = document.getElementById("recentGrid");
const recentlyViewed = document.getElementById("recentlyViewed");
let searchAnalyticsTimer;

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "ดูราคาล่าสุด";
  return `฿${number.toLocaleString("th-TH", {maximumFractionDigits: 0})}`;
}

function formatSold(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  return `ขายแล้ว ${number.toLocaleString("th-TH")}`;
}

function productDetailUrl(product) {
  if (product.detailUrl) return product.detailUrl;
  if (/^[a-f0-9]{16}$/.test(product.id || "")) return `/products/${product.id}/`;
  return product.url || "/#products";
}

function displayCategory(value) {
  const category = String(value ?? "").trim();
  const placeholder = category.toLocaleLowerCase("en-US");
  if (["", "nan", "none", "null", "product", "products", "foreign", "ต่างด้าว"].includes(placeholder)) {
    return "สินค้าแนะนำ";
  }
  return category;
}

function productCard(product) {
  const detailUrl = productDetailUrl(product);
  const category = displayCategory(product.category);
  return `
    <article class="card">
      <a class="card-image-link" href="${esc(detailUrl)}"><img src="${esc(product.image)}" alt="${esc(product.title)}" loading="lazy" decoding="async" width="600" height="600"
        onerror="this.src='https://placehold.co/800x800?text=Pickora'"></a>
      <div class="card-body">
        ${product.categoryUrl ? `<a class="category" href="${esc(product.categoryUrl)}">${esc(category)}</a>` : ""}
        <h3 class="title">${esc(product.title)}</h3>
        <div class="meta"><span>${product.rating ? `★ ${esc(product.rating)}` : ""}</span><span>${esc(formatSold(product.sold))}</span></div>
        ${product.pickoraScore || product.score ? `<div class="score-badge">Pickora Score ${esc(product.pickoraScore || product.score)}</div>` : ""}
        <div class="price">${esc(formatPrice(product.price))}</div>
        <a class="primary buy" href="${esc(detailUrl)}" aria-label="ดูรายละเอียด ${esc(product.title)}">ดูรายละเอียด <span aria-hidden="true">→</span></a>
      </div>
    </article>`;
}

function renderFilters() {
  const categoryCounts = products.reduce((counts, product) => {
    const category = displayCategory(product.category);
    if (category) counts.set(category, (counts.get(category) || 0) + 1);
    return counts;
  }, new Map());

  const categories = [
    "ทั้งหมด",
    ...[...categoryCounts]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([category]) => category)
  ];

  filters.innerHTML = categories.map(category => `
    <button class="filter ${category === activeCategory ? "active" : ""}"
        data-category="${esc(category)}">${esc(category)} <span>${category === "ทั้งหมด" ? products.length : categoryCounts.get(category)}</span></button>
  `).join("");

  filters.querySelectorAll(".filter").forEach(button => {
    button.addEventListener("click", () => {
      activeCategory = button.dataset.category;
      renderFilters();
      renderProducts();
    });
  });
}

function renderProducts() {
  const keyword = search.value.trim().toLowerCase();

  const visible = products.filter(product => {
    const categoryOk =
      activeCategory === "ทั้งหมด" || displayCategory(product.category) === activeCategory;
    const text = `${product.title} ${product.category} ${product.shop}`.toLowerCase();
    return categoryOk && text.includes(keyword);
  }).sort((a, b) => {
    switch (sort.value) {
      case "price-asc":
        return (Number(a.price) || Number.MAX_SAFE_INTEGER) - (Number(b.price) || Number.MAX_SAFE_INTEGER);
      case "price-desc":
        return (Number(b.price) || 0) - (Number(a.price) || 0);
      case "rating-desc":
        return (Number(b.rating) || 0) - (Number(a.rating) || 0);
      case "sold-desc":
        return (Number(b.sold) || 0) - (Number(a.sold) || 0);
      default:
        return (Number(b.score) || 0) - (Number(a.score) || 0);
    }
  });

  grid.innerHTML = visible.map(productCard).join("");

  empty.hidden = visible.length > 0;
  return visible.length;
}

function renderDiscovery() {
  const popular = [...products]
    .sort((a, b) => (Number(b.sold) || 0) - (Number(a.sold) || 0))
    .slice(0, 4);
  popularGrid.innerHTML = popular.map(productCard).join("");

  try {
    const recent = JSON.parse(localStorage.getItem("pickoraRecentlyViewed") || "[]")
      .filter(item => item?.id && item?.url)
      .slice(0, 4);
    recentlyViewed.hidden = recent.length === 0;
    recentGrid.innerHTML = recent.map(productCard).join("");
  } catch {
    recentlyViewed.hidden = true;
  }
}

async function loadProducts() {
  const [productResponse, statusResponse] = await Promise.all([
    fetch("/data/products.json", {cache: "no-store"}),
    fetch("/data/feed-status.json", {cache: "no-store"}).catch(() => null)
  ]);

  if (!productResponse.ok) {
    throw new Error("products.json ยังไม่พร้อม");
  }

  products = await productResponse.json();

  if (statusResponse && statusResponse.ok) {
    const status = await statusResponse.json();
    const updatedAt = new Date(status.updatedAt);
    const stale = Date.now() - updatedAt.getTime() > 48 * 60 * 60 * 1000;
    feedStatus.textContent =
      `${stale ? "⚠️ ข้อมูลอาจไม่ล่าสุด · " : ""}อัปเดตล่าสุด ${updatedAt.toLocaleString("th-TH")} · ${products.length.toLocaleString("th-TH")} สินค้า`;
    feedStatus.classList.toggle("stale-status", stale);
  } else {
    feedStatus.textContent = `${products.length.toLocaleString("th-TH")} สินค้า`;
  }

  renderFilters();
  renderProducts();
  renderDiscovery();
}

search.addEventListener("input", () => {
  const resultCount = renderProducts();
  clearTimeout(searchAnalyticsTimer);
  const term = search.value.trim();
  if (term.length >= 2) {
    searchAnalyticsTimer = setTimeout(() => {
      document.dispatchEvent(new CustomEvent("pickora:search", {
        detail: {term, resultCount, category: activeCategory, sort: sort.value}
      }));
    }, 800);
  }
});
sort.addEventListener("change", renderProducts);

const year = document.getElementById("year");
if (year) year.textContent = new Date().getFullYear();

loadProducts().catch(error => {
  console.error(error);
  feedStatus.textContent = "ระบบกำลังเตรียมข้อมูลสินค้า กรุณาตรวจสอบ worker log";
  empty.hidden = false;
  empty.textContent = "ยังไม่มีข้อมูลสินค้า";
});
