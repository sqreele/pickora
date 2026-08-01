(async function () {
  const container = document.getElementById("comparison");
  let selected = [];
  try {
    selected = JSON.parse(localStorage.getItem("pickoraCompare") || "[]").slice(0, 4);
  } catch {}
  if (!selected.length) {
    container.innerHTML = "<p>ยังไม่มีสินค้าที่เลือก กรุณาเปิดหน้าสินค้าแล้วกด “เพิ่มเพื่อเปรียบเทียบ”</p>";
    return;
  }
  try {
    const response = await fetch("/data/products.json", {cache: "no-store"});
    if (!response.ok) throw new Error("products unavailable");
    const products = await response.json();
    const items = selected.map(id => products.find(product => product.id === id)).filter(Boolean);
    if (!items.length) throw new Error("selected products expired");
    const rows = [
      ["สินค้า", item => `<a href="${escapeHtml(item.detailUrl)}"><img src="${escapeHtml(item.image)}" alt="" loading="lazy" width="180" height="180"><strong>${escapeHtml(item.title)}</strong></a>`],
      ["ราคา", item => Number(item.price) > 0 ? `฿${Number(item.price).toLocaleString("th-TH")}` : "ดูราคาล่าสุด"],
      ["Pickora Score", item => item.pickoraScore || "–"],
      ["คะแนน", item => item.rating || "–"],
      ["ยอดขาย", item => Number(item.sold || 0).toLocaleString("th-TH")],
      ["หมวด", item => escapeHtml(item.category || "–")]
    ];
    container.innerHTML = `<table class="comparison-table"><tbody>${rows.map(([label, value]) =>
      `<tr><th>${label}</th>${items.map(item => `<td>${value(item)}</td>`).join("")}</tr>`
    ).join("")}</tbody></table>`;
  } catch {
    container.innerHTML = "<p>ไม่สามารถโหลดสินค้าที่เลือกได้ กรุณากลับไปเลือกใหม่</p>";
  }

  function escapeHtml(value) {
    const element = document.createElement("span");
    element.textContent = String(value ?? "");
    return element.innerHTML;
  }
}());
