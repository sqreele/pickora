let products = [];
let activeCategory = "ทั้งหมด";

const grid = document.getElementById("grid");
const filters = document.getElementById("filters");
const search = document.getElementById("search");
const empty = document.getElementById("empty");
const feedStatus = document.getElementById("feedStatus");

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

function renderFilters() {
  const categories = [
    "ทั้งหมด",
    ...new Set(products.map(p => p.category).filter(Boolean).slice(0, 12))
  ];

  filters.innerHTML = categories.map(category => `
    <button class="filter ${category === activeCategory ? "active" : ""}"
      data-category="${esc(category)}">${esc(category)}</button>
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
      activeCategory === "ทั้งหมด" || product.category === activeCategory;
    const text = `${product.title} ${product.category} ${product.shop}`.toLowerCase();
    return categoryOk && text.includes(keyword);
  });

  grid.innerHTML = visible.map(product => `
    <article class="card">
      <img src="${esc(product.image)}" alt="${esc(product.title)}" loading="lazy"
        onerror="this.src='https://placehold.co/800x800?text=Pickora'">
      <div class="card-body">
        <div class="category">${esc(product.category || "สินค้าแนะนำ")}</div>
        <h3 class="title">${esc(product.title)}</h3>
        <div class="meta">
          <span>${product.rating ? `★ ${esc(product.rating)}` : ""}</span>
          <span>${esc(formatSold(product.sold))}</span>
        </div>
        <div class="price">${esc(formatPrice(product.price))}</div>
        <a class="primary buy" href="${esc(product.link)}"
          target="_blank" rel="nofollow sponsored noopener"
          data-product="${esc(product.title)}">เช็กราคาใน Shopee <span aria-hidden="true">→</span></a>
      </div>
    </article>
  `).join("");

  empty.hidden = visible.length > 0;
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
    feedStatus.textContent =
      `อัปเดตล่าสุด ${new Date(status.updatedAt).toLocaleString("th-TH")} · ${products.length.toLocaleString("th-TH")} สินค้า`;
  } else {
    feedStatus.textContent = `${products.length.toLocaleString("th-TH")} สินค้า`;
  }

  renderFilters();
  renderProducts();
}

search.addEventListener("input", renderProducts);

document.addEventListener("click", event => {
  const link = event.target.closest("[data-product]");
  if (!link) return;
  const stats = JSON.parse(localStorage.getItem("pickoraClicks") || "{}");
  const name = link.dataset.product;
  stats[name] = (stats[name] || 0) + 1;
  localStorage.setItem("pickoraClicks", JSON.stringify(stats));
});

document.getElementById("year").textContent = new Date().getFullYear();

loadProducts().catch(error => {
  console.error(error);
  feedStatus.textContent = "ระบบกำลังเตรียมข้อมูลสินค้า กรุณาตรวจสอบ worker log";
  empty.hidden = false;
  empty.textContent = "ยังไม่มีข้อมูลสินค้า";
});
