(function () {
  const labels = {
    affiliate_click: "Affiliate clicks", search: "Searches", share: "Shares",
    add_to_compare: "Added to compare", experiment_impression: "CTA impressions",
    experiment_conversion: "CTA conversions", sign_up: "Email sign-ups",
    experiment_impression_A: "CTA A impressions", experiment_conversion_A: "CTA A conversions",
    experiment_impression_B: "CTA B impressions", experiment_conversion_B: "CTA B conversions"
  };
  function render() {
    let metrics = {};
    try { metrics = JSON.parse(localStorage.getItem("pickoraAnalytics") || "{}"); } catch {}
    document.getElementById("metricGrid").innerHTML = Object.entries(labels).map(([key, label]) =>
      `<article class="metric-card"><span>${label}</span><strong>${Number(metrics[key] || 0).toLocaleString()}</strong></article>`
    ).join("");
  }
  async function renderSystemStatus() {
    const grid = document.getElementById("systemGrid");
    try {
      const [seoResponse, linkResponse, feedResponse] = await Promise.all([
        fetch("/data/seo-status.json", {cache: "no-store"}),
        fetch("/data/link-health.json", {cache: "no-store"}).catch(() => null),
        fetch("/data/feed-status.json", {cache: "no-store"})
      ]);
      const seo = seoResponse.ok ? await seoResponse.json() : {};
      const links = linkResponse?.ok ? await linkResponse.json() : {};
      const feed = feedResponse.ok ? await feedResponse.json() : {};
      const cards = [
        ["Feed", feed.status || "unknown"],
        ["Products", seo.products ?? "–"],
        ["Categories", seo.categories ?? "–"],
        ["Invalid dropped", seo.invalidProductsDropped ?? "–"],
        ["Link health", links.status || "not checked"],
        ["Last generated", seo.generatedAt ? new Date(seo.generatedAt).toLocaleString("th-TH") : "–"]
      ];
      grid.innerHTML = cards.map(([label, value]) =>
        `<article class="metric-card"><span>${label}</span><strong>${value}</strong></article>`
      ).join("");
    } catch {
      grid.innerHTML = '<div class="article-note">ยังไม่มีรายงานระบบ กรุณารัน worker ก่อน</div>';
    }
  }
  async function renderAggregate(token) {
    const status = document.getElementById("aggregateStatus");
    const aggregateGrid = document.getElementById("aggregateGrid");
    const funnelGrid = document.getElementById("funnelGrid");
    try {
      const response = await fetch("/data/analytics-summary.json", {
        cache: "no-store", headers: {"X-Analytics-Token": token}
      });
      if (!response.ok) throw new Error("summary unavailable");
      const report = await response.json();
      if (report.status !== "ready") {
        status.textContent = report.status === "error"
          ? "เชื่อม Google APIs ไม่สำเร็จ กรุณาตรวจ worker log และสิทธิ์ service account"
          : "ยังไม่ได้ตั้งค่า GA4_PROPERTY_ID หรือ Google service account";
        return;
      }
      status.textContent = `อัปเดต ${new Date(report.generatedAt).toLocaleString("th-TH")}`;
      const ga = report.ga4?.totals || {};
      const search = report.searchConsole || {};
      const cards = [
        ["Active users", ga.activeUsers ?? "–"],
        ["Sessions", ga.sessions ?? "–"],
        ["Page views", ga.pageViews ?? "–"],
        ["Organic clicks", search.clicks ?? "–"],
        ["Impressions", search.impressions ?? "–"],
        ["Average position", search.averagePosition ?? "–"]
      ];
      aggregateGrid.innerHTML = cards.map(([label, value]) =>
        `<article class="metric-card"><span>${label}</span><strong>${Number.isFinite(Number(value)) ? Number(value).toLocaleString() : value}</strong></article>`
      ).join("");
      const events = report.ga4?.events || {};
      const funnel = [
        ["Product selected", events.select_item || 0],
        ["Product viewed", events.view_item || 0],
        ["Affiliate intent", events.begin_checkout || 0],
        ["Outbound clicks", events.affiliate_click || 0]
      ];
      funnelGrid.innerHTML = funnel.map(([label, value]) =>
        `<article class="metric-card"><span>${label}</span><strong>${Number(value).toLocaleString()}</strong></article>`
      ).join("");
    } catch {
      status.textContent = "ยังไม่มี analytics summary กรุณารัน worker หลังตั้งค่า Google APIs";
    }
  }
  document.getElementById("clearMetrics").addEventListener("click", () => {
    localStorage.removeItem("pickoraAnalytics");
    render();
  });
  document.getElementById("analyticsAccess").addEventListener("submit", event => {
    event.preventDefault();
    const token = document.getElementById("analyticsToken").value;
    sessionStorage.setItem("pickoraAnalyticsToken", token);
    renderAggregate(token);
  });
  render();
  renderSystemStatus();
  const savedToken = sessionStorage.getItem("pickoraAnalyticsToken");
  if (savedToken) {
    document.getElementById("analyticsToken").value = savedToken;
    renderAggregate(savedToken);
  }
}());
